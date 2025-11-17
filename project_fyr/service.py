"""Runtime orchestration for Project Fyr."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Any

from kubernetes import client, config, watch
from kubernetes.config.config_exception import ConfigException

from .analyzer import Analyzer
from .config import Settings, settings
from .context import RawContextCollector
from .db import RolloutRepo, init_db
from .models import RolloutStatus
from .reducer import ContextReducer
from .slack import SlackNotifier


def evaluate_deployment_phase(dep) -> str:
    status = dep.status or {}
    available = getattr(status, "available_replicas", None) or status.get("available_replicas")
    desired = dep.spec.replicas or 0
    conditions = {c.type: c.status for c in (status.conditions or [])}
    if available and available >= desired and desired > 0:
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
    git = {
        "project": getattr(rollout, "git_project", None),
        "commit": getattr(rollout, "git_commit", None),
        "pipeline_url": getattr(rollout, "pipeline_url", None),
        "mr_url": getattr(rollout, "mr_url", None),
    }
    git = {k: v for k, v in git.items() if v}
    if git:
        metadata["git"] = git
        if "pipeline_url" not in metadata and git.get("pipeline_url"):
            metadata["pipeline_url"] = git["pipeline_url"]
    if getattr(rollout, "team", None):
        metadata["team"] = rollout.team
    if rollout.metadata_json:
        metadata.update({k: v for k, v in rollout.metadata_json.items() if v is not None})
    return metadata


def handle_deployment_event(dep, event_type: str, repo: RolloutRepo, cluster: str):
    if event_type == "DELETED":
        return

    labels = dep.metadata.labels or {}
    if labels.get("project-fyr/enabled") != "true":
        return

    ns = dep.metadata.namespace
    name = dep.metadata.name
    generation = dep.metadata.generation or 1

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
        )
    else:
        # The reconcile loop will adjust statuses.
        pass


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
            print(f"pod analysis error: {exc}")
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
        self._collector = RawContextCollector(
            log_tail_seconds=config.log_tail_seconds,
            max_log_lines=config.max_log_lines,
        )
        self._reducer = ContextReducer(
            max_events=config.reducer_max_events,
            max_clusters=config.reducer_max_clusters,
        )
        self._analyzer = Analyzer(
            model_name=config.langchain_model_name,
            api_key=config.openai_api_key,
        )
        self._slack = SlackNotifier(
            token=config.slack_bot_token,
            default_channel=config.slack_default_channel,
        )

    def loop(self):
        while True:
            rollouts = self._repo.list_failed(self._cluster)
            for rollout in rollouts:
                try:
                    raw = self._collector.collect(rollout.namespace, rollout.deployment)
                    reduced = self._reducer.reduce(raw)
                    analysis = self._analyzer.analyze(reduced)
                    metadata = rollout_metadata_dict(rollout)
                    channel = rollout.slack_channel
                    rollout_ref = f"{rollout.namespace}/{rollout.deployment}#{rollout.generation}"
                    self._slack.send_analysis(
                        channel=channel,
                        rollout_ref=rollout_ref,
                        analysis=analysis,
                        metadata=metadata,
                    )
                    self._repo.append_analysis(
                        rollout.id,
                        reduced_context=reduced,
                        analysis=analysis,
                        model_name=self._config.langchain_model_name,
                    )
                except Exception as exc:  # pragma: no cover - diagnostic path
                    print(f"analysis loop error: {exc}")
            time.sleep(15)


class ProjectFyrService:
    def __init__(self, config: Settings | None = None):
        self._config = config or settings
        self._engine = init_db(self._config.database_url)
        self._repo = RolloutRepo(self._engine)
        self._threads: list[threading.Thread] = []

    def start(self):
        try:
            config.load_incluster_config()
        except ConfigException:
            config.load_kube_config()

        cluster = self._config.k8s_cluster_name
        analysis_worker = AnalysisWorker(self._repo, cluster, self._config)

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
        analysis_thread = threading.Thread(target=analysis_worker.loop, daemon=True, name="analysis")

        self._threads.extend([watch_thread, reconcile_thread, analysis_thread])
        for t in self._threads:
            t.start()

        for t in self._threads:
            t.join()

    def _watch_loop(self, cluster: str):
        v1_apps = client.AppsV1Api()
        w = watch.Watch()
        selector = "project-fyr/enabled=true"
        while True:
            try:
                stream = w.stream(
                    v1_apps.list_deployment_for_all_namespaces,
                    label_selector=selector,
                    timeout_seconds=60,
                )
                for event in stream:
                    dep = event["object"]
                    etype = event["type"]
                    handle_deployment_event(dep, etype, self._repo, cluster)
            except Exception as exc:
                print(f"watch error: {exc}")
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
                    print(f"reconcile error: {exc}")
            time.sleep(10)


def main():
    service = ProjectFyrService()
    service.start()


if __name__ == "__main__":  # pragma: no cover
    main()
