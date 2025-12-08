"""Kubernetes tools for the Investigator Agent."""

from __future__ import annotations

import logging
from typing import Any, Optional
from datetime import datetime, timedelta

import yaml
import requests
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from langchain_core.tools import tool

from .config import settings

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


@tool
def k8s_get_configmap(name: str, namespace: str) -> str:
    """
    Get the content of a ConfigMap.
    
    Args:
        name: The name of the ConfigMap.
        namespace: The namespace of the ConfigMap.
    """
    core_v1 = _get_core_v1()
    try:
        cm = core_v1.read_namespaced_config_map(name, namespace)
        data = cm.data or {}
        return yaml.dump(data) if data else "Empty ConfigMap"
    except ApiException as e:
        if e.status == 404:
            return f"ConfigMap '{name}' not found in namespace '{namespace}'."
        return f"Error getting ConfigMap: {e.reason}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


@tool
def k8s_get_secret_structure(name: str, namespace: str) -> str:
    """
    Get the structure (keys only) of a Secret. Values are REDACTED.
    
    Args:
        name: The name of the Secret.
        namespace: The namespace of the Secret.
    """
    core_v1 = _get_core_v1()
    try:
        secret = core_v1.read_namespaced_secret(name, namespace)
        keys = list(secret.data.keys()) if secret.data else []
        return f"Secret '{name}' contains keys: {', '.join(keys)}"
    except ApiException as e:
        if e.status == 404:
            return f"Secret '{name}' not found in namespace '{namespace}'."
        return f"Error getting Secret: {e.reason}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


@tool
def k8s_get_storage(namespace: str) -> str:
    """
    List PersistentVolumeClaims (PVCs) in a namespace and their status.
    
    Args:
        namespace: The namespace to list PVCs in.
    """
    core_v1 = _get_core_v1()
    try:
        pvcs = core_v1.list_namespaced_persistent_volume_claim(namespace).items
        if not pvcs:
            return "No PVCs found."
        
        output = ["PersistentVolumeClaims:"]
        for pvc in pvcs:
            name = pvc.metadata.name
            phase = pvc.status.phase
            capacity = pvc.status.capacity.get("storage", "Unknown") if pvc.status.capacity else "Unknown"
            volume = pvc.spec.volume_name or "Pending"
            output.append(f"- {name}: {phase} (Capacity: {capacity}, Volume: {volume})")
            
        return "\n".join(output)
    except ApiException as e:
        return f"Error listing PVCs: {e.reason}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


def _get_networking_v1() -> client.NetworkingV1Api:
    try:
        return client.NetworkingV1Api()
    except Exception:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        return client.NetworkingV1Api()


@tool
def k8s_get_network(namespace: str) -> str:
    """
    List Services and Ingresses in a namespace.
    
    Args:
        namespace: The namespace to list network resources in.
    """
    core_v1 = _get_core_v1()
    net_v1 = _get_networking_v1()
    
    output = []
    
    try:
        # Services
        services = core_v1.list_namespaced_service(namespace).items
        if services:
            output.append("Services:")
            for svc in services:
                name = svc.metadata.name
                type_ = svc.spec.type
                ports = ", ".join([f"{p.port}/{p.protocol}" for p in svc.spec.ports]) if svc.spec.ports else "No ports"
                cluster_ip = svc.spec.cluster_ip
                output.append(f"- {name} ({type_}): {cluster_ip} [{ports}]")
        else:
            output.append("No Services found.")
            
        output.append("")
        
        # Ingresses
        ingresses = net_v1.list_namespaced_ingress(namespace).items
        if ingresses:
            output.append("Ingresses:")
            for ing in ingresses:
                name = ing.metadata.name
                rules = []
                for rule in (ing.spec.rules or []):
                    host = rule.host or "*"
                    paths = [p.path for p in (rule.http.paths or [])]
                    rules.append(f"{host}{paths}")
                output.append(f"- {name}: {', '.join(rules)}")
        else:
            output.append("No Ingresses found.")
            
        return "\n".join(output)
        
    except ApiException as e:
        return f"Error listing network resources: {e.reason}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


@tool
def k8s_get_nodes() -> str:
    """
    List nodes with their status, roles, and taints.
    Useful for diagnosing scheduling issues (Pending pods).
    """
    core_v1 = _get_core_v1()
    try:
        nodes = core_v1.list_node().items
        output = ["Nodes:"]
        for node in nodes:
            name = node.metadata.name
            
            # Status
            conditions = node.status.conditions or []
            ready_cond = next((c for c in conditions if c.type == "Ready"), None)
            status = "Ready" if ready_cond and ready_cond.status == "True" else "NotReady"
            
            # Roles
            labels = node.metadata.labels or {}
            roles = [k.split("/")[-1] for k in labels.keys() if "node-role.kubernetes.io" in k]
            roles_str = ", ".join(roles) if roles else "worker"
            
            # Taints
            taints = node.spec.taints or []
            taints_str = ", ".join([f"{t.key}={t.value}:{t.effect}" for t in taints]) if taints else "None"
            
            # Capacity (simplified)
            cpu = node.status.allocatable.get("cpu", "?")
            mem = node.status.allocatable.get("memory", "?")
            
            output.append(f"- {name} ({roles_str}): {status} [CPU: {cpu}, Mem: {mem}] Taints: {taints_str}")
            
        return "\n".join(output)
    except ApiException as e:
        return f"Error listing nodes: {e.reason}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


def _get_auth_v1() -> client.AuthorizationV1Api:
    try:
        return client.AuthorizationV1Api()
    except Exception:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        return client.AuthorizationV1Api()


@tool
def k8s_check_rbac(service_account: str, namespace: str, verb: str, resource: str, resource_name: Optional[str] = None) -> str:
    """
    Check if a ServiceAccount has permission to perform an action.
    
    Args:
        service_account: Name of the ServiceAccount.
        namespace: Namespace of the ServiceAccount.
        verb: The action (get, list, watch, create, update, patch, delete).
        resource: The resource type (pods, secrets, configmaps, etc.).
        resource_name: Optional name of the specific resource.
    """
    auth_v1 = _get_auth_v1()
    try:
        # Construct the SubjectAccessReview
        sar = client.V1SubjectAccessReview(
            spec=client.V1SubjectAccessReviewSpec(
                resource_attributes=client.V1ResourceAttributes(
                    namespace=namespace,
                    verb=verb,
                    resource=resource,
                    name=resource_name,
                ),
                user=f"system:serviceaccount:{namespace}:{service_account}",
            )
        )
        
        response = auth_v1.create_subject_access_review(sar)
        allowed = response.status.allowed
        reason = response.status.reason or "No reason provided"
        
        result = "ALLOWED" if allowed else "DENIED"
        return f"Permission check for {service_account} to {verb} {resource}/{resource_name or '*'}: {result} ({reason})"
        
    except ApiException as e:
        return f"Error checking RBAC: {e.reason}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


@tool
def k8s_get_network_policies(namespace: str) -> str:
    """
    List NetworkPolicies in a namespace.
    """
    net_v1 = _get_networking_v1()
    try:
        policies = net_v1.list_namespaced_network_policy(namespace).items
        if not policies:
            return "No NetworkPolicies found (all traffic allowed unless denied by other means)."
            
        output = ["NetworkPolicies:"]
        for np in policies:
            name = np.metadata.name
            pod_selector = np.spec.pod_selector.match_labels or {}
            policy_types = np.spec.policy_types or []
            output.append(f"- {name}: Selects {pod_selector}, Types: {policy_types}")
            
        return "\n".join(output)
    except ApiException as e:
        return f"Error listing NetworkPolicies: {e.reason}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


@tool
def k8s_get_endpoints(service_name: str, namespace: str) -> str:
    """
    List Endpoints for a Service to check if it targets any pods.
    """
    core_v1 = _get_core_v1()
    try:
        eps = core_v1.read_namespaced_endpoints(service_name, namespace)
        subsets = eps.subsets or []
        
        output = [f"Endpoints for {service_name}:"]
        
        total_addresses = 0
        total_not_ready = 0
        
        for subset in subsets:
            addresses = subset.addresses or []
            not_ready = subset.not_ready_addresses or []
            ports = subset.ports or []
            
            total_addresses += len(addresses)
            total_not_ready += len(not_ready)
            
            ports_str = ", ".join([f"{p.port}/{p.protocol}" for p in ports])
            
            if addresses:
                ips = ", ".join([a.ip for a in addresses])
                output.append(f"  Ready IPs ({ports_str}): {ips}")
            
            if not_ready:
                ips = ", ".join([a.ip for a in not_ready])
                output.append(f"  NotReady IPs ({ports_str}): {ips}")
                
        if total_addresses == 0 and total_not_ready == 0:
            return f"Service {service_name} has NO endpoints. Check selector labels."
            
        return "\n".join(output)
        
    except ApiException as e:
        if e.status == 404:
            return f"Endpoints for service '{service_name}' not found."
        return f"Error getting endpoints: {e.reason}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


@tool
def k8s_query_prometheus(
    query: str,
    namespace: str,
    pod_pattern: Optional[str] = None,
    lookback_minutes: int = 60
) -> str:
    """Query Prometheus metrics for debugging Kubernetes deployments.
    
    Useful for identifying:
    - Pod restart patterns and frequency
    - OOMKills and memory pressure
    - CPU throttling indicating resource constraints
    - Network errors or connectivity issues
    
    Common query patterns:
    - "restarts" - Shows container restart count over time
    - "oom" - Shows OOMKilled containers
    - "cpu_throttled" - Shows CPU throttling percentage
    - "memory_usage" - Shows memory usage vs limits
    - "network_errors" - Shows network receive/transmit errors
    
    Args:
        query: Query type - one of: restarts, oom, cpu_throttled, memory_usage, network_errors, custom
        namespace: Kubernetes namespace to query
        pod_pattern: Optional pod name pattern (regex) to filter by
        lookback_minutes: How many minutes to look back (default 60)
    """
    if not settings.prometheus_url:
        return "Prometheus is not configured. Set PROJECT_FYR_PROMETHEUS_URL environment variable."
    
    try:
        # Build PromQL query based on the requested metric
        pod_filter = f', pod=~"{pod_pattern}.*"' if pod_pattern else ''
        
        promql_queries = {
            "restarts": f'increase(kube_pod_container_status_restarts_total{{namespace="{namespace}"{pod_filter}}}[{lookback_minutes}m])',
            "oom": f'kube_pod_container_status_terminated_reason{{reason="OOMKilled", namespace="{namespace}"{pod_filter}}}',
            "cpu_throttled": f'rate(container_cpu_cfs_throttled_seconds_total{{namespace="{namespace}"{pod_filter}}}[5m]) * 100',
            "memory_usage": f'(container_memory_working_set_bytes{{namespace="{namespace}"{pod_filter}}} / container_spec_memory_limit_bytes{{namespace="{namespace}"{pod_filter}}}) * 100',
            "network_errors": f'rate(container_network_receive_errors_total{{namespace="{namespace}"{pod_filter}}}[5m]) + rate(container_network_transmit_errors_total{{namespace="{namespace}"{pod_filter}}}[5m])',
        }
        
        promql = promql_queries.get(query, query)
        
        # Query Prometheus
        url = f"{settings.prometheus_url.rstrip('/')}/api/v1/query"
        response = requests.get(
            url,
            params={"query": promql},
            timeout=10
        )
        response.raise_for_status()
        
        data = response.json()
        if data["status"] != "success":
            return f"Prometheus query failed: {data.get('error', 'unknown error')}"
        
        results = data["data"]["result"]
        if not results:
            return f"No metrics found for query '{query}' in namespace '{namespace}'"
        
        # Format results
        output = [f"Prometheus Metrics ({query}, last {lookback_minutes}m):"]
        
        for result in results:
            metric = result["metric"]
            value = result["value"][1]  # [timestamp, value]
            
            # Extract relevant labels
            pod = metric.get("pod", "")
            container = metric.get("container", "")
            
            # Format based on query type
            if query == "restarts":
                if float(value) > 0:
                    output.append(f"  • Pod {pod}: {float(value):.0f} restarts")
            elif query == "oom":
                output.append(f"  • Pod {pod}, Container {container}: OOMKilled")
            elif query == "cpu_throttled":
                throttle_pct = float(value)
                if throttle_pct > 1:  # Only show significant throttling
                    output.append(f"  • Pod {pod}, Container {container}: {throttle_pct:.1f}% CPU throttled")
            elif query == "memory_usage":
                mem_pct = float(value)
                output.append(f"  • Pod {pod}, Container {container}: {mem_pct:.1f}% memory usage")
            elif query == "network_errors":
                error_rate = float(value)
                if error_rate > 0:
                    output.append(f"  • Pod {pod}: {error_rate:.2f} network errors/sec")
            else:
                # Custom query - just show the metric
                output.append(f"  • {metric}: {value}")
        
        if len(output) == 1:  # Only header, no results
            output.append("  No significant issues detected")
        
        return "\n".join(output)
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Prometheus query failed: {e}")
        return f"Failed to query Prometheus: {str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error querying Prometheus: {e}", exc_info=True)
        return f"Error querying Prometheus: {str(e)}"
