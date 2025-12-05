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
        self._custom = client.CustomObjectsApi(self._api_client)
        self._log_tail_seconds = log_tail_seconds
        self._max_log_lines = max_log_lines

    def _fetch_argocd_app(self, namespace: str, deployment_name: str, labels: dict) -> dict | None:
        # ArgoCD usually labels resources with app.kubernetes.io/instance=<app-name>
        app_name = labels.get("app.kubernetes.io/instance")
        if not app_name:
            return None
        
        # We assume the Application is in the 'argocd' namespace by default, 
        # but it could be elsewhere. We'll try to find it.
        # For simplicity, let's try to find it in the same namespace or 'argocd'.
        # Actually, we can list Applications with label selector.
        
        try:
            # Try to find the Application object
            # Group: argoproj.io, Version: v1alpha1, Plural: applications
            # We search across all namespaces because the App might be in a control plane ns.
            apps = self._custom.list_cluster_custom_object(
                group="argoproj.io",
                version="v1alpha1",
                plural="applications",
                label_selector=f"argocd.argoproj.io/instance={app_name}" # This might not be right.
                # The label on the Deployment is app.kubernetes.io/instance=<app-name>
                # The Application object itself usually has metadata.name = <app-name>
            )
            
            # If listing by label doesn't work (because the App object doesn't have the label pointing to itself),
            # we might need to guess the name.
            # Let's try listing with field selector if possible, or just filter.
            
            # Re-thinking: The label `app.kubernetes.io/instance` on the Deployment matches the Application name.
            # But the Application might be in a different namespace (e.g. argocd).
            # We can try to list all Applications and filter by name, or assume 'argocd' namespace.
            # Listing all might be heavy.
            
            # Let's try a safer approach:
            # 1. Check if we can find an Application with name `app_name` in 'argocd' namespace.
            try:
                app = self._custom.get_namespaced_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    namespace="argocd",
                    plural="applications",
                    name=app_name
                )
                return app
            except client.ApiException:
                pass
            
            # 2. If not found, maybe it's in the same namespace?
            try:
                app = self._custom.get_namespaced_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="applications",
                    name=app_name
                )
                return app
            except client.ApiException:
                pass
                
        except Exception:
            pass
        return None

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
        
        argocd_app = self._fetch_argocd_app(namespace, deployment_name, dep.metadata.labels or {})
        argocd_dict = self._api_client.sanitize_for_serialization(argocd_app) if argocd_app else None

        return RawContext(deployment=dep_dict, pods=pod_dicts, events=warnings, logs=logs, argocd_app=argocd_dict)
