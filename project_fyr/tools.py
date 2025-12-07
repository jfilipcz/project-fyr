"""Kubernetes tools for the Investigator Agent."""

from __future__ import annotations

import logging
from typing import Any, Optional

import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _get_core_v1() -> client.CoreV1Api:
    try:
        return client.CoreV1Api()
    except Exception:
        # Fallback if not initialized (though service.py should have done it)
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        return client.CoreV1Api()


def _get_apps_v1() -> client.AppsV1Api:
    try:
        return client.AppsV1Api()
    except Exception:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        return client.AppsV1Api()


def _clean_metadata(obj: dict) -> dict:
    """Remove noisy fields from k8s object metadata."""
    if "metadata" in obj:
        meta = obj["metadata"]
        for key in ["managedFields", "uid", "resourceVersion", "generation", "creationTimestamp"]:
            meta.pop(key, None)
        if "annotations" in meta:
            # Remove kubectl-last-applied-configuration as it's huge
            meta["annotations"].pop("kubectl.kubernetes.io/last-applied-configuration", None)
    return obj


@tool
def k8s_get_resources(kind: str, namespace: str, label_selector: Optional[str] = None) -> str:
    """
    List Kubernetes resources of a specific kind in a namespace.
    
    Args:
        kind: The kind of resource (e.g., "Pod", "Service", "Deployment", "Event").
        namespace: The namespace to list resources in.
        label_selector: Optional label selector to filter resources (e.g., "app=frontend").
    """
    core_v1 = _get_core_v1()
    apps_v1 = _get_apps_v1()
    
    try:
        if kind.lower() == "pod":
            items = core_v1.list_namespaced_pod(namespace, label_selector=label_selector).items
        elif kind.lower() == "service":
            items = core_v1.list_namespaced_service(namespace, label_selector=label_selector).items
        elif kind.lower() == "deployment":
            items = apps_v1.list_namespaced_deployment(namespace, label_selector=label_selector).items
        elif kind.lower() == "event":
            items = core_v1.list_namespaced_event(namespace).items
        else:
            return f"Error: Unsupported resource kind '{kind}'"
        
        # Summarize output
        summary = []
        for item in items:
            name = item.metadata.name
            if kind.lower() == "pod":
                status = item.status.phase
                restarts = sum(c.restart_count for c in (item.status.container_statuses or []))
                summary.append(f"{name} (Status: {status}, Restarts: {restarts})")
            elif kind.lower() == "deployment":
                ready = f"{item.status.ready_replicas}/{item.status.replicas}"
                summary.append(f"{name} (Ready: {ready})")
            elif kind.lower() == "event":
                summary.append(f"{item.last_timestamp} - {item.reason}: {item.message}")
            else:
                summary.append(name)
        
        return "\n".join(summary) if summary else "No resources found."
        
    except ApiException as e:
        return f"Error listing {kind}: {e.reason}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


@tool
def k8s_describe(kind: str, name: str, namespace: str) -> str:
    """
    Get detailed information about a specific Kubernetes resource (like kubectl describe/get -o yaml).
    
    Args:
        kind: The kind of resource (e.g., "Pod", "Deployment").
        name: The name of the resource.
        namespace: The namespace of the resource.
    """
    core_v1 = _get_core_v1()
    apps_v1 = _get_apps_v1()
    
    try:
        obj = None
        if kind.lower() == "pod":
            obj = core_v1.read_namespaced_pod(name, namespace)
        elif kind.lower() == "deployment":
            obj = apps_v1.read_namespaced_deployment(name, namespace)
        elif kind.lower() == "service":
            obj = core_v1.read_namespaced_service(name, namespace)
        else:
            return f"Error: Unsupported resource kind '{kind}'"
        
        # Convert to dict and clean up
        obj_dict = obj.to_dict()
        clean_obj = _clean_metadata(obj_dict)
        
        # Dump to YAML for readability
        return yaml.dump(clean_obj)
        
    except ApiException as e:
        if e.status == 404:
            return f"Error: {kind} '{name}' not found in namespace '{namespace}'"
        return f"Error getting {kind} '{name}': {e.reason}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


@tool
def k8s_logs(name: str, namespace: str, container: Optional[str] = None, tail_lines: int = 50, previous: bool = False) -> str:
    """
    Fetch logs for a specific pod.
    
    Args:
        name: The name of the pod.
        namespace: The namespace of the pod.
        container: Optional container name (defaults to the first one).
        tail_lines: Number of lines to retrieve (default 50).
        previous: If True, fetch logs from the previous instantiated container (useful for crash loops).
    """
    core_v1 = _get_core_v1()
    
    try:
        logs = core_v1.read_namespaced_pod_log(
            name,
            namespace,
            container=container,
            tail_lines=tail_lines,
            previous=previous
        )
        return logs
    except ApiException as e:
        if "ContainerCreating" in str(e):
            return "Error: Pod is still creating container."
        return f"Error fetching logs for '{name}': {e.reason}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


@tool
def k8s_events(namespace: str, involved_object_name: Optional[str] = None) -> str:
    """
    Fetch events for a namespace, optionally filtered by an involved object.
    
    Args:
        namespace: The namespace to list events in.
        involved_object_name: Optional name of the object to filter events for (e.g. pod name).
    """
    core_v1 = _get_core_v1()
    
    try:
        events = core_v1.list_namespaced_event(namespace).items
        
        # Sort by timestamp descending
        events.sort(key=lambda x: x.last_timestamp or x.event_time or x.creation_timestamp, reverse=True)
        
        output = []
        for e in events:
            if involved_object_name and e.involved_object.name != involved_object_name:
                continue
                
            ts = e.last_timestamp or e.event_time or e.creation_timestamp
            output.append(f"[{ts}] {e.type} {e.reason} ({e.involved_object.kind}/{e.involved_object.name}): {e.message}")
            
        return "\n".join(output[:20]) if output else "No events found."
        
    except ApiException as e:
        return f"Error listing events: {e.reason}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


def _get_custom_objects_api() -> client.CustomObjectsApi:
    try:
        return client.CustomObjectsApi()
    except Exception:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        return client.CustomObjectsApi()


@tool
def k8s_get_argocd_application(name: str, namespace: str = "argocd") -> str:
    """
    Get the status of an ArgoCD Application.
    
    Args:
        name: The name of the ArgoCD Application.
        namespace: The namespace where ArgoCD is installed (default: "argocd").
    """
    api = _get_custom_objects_api()
    try:
        # ArgoCD Applications are usually in group argoproj.io, version v1alpha1
        app = api.get_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace=namespace,
            plural="applications",
            name=name,
        )
        
        status = app.get("status", {})
        health = status.get("health", {}).get("status", "Unknown")
        sync = status.get("sync", {}).get("status", "Unknown")
        conditions = status.get("conditions", [])
        
        summary = [
            f"ArgoCD Application: {name}",
            f"Health: {health}",
            f"Sync Status: {sync}",
        ]
        
        if conditions:
            summary.append("Conditions:")
            for c in conditions:
                summary.append(f"- {c.get('type')}: {c.get('message')}")
                
        # Include sync result if failed
        if sync == "OutOfSync" or status.get("operationState", {}).get("phase") == "Failed":
             op_state = status.get("operationState", {})
             summary.append(f"Last Operation: {op_state.get('phase')} - {op_state.get('message')}")
             
        return "\n".join(summary)
        
    except ApiException as e:
        if e.status == 404:
            return f"ArgoCD Application '{name}' not found in namespace '{namespace}'."
        return f"Error getting ArgoCD Application: {e.reason}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


@tool
def k8s_list_helm_releases(namespace: str) -> str:
    """
    List Helm releases in a namespace by inspecting Helm secrets.
    
    Args:
        namespace: The namespace to list releases in.
    """
    core_v1 = _get_core_v1()
    try:
        # Helm v3 stores releases as secrets with type 'helm.sh/release.v1'
        # and label 'owner=helm'
        secrets = core_v1.list_namespaced_secret(
            namespace,
            label_selector="owner=helm"
        ).items
        
        # Group by release name
        releases = {}
        for s in secrets:
            # Secret name format: sh.helm.release.v1.<release_name>.v<version>
            # But we can rely on labels usually: name, status
            labels = s.metadata.labels or {}
            name = labels.get("name")
            status = labels.get("status")
            version = labels.get("version")
            modified = s.metadata.creation_timestamp
            
            if name:
                # Keep the latest version for each release
                if name not in releases or int(version or 0) > int(releases[name]["version"] or 0):
                    releases[name] = {
                        "status": status,
                        "version": version,
                        "modified": modified
                    }
        
        if not releases:
            return "No Helm releases found."
            
        output = ["Helm Releases:"]
        for name, info in releases.items():
            output.append(f"- {name} (Rev: {info['version']}, Status: {info['status']}, Updated: {info['modified']})")
            
        return "\n".join(output)
        
    except ApiException as e:
        return f"Error listing Helm releases: {e.reason}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"
