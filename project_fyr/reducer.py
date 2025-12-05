"""Reduce RawContext into a compact representation for the LLM."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

from .models import EventSummary, LogCluster, RawContext, ReducedContext


def _determine_phase(dep: dict, pods: list[dict]) -> tuple[str, list[str]]:
    status = dep.get("status", {})
    available = status.get("availableReplicas", 0)
    desired = dep.get("spec", {}).get("replicas", 0)
    progressing = status.get("conditions", [])
    failing_pods: list[str] = []
    for pod in pods:
        phase = pod.get("status", {}).get("phase")
        if phase not in {"Running", "Succeeded"}:
            failing_pods.append(pod.get("metadata", {}).get("name", "unknown"))

    condition_map = {c.get("type"): c for c in progressing}
    if available >= desired and desired != 0:
        phase = "STABLE"
    elif condition_map.get("Progressing", {}).get("status") == "False":
        phase = "FAILED_PROGRESS"
    else:
        phase = "ROLLING_OUT"
    return phase, failing_pods


def _cluster_logs(logs: dict[str, list[str]], limit: int) -> list[LogCluster]:
    clusters: list[LogCluster] = []
    for key, lines in logs.items():
        pod, container = key.split("/", 1)
        template_counter = Counter(line.split(" ")[0] for line in lines if line)
        if not template_counter:
            continue
        template, count = template_counter.most_common(1)[0]
        example = next((line for line in lines if line.startswith(template)), lines[0])
        clusters.append(
            LogCluster(
                pod=pod,
                container=container,
                template=template,
                example=example[:300],
                count=count,
                last_timestamp=lines[-1][:32] if lines else None,
            )
        )
    clusters.sort(key=lambda c: c.count, reverse=True)
    return clusters[:limit]


def _summarize_events(events: Iterable[dict], limit: int) -> list[EventSummary]:
    grouped = defaultdict(list)
    for event in events:
        reason = event.get("reason", "Unknown")
        grouped[reason].append(event)

    summaries: list[EventSummary] = []
    for reason, records in grouped.items():
        template_counter = Counter(r.get("note") for r in records)
        template, count = template_counter.most_common(1)[0]
        last_timestamp = max((r.get("eventTime") or r.get("deprecatedLastTimestamp")) for r in records)
        summaries.append(
            EventSummary(
                reason=reason,
                message_template=(template or "" )[:300],
                count=count,
                last_timestamp=last_timestamp or "",
            )
        )

    summaries.sort(key=lambda s: s.count, reverse=True)
    return summaries[:limit]


class ContextReducer:
    def __init__(self, *, max_events: int = 20, max_clusters: int = 8):
        self._max_events = max_events
        self._max_clusters = max_clusters

    def reduce(self, raw: RawContext) -> ReducedContext:
        metadata = raw.deployment.get("metadata", {})
        ns = metadata.get("namespace", "default")
        name = metadata.get("name", "deployment")
        generation = metadata.get("generation", 1)
        phase, failing = _determine_phase(raw.deployment, raw.pods)
        log_clusters = _cluster_logs(raw.logs, self._max_clusters)
        events = _summarize_events(raw.events, self._max_events)
        summary = f"{name} desired replicas {raw.deployment.get('spec', {}).get('replicas', 0)}; failing pods: {', '.join(failing) or 'none'}"

        return ReducedContext(
            namespace=ns,
            deployment=name,
            generation=generation,
            summary=summary[:500],
            phase=phase,
            failing_pods=failing,
            log_clusters=log_clusters,
            events=events,
            argocd_status=raw.argocd_app.get("status") if raw.argocd_app else None,
        )
