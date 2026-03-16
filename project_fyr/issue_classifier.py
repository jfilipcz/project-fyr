# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Deterministic issue cause-family classifier."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class Classification:
    cause_family: str
    issue_type: str
    dependency_target: str | None
    reasons: list[str]


_DEPENDENCY_TARGETS = ("mysql", "temporal", "redis", "postgres", "postgresql", "rabbitmq")


def _normalize_text(parts: list[str]) -> str:
    return "\n".join(part.strip().lower() for part in parts if part and part.strip())


def _extract_dependency_target(text: str) -> str | None:
    for target in _DEPENDENCY_TARGETS:
        if target in text:
            if target == "postgresql":
                return "postgres"
            return target
    return None


def _extract_storage_target(text: str) -> str | None:
    patterns = [
        r"pvc[`'\s:()\"]+([a-z0-9][a-z0-9-]*)",
        r"persistentvolumeclaim[`'\"\s(]*([a-z0-9][a-z0-9-]*)",
        r"volumemount[`'\"\s(]*([a-z0-9][a-z0-9-]*)",
        r"mount(?:ed)? pvc[`'\"\s(]*([a-z0-9][a-z0-9-]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def classify_issue_signal(
    *,
    signal_type: str,
    trigger_context: dict[str, Any] | None = None,
    analysis_text: str | None = None,
) -> Classification:
    del signal_type  # reserved for future use

    trigger_context = trigger_context or {}
    failure_type = str(trigger_context.get("failure_type") or "").strip().lower()
    observed_failures = [str(item) for item in trigger_context.get("observed_failures") or []]
    combined_text = _normalize_text([failure_type, *observed_failures, analysis_text or ""])
    reasons: list[str] = []

    storage_target = _extract_storage_target(combined_text)
    if any(
        marker in combined_text
        for marker in (
            "stale nfs file handle",
            "broken nfs",
            "persistentvolumeclaim",
            "pvc ",
            "subpath",
            "failed to prepare subpath",
            "volume mount",
            "volumemount",
        )
    ):
        reasons.append("shared storage or pvc mount failure evidence detected")
        return Classification(
            cause_family="shared_storage_failure",
            issue_type=failure_type or "storage_failure",
            dependency_target=storage_target,
            reasons=reasons,
        )

    if failure_type == "unschedulable" or "unschedulable" in combined_text:
        if any(
            marker in combined_text
            for marker in (
                "untolerated taint",
                "node affinity/selector",
                "didn't match pod's node affinity/selector",
                "didn't match pod's node affinity",
                "node selector",
                "node(s) had untolerated taint",
            )
        ):
            reasons.append("untolerated taint or affinity/selector mismatch detected")
            return Classification(
                cause_family="placement_mismatch",
                issue_type="unschedulable",
                dependency_target=None,
                reasons=reasons,
            )

        if any(marker in combined_text for marker in ("insufficient cpu", "insufficient memory")):
            reasons.append("pure capacity shortage detected")
            return Classification(
                cause_family="capacity_autoscale_pending",
                issue_type="unschedulable",
                dependency_target=None,
                reasons=reasons,
            )

    dependency_target = _extract_dependency_target(combined_text)
    if dependency_target or any(
        marker in combined_text
        for marker in (
            "connection refused",
            "failed to connect",
            "service unavailable",
            "migration",
            "init container",
            "database connection",
        )
    ):
        if dependency_target:
            reasons.append(f"shared dependency target '{dependency_target}' detected")
        else:
            reasons.append("shared dependency failure evidence detected")
        return Classification(
            cause_family="shared_dependency_failure",
            issue_type=failure_type or "dependency_failure",
            dependency_target=dependency_target,
            reasons=reasons,
        )

    if failure_type == "config_error" or any(
        marker in combined_text
        for marker in (
            "createcontainerconfigerror",
            "secret",
            "configmap",
            "not found",
            "invalid env",
        )
    ):
        reasons.append("config or secret error detected")
        return Classification(
            cause_family="app_config_or_secret",
            issue_type=failure_type or "config_error",
            dependency_target=None,
            reasons=reasons,
        )

    if failure_type == "timeout":
        reasons.append("single rollout timeout detected")
        return Classification(
            cause_family="single_rollout_timeout",
            issue_type="timeout",
            dependency_target=None,
            reasons=reasons,
        )

    reasons.append("no specialized classifier matched")
    return Classification(
        cause_family="other",
        issue_type=failure_type or "other",
        dependency_target=None,
        reasons=reasons,
    )
