"""Simple heuristics to triage failures to the right team."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .models import Analysis, ReducedContext


class TriageTeam(str, Enum):
    APPLICATION = "application"
    INFRA = "infra"
    SECURITY = "security"


@dataclass
class TriageDecision:
    team: TriageTeam
    reason: str


SECURITY_KEYWORDS = {
    "forbidden",
    "unauthorized",
    "tls handshake",
    "certificate",
    "secret",
    "token",
    "rbac",
    "psp",
    "policy",
    "security",
    "encryption",
}

INFRA_KEYWORDS = {
    "failedscheduling",
    "failed scheduling",
    "insufficient",
    "nodepressure",
    "taint",
    "toleration",
    "cni",
    "network plugin",
    "persistentvolume",
    "pv ",
    "pvc ",
    "storage",
    "dns",
    "connection timeout",
    "imagepullbackoff",
    "errimagepull",
}


def _text_from_events(events: Iterable) -> str:
    parts: list[str] = []
    for event in events:
        parts.append(event.reason or "")
        parts.append(event.message_template or "")
    return " ".join(parts)


def _text_from_logs(log_clusters: Iterable) -> str:
    parts: list[str] = []
    for cluster in log_clusters:
        parts.append(cluster.template or "")
        parts.append(cluster.example or "")
    return " ".join(parts)


def triage_failure(context: ReducedContext, analysis: Analysis) -> TriageDecision:
    text_segments = [
        context.summary,
        context.phase,
        analysis.summary,
        analysis.likely_cause,
        _text_from_events(context.events),
        _text_from_logs(context.log_clusters),
    ]
    haystack = " ".join([segment or "" for segment in text_segments]).lower()

    if any(keyword in haystack for keyword in SECURITY_KEYWORDS):
        return TriageDecision(
            team=TriageTeam.SECURITY,
            reason="Security-related keywords detected (permissions, secrets, TLS).",
        )

    if any(keyword in haystack for keyword in INFRA_KEYWORDS):
        return TriageDecision(
            team=TriageTeam.INFRA,
            reason="Infrastructure symptoms detected (scheduling, networking, storage).",
        )

    return TriageDecision(
        team=TriageTeam.APPLICATION,
        reason="Defaulted to application owners after no infra/security signals were found.",
    )
