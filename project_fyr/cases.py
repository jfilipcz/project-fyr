# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Namespace-case ingestion helpers."""

from __future__ import annotations

from typing import Any

from .db import NamespaceCaseRepo, WorkItemRepo
from .issue_classifier import classify_issue_signal
from .models import IssueScope, IssueStatus, WorkItemKind


class CaseIngestionService:
    """Convert runtime failure signals into namespace-case records."""

    def __init__(self, engine):
        self._case_repo = NamespaceCaseRepo(engine)
        self._work_repo = WorkItemRepo(engine)

    def record_rollout_failure(
        self,
        *,
        rollout,
        trigger_reason: str,
        trigger_context: dict[str, Any] | None = None,
        source: str = "reconcile",
    ):
        trigger_context = trigger_context or {}
        classification = classify_issue_signal(
            signal_type=trigger_reason,
            trigger_context=trigger_context,
        )

        case = self._case_repo.get_or_create_open_case(
            rollout.cluster,
            rollout.namespace,
            slack_channel=getattr(rollout, "slack_channel", None),
            metadata_json={
                "latest_source": source,
                "team": getattr(rollout, "team", None),
            },
        )
        issue = self._case_repo.get_or_create_issue(
            namespace_case_id=case.id,
            scope=IssueScope.DEPLOYMENT,
            resource_kind="Deployment",
            resource_name=rollout.deployment,
            rollout_id=rollout.id,
            cause_family=classification.cause_family,
            issue_type=classification.issue_type,
            status=IssueStatus.ACTIVE,
            metadata_json={
                "generation": getattr(rollout, "generation", None),
                "trigger_reason": trigger_reason,
                "classification_reasons": classification.reasons,
                "dependency_target": classification.dependency_target,
            },
        )
        self._case_repo.append_issue_observation(
            issue.id,
            source=source,
            signal_type=trigger_reason,
            payload_json={
                "rollout_id": rollout.id,
                "generation": getattr(rollout, "generation", None),
                "deployment": rollout.deployment,
                "namespace": rollout.namespace,
                "trigger_context": trigger_context,
                "classification": {
                    "cause_family": classification.cause_family,
                    "issue_type": classification.issue_type,
                    "dependency_target": classification.dependency_target,
                    "reasons": classification.reasons,
                },
            },
        )
        self._work_repo.enqueue_work_item(
            kind=WorkItemKind.ISSUE_INVESTIGATION,
            namespace_case_id=case.id,
            issue_id=issue.id,
        )
        self._work_repo.enqueue_work_item(
            kind=WorkItemKind.CASE_RECHECK,
            namespace_case_id=case.id,
        )
        return case, issue

    def record_namespace_issue(
        self,
        *,
        cluster: str,
        namespace: str,
        issue_type: str,
        metadata: dict[str, Any] | None = None,
        source: str = "namespace_monitor",
        team: str | None = None,
        slack_channel: str | None = None,
    ):
        metadata = metadata or {}
        case = self._case_repo.get_or_create_open_case(
            cluster,
            namespace,
            slack_channel=slack_channel,
            metadata_json={
                "latest_source": source,
                "team": team,
            },
        )
        issue = self._case_repo.get_or_create_issue(
            namespace_case_id=case.id,
            scope=IssueScope.NAMESPACE,
            resource_kind="Namespace",
            resource_name=namespace,
            cause_family="other",
            issue_type=issue_type,
            status=IssueStatus.ACTIVE,
            metadata_json=metadata,
        )
        self._case_repo.append_issue_observation(
            issue.id,
            source=source,
            signal_type=issue_type,
            payload_json={
                "namespace": namespace,
                "issue_type": issue_type,
                "metadata": metadata,
            },
        )
        self._work_repo.enqueue_work_item(
            kind=WorkItemKind.ISSUE_INVESTIGATION,
            namespace_case_id=case.id,
            issue_id=issue.id,
        )
        self._work_repo.enqueue_work_item(
            kind=WorkItemKind.CASE_RECHECK,
            namespace_case_id=case.id,
        )
        return case, issue
