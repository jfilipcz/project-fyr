# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Runtime orchestration for Project Fyr."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from project_fyr import utcnow

from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
from kubernetes.config.config_exception import ConfigException

from .agent import InvestigatorAgent
from .cases import CaseIngestionService
from .config import Settings, settings
from .db import AlertRepo, NamespaceCaseRepo, RolloutRepo, WorkItemRepo, init_db
from .issue_classifier import classify_issue_signal
from .models import (
    Analysis,
    IssueScope,
    IssueStatus,
    NamespaceCaseStatus,
    NotifyStatus,
    ReducedContext,
    RolloutStatus,
    WorkItemKind,
    WorkItemStatus,
)
from .slack import SlackNotifier
from .triage import triage_failure


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RolloutNotificationDecision:
    classification: str
    notify_immediately: bool
    reason: str


def _normalized_strings(values: list[Any] | None) -> list[str]:
    if not values:
        return []
    result: list[str] = []
    for value in values:
        text = str(value).strip().lower()
        if text:
            result.append(text)
    return result


def classify_rollout_notification_decision(
    *,
    trigger_context: dict[str, Any] | None,
    analysis,
    config: Settings,
) -> RolloutNotificationDecision:
    trigger_context = trigger_context or {}
    failure_type = str(trigger_context.get("failure_type") or "").strip().lower()
    observed_failures = _normalized_strings(trigger_context.get("observed_failures"))
    transient_types = {item.lower() for item in config.rollout_transient_failure_types}
    immediate_types = {item.lower() for item in config.rollout_immediate_actionable_failure_types}
    patterns = [item.lower() for item in config.rollout_transient_observation_patterns]
    mode = str(getattr(config, "rollout_transient_classification_mode", "hybrid")).strip().lower()

    haystack_parts = observed_failures + _normalized_strings(
        [
            failure_type,
            getattr(analysis, "summary", ""),
            getattr(analysis, "likely_cause", ""),
            *getattr(analysis, "recommended_steps", []),
        ]
    )
    haystack = "\n".join(haystack_parts)

    if failure_type and failure_type in immediate_types:
        return RolloutNotificationDecision(
            classification="immediate_actionable",
            notify_immediately=True,
            reason=f"failure_type '{failure_type}' configured as immediate actionable",
        )

    pattern_match = next((pattern for pattern in patterns if pattern and pattern in haystack), None)
    transient_match = bool(failure_type and failure_type in transient_types) or pattern_match is not None

    if mode in {"allowlist", "hybrid"} and transient_match:
        if failure_type and failure_type in transient_types:
            reason = f"failure_type '{failure_type}' matched transient allowlist"
        else:
            reason = f"transient observation pattern '{pattern_match}' matched rollout evidence"
        return RolloutNotificationDecision(
            classification="transient",
            notify_immediately=False,
            reason=reason,
        )

    return RolloutNotificationDecision(
        classification="unknown",
        notify_immediately=False,
        reason="no immediate-actionable or transient classification matched",
    )


def is_transient_rollout_candidate(
    *,
    trigger_context: dict[str, Any] | None,
    config: Settings,
) -> tuple[bool, str]:
    trigger_context = trigger_context or {}
    failure_type = str(trigger_context.get("failure_type") or "").strip().lower()
    observed_failures = _normalized_strings(trigger_context.get("observed_failures"))
    transient_types = {item.lower() for item in config.rollout_transient_failure_types}
    patterns = [item.lower() for item in config.rollout_transient_observation_patterns]
    haystack = "\n".join(_normalized_strings([failure_type, *observed_failures]))

    if failure_type and failure_type in transient_types:
        return True, f"failure_type '{failure_type}' matched transient allowlist"

    pattern_match = next((pattern for pattern in patterns if pattern and pattern in haystack), None)
    if pattern_match:
        return True, f"trigger observation matched transient pattern '{pattern_match}'"

    return False, "no transient candidate match"


class AlertBatcher:
    def __init__(self, repo: AlertRepo, config: Settings):
        self._repo = repo
        self._config = config
        self._window = config.alert_correlation_window_seconds
        self._min_count = config.alert_batch_min_count

    def run_once(self):
        # Look back window
        now = utcnow()
        window_start = now - timedelta(seconds=self._window)

        alerts = self._repo.get_unbatched_alerts(window_start)
        if not alerts:
            return

        # Simple grouping: by namespace + service (if label exists)
        # Fallback: by alertname
        groups: dict[str, list] = {}

        for alert in alerts:
            # Check if alert is old enough to be batched (wait for window to close slightly?)
            # For simplicity, we batch everything that is in the window.
            # Real implementation might wait until alert.received_at < now - window/2

            ns = alert.labels.get("namespace", "default")
            svc = alert.labels.get("service") or alert.labels.get("app") or "unknown"
            key = f"{ns}/{svc}"
            if key not in groups:
                groups[key] = []
            groups[key].append(alert)

        for key, group in groups.items():
            if len(group) < self._min_count:
                continue

            ns, svc = key.split("/", 1)

            # Summary
            alert_names = list(set(a.labels.get("alertname", "unknown") for a in group))
            summary = f"Batch of {len(group)} alerts for {key}. Alerts: {', '.join(alert_names)}"

            logger.info(f"Creating batch for {key} with {len(group)} alerts")
            self._repo.create_batch(
                alerts=group,
                summary=summary,
                primary_fingerprint=group[0].fingerprint,
                namespace=ns,
                service=svc,
                window_start=min(a.starts_at for a in group),
                window_end=max(a.received_at for a in group)
            )


class AnalysisWorker:
    def __init__(self, repo: RolloutRepo, alert_repo: AlertRepo, cluster: str, config: Settings):
        self._repo = repo
        self._alert_repo = alert_repo
        self._cluster = cluster
        self._config = config
        self._agent = InvestigatorAgent(
            model_name=config.langchain_model_name,
            api_key=config.openai_api_key,
            api_base=config.openai_api_base,
            api_version=config.openai_api_version,
            azure_deployment=config.azure_deployment,
        )
        # Store config for namespace agent creation
        self._agent._api_key = config.openai_api_key
        self._agent._api_base = config.openai_api_base
        self._agent._api_version = config.openai_api_version
        self._agent._azure_deployment = config.azure_deployment
        self._slack = SlackNotifier(
            token=config.slack_bot_token,
            default_channel=config.slack_default_channel,
            mock_log_file=config.slack_mock_log_file,
            base_url=config.slack_api_url,
            enable_requestor_dm=config.enable_requestor_dm,
            enable_owner_channel=config.enable_owner_channel,
            cache_ttl_seconds=config.slack_routing_cache_ttl_seconds,
        )
        # Initialize k8s API clients for pre-checks
        self._apps_v1 = client.AppsV1Api()
        self._core_v1 = client.CoreV1Api()
        self._case_repo = NamespaceCaseRepo(repo._engine)
        self._work_item_repo = WorkItemRepo(repo._engine)

    def _ensure_case_repos(self) -> None:
        engine = getattr(self._repo, "_engine", None)
        if getattr(self, "_case_repo", None) is None and engine is not None:
            self._case_repo = NamespaceCaseRepo(engine)
        if getattr(self, "_work_item_repo", None) is None and engine is not None:
            self._work_item_repo = WorkItemRepo(engine)

    def _get_namespace_annotations(self, namespace: str) -> dict[str, str]:
        try:
            ns = self._core_v1.read_namespace(namespace)
        except Exception as exc:
            logger.warning(f"namespace lookup failed for {namespace}: {exc}")
            return {}
        return getattr(ns.metadata, "annotations", None) or {}

    def _get_deployment_labels(self, namespace: str, deployment: str) -> dict[str, str]:
        try:
            dep = self._apps_v1.read_namespaced_deployment(deployment, namespace)
        except Exception as exc:
            logger.warning(f"deployment lookup failed for {namespace}/{deployment}: {exc}")
            return {}
        return getattr(dep.metadata, "labels", None) or {}

    def _build_slack_routing_metadata(
        self,
        *,
        namespace: str,
        deployment: str | None = None,
        namespace_channel: str | None = None,
    ) -> dict[str, Any]:
        annotations = self._get_namespace_annotations(namespace)
        requestor_email = extract_requestor_email_from_annotations(annotations)
        resolved_namespace_channel = namespace_channel or annotations.get(ANNOTATION_SLACK_CHANNEL)

        owner_channel = None
        if deployment:
            labels = self._get_deployment_labels(namespace, deployment)
            owner_channel = labels.get(LABEL_OWNER_CHANNEL)

        metadata: dict[str, Any] = {}
        if requestor_email:
            metadata["requestor_email"] = requestor_email
        if owner_channel:
            metadata["owner_channel"] = owner_channel
        if resolved_namespace_channel:
            metadata["namespace_channel"] = resolved_namespace_channel
        return metadata

    def _is_deployment_healthy_now(self, deployment: str, namespace: str) -> bool:
        """
        Quick pre-check: Is the deployment actually healthy RIGHT NOW?

        This catches false positives BEFORE expensive LLM investigation.
        Returns True if deployment is healthy (should skip investigation).
        Returns False if deployment has issues (should investigate).
        """
        try:
            # Check if namespace still exists
            try:
                self._core_v1.read_namespace(namespace)
            except client.rest.ApiException as e:
                if e.status == 404:
                    logger.info(f"Pre-check: namespace_missing {namespace} (404) - skipping")
                    return True  # Namespace deleted, skip investigation
                raise

            # Check if deployment still exists
            try:
                dep = self._apps_v1.read_namespaced_deployment(deployment, namespace)
            except client.rest.ApiException as e:
                if e.status == 404:
                    logger.info(f"Pre-check: deployment_missing {namespace}/{deployment} (404) - skipping")
                    return True  # Deployment deleted, skip investigation
                raise

            # Check deployment health using existing evaluate_deployment_phase logic
            phase = evaluate_deployment_phase(dep)

            desired = getattr(dep.spec, "replicas", 0) or 0
            if desired == 0:
                logger.info(
                    f"Pre-check: desired_zero {namespace}/{deployment} desired=0 - skipping"
                )
                return True

            if phase == "STABLE":
                available = getattr(dep.status, "available_replicas", None) or getattr(dep.status, "availableReplicas", None)
                logger.info(
                    f"Pre-check: healthy_phase {namespace}/{deployment} phase=STABLE "
                    f"available={available} desired={desired}"
                )
                return True

            # Also check pod health - maybe rollout succeeded
            selector = dep.spec.selector.match_labels or {}
            label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
            pods = self._core_v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)

            if not pods.items:
                # No pods at all - likely namespace being torn down
                logger.info(
                    f"Pre-check: healthy_no_pods {namespace}/{deployment} selector={label_selector} - likely teardown"
                )
                return True

            # Count healthy vs unhealthy pods
            running_ready = 0
            total_pods = len(pods.items)

            for pod in pods.items:
                if pod.status.phase == "Running":
                    # Check if all containers are ready
                    if pod.status.container_statuses:
                        all_ready = all(cs.ready for cs in pod.status.container_statuses)
                        if all_ready:
                            running_ready += 1

            # If all pods are running and ready, deployment is healthy
            if running_ready == total_pods and total_pods > 0:
                logger.info(
                    f"Pre-check: healthy_all_ready {namespace}/{deployment} "
                    f"ready={running_ready} total={total_pods} selector={label_selector}"
                )
                return True

            # Check for active failure signals
            signals = analyze_pod_failures(pods.items)
            if signals.total_failing == 0 and phase not in ("FAILED_PROGRESS", "PENDING"):
                # No active failures detected - might be transient
                logger.info(
                    f"Pre-check: {namespace}/{deployment} - no active failure signals "
                    f"(phase={phase}, failing=0) - possible recovery in progress"
                )
                # Don't mark as healthy yet - let investigation proceed to confirm
                return False

            # Has failure signals - should investigate
            logger.debug(
                f"Pre-check: {namespace}/{deployment} has issues - "
                f"phase={phase}, failing_pods={signals.total_failing}/{signals.total_pods}"
            )
            return False

        except Exception as e:
            logger.warning(f"Pre-check error for {namespace}/{deployment}: {e} - proceeding with investigation")
            return False  # On error, proceed with investigation to be safe

    def loop(self):
        while True:
            # 0. Process new issue-based control plane work.
            try:
                self._process_issue_work_items()
            except Exception as exc:
                logger.error(f"issue processing loop error: {exc}")

            try:
                self._process_case_recheck_work_items()
            except Exception as exc:
                logger.error(f"case recheck loop error: {exc}")

            # 1. Process Rollouts (Legacy/Existing path)
            try:
                self._process_rollouts()
            except Exception as exc:
                logger.error(f"rollout processing loop error: {exc}")

            # 1b. Re-evaluate rollout notifications deferred for noise reduction.
            try:
                self._process_deferred_rollout_notifications()
            except Exception as exc:
                logger.error(f"deferred rollout notification loop error: {exc}")

            # 2. Process Alert Jobs
            try:
                self._process_alert_jobs()
            except Exception as exc:
                logger.error(f"alert processing loop error: {exc}")

            # 3. Process Namespace Investigation Jobs
            try:
                self._process_namespace_jobs()
            except Exception as exc:
                logger.error(f"namespace processing loop error: {exc}")

            time.sleep(15)

    def _process_issue_work_items(self):
        self._ensure_case_repos()
        work_repo = getattr(self, "_work_item_repo", None)
        case_repo = getattr(self, "_case_repo", None)
        if work_repo is None or case_repo is None:
            return

        work_item = work_repo.claim_work_item(WorkItemKind.ISSUE_INVESTIGATION)
        if work_item is None:
            return
        if work_item.issue_id is None:
            work_repo.update_work_item_status(
                work_item.id,
                WorkItemStatus.FAILED,
                error="issue investigation work item missing issue_id",
            )
            return

        issue = case_repo.get_issue_by_id(work_item.issue_id)
        if issue is None:
            work_repo.update_work_item_status(
                work_item.id,
                WorkItemStatus.FAILED,
                error=f"issue {work_item.issue_id} not found",
            )
            return

        case_repo.update_issue_status(issue.id, IssueStatus.INVESTIGATING)
        try:
            self._investigate_issue(issue)
        except Exception as exc:
            logger.error(f"issue investigation failed for issue {issue.id}: {exc}")
            work_repo.update_work_item_status(
                work_item.id,
                WorkItemStatus.FAILED,
                error=str(exc),
            )
            return

        work_repo.update_work_item_status(work_item.id, WorkItemStatus.COMPLETED)

    def _process_case_recheck_work_items(self):
        self._ensure_case_repos()
        work_repo = getattr(self, "_work_item_repo", None)
        case_repo = getattr(self, "_case_repo", None)
        if work_repo is None or case_repo is None:
            return

        work_item = work_repo.claim_work_item(WorkItemKind.CASE_RECHECK)
        if work_item is None:
            return
        if work_item.namespace_case_id is None:
            work_repo.update_work_item_status(
                work_item.id,
                WorkItemStatus.FAILED,
                error="case recheck work item missing namespace_case_id",
            )
            return

        case_record = case_repo.get_case_by_id(work_item.namespace_case_id)
        if case_record is None:
            work_repo.update_work_item_status(
                work_item.id,
                WorkItemStatus.FAILED,
                error=f"namespace case {work_item.namespace_case_id} not found",
            )
            return

        try:
            self._recheck_case(case_record.id)
        except Exception as exc:
            logger.error(f"case recheck failed for case {case_record.id}: {exc}")
            work_repo.update_work_item_status(
                work_item.id,
                WorkItemStatus.FAILED,
                error=str(exc),
            )
            return

        work_repo.update_work_item_status(work_item.id, WorkItemStatus.COMPLETED)

    def _recheck_case(self, case_id: int) -> None:
        self._ensure_case_repos()
        case_repo = self._case_repo
        case_record = case_repo.get_case_by_id(case_id)
        if case_record is None or case_record.status == NamespaceCaseStatus.CLOSED:
            return

        all_issues = case_repo.list_case_issues(case_id)
        active_deployment_issues = [
            issue for issue in all_issues
            if issue.scope == IssueScope.DEPLOYMENT
            and issue.status in {IssueStatus.ACTIVE, IssueStatus.INVESTIGATING}
        ]

        grouped_by_cause: dict[str, list[Any]] = {}
        for issue in active_deployment_issues:
            grouped_by_cause.setdefault(issue.cause_family, []).append(issue)

        for cause_family, related in grouped_by_cause.items():
            if len(related) < 3:
                continue
            case_repo.get_or_create_issue(
                namespace_case_id=case_id,
                scope=IssueScope.NAMESPACE,
                resource_kind="Namespace",
                resource_name=case_record.namespace,
                cause_family=cause_family,
                issue_type="correlated_namespace_issue",
                status=IssueStatus.ACTIVE,
                metadata_json={
                    "synthesized": True,
                    "child_issue_ids": [issue.id for issue in related],
                },
            )

        refreshed_issues = case_repo.list_case_issues(case_id)
        for issue in refreshed_issues:
            if issue.scope != IssueScope.NAMESPACE:
                continue
            metadata = dict(issue.metadata_json or {})
            if not metadata.get("synthesized"):
                continue
            related = grouped_by_cause.get(issue.cause_family, [])
            if len(related) >= 3:
                continue
            if issue.status not in {IssueStatus.ACTIVE, IssueStatus.INVESTIGATING}:
                continue
            case_repo.update_issue_status(
                issue.id,
                IssueStatus.RESOLVED,
                metadata_json={"resolution_reason": "correlated child issue pattern cleared"},
            )

        active_issues = case_repo.list_active_issues(case_id)
        if active_issues:
            if case_record.status != NamespaceCaseStatus.OPEN:
                case_repo.update_case_status(case_id, NamespaceCaseStatus.OPEN)
            return

        if case_record.status == NamespaceCaseStatus.OPEN:
            case_repo.update_case_status(case_id, NamespaceCaseStatus.QUIETING)
            return

        quiet_seconds = int(getattr(self._config, "namespace_case_auto_close_seconds", 900))
        if case_record.status == NamespaceCaseStatus.QUIETING:
            quieting_at = case_record.quieting_at or utcnow()
            if (utcnow() - quieting_at).total_seconds() >= quiet_seconds:
                case_repo.update_case_status(case_id, NamespaceCaseStatus.CLOSED)

    def _investigate_issue(self, issue) -> None:
        self._ensure_case_repos()
        case_repo = self._case_repo
        work_repo = self._work_item_repo

        if issue.scope == IssueScope.DEPLOYMENT:
            rollout = self._repo.get_by_id(issue.rollout_id) if issue.rollout_id else None
            if rollout is None:
                case_repo.update_issue_status(
                    issue.id,
                    IssueStatus.SUPPRESSED,
                    metadata_json={"resolution_reason": "missing rollout for deployment issue"},
                )
                work_repo.enqueue_work_item(
                    kind=WorkItemKind.CASE_RECHECK,
                    namespace_case_id=issue.namespace_case_id,
                )
                return

            if self._is_deployment_healthy_now(rollout.deployment, rollout.namespace):
                case_repo.update_issue_status(
                    issue.id,
                    IssueStatus.RESOLVED,
                    metadata_json={"resolution_reason": "deployment recovered before issue investigation"},
                )
                work_repo.enqueue_work_item(
                    kind=WorkItemKind.CASE_RECHECK,
                    namespace_case_id=issue.namespace_case_id,
                )
                return

            trigger_context = (rollout.metadata_json or {}).get("trigger_context") or {}
            analysis = self._agent.investigate(
                rollout.deployment,
                rollout.namespace,
                trigger_context=trigger_context,
            )
            classification = classify_issue_signal(
                signal_type=str((issue.metadata_json or {}).get("trigger_reason") or "issue_investigation"),
                trigger_context=trigger_context,
                analysis_text="\n".join(
                    [
                        analysis.summary,
                        analysis.likely_cause,
                        *analysis.recommended_steps,
                    ]
                ),
            )
            refreshed_issue = case_repo.get_or_create_issue(
                namespace_case_id=issue.namespace_case_id,
                scope=issue.scope,
                resource_kind=issue.resource_kind,
                resource_name=issue.resource_name,
                rollout_id=issue.rollout_id,
                cause_family=classification.cause_family,
                issue_type=classification.issue_type,
                status=IssueStatus.ACTIVE,
                metadata_json={
                    "analysis_summary": analysis.summary,
                    "classification_reasons": classification.reasons,
                    "dependency_target": classification.dependency_target,
                },
            )
            case_repo.append_issue_analysis(
                refreshed_issue.id,
                analysis=analysis,
                model_name=self._config.langchain_model_name,
            )
            case_repo.update_issue_status(refreshed_issue.id, IssueStatus.ACTIVE)
            work_repo.enqueue_work_item(
                kind=WorkItemKind.CASE_RECHECK,
                namespace_case_id=issue.namespace_case_id,
            )
            return

        metadata = dict(issue.metadata_json or {})
        if issue.issue_type == "terminating_stuck":
            likely_cause = "Namespace has remained in Terminating state past the configured threshold."
            steps = [
                "Inspect namespace finalizers and remaining namespaced resources.",
                "Check for API objects blocked on finalizer cleanup.",
            ]
        else:
            likely_cause = f"Namespace-level issue '{issue.issue_type}' remains active."
            steps = ["Inspect namespace events and dependent resources."]
        analysis = Analysis(
            summary=f"Namespace issue detected for {issue.resource_name}",
            likely_cause=likely_cause,
            recommended_steps=steps,
            severity="medium",
        )
        case_repo.append_issue_analysis(
            issue.id,
            analysis=analysis,
            model_name=self._config.langchain_model_name,
            reduced_context={
                "namespace": issue.resource_name,
                "issue_type": issue.issue_type,
                "metadata": metadata,
            },
        )
        case_repo.update_issue_status(issue.id, IssueStatus.ACTIVE)
        work_repo.enqueue_work_item(
            kind=WorkItemKind.CASE_RECHECK,
            namespace_case_id=issue.namespace_case_id,
        )

    def _process_rollouts(self):
        rollouts = self._repo.list_failed(self._cluster)

        if not rollouts:
            return

        # Batch check: Get unique namespaces and check which still exist
        unique_namespaces = {r.namespace for r in rollouts}
        existing_namespaces = set()
        deleted_namespaces = set()
        terminating_namespaces = set()

        logger.debug(f"Checking {len(unique_namespaces)} unique namespaces for {len(rollouts)} pending rollouts")

        for ns in unique_namespaces:
            try:
                namespace_obj = self._core_v1.read_namespace(ns)
                ns_phase = getattr(getattr(namespace_obj, "status", None), "phase", None)
                if ns_phase == "Terminating":
                    terminating_namespaces.add(ns)
                    logger.info(f"Namespace {ns} is terminating")
                else:
                    existing_namespaces.add(ns)
            except ApiException as e:
                if e.status == 404:
                    deleted_namespaces.add(ns)
                    logger.info(f"Namespace {ns} no longer exists")
                else:
                    # For other errors (permission issues, etc), assume namespace exists
                    # and let the individual rollout processing handle it
                    logger.warning(f"Error checking namespace {ns}: {e}")
                    existing_namespaces.add(ns)

        # Batch discard all rollouts from deleted/terminating namespaces
        namespaces_to_discard = deleted_namespaces | terminating_namespaces
        if namespaces_to_discard:
            rollouts_to_discard = [r for r in rollouts if r.namespace in namespaces_to_discard]
            logger.info(
                f"Discarding {len(rollouts_to_discard)} rollouts from {len(namespaces_to_discard)} deleted/terminating namespaces"
            )
            for rollout in rollouts_to_discard:
                try:
                    if rollout.namespace in terminating_namespaces:
                        reason = "Namespace terminating (ephemeral CI environment)"
                    else:
                        reason = "Namespace deleted (ephemeral CI environment)"
                    self._repo.discard_analysis(
                        rollout.id,
                        reason=reason,
                    )
                except Exception as exc:
                    logger.error(f"Error discarding rollout {rollout.id}: {exc}")

        # Process remaining rollouts from existing namespaces
        rollouts_to_process = [r for r in rollouts if r.namespace in existing_namespaces]
        logger.info(f"Processing {len(rollouts_to_process)} rollouts from existing namespaces")
        cfg = getattr(self, "_config", settings)
        grace_seconds = max(
            0,
            int(getattr(cfg, "analysis_healthy_recheck_delay_seconds", 90)),
        )
        grace_window = timedelta(seconds=grace_seconds)
        transient_window_seconds = max(
            0,
            int(getattr(cfg, "rollout_transient_persistence_window_seconds", 300)),
        )
        transient_window = timedelta(seconds=transient_window_seconds)
        investigation_mode = str(
            getattr(cfg, "rollout_transient_investigation_mode", "immediate")
        ).strip().lower()

        for rollout in rollouts_to_process:
            try:
                trigger_context = (rollout.metadata_json or {}).get("trigger_context") or {}

                # Namespace may disappear between batch pre-check and per-rollout processing.
                try:
                    namespace_obj = self._core_v1.read_namespace(rollout.namespace)
                    ns_phase = getattr(getattr(namespace_obj, "status", None), "phase", None)
                    if ns_phase == "Terminating":
                        reason = "Namespace terminating before analysis could run (ephemeral CI environment)"
                        logger.info(
                            f"Discarding rollout {rollout.id} for terminating namespace {rollout.namespace}"
                        )
                        self._repo.discard_analysis(rollout.id, reason=reason)
                        continue
                except ApiException as exc:
                    if exc.status == 404:
                        reason = "Namespace deleted before analysis could run (ephemeral CI environment)"
                        logger.info(
                            f"Discarding rollout {rollout.id} for deleted namespace {rollout.namespace}"
                        )
                        self._repo.discard_analysis(rollout.id, reason=reason)
                        continue
                    logger.warning(
                        f"Error re-checking namespace {rollout.namespace} before analysis: {exc}"
                    )

                # For transient failures, cluster state may have already recovered by analysis time.
                if self._is_deployment_healthy_now(rollout.deployment, rollout.namespace):
                    reference_time = rollout.failed_at or rollout.started_at
                    if reference_time is not None:
                        age = utcnow() - reference_time
                        if age < grace_window:
                            remaining = int((grace_window - age).total_seconds())
                            logger.info(
                                f"Pre-check: {rollout.namespace}/{rollout.deployment} is healthy "
                                f"within grace window ({int(age.total_seconds())}s/{grace_seconds}s), "
                                f"deferring analysis for rollout {rollout.id} (~{remaining}s remaining)"
                            )
                            continue
                        reason = (
                            "Recovered before analysis "
                            f"(healthy at pre-check {int(age.total_seconds())}s after failure; "
                            f"grace={grace_seconds}s)"
                        )
                    else:
                        reason = (
                            "Recovered before analysis (healthy at pre-check; "
                            "failure timestamp unavailable)"
                        )
                    logger.info(
                        f"Discarding rollout {rollout.id} for recovered deployment "
                        f"{rollout.namespace}/{rollout.deployment}: {reason}"
                    )
                    self._repo.discard_analysis(rollout.id, reason=reason)
                    continue

                if investigation_mode == "delayed":
                    transient_candidate, candidate_reason = is_transient_rollout_candidate(
                        trigger_context=trigger_context,
                        config=cfg,
                    )
                    if transient_candidate:
                        reference_time = rollout.failed_at or rollout.started_at
                        if reference_time is not None:
                            candidate_age = utcnow() - reference_time
                            if candidate_age < transient_window:
                                remaining = int((transient_window - candidate_age).total_seconds())
                                logger.info(
                                    f"Deferring investigation for transient rollout candidate "
                                    f"{rollout.namespace}/{rollout.deployment} ({candidate_reason}); "
                                    f"{remaining}s remaining in quiet window"
                                )
                                continue

                logger.info(f"Starting investigation for rollout {rollout.namespace}/{rollout.deployment}")
                self._investigate_rollout(rollout)
            except Exception as exc:
                logger.error(f"rollout analysis error: {exc}")

    def _rollout_notification_policy(self) -> dict[str, Any]:
        return {
            "classification_mode": self._config.rollout_transient_classification_mode,
            "investigation_mode": self._config.rollout_transient_investigation_mode,
            "slack_mode": self._config.rollout_transient_slack_mode,
            "persistence_window_seconds": self._config.rollout_transient_persistence_window_seconds,
            "transient_failure_types": list(self._config.rollout_transient_failure_types),
            "transient_observation_patterns": list(self._config.rollout_transient_observation_patterns),
            "immediate_actionable_failure_types": list(self._config.rollout_immediate_actionable_failure_types),
        }

    def _build_rollout_notification_metadata(
        self,
        rollout,
        analysis: Analysis,
    ) -> tuple[dict[str, Any], str]:
        metadata = rollout_metadata_dict(rollout)
        metadata.update(
            {
                "cluster": self._config.k8s_cluster_name,
                "namespace": rollout.namespace,
                "deployment": rollout.deployment,
                "triage_team": analysis.triage_team,
                "triage_reason": analysis.triage_reason,
            }
        )
        metadata.update(
            self._build_slack_routing_metadata(
                namespace=rollout.namespace,
                deployment=rollout.deployment,
                namespace_channel=rollout.slack_channel,
            )
        )
        rollout_ref = f"{rollout.namespace}/{rollout.deployment}#{rollout.generation}"
        return metadata, rollout_ref

    def _send_rollout_analysis_notification(
        self,
        rollout,
        analysis: Analysis,
        metadata: dict[str, Any],
        rollout_ref: str,
    ) -> bool:
        return self._slack.send_analysis(
            channel=rollout.slack_channel,
            rollout_ref=rollout_ref,
            analysis=analysis,
            metadata=metadata,
            rollout_id=rollout.id,
        )

    def _investigate_rollout(self, rollout):
        # Extract trigger context from rollout metadata for investigation enrichment
        trigger_context = None
        if rollout.metadata_json:
            trigger_context = rollout.metadata_json.get("trigger_context")

        # Agentic investigation with trigger context
        analysis = self._agent.investigate(
            rollout.deployment,
            rollout.namespace,
            trigger_context=trigger_context,
        )

        # Create a dummy ReducedContext for DB compatibility
        reduced = ReducedContext(
            namespace=rollout.namespace,
            deployment=rollout.deployment,
            generation=rollout.generation,
            summary="Agentic Investigation",
            phase="FAILED",
            failing_pods=[],
            log_clusters=[],
            events=[],
            argocd_status=None,
        )

        triage = triage_failure(reduced, analysis)
        analysis.triage_team = triage.team
        analysis.triage_reason = triage.reason

        self._repo.append_analysis(
            rollout.id,
            reduced_context=reduced,
            analysis=analysis,
            model_name=self._config.langchain_model_name,
        )

        decision = classify_rollout_notification_decision(
            trigger_context=trigger_context,
            analysis=analysis,
            config=self._config,
        )
        policy = self._rollout_notification_policy()
        metadata, rollout_ref = self._build_rollout_notification_metadata(rollout, analysis)
        slack_mode = str(
            getattr(self._config, "rollout_transient_slack_mode", "actionable_only")
        ).strip().lower()
        should_send_now = decision.notify_immediately or slack_mode == "always"

        if not should_send_now:
            deferred_until = utcnow() + timedelta(
                seconds=max(0, int(self._config.rollout_transient_persistence_window_seconds))
            )
            self._repo.set_rollout_notification_state(
                rollout.id,
                state="deferred",
                classification=decision.classification,
                reason=decision.reason,
                deferred_until=deferred_until,
                policy=policy,
            )
            return

        sent = self._send_rollout_analysis_notification(
            rollout,
            analysis,
            metadata,
            rollout_ref,
        )
        self._repo.set_rollout_notification_state(
            rollout.id,
            state="sent" if sent else "failed",
            classification=decision.classification,
            reason=decision.reason,
            notify_status=NotifyStatus.SENT if sent else NotifyStatus.FAILED,
            policy=policy,
        )

    def _process_deferred_rollout_notifications(self):
        cluster = getattr(self, "_cluster", self._config.k8s_cluster_name)
        rollouts = self._repo.list_deferred_rollout_notifications_due(cluster)
        for rollout in rollouts:
            metadata = dict(rollout.metadata_json or {})
            classification = metadata.get("notification_classification")
            existing_reason = metadata.get("notification_decision_reason")

            if self._is_deployment_healthy_now(rollout.deployment, rollout.namespace):
                self._repo.set_rollout_notification_state(
                    rollout.id,
                    state="suppressed",
                    classification=classification,
                    reason="recovered before notification",
                    recovered_before_notification=True,
                    notify_status=NotifyStatus.SENT,
                )
                continue

            if not rollout.analysis_id:
                logger.warning(
                    f"Deferred rollout {rollout.id} has no analysis_id; suppressing notification retry"
                )
                self._repo.set_rollout_notification_state(
                    rollout.id,
                    state="suppressed",
                    classification=classification,
                    reason="analysis record missing for deferred notification",
                    notify_status=NotifyStatus.SENT,
                )
                continue

            record = self._repo.get_analysis(rollout.analysis_id)
            if not record or not record.analysis:
                logger.warning(
                    f"Deferred rollout {rollout.id} analysis record unavailable; suppressing notification retry"
                )
                self._repo.set_rollout_notification_state(
                    rollout.id,
                    state="suppressed",
                    classification=classification,
                    reason="analysis record unavailable for deferred notification",
                    notify_status=NotifyStatus.SENT,
                )
                continue

            analysis = Analysis.model_validate(record.analysis)
            notification_metadata, rollout_ref = self._build_rollout_notification_metadata(
                rollout,
                analysis,
            )
            sent = self._send_rollout_analysis_notification(
                rollout,
                analysis,
                notification_metadata,
                rollout_ref,
            )
            self._repo.set_rollout_notification_state(
                rollout.id,
                state="sent" if sent else "failed",
                classification=classification,
                reason=existing_reason or "still actionable after quiet-first window",
                notify_status=NotifyStatus.SENT if sent else NotifyStatus.FAILED,
            )

    # TODO(project-fyr): apply quiet-first actionable-only notification gating to
    # alert-triggered investigations; deferred to keep this change rollout-only.
    def _process_alert_jobs(self):
        jobs = self._alert_repo.get_pending_alert_jobs()
        for job in jobs:
            try:
                logger.info(f"Processing alert job {job.id} for batch {job.alert_batch_id}")
                self._investigate_alert_batch(job)
            except Exception as exc:
                logger.error(f"alert job error: {exc}")
                self._alert_repo.update_job_status(job.id, "failed")

    def _investigate_alert_batch(self, job):
        self._alert_repo.update_job_status(job.id, "running", started_at=utcnow())

        batch = self._alert_repo.get_batch(job.alert_batch_id)
        if not batch:
            logger.error(f"Batch {job.alert_batch_id} not found")
            self._alert_repo.update_job_status(job.id, "failed")
            return

        alerts = self._alert_repo.get_batch_alerts(batch.id)

        # Prepare context
        alert_context = {
            "summary": batch.context_summary,
            "alerts": [
                {
                    "name": a.labels.get("alertname"),
                    "severity": a.labels.get("severity"),
                    "instance": a.labels.get("instance"),
                    "description": a.annotations.get("description") or a.annotations.get("message"),
                    "starts_at": str(a.starts_at)
                }
                for a in alerts
            ]
        }

        # Deployment/Namespace might be inferred
        deployment = batch.service or "unknown"
        namespace = batch.namespace or "default"

        # Agent investigation
        analysis = self._agent.investigate(deployment, namespace, alert_context=alert_context)

        # Notify Slack with alert-specific blocks
        alerts_payload = [
            {
                "labels": a.labels or {},
                "annotations": a.annotations or {},
                "starts_at": str(a.starts_at),
                "ends_at": str(a.ends_at) if a.ends_at else None,
            }
            for a in alerts
        ]

        routing_metadata = self._build_slack_routing_metadata(
            namespace=namespace,
            deployment=None if deployment == "unknown" else deployment,
        )

        self._slack.send_alert_batch(
            channel=None,
            batch_id=batch.id,
            namespace=namespace,
            alerts=alerts_payload,
            analysis=analysis,
            primary_alert_name=batch.primary_fingerprint,
            metadata=routing_metadata,
        )

        self._alert_repo.update_job_status(job.id, "done", completed_at=utcnow())

    # TODO(project-fyr): align namespace-incident notifications with the same
    # deferred/suppressed rollout semantics; deferred from rollout cleanup pass.
    def _process_namespace_jobs(self):
        """Process pending namespace investigation jobs."""
        jobs = self._alert_repo.get_pending_namespace_jobs()
        for job in jobs:
            try:
                logger.info(f"Processing namespace job {job.id} for incident {job.namespace_incident_id}")
                self._investigate_namespace_incident(job)
            except Exception as exc:
                logger.error(f"namespace job error: {exc}")
                self._alert_repo.update_job_status(job.id, "failed")

    def _investigate_namespace_incident(self, job):
        """Investigate a namespace incident using the agent."""
        from .db import NamespaceIncidentRepo

        self._alert_repo.update_job_status(job.id, "running", started_at=utcnow())

        # Get the namespace incident
        incident_repo = NamespaceIncidentRepo(self._repo._engine)
        incident = incident_repo.get_by_id(job.namespace_incident_id)

        if not incident:
            logger.error(f"Namespace incident {job.namespace_incident_id} not found")
            self._alert_repo.update_job_status(job.id, "failed")
            return

        # Update incident status to INVESTIGATING
        from .models import NamespaceIncidentStatus
        incident_repo.update_status(incident.id, NamespaceIncidentStatus.INVESTIGATING)

        # Run agent investigation
        analysis = self._agent.investigate_namespace(
            namespace=incident.namespace,
            cluster=incident.cluster,
            incident_type=incident.incident_type,
            started_at=str(incident.started_at),
            metadata=incident.metadata or {}
        )

        # Build a reduced context dict for storage (NamespaceContext requires
        # fields we don't have here; store the serialisable metadata instead)
        reduced_context = {
            "namespace": incident.namespace,
            "cluster": incident.cluster,
            "incident_type": incident.incident_type,
            "metadata": incident.metadata or {},
        }

        # Store analysis in incident
        incident_repo.append_analysis(
            incident.id,
            reduced_context=reduced_context,
            analysis=analysis,
            model_name=self._config.langchain_model_name
        )

        # Send Slack notification
        ref = f"Namespace {incident.namespace} - {incident.incident_type}"
        metadata = {
            "cluster": incident.cluster,
            "incident_type": incident.incident_type,
            "started_at": str(incident.started_at),
        }
        if incident.metadata:
            metadata.update(incident.metadata)

        metadata.update(
            self._build_slack_routing_metadata(
                namespace=incident.namespace,
                namespace_channel=incident.slack_channel,
            )
        )

        channel = incident.slack_channel
        sent = self._slack.send_analysis(
            channel=channel,
            rollout_ref=ref,
            analysis=analysis,
            metadata=metadata
        )

        # Update incident notify status
        from .models import NotifyStatus
        incident_repo.update_notify_status(
            incident.id,
            NotifyStatus.SENT if sent else NotifyStatus.FAILED
        )

        # Mark job as completed
        self._alert_repo.update_job_status(
            job.id,
            "completed",
            completed_at=utcnow(),
            analysis_summary=analysis.summary
        )


class WatcherService:
    def __init__(self, config: Settings | None = None):
        self._config = config or settings
        self._engine = init_db(self._config.database_url)
        self._repo = RolloutRepo(self._engine, annotation_prefix=self._config.annotation_prefix)
        self._case_ingestor = CaseIngestionService(self._engine)

    def start(self):
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster config")
        except ConfigException:
            config.load_kube_config()
            logger.info("Loaded kube config")

        cluster = self._config.k8s_cluster_name
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

        # Add namespace monitoring thread if enabled
        if self._config.namespace_monitoring_enabled:
            namespace_monitor_thread = threading.Thread(
                target=self._namespace_monitor_loop,
                args=(cluster,),
                daemon=True,
                name="project-fyr-namespace-monitor",
            )
            threads.append(namespace_monitor_thread)
            logger.info("Namespace monitoring enabled")

        for t in threads:
            t.start()

        for t in threads:
            t.join()

    def _watch_loop(self, cluster: str):
        v1_apps = client.AppsV1Api()
        core_v1 = client.CoreV1Api()
        namespace_cache = NamespaceMetadataCache(core_v1)
        w = watch.Watch()

        # If watch_all_namespaces is True, don't filter by label
        selector = None if self._config.watch_all_namespaces else f"{self._config.annotation_prefix}/enabled=true"

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
                    ns_meta = namespace_cache.get(dep.metadata.namespace)
                    handle_deployment_event(
                        dep, etype, self._repo, cluster,
                        namespace_metadata=ns_meta,
                        config=self._config
                    )
            except Exception as exc:
                logger.error(f"watch error: {exc}")
                time.sleep(2)

    def _reconcile_loop(self, cluster: str):
        v1_apps = client.AppsV1Api()
        timeout = timedelta(seconds=self._config.rollout_timeout_seconds)
        core_v1 = client.CoreV1Api()
        while True:
            try:
                now = utcnow()
                rollouts = self._repo.list_active(cluster)
                for rollout in rollouts:
                    try:
                        dep = v1_apps.read_namespaced_deployment(rollout.deployment, rollout.namespace)
                        reconcile_rollout(
                            dep,
                            rollout,
                            now,
                            self._repo,
                            timeout,
                            core_v1=core_v1,
                            case_ingestor=self._case_ingestor,
                        )
                    except ApiException as exc:
                        if exc.status == 404:
                            reason = (
                                f"Deployment {rollout.namespace}/{rollout.deployment} no longer exists"
                            )
                            logger.info(
                                f"Discarding stale rollout {rollout.id}: {reason}"
                            )
                            try:
                                self._repo.discard_analysis(
                                    rollout.id,
                                    reason=reason,
                                    mark_success=True,
                                )
                            except Exception as discard_exc:
                                logger.error(
                                    f"reconcile discard error for rollout {rollout.id}: {discard_exc}"
                                )
                            continue
                        logger.error(f"reconcile error: {exc}")
                    except Exception as exc:
                        logger.error(f"reconcile error: {exc}")
            except Exception as exc:
                logger.error(f"reconcile loop error: {exc}")
            time.sleep(10)

    def _namespace_monitor_loop(self, cluster: str):
        """Periodically check for namespace-level issues."""
        core_v1 = client.CoreV1Api()

        logger.info(f"Starting namespace monitor loop (interval: {self._config.namespace_monitoring_interval_seconds}s)")

        while True:
            try:
                # Get all namespaces
                namespaces = core_v1.list_namespace()
                logger.info(f"Checking {len(namespaces.items)} namespaces for issues")

                for ns in namespaces.items:
                    ns_name = ns.metadata.name

                    # Check if namespace has project-fyr/enabled annotation
                    annotations = ns.metadata.annotations or {}
                    if not annotations.get(ANNOTATION_ENABLED) == "true":
                        continue

                    logger.info(f"Monitoring namespace {ns_name} (phase: {ns.status.phase if ns.status else 'Unknown'})")

                    # Extract team/channel info
                    team = annotations.get(ANNOTATION_TEAM)
                    slack_channel = annotations.get(ANNOTATION_SLACK_CHANNEL)

                    # Check for stuck terminating
                    if ns.status and ns.status.phase == "Terminating":
                        self._check_terminating_stuck(
                            cluster, ns_name, ns, team=team, slack_channel=slack_channel
                        )

                    # TODO(project-fyr): route quota, eviction, and restart namespace
                    # sensors into namespace cases; deferred while migrating the first
                    # namespace signal off legacy namespace_incidents.

            except Exception as exc:
                logger.error(f"namespace monitor error: {exc}")

            time.sleep(self._config.namespace_monitoring_interval_seconds)

    def _check_terminating_stuck(
        self, cluster: str, ns_name: str, ns, *, team, slack_channel
    ):
        """Check if namespace is stuck in Terminating state."""

        # Check how long it's been terminating
        deletion_timestamp = ns.metadata.deletion_timestamp
        if not deletion_timestamp:
            return

        now = utcnow()
        # Handle timezone-aware datetime from k8s
        if deletion_timestamp.tzinfo:
            deletion_timestamp = deletion_timestamp.replace(tzinfo=None)

        stuck_duration = now - deletion_timestamp
        threshold = timedelta(minutes=self._config.namespace_terminating_threshold_minutes)

        if stuck_duration < threshold:
            return

        # Check rate limits
        if not self._check_rate_limits(cluster, ns_name):
            logger.warning(
                f"Rate limit exceeded for namespace {ns_name}, skipping investigation"
            )
            return

        # Create incident
        logger.info(
            f"Namespace {ns_name} stuck in Terminating for {stuck_duration}, creating incident"
        )

        metadata = {
            "deletion_timestamp": deletion_timestamp.isoformat(),
            "stuck_duration_seconds": int(stuck_duration.total_seconds()),
            "finalizers": ns.metadata.finalizers or [],
        }

        case, issue = self._case_ingestor.record_namespace_issue(
            cluster=cluster,
            namespace=ns_name,
            issue_type="terminating_stuck",
            metadata=metadata,
            team=team,
            slack_channel=slack_channel,
        )

        logger.info(
            f"Created namespace case {case.id} and namespace issue {issue.id} for {ns_name}"
        )

    def _create_investigation_job(self, resource_id: int, job_type: str):
        """Create an investigation job for a rollout or namespace incident."""
        from .db import AlertRepo

        # Reuse AlertRepo for job creation (it has the job methods)
        alert_repo = AlertRepo(self._engine)

        with alert_repo.session() as s:
            from .db import InvestigationJob

            job_data = {
                "type": job_type,
                "status": "pending",
            }

            if job_type == "namespace":
                job_data["namespace_incident_id"] = resource_id
            elif job_type == "rollout":
                job_data["rollout_id"] = resource_id

            job = InvestigationJob(**job_data)
            s.add(job)
            s.commit()
            s.refresh(job)
            logger.info(f"Created investigation job {job.id} for {job_type} {resource_id}")

    def _check_rate_limits(self, cluster: str, namespace: str) -> bool:
        """Check if we're within rate limits for investigations."""
        # TODO(project-fyr): move namespace monitor rate limiting onto namespace
        # cases/issues/work_items so the limiter reflects the new control plane
        # instead of the legacy namespace_incidents table.
        del cluster, namespace
        return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    service = WatcherService()
    service.start()


class AnalyzerService:
    def __init__(self, config: Settings | None = None):
        self._config = config or settings
        self._engine = init_db(self._config.database_url)
        self._repo = RolloutRepo(self._engine, annotation_prefix=self._config.annotation_prefix)
        self._alert_repo = AlertRepo(self._engine)
        self._batcher = AlertBatcher(self._alert_repo, self._config)

    def start(self):
        try:
            config.load_incluster_config()
            logger.info("Analyzer loaded in-cluster config")
        except ConfigException:
            config.load_kube_config()
            logger.info("Analyzer loaded kube config")

        # Start Prometheus metrics server in a background thread
        self._start_metrics_server()

        # Start Batcher thread
        batcher_thread = threading.Thread(
            target=self._batcher_loop,
            daemon=True,
            name="alert-batcher"
        )
        batcher_thread.start()

        worker = AnalysisWorker(self._repo, self._alert_repo, self._config.k8s_cluster_name, self._config)
        worker.loop()

    def _batcher_loop(self):
        while True:
            try:
                self._batcher.run_once()
            except Exception as e:
                logger.error(f"Batcher error: {e}")
            time.sleep(10)

    def _start_metrics_server(self):
        """Start Prometheus metrics HTTP server on port 8000."""
        from prometheus_client import start_http_server
        try:
            start_http_server(8000)
            logger.info("Prometheus metrics server started on port 8000")
        except Exception as e:
            logger.warning(f"Failed to start Prometheus metrics server: {e}")


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
    """Signals indicating pod failures, separated by permanence."""
    total_pods: int = 0

    # Image issues (may be transient or permanent)
    image_pull_pods: int = 0  # ImagePullBackOff, ErrImagePull, InvalidImageName

    # Container configuration issues (permanent)
    config_error_pods: int = 0  # CreateContainerConfigError (missing Secret/ConfigMap)
    container_error_pods: int = 0  # CreateContainerError, RunContainerError

    # Runtime failures (usually permanent after multiple restarts)
    crashloop_pods: int = 0  # CrashLoopBackOff

    # Scheduling issues (usually permanent)
    unschedulable_pods: int = 0  # PodScheduled=False with Unschedulable reason

    # Track transient vs permanent for smarter alerting
    transient_failure_pods: int = 0  # ErrImagePull, ImagePullBackOff (may self-heal)
    permanent_failure_pods: int = 0  # InvalidImageName, CreateContainerConfigError, etc.

    # Track specific transient patterns
    qps_exceeded_pods: int = 0  # Specifically "pull QPS exceeded" - very likely transient

    # Collected failure reasons for logging/analysis
    failure_reasons: list = None

    def __post_init__(self):
        if self.failure_reasons is None:
            self.failure_reasons = []

    @property
    def total_failing(self) -> int:
        """Total pods with any failure conditions."""
        return (
            self.image_pull_pods +
            self.config_error_pods +
            self.container_error_pods +
            self.crashloop_pods +
            self.unschedulable_pods
        )

    @property
    def has_only_transient_failures(self) -> bool:
        """Check if all failures are transient (may self-heal)."""
        return self.transient_failure_pods > 0 and self.permanent_failure_pods == 0

    @property
    def is_likely_qps_issue(self) -> bool:
        """Check if this looks like a registry rate limit issue."""
        return self.qps_exceeded_pods > 0 and self.permanent_failure_pods == 0


# Waiting reasons that indicate permanent failures (won't self-heal)
# Transient failure reasons that often self-heal (need confirmation before alerting)
TRANSIENT_FAILURE_REASONS = {
    # Rate limits and temporary network issues often resolve
    "ErrImagePull",  # First pull attempt - may be QPS limit, network blip
    "ImagePullBackOff",  # Backoff state - kubelet will retry
}

# Truly permanent failure reasons (alert immediately)
PERMANENT_FAILURE_REASONS = {
    # Image issues that won't self-heal
    "InvalidImageName",  # Typo in image name - won't fix itself
    "RegistryUnavailable",  # Registry is down or unreachable

    # Container configuration (missing Secret/ConfigMap, bad volume mounts)
    "CreateContainerConfigError",
    "CreateContainerError",
    "RunContainerError",

    # Runtime crashes
    "CrashLoopBackOff",

    # Init container failures
    "InitContainerCrashLoopBackOff",
}

# Combined set for detection (but we handle them differently)
ALL_FAILURE_REASONS = TRANSIENT_FAILURE_REASONS | PERMANENT_FAILURE_REASONS


def analyze_pod_failures(pods: list) -> PodFailureSignals:
    """Analyze pods for failure conditions, distinguishing transient from permanent."""
    signals = PodFailureSignals(total_pods=len(pods))

    for pod in pods:
        pod_name = pod.metadata.name if pod.metadata else "unknown"

        # Check container statuses (including init containers)
        all_container_statuses = list(pod.status.container_statuses or [])
        all_container_statuses.extend(pod.status.init_container_statuses or [])

        for cs in all_container_statuses:
            waiting = cs.state.waiting if cs.state else None
            if not waiting:
                continue
            reason = waiting.reason or ""
            message = waiting.message or ""

            if reason in ALL_FAILURE_REASONS:
                signals.failure_reasons.append(f"{pod_name}: {reason} - {message[:100]}")

            # Track transient vs permanent
            if reason in TRANSIENT_FAILURE_REASONS:
                signals.transient_failure_pods += 1
                # Check for specific QPS exceeded pattern
                if "QPS exceeded" in message or "pull QPS" in message.lower():
                    signals.qps_exceeded_pods += 1
            elif reason in PERMANENT_FAILURE_REASONS:
                signals.permanent_failure_pods += 1

            # Categorize by failure type (for backwards compatibility)
            if reason in ("ImagePullBackOff", "ErrImagePull", "InvalidImageName", "RegistryUnavailable"):
                signals.image_pull_pods += 1
            elif reason == "CreateContainerConfigError":
                signals.config_error_pods += 1
            elif reason in ("CreateContainerError", "RunContainerError"):
                signals.container_error_pods += 1
            elif reason in ("CrashLoopBackOff", "InitContainerCrashLoopBackOff"):
                signals.crashloop_pods += 1

        # Check pod conditions for scheduling failures
        for condition in pod.status.conditions or []:
            if condition.type == "PodScheduled" and condition.status == "False":
                reason = condition.reason or ""
                message = condition.message or ""
                # Unschedulable means no node can satisfy requirements (resources, selectors, taints)
                if reason == "Unschedulable":
                    signals.unschedulable_pods += 1
                    signals.permanent_failure_pods += 1  # Unschedulable is permanent
                    signals.failure_reasons.append(f"{pod_name}: Unschedulable - {message}")

    return signals


def should_fail_early(signals: PodFailureSignals, min_pods: int = 1) -> bool:
    """
    Determine if we should mark rollout as failed immediately.

    Only triggers early failure for PERMANENT failures. Transient failures
    (like QPS rate limits) should be given time to self-heal.
    """
    if signals.total_pods < min_pods:
        return False

    # If we only have transient failures (QPS exceeded, first image pull attempts),
    # do NOT fail early - these often self-heal
    if signals.has_only_transient_failures:
        return False

    # If this looks like a pure QPS/rate limit issue, don't fail early
    if signals.is_likely_qps_issue:
        return False

    # For permanent failures, fail if at least 1 pod (or half of total pods) is affected
    threshold = max(1, signals.total_pods // 2)
    if signals.permanent_failure_pods >= threshold:
        return True

    # Also fail for crashloop (after some restarts) and config errors
    if signals.config_error_pods > 0 or signals.container_error_pods > 0:
        return True

    if signals.crashloop_pods > 0:
        return True

    return False


def rollout_metadata_dict(rollout) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if rollout.team:
        metadata["team"] = rollout.team
    if rollout.metadata_json:
        metadata.update({k: v for k, v in rollout.metadata_json.items() if v is not None})
    return metadata


# Annotation keys derived from configurable prefix
_prefix = settings.annotation_prefix
ANNOTATION_SLACK_CHANNEL = f"{_prefix}/slack-channel"
ANNOTATION_TEAM = f"{_prefix}/team"
ANNOTATION_PREFIX = f"{_prefix}/"
ANNOTATION_REQUESTOR_EMAIL = f"{_prefix}/requestor-email"
ANNOTATION_REQUESTOR_EMAIL_BARE = "requestor"  # Bare annotation (no prefix)
ANNOTATION_REQUESTOR_EMAIL_METADATA = "metadata.annotations.requestor"
ANNOTATION_ENABLED = f"{_prefix}/enabled"
LABEL_OWNER_CHANNEL = f"{_prefix}/owner-channel"


def extract_requestor_email_from_annotations(annotations: dict[str, str] | None) -> str:
    annotations = annotations or {}
    return (
        annotations.get(ANNOTATION_REQUESTOR_EMAIL)
        or annotations.get(ANNOTATION_REQUESTOR_EMAIL_BARE)
        or annotations.get(ANNOTATION_REQUESTOR_EMAIL_METADATA)
        or ""
    ).strip()


def parse_namespace_annotations(annotations: dict[str, str] | None) -> dict[str, Any]:
    annotations = annotations or {}
    namespace_specific = {k: v for k, v in annotations.items() if k.startswith(ANNOTATION_PREFIX)}
    # Support requestor email annotation variants.
    requestor_email = extract_requestor_email_from_annotations(annotations)
    if requestor_email:
        namespace_specific[ANNOTATION_REQUESTOR_EMAIL] = requestor_email
    metadata: dict[str, Any] = {}
    if namespace_specific:
        metadata["metadata_json"] = namespace_specific
    if team := annotations.get(ANNOTATION_TEAM):
        metadata["team"] = team
    if channel := annotations.get(ANNOTATION_SLACK_CHANNEL):
        metadata["slack_channel"] = channel
    return metadata


def parse_deployment_labels(labels: dict[str, str] | None) -> dict[str, Any]:
    labels = labels or {}
    metadata: dict[str, Any] = {}
    if owner_channel := labels.get(LABEL_OWNER_CHANNEL):
        metadata["deployment_labels"] = {LABEL_OWNER_CHANNEL: owner_channel}
    return metadata


def merge_rollout_metadata(namespace_metadata: dict[str, Any], deployment_labels: dict[str, str] | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    ns_metadata_json = namespace_metadata.get("metadata_json") or {}
    metadata.update(ns_metadata_json)
    if ANNOTATION_REQUESTOR_EMAIL in ns_metadata_json:
        metadata.update(parse_deployment_labels(deployment_labels))
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
    config: Settings | None = None,
):
    if event_type == "DELETED":
        return

    cfg = config or settings

    # Skip system namespaces
    if dep.metadata.namespace in cfg.system_namespaces:
        logger.debug(f"Skipping system namespace: {dep.metadata.namespace}")
        return

    labels = dep.metadata.labels or {}
    ns_meta = namespace_metadata or {}
    merged_metadata_json = merge_rollout_metadata(ns_meta, labels)

    # When watch_all_namespaces=False, check if this deployment should be watched
    # The watch selector filters by deployment label, but we also support namespace-level
    # annotations. So we need this additional check for namespace-level opt-in.
    if not cfg.watch_all_namespaces:
        # Deployment has the label
        deployment_enabled = labels.get(ANNOTATION_ENABLED) == "true"

        # Namespace has the annotation (if namespace_label_enabled is True)
        namespace_enabled = (
            cfg.namespace_label_enabled and
            ns_meta.get("metadata_json", {}).get(ANNOTATION_ENABLED) == "true"
        )

        if not (deployment_enabled or namespace_enabled):
            return

    ns = dep.metadata.namespace
    name = dep.metadata.name
    generation = dep.metadata.generation or 1
    ns_meta = namespace_metadata or {}

    rollout = repo.get_by_key(cluster, ns, name, generation)
    phase = evaluate_deployment_phase(dep)
    now = utcnow()

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
            metadata_json=merged_metadata_json,
            team=ns_meta.get("team"),
            slack_channel=ns_meta.get("slack_channel"),
        )
    else:
        if ns_meta or merged_metadata_json:
            repo.update_metadata(
                rollout.id,
                metadata_json=merged_metadata_json,
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
    case_ingestor: CaseIngestionService | None = None,
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
                # Log the specific failure reasons for debugging
                logger.info(
                    f"Early failure detected for {rollout.namespace}/{rollout.deployment}: "
                    f"image_pull={signals.image_pull_pods}, config_error={signals.config_error_pods}, "
                    f"container_error={signals.container_error_pods}, crashloop={signals.crashloop_pods}, "
                    f"unschedulable={signals.unschedulable_pods}"
                )
                if signals.failure_reasons:
                    for reason in signals.failure_reasons[:5]:  # Log first 5 reasons
                        logger.info(f"  - {reason}")

                # Store trigger context for investigation enrichment
                failure_type = "permanent"
                if signals.crashloop_pods > 0:
                    failure_type = "crashloop"
                elif signals.config_error_pods > 0:
                    failure_type = "config_error"
                elif signals.unschedulable_pods > 0:
                    failure_type = "unschedulable"

                time_to_failure = int(age.total_seconds()) if age else None
                repo.append_trigger_context(
                    rollout.id,
                    trigger_reason="permanent_failure_detected",
                    failure_observations=signals.failure_reasons[:5],
                    is_transient=False,
                    time_to_failure_seconds=time_to_failure,
                    failure_type=failure_type,
                )

                repo.update_status(rollout.id, RolloutStatus.FAILED, failed_at=now)
                # Queue for immediate analysis
                repo.queue_for_analysis(rollout.id)
                if case_ingestor is not None:
                    case_ingestor.record_rollout_failure(
                        rollout=repo.get_by_id(rollout.id) or rollout,
                        trigger_reason="permanent_failure_detected",
                        trigger_context={
                            "trigger_reason": "permanent_failure_detected",
                            "failure_type": failure_type,
                            "observed_failures": signals.failure_reasons[:5],
                            "is_transient": False,
                            "time_to_failure_seconds": time_to_failure,
                        },
                    )
                return
            elif signals.transient_failure_pods > 0:
                # Log transient failures that we're NOT failing early on
                logger.info(
                    f"Transient failures detected for {rollout.namespace}/{rollout.deployment}, "
                    f"waiting for confirmation: transient={signals.transient_failure_pods}, "
                    f"qps_exceeded={signals.qps_exceeded_pods}, permanent={signals.permanent_failure_pods}"
                )
                # Store transient failure observations for later investigation context
                repo.append_trigger_context(
                    rollout.id,
                    trigger_reason="transient_failure_observed",
                    failure_observations=signals.failure_reasons[:5],
                    is_transient=True,
                    failure_type="transient_image_pull" if signals.qps_exceeded_pods > 0 else "transient",
                )

    if phase == "STABLE":
        repo.update_status(rollout.id, RolloutStatus.SUCCESS, completed_at=now)
        # No analysis needed for successful rollouts
        return

    if phase == "FAILED_PROGRESS":
        # Store trigger context for investigation
        time_to_failure = int(age.total_seconds()) if age else None
        repo.append_trigger_context(
            rollout.id,
            trigger_reason="deployment_progress_failed",
            failure_observations=["Kubernetes reported Progressing=False condition"],
            is_transient=False,
            time_to_failure_seconds=time_to_failure,
            failure_type="failed_progress",
        )
        repo.update_status(rollout.id, RolloutStatus.FAILED, failed_at=now)
        # Queue for analysis
        repo.queue_for_analysis(rollout.id)
        if case_ingestor is not None:
            case_ingestor.record_rollout_failure(
                rollout=repo.get_by_id(rollout.id) or rollout,
                trigger_reason="deployment_progress_failed",
                trigger_context={
                    "trigger_reason": "deployment_progress_failed",
                    "failure_type": "failed_progress",
                    "observed_failures": ["Kubernetes reported Progressing=False condition"],
                    "is_transient": False,
                    "time_to_failure_seconds": time_to_failure,
                },
            )
        return

    if age > timeout:
        # Store trigger context for timeout investigation
        time_to_failure = int(age.total_seconds()) if age else None
        repo.append_trigger_context(
            rollout.id,
            trigger_reason="rollout_timeout",
            failure_observations=[f"Rollout exceeded {timeout.total_seconds()}s timeout without completing"],
            is_transient=False,
            time_to_failure_seconds=time_to_failure,
            failure_type="timeout",
        )
        repo.update_status(rollout.id, RolloutStatus.FAILED, failed_at=now)
        # Queue for analysis (timeout failure)
        repo.queue_for_analysis(rollout.id)
        if case_ingestor is not None:
            case_ingestor.record_rollout_failure(
                rollout=repo.get_by_id(rollout.id) or rollout,
                trigger_reason="rollout_timeout",
                trigger_context={
                    "trigger_reason": "rollout_timeout",
                    "failure_type": "timeout",
                    "observed_failures": [
                        f"Rollout exceeded {timeout.total_seconds()}s timeout without completing"
                    ],
                    "is_transient": False,
                    "time_to_failure_seconds": time_to_failure,
                },
            )
        return

    new_status = RolloutStatus.PENDING if phase == "PENDING" else RolloutStatus.ROLLING_OUT
    if rollout.status != new_status:
        repo.update_status(rollout.id, new_status)


def run_watcher():
    WatcherService().start()


def run_analyzer():
    AnalyzerService().start()


__all__ = ["WatcherService", "AnalyzerService", "run_watcher", "run_analyzer"]


if __name__ == "__main__":
    main()
