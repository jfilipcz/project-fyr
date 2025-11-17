"""Collect raw Kubernetes state for a rollout."""

from __future__ import annotations

from datetime import datetime, timedelta

from kubernetes import client

from .models import RawContext


class RawContextCollector:
    def __init__(self, api_client: client.ApiClient | None = None, *, log_tail_seconds: int = 300, max_log_lines: int = 200):
        self._api_client = api_client or client.ApiClient()
        self._apps = client.AppsV1Api(self._api_client)
        self._core = client.CoreV1Api(self._api_client)
        self._events = client.EventsV1Api(self._api_client)
        self._log_tail_seconds = log_tail_seconds
        self._max_log_lines = max_log_lines

    def collect(self, namespace: str, deployment_name: str) -> RawContext:
        dep = self._apps.read_namespaced_deployment(deployment_name, namespace)
        selector = dep.spec.selector.match_labels or {}
        label_selector = ",".join([f"{k}={v}" for k, v in selector.items()])
        pods = self._core.list_namespaced_pod(namespace, label_selector=label_selector)
        now = datetime.utcnow()
        events = self._events.list_namespaced_event(namespace, field_selector=f"regarding.name={deployment_name}")

        pod_dicts = [self._api_client.sanitize_for_serialization(p) for p in pods.items]
        event_dicts = [self._api_client.sanitize_for_serialization(e) for e in events.items]
        dep_dict = self._api_client.sanitize_for_serialization(dep)

        logs: dict[str, list[str]] = {}
        for pod in pods.items:
            for container in pod.spec.containers:
                name = container.name
                pod_name = pod.metadata.name
                key = f"{pod_name}/{name}"
                since_time = (now - timedelta(seconds=self._log_tail_seconds)).isoformat("T") + "Z"
                try:
                    raw_log = self._core.read_namespaced_pod_log(
                        name=pod_name,
                        namespace=namespace,
                        container=name,
                        since_time=since_time,
                        tail_lines=self._max_log_lines,
                        timestamps=True,
                    )
                    logs[key] = raw_log.splitlines()[-self._max_log_lines :]
                except Exception:
                    logs[key] = ["<log collection failed>"]

        warnings = [e for e in event_dicts if e.get("type") == "Warning"]
        return RawContext(deployment=dep_dict, pods=pod_dicts, events=warnings, logs=logs)
