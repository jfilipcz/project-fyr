"""Runtime orchestration for Project Fyr."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from dataclasses import dataclass
import logging
from typing import Any

from kubernetes import client, config, watch
from kubernetes.config.config_exception import ConfigException

from .agent import InvestigatorAgent
from .config import Settings, settings
from .db import RolloutRepo, init_db
from .models import NotifyStatus, ReducedContext, RolloutStatus
from .slack import SlackNotifier
from .triage import triage_failure


logger = logging.getLogger(__name__)


def evaluate_deployment_phase(dep) -> str:
    """Map a Deployment status object to a coarse rollout phase."""

    status = dep.status or None
    available = getattr(status, "available_replicas", None) or getattr(status, "availableReplicas", None)
    desired = getattr(dep.spec, "replicas", 0) or 0
    conditions = {c.type: c.status for c in (getattr(status, "conditions", []) or [])}

    if available is not None and desired > 0 and available >= desired:
        return "STABLE"
    if conditions.get("Progressing") == "False":
        return "FAILED_PROGRESS"
    if conditions.get("Available") == "False":
        return "PENDING"
    return "ROLLING_OUT"


def list_deployment_pods(core_v1: client.CoreV1Api, dep) -> list:
    ns = dep.metadata.namespace
    selector = dep.spec.selector.match_labels or {}
    label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
    pods = core_v1.list_namespaced_pod(namespace=ns, label_selector=label_selector)
    return pods.items


@dataclass
class PodFailureSignals:
    crashloop_pods: int = 0
    image_pull_pods: int = 0
    pending_scheduling_pods: int = 0
    total_pods: int = 0


def analyze_pod_failures(pods: list) -> PodFailureSignals:
    signals = PodFailureSignals(total_pods=len(pods))
    for pod in pods:
        phase = (pod.status.phase or "").upper()
        for cs in pod.status.container_statuses or []:
            waiting = cs.state.waiting if cs.state else None
            if not waiting:
                continue
            reason = waiting.reason or ""
            if reason == "CrashLoopBackOff":
                signals.crashloop_pods += 1
            if reason in ("ImagePullBackOff", "ErrImagePull"):
                signals.image_pull_pods += 1
        if phase == "PENDING":
            signals.pending_scheduling_pods += 1
    return signals


def should_fail_early(signals: PodFailureSignals, min_pods: int = 1) -> bool:
    if signals.total_pods < min_pods:
        return False
    failing = signals.crashloop_pods + signals.image_pull_pods
    if failing >= max(1, signals.total_pods // 2):
        return True
    return False


def rollout_metadata_dict(rollout) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if rollout.team:
        metadata["team"] = rollout.team
    if rollout.metadata_json:
        metadata.update({k: v for k, v in rollout.metadata_json.items() if v is not None})
    return metadata


ANNOTATION_SLACK_CHANNEL = "project-fyr/slack-channel"
ANNOTATION_TEAM = "project-fyr/team"
ANNOTATION_PREFIX = "project-fyr/"


def parse_namespace_annotations(annotations: dict[str, str] | None) -> dict[str, Any]:
    annotations = annotations or {}
    namespace_specific = {k: v for k, v in annotations.items() if k.startswith(ANNOTATION_PREFIX)}
    metadata: dict[str, Any] = {}
    if namespace_specific:
        metadata["metadata_json"] = {"namespace_annotations": namespace_specific}
    if team := annotations.get(ANNOTATION_TEAM):
        metadata["team"] = team
    if channel := annotations.get(ANNOTATION_SLACK_CHANNEL):
        metadata["slack_channel"] = channel
    return metadata


def fetch_namespace_metadata(core_v1: client.CoreV1Api, namespace: str) -> dict[str, Any]:
    try:
        ns = core_v1.read_namespace(namespace)
    except Exception as exc:  # pragma: no cover - diagnostic path
        logger.error(f"namespace metadata error ({namespace}): {exc}")
        return {}
    return parse_namespace_annotations(getattr(ns.metadata, "annotations", None))


class NamespaceMetadataCache:
    def __init__(self, core_v1: client.CoreV1Api, ttl_seconds: int = 60):
        self._core_v1 = core_v1
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def get(self, namespace: str) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            entry = self._cache.get(namespace)
            if entry and now - entry[0] < self._ttl:
                return entry[1]
        data = fetch_namespace_metadata(self._core_v1, namespace)
        with self._lock:
            self._cache[namespace] = (now, data)
        return data


def handle_deployment_event(
    dep,
    event_type: str,
    repo: RolloutRepo,
    cluster: str,
    namespace_metadata: dict[str, Any] | None = None,
):
    if event_type == "DELETED":
        return

    labels = dep.metadata.labels or {}
    if labels.get("project-fyr/enabled") != "true":
        return

    ns = dep.metadata.namespace
    name = dep.metadata.name
    generation = dep.metadata.generation or 1
    ns_meta = namespace_metadata or {}

    rollout = repo.get_by_key(cluster, ns, name, generation)
    phase = evaluate_deployment_phase(dep)
    now = datetime.utcnow()

    if rollout is None:
        status = RolloutStatus.PENDING if phase == "PENDING" else RolloutStatus.ROLLING_OUT
        repo.create(
            cluster=cluster,
            namespace=ns,
            deployment=name,
            generation=generation,
            status=status,
            started_at=now,
            origin="k8s",
            metadata_json=ns_meta.get("metadata_json") or {},
            team=ns_meta.get("team"),
            slack_channel=ns_meta.get("slack_channel"),
        )
    else:
        if ns_meta:
            repo.update_metadata(
                rollout.id,
                metadata_json=ns_meta.get("metadata_json"),
                team=ns_meta.get("team"),
                slack_channel=ns_meta.get("slack_channel"),
            )


def reconcile_rollout(
    dep,
    rollout,
    now: datetime,
    repo: RolloutRepo,
    timeout: timedelta,
    core_v1: client.CoreV1Api | None = None,
):
    phase = evaluate_deployment_phase(dep)
    started_at = rollout.started_at or now
    age = now - started_at

    if core_v1 is not None:
        try:
            pods = list_deployment_pods(core_v1, dep)
            signals = analyze_pod_failures(pods)
        except Exception as exc:  # pragma: no cover - diagnostic path
            logger.error(f"pod analysis error: {exc}")
        else:
            if should_fail_early(signals, min_pods=1):
                repo.update_status(rollout.id, RolloutStatus.FAILED, failed_at=now)
                return

    if phase == "STABLE":
        repo.update_status(rollout.id, RolloutStatus.SUCCESS, completed_at=now)
        return

    if phase == "FAILED_PROGRESS":
        repo.update_status(rollout.id, RolloutStatus.FAILED, failed_at=now)
        return

    if age > timeout:
        repo.update_status(rollout.id, RolloutStatus.FAILED, failed_at=now)
        return

    new_status = RolloutStatus.PENDING if phase == "PENDING" else RolloutStatus.ROLLING_OUT
    if rollout.status != new_status:
        repo.update_status(rollout.id, new_status)


class AnalysisWorker:
    def __init__(self, repo: RolloutRepo, cluster: str, config: Settings):
        self._repo = repo
        self._cluster = cluster
        self._config = config
        self._agent = InvestigatorAgent(
            model_name=config.langchain_model_name,
            api_key=config.openai_api_key,
            api_base=config.openai_api_base,
            api_version=config.openai_api_version,
            azure_deployment=config.azure_deployment,
        )
        self._slack = SlackNotifier(
            token=config.slack_bot_token,
            default_channel=config.slack_default_channel,
            mock_log_file=config.slack_mock_log_file,
            base_url=config.slack_api_url,
        )

    def loop(self):
        while True:
            rollouts = self._repo.list_failed(self._cluster)
            for rollout in rollouts:
                try:
                    logger.info(f"Starting investigation for {rollout.namespace}/{rollout.deployment}")
                    
                    # Agentic investigation
                    analysis = self._agent.investigate(rollout.deployment, rollout.namespace)
                    
                    # Create a dummy ReducedContext for DB compatibility
                    # The agent pulls data dynamically, so we don't have a static reduced context to store.
                    # We store a placeholder to satisfy the schema.
                    reduced = ReducedContext(
                        namespace=rollout.namespace,
                        deployment=rollout.deployment,
                        generation=rollout.generation,
                        summary="Agentic Investigation",
                        phase="FAILED", # Assumed since we are processing failed rollouts
                        failing_pods=[],
                        log_clusters=[],
                        events=[],
                        argocd_status=None,
                    )

                    triage = triage_failure(reduced, analysis)
                    analysis.triage_team = triage.team
                    analysis.triage_reason = triage.reason
                    
                    metadata = rollout_metadata_dict(rollout)
                    metadata.update(
                        {
                            "triage_team": triage.team,
                            "triage_reason": triage.reason,
                        }
                    )
                    
                    channel = rollout.slack_channel
                    rollout_ref = f"{rollout.namespace}/{rollout.deployment}#{rollout.generation}"
                    
                    sent = self._slack.send_analysis(
                        channel=channel,
                        rollout_ref=rollout_ref,
                        analysis=analysis,
                        metadata=metadata,
                    )
                    
                    self._repo.update_notify_status(
                        rollout.id, NotifyStatus.SENT if sent else NotifyStatus.FAILED
                    )
                    
                    self._repo.append_analysis(
                        rollout.id,
                        reduced_context=reduced,
                        analysis=analysis,
                        model_name=self._config.langchain_model_name,
                    )
                except Exception as exc:  # pragma: no cover - diagnostic path
                    logger.error(f"analysis loop error: {exc}")
            time.sleep(15)


class WatcherService:
    def __init__(self, config: Settings | None = None):
        self._config = config or settings
        self._engine = init_db(self._config.database_url)
        self._repo = RolloutRepo(self._engine)

    def start(self):
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster config")
        except ConfigException:
            config.load_kube_config()
            logger.info("Loaded kube config")

        cluster = self._config.k8s_cluster_name
        logger.info(f"Starting WatcherService for cluster: {cluster}")
        threads: list[threading.Thread] = []
        watch_thread = threading.Thread(
            target=self._watch_loop,
            args=(cluster,),
            daemon=True,
            name="project-fyr-watch",
        )
        reconcile_thread = threading.Thread(
            target=self._reconcile_loop,
            args=(cluster,),
            daemon=True,
            name="project-fyr-reconcile",
        )
        threads.extend([watch_thread, reconcile_thread])
        logger.info(f"Starting {len(threads)} threads: watch and reconcile")
        for t in threads:
            t.start()
            logger.info(f"Started thread: {t.name}")

        logger.info("All threads started, entering main loop")
        for t in threads:
            t.join()

    def _watch_loop(self, cluster: str):
        v1_apps = client.AppsV1Api()
        core_v1 = client.CoreV1Api()
        namespace_cache = NamespaceMetadataCache(core_v1)
        w = watch.Watch()
        selector = "project-fyr/enabled=true"
        logger.info(f"Starting watch loop with label selector: {selector}")
        iteration = 0
        while True:
            try:
                iteration += 1
                logger.info(f"Watch loop iteration #{iteration}, initiating stream...")
                stream = w.stream(
                    v1_apps.list_deployment_for_all_namespaces,
                    label_selector=selector,
                    timeout_seconds=60,
                )
                logger.info(f"Stream created, entering event loop...")
                event_count = 0
                for event in stream:
                    event_count += 1
                    dep = event["object"]
                    etype = event["type"]
                    logger.info(f"Event #{event_count}: type={etype}, deployment={dep.metadata.name}, namespace={dep.metadata.namespace}")
                    ns_meta = namespace_cache.get(dep.metadata.namespace)
                    handle_deployment_event(dep, etype, self._repo, cluster, namespace_metadata=ns_meta)
                logger.info(f"Watch stream ended after {event_count} events, restarting...")
            except Exception as exc:
                logger.error(f"watch error: {exc}", exc_info=True)
                time.sleep(2)

    def _reconcile_loop(self, cluster: str):
        v1_apps = client.AppsV1Api()
        timeout = timedelta(seconds=self._config.rollout_timeout_seconds)
        core_v1 = client.CoreV1Api()
        while True:
            now = datetime.utcnow()
            rollouts = self._repo.list_active(cluster)
            for rollout in rollouts:
                try:
                    dep = v1_apps.read_namespaced_deployment(rollout.deployment, rollout.namespace)
                    reconcile_rollout(dep, rollout, now, self._repo, timeout, core_v1=core_v1)
                except Exception as exc:
                    logger.error(f"reconcile error: {exc}")
            time.sleep(10)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    service = WatcherService()
    service.start()


class AnalyzerService:
    def __init__(self, config: Settings | None = None):
        self._config = config or settings
        self._engine = init_db(self._config.database_url)
        self._repo = RolloutRepo(self._engine)

    def start(self):
        try:
            config.load_incluster_config()
            logger.info("Analyzer loaded in-cluster config")
        except ConfigException:
            config.load_kube_config()
            logger.info("Analyzer loaded kube config")

        # Start Prometheus metrics server in a background thread
        self._start_metrics_server()

        worker = AnalysisWorker(self._repo, self._config.k8s_cluster_name, self._config)
        worker.loop()
    
    def _start_metrics_server(self):
        """Start Prometheus metrics HTTP server on port 8000."""
        from prometheus_client import start_http_server
        try:
            start_http_server(8000)
            logger.info("Prometheus metrics server started on port 8000")
        except Exception as e:
            logger.warning(f"Failed to start Prometheus metrics server: {e}")


def run_watcher():
    WatcherService().start()


def run_analyzer():
    AnalyzerService().start()


__all__ = ["WatcherService", "AnalyzerService", "run_watcher", "run_analyzer"]


if __name__ == "__main__":  # pragma: no cover
    main()
