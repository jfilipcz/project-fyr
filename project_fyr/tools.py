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
        if "annotations" in meta and meta["annotations"]:
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


# Namespace-specific investigation tools

@tool
def get_namespace_details(namespace: str) -> str:
    """
    Get detailed information about a namespace including status, labels, annotations, and finalizers.
    Use this to investigate namespace-level issues like stuck terminating state.
    
    Args:
        namespace: The name of the namespace to investigate.
    """
    core_v1 = _get_core_v1()
    
    try:
        ns = core_v1.read_namespace(namespace)
        
        details = [
            f"Namespace: {ns.metadata.name}",
            f"Status: {ns.status.phase if ns.status else 'Unknown'}",
            f"Created: {ns.metadata.creation_timestamp}",
        ]
        
        if ns.metadata.deletion_timestamp:
            details.append(f"Deletion Timestamp: {ns.metadata.deletion_timestamp}")
        
        if ns.metadata.finalizers:
            details.append(f"Finalizers: {', '.join(ns.metadata.finalizers)}")
        
        if ns.metadata.labels:
            details.append("Labels:")
            for k, v in ns.metadata.labels.items():
                details.append(f"  {k}: {v}")
        
        if ns.metadata.annotations:
            details.append("Annotations:")
            for k, v in ns.metadata.annotations.items():
                if k != "kubectl.kubernetes.io/last-applied-configuration":
                    details.append(f"  {k}: {v}")
        
        return "\n".join(details)
        
    except ApiException as e:
        return f"Error getting namespace details: {e.reason}"
    except Exception as e:
        logger.error(f"Error in get_namespace_details: {e}", exc_info=True)
        return f"Error: {str(e)}"


@tool
def get_namespace_resource_quotas(namespace: str) -> str:
    """
    Get resource quotas and current usage for a namespace.
    Use this to investigate quota-related issues.
    
    Args:
        namespace: The name of the namespace.
    """
    core_v1 = _get_core_v1()
    
    try:
        quotas = core_v1.list_namespaced_resource_quota(namespace).items
        
        if not quotas:
            return f"No resource quotas defined for namespace {namespace}"
        
        details = [f"Resource Quotas for {namespace}:"]
        
        for quota in quotas:
            details.append(f"\n{quota.metadata.name}:")
            
            if quota.status.hard:
                details.append("  Hard Limits:")
                for resource, limit in quota.status.hard.items():
                    used = quota.status.used.get(resource, "0") if quota.status.used else "0"
                    details.append(f"    {resource}: {used} / {limit}")
        
        return "\n".join(details)
        
    except ApiException as e:
        return f"Error getting resource quotas: {e.reason}"
    except Exception as e:
        logger.error(f"Error in get_namespace_resource_quotas: {e}", exc_info=True)
        return f"Error: {str(e)}"


@tool
def get_namespace_pods_summary(namespace: str) -> str:
    """
    Get a summary of all pods in a namespace including their status, restarts, and age.
    Use this to investigate namespace-wide pod issues.
    
    Args:
        namespace: The name of the namespace.
    """
    core_v1 = _get_core_v1()
    
    try:
        pods = core_v1.list_namespaced_pod(namespace).items
        
        if not pods:
            return f"No pods found in namespace {namespace}"
        
        # Group by status
        by_status = {}
        total_restarts = 0
        failing_pods = []
        
        for pod in pods:
            phase = pod.status.phase
            by_status[phase] = by_status.get(phase, 0) + 1
            
            restarts = sum(c.restart_count for c in (pod.status.container_statuses or []))
            total_restarts += restarts
            
            if phase in ["Failed", "Unknown"] or restarts > 5:
                failing_pods.append({
                    "name": pod.metadata.name,
                    "phase": phase,
                    "restarts": restarts,
                    "reason": pod.status.reason or "N/A"
                })
        
        summary = [
            f"Pod Summary for {namespace}:",
            f"Total Pods: {len(pods)}",
            "By Status:",
        ]
        
        for status, count in sorted(by_status.items()):
            summary.append(f"  {status}: {count}")
        
        summary.append(f"Total Container Restarts: {total_restarts}")
        
        if failing_pods:
            summary.append("\nPods with Issues:")
            for pod in failing_pods[:10]:  # Limit to 10
                summary.append(f"  • {pod['name']}: {pod['phase']} (Restarts: {pod['restarts']}, Reason: {pod['reason']})")
        
        return "\n".join(summary)
        
    except ApiException as e:
        return f"Error getting pods summary: {e.reason}"
    except Exception as e:
        logger.error(f"Error in get_namespace_pods_summary: {e}", exc_info=True)
        return f"Error: {str(e)}"


@tool
def get_namespace_events(namespace: str, last_minutes: int = 60) -> str:
    """
    Get recent events in a namespace to understand what's happening.
    Use this to investigate the sequence of events leading to issues.
    
    Args:
        namespace: The name of the namespace.
        last_minutes: Only show events from the last N minutes (default 60).
    """
    core_v1 = _get_core_v1()
    
    try:
        events = core_v1.list_namespaced_event(namespace).items
        
        if not events:
            return f"No events found in namespace {namespace}"
        
        # Filter by time
        cutoff = datetime.utcnow() - timedelta(minutes=last_minutes)
        recent_events = []
        
        for event in events:
            if event.last_timestamp:
                # Handle timezone-aware datetime
                event_time = event.last_timestamp
                if event_time.tzinfo:
                    event_time = event_time.replace(tzinfo=None)
                
                if event_time >= cutoff:
                    recent_events.append(event)
        
        if not recent_events:
            return f"No events in the last {last_minutes} minutes for namespace {namespace}"
        
        # Sort by time
        recent_events.sort(key=lambda e: e.last_timestamp or datetime.min, reverse=True)
        
        summary = [f"Recent Events (last {last_minutes}min) for {namespace}:"]
        
        for event in recent_events[:20]:  # Limit to 20
            time_str = event.last_timestamp.strftime("%H:%M:%S") if event.last_timestamp else "Unknown"
            obj_ref = f"{event.involved_object.kind}/{event.involved_object.name}" if event.involved_object else "Unknown"
            summary.append(f"  [{time_str}] {event.type} - {event.reason}: {event.message} ({obj_ref})")
        
        return "\n".join(summary)
        
    except ApiException as e:
        return f"Error getting namespace events: {e.reason}"
    except Exception as e:
        logger.error(f"Error in get_namespace_events: {e}", exc_info=True)
        return f"Error: {str(e)}"


# ============================================================================
# Phase 1 Tools: Core Troubleshooting Gaps
# ============================================================================

@tool
def k8s_get_init_containers(pod: str, namespace: str) -> str:
    """
    Get init container status and logs for a pod. Init container failures are
    a very common cause of pod startup issues.
    
    Args:
        pod: The name of the pod.
        namespace: The namespace of the pod.
    """
    core_v1 = _get_core_v1()
    
    try:
        pod_obj = core_v1.read_namespaced_pod(pod, namespace)
        
        # Check if pod has init containers
        init_containers = pod_obj.spec.init_containers or []
        if not init_containers:
            return f"Pod {pod} has no init containers."
        
        init_statuses = pod_obj.status.init_container_statuses or []
        
        output = [f"Init Containers for {pod}:"]
        
        for i, container in enumerate(init_containers):
            name = container.name
            image = container.image
            
            # Find matching status
            status = next((s for s in init_statuses if s.name == name), None)
            
            output.append(f"\n[{i+1}] {name}")
            output.append(f"    Image: {image}")
            
            if status:
                # Determine state
                if status.state.running:
                    output.append(f"    State: Running (started: {status.state.running.started_at})")
                elif status.state.terminated:
                    term = status.state.terminated
                    output.append(f"    State: Terminated")
                    output.append(f"    Exit Code: {term.exit_code}")
                    output.append(f"    Reason: {term.reason or 'N/A'}")
                    if term.message:
                        output.append(f"    Message: {term.message[:200]}")
                elif status.state.waiting:
                    wait = status.state.waiting
                    output.append(f"    State: Waiting")
                    output.append(f"    Reason: {wait.reason or 'N/A'}")
                    if wait.message:
                        output.append(f"    Message: {wait.message[:200]}")
                
                # Check last termination if available (useful for restarts)
                if status.last_state and status.last_state.terminated:
                    last = status.last_state.terminated
                    output.append(f"    Last Termination: Exit {last.exit_code}, Reason: {last.reason}")
                
                output.append(f"    Restart Count: {status.restart_count}")
                
                # Fetch logs if container has run (terminated or running)
                if status.state.terminated or status.state.running:
                    try:
                        logs = core_v1.read_namespaced_pod_log(
                            pod,
                            namespace,
                            container=name,
                            tail_lines=20,
                            previous=False
                        )
                        if logs.strip():
                            output.append(f"    Logs (last 20 lines):")
                            for line in logs.strip().split('\n')[-20:]:
                                output.append(f"      | {line[:150]}")
                    except ApiException as e:
                        output.append(f"    Logs: Unable to fetch ({e.reason})")
                
                # If terminated with error, try to get previous logs
                if status.state.terminated and status.state.terminated.exit_code != 0:
                    try:
                        prev_logs = core_v1.read_namespaced_pod_log(
                            pod,
                            namespace,
                            container=name,
                            tail_lines=20,
                            previous=True
                        )
                        if prev_logs.strip():
                            output.append(f"    Previous Logs:")
                            for line in prev_logs.strip().split('\n')[-20:]:
                                output.append(f"      | {line[:150]}")
                    except ApiException:
                        pass  # No previous logs available
            else:
                output.append(f"    State: Not started yet")
        
        return "\n".join(output)
        
    except ApiException as e:
        if e.status == 404:
            return f"Pod '{pod}' not found in namespace '{namespace}'."
        return f"Error getting init containers: {e.reason}"
    except Exception as e:
        logger.error(f"Error in k8s_get_init_containers: {e}", exc_info=True)
        return f"Error: {str(e)}"


@tool
def k8s_get_replicasets(deployment: str, namespace: str) -> str:
    """
    List ReplicaSets for a deployment with their revision history.
    Useful for understanding rollout issues, stuck deployments, and revision conflicts.
    
    Args:
        deployment: The name of the deployment.
        namespace: The namespace of the deployment.
    """
    apps_v1 = _get_apps_v1()
    
    try:
        # First get the deployment to find its selector
        dep = apps_v1.read_namespaced_deployment(deployment, namespace)
        
        # Build label selector from deployment's selector
        selector = dep.spec.selector.match_labels or {}
        label_selector = ",".join([f"{k}={v}" for k, v in selector.items()])
        
        # List ReplicaSets matching the selector
        rs_list = apps_v1.list_namespaced_replica_set(namespace, label_selector=label_selector)
        
        if not rs_list.items:
            return f"No ReplicaSets found for deployment {deployment}"
        
        # Sort by revision (annotation: deployment.kubernetes.io/revision)
        def get_revision(rs):
            annotations = rs.metadata.annotations or {}
            rev = annotations.get("deployment.kubernetes.io/revision", "0")
            try:
                return int(rev)
            except ValueError:
                return 0
        
        replica_sets = sorted(rs_list.items, key=get_revision, reverse=True)
        
        output = [f"ReplicaSets for {deployment} (newest first):"]
        output.append(f"Current Deployment Generation: {dep.metadata.generation}")
        output.append(f"Observed Generation: {dep.status.observed_generation}")
        output.append("")
        
        for rs in replica_sets[:5]:  # Show last 5 revisions
            name = rs.metadata.name
            revision = get_revision(rs)
            
            # Replicas info
            desired = rs.spec.replicas or 0
            ready = rs.status.ready_replicas or 0
            available = rs.status.available_replicas or 0
            
            # Get image from first container
            containers = rs.spec.template.spec.containers or []
            image = containers[0].image if containers else "N/A"
            # Shorten image to just tag if possible
            if ":" in image:
                image_short = image.split("/")[-1]  # Get image:tag part
            else:
                image_short = image
            
            # Age
            created = rs.metadata.creation_timestamp
            age = datetime.utcnow() - created.replace(tzinfo=None) if created else None
            age_str = f"{age.days}d" if age and age.days > 0 else f"{int(age.total_seconds() // 3600)}h" if age else "?"
            
            # Status indicator
            if desired == 0:
                status = "scaled-down"
            elif ready == desired:
                status = "ready"
            elif ready > 0:
                status = "partial"
            else:
                status = "not-ready"
            
            output.append(f"[Rev {revision}] {name}")
            output.append(f"    Replicas: {ready}/{desired} ready, {available} available ({status})")
            output.append(f"    Image: {image_short}")
            output.append(f"    Age: {age_str}")
            
            # Show conditions if any issues
            if rs.status.conditions:
                for cond in rs.status.conditions:
                    if cond.status != "True" or cond.type == "ReplicaFailure":
                        output.append(f"    Condition: {cond.type}={cond.status} - {cond.message}")
            
            output.append("")
        
        return "\n".join(output)
        
    except ApiException as e:
        if e.status == 404:
            return f"Deployment '{deployment}' not found in namespace '{namespace}'."
        return f"Error getting ReplicaSets: {e.reason}"
    except Exception as e:
        logger.error(f"Error in k8s_get_replicasets: {e}", exc_info=True)
        return f"Error: {str(e)}"


@tool
def k8s_get_deployment_history(deployment: str, namespace: str, revisions: int = 3) -> str:
    """
    Get deployment rollout history with configuration differences between revisions.
    Critical for understanding what changed and caused a failure.
    
    Args:
        deployment: The name of the deployment.
        namespace: The namespace of the deployment.
        revisions: Number of revisions to compare (default 3).
    """
    apps_v1 = _get_apps_v1()
    
    try:
        # Get the deployment
        dep = apps_v1.read_namespaced_deployment(deployment, namespace)
        
        # Build label selector
        selector = dep.spec.selector.match_labels or {}
        label_selector = ",".join([f"{k}={v}" for k, v in selector.items()])
        
        # List ReplicaSets
        rs_list = apps_v1.list_namespaced_replica_set(namespace, label_selector=label_selector)
        
        if not rs_list.items:
            return f"No revision history found for deployment {deployment}"
        
        # Sort by revision
        def get_revision(rs):
            annotations = rs.metadata.annotations or {}
            rev = annotations.get("deployment.kubernetes.io/revision", "0")
            try:
                return int(rev)
            except ValueError:
                return 0
        
        replica_sets = sorted(rs_list.items, key=get_revision, reverse=True)[:revisions]
        
        output = [f"Deployment History for {deployment}:"]
        output.append(f"Strategy: {dep.spec.strategy.type}")
        if dep.spec.strategy.rolling_update:
            ru = dep.spec.strategy.rolling_update
            output.append(f"  Max Unavailable: {ru.max_unavailable}, Max Surge: {ru.max_surge}")
        output.append("")
        
        # Extract config from each revision for comparison
        configs = []
        for rs in replica_sets:
            revision = get_revision(rs)
            containers = rs.spec.template.spec.containers or []
            init_containers = rs.spec.template.spec.init_containers or []
            
            config = {
                "revision": revision,
                "name": rs.metadata.name,
                "replicas": rs.spec.replicas,
                "containers": {},
                "init_containers": {},
            }
            
            for c in containers:
                config["containers"][c.name] = {
                    "image": c.image,
                    "resources": {
                        "requests": c.resources.requests if c.resources and c.resources.requests else {},
                        "limits": c.resources.limits if c.resources and c.resources.limits else {},
                    },
                    "env_count": len(c.env or []),
                    "env_from_count": len(c.env_from or []),
                }
            
            for c in init_containers:
                config["init_containers"][c.name] = {
                    "image": c.image,
                }
            
            configs.append(config)
        
        # Output each revision
        for i, config in enumerate(configs):
            is_current = i == 0
            marker = " (CURRENT)" if is_current else ""
            output.append(f"═══ Revision {config['revision']}{marker} ═══")
            output.append(f"ReplicaSet: {config['name']}")
            output.append(f"Replicas: {config['replicas']}")
            
            output.append("Containers:")
            for name, c in config["containers"].items():
                output.append(f"  • {name}")
                output.append(f"    Image: {c['image']}")
                if c["resources"]["requests"]:
                    output.append(f"    Requests: {c['resources']['requests']}")
                if c["resources"]["limits"]:
                    output.append(f"    Limits: {c['resources']['limits']}")
                output.append(f"    Env vars: {c['env_count']}, EnvFrom: {c['env_from_count']}")
            
            if config["init_containers"]:
                output.append("Init Containers:")
                for name, c in config["init_containers"].items():
                    output.append(f"  • {name}: {c['image']}")
            
            output.append("")
        
        # Show diffs between current and previous
        if len(configs) >= 2:
            output.append("═══ Changes (Current vs Previous) ═══")
            current = configs[0]
            previous = configs[1]
            
            changes_found = False
            
            # Compare containers
            for name in set(list(current["containers"].keys()) + list(previous["containers"].keys())):
                curr_c = current["containers"].get(name)
                prev_c = previous["containers"].get(name)
                
                if curr_c and not prev_c:
                    output.append(f"  + Container ADDED: {name}")
                    changes_found = True
                elif prev_c and not curr_c:
                    output.append(f"  - Container REMOVED: {name}")
                    changes_found = True
                elif curr_c and prev_c:
                    if curr_c["image"] != prev_c["image"]:
                        output.append(f"  ~ {name} image changed:")
                        output.append(f"      - {prev_c['image']}")
                        output.append(f"      + {curr_c['image']}")
                        changes_found = True
                    if curr_c["resources"] != prev_c["resources"]:
                        output.append(f"  ~ {name} resources changed:")
                        output.append(f"      - {prev_c['resources']}")
                        output.append(f"      + {curr_c['resources']}")
                        changes_found = True
                    if curr_c["env_count"] != prev_c["env_count"]:
                        output.append(f"  ~ {name} env var count: {prev_c['env_count']} -> {curr_c['env_count']}")
                        changes_found = True
            
            if not changes_found:
                output.append("  No significant configuration changes detected.")
                output.append("  (Rollout may have been triggered by configmap/secret change or manual restart)")
        
        return "\n".join(output)
        
    except ApiException as e:
        if e.status == 404:
            return f"Deployment '{deployment}' not found in namespace '{namespace}'."
        return f"Error getting deployment history: {e.reason}"
    except Exception as e:
        logger.error(f"Error in k8s_get_deployment_history: {e}", exc_info=True)
        return f"Error: {str(e)}"


# =============================================================================
# Phase 2 Tools - Scaling & Resource Issues
# =============================================================================

@tool
def k8s_get_hpa(namespace: str, name: Optional[str] = None) -> str:
    """Get HorizontalPodAutoscaler status and metrics.
    
    Useful for diagnosing:
    - Pods not scaling up under load
    - Thrashing between min/max replicas
    - Metrics not being collected (Unknown status)
    - ScalingLimited conditions
    
    Args:
        namespace: Kubernetes namespace
        name: Optional HPA name (if omitted, lists all HPAs in namespace)
    """
    try:
        autoscaling_v2 = client.AutoscalingV2Api()
        
        if name:
            hpas = [autoscaling_v2.read_namespaced_horizontal_pod_autoscaler(name, namespace)]
        else:
            hpas = autoscaling_v2.list_namespaced_horizontal_pod_autoscaler(namespace).items
        
        if not hpas:
            return f"No HorizontalPodAutoscalers found in namespace '{namespace}'."
        
        output = []
        
        for hpa in hpas:
            output.append(f"═══ HPA: {hpa.metadata.name} ═══")
            
            # Target reference
            target = hpa.spec.scale_target_ref
            output.append(f"Target: {target.kind}/{target.name}")
            
            # Replica counts
            spec = hpa.spec
            status = hpa.status
            output.append(f"Replicas: {status.current_replicas or 0} (min: {spec.min_replicas}, max: {spec.max_replicas})")
            output.append(f"Desired: {status.desired_replicas or 0}")
            
            # Metrics status
            output.append("Metrics:")
            if status.current_metrics:
                for metric in status.current_metrics:
                    if metric.type == "Resource":
                        res = metric.resource
                        current = res.current.average_utilization if res.current.average_utilization else "N/A"
                        output.append(f"  • {res.name}: {current}% utilization")
                    elif metric.type == "Pods":
                        pods = metric.pods
                        output.append(f"  • {pods.metric.name}: {pods.current.average_value}")
                    elif metric.type == "External":
                        ext = metric.external
                        output.append(f"  • {ext.metric.name} (external): {ext.current.value or ext.current.average_value}")
            else:
                output.append("  No current metrics available")
            
            # Target metrics from spec
            output.append("Targets:")
            if spec.metrics:
                for metric in spec.metrics:
                    if metric.type == "Resource":
                        res = metric.resource
                        target_val = res.target.average_utilization if res.target.average_utilization else res.target.average_value
                        output.append(f"  • {res.name}: target {target_val}%")
                    elif metric.type == "Pods":
                        pods = metric.pods
                        output.append(f"  • {pods.metric.name}: target {pods.target.average_value}")
            
            # Conditions
            output.append("Conditions:")
            if status.conditions:
                for cond in status.conditions:
                    status_icon = "✓" if cond.status == "True" else "✗"
                    output.append(f"  {status_icon} {cond.type}: {cond.status}")
                    if cond.reason:
                        output.append(f"    Reason: {cond.reason}")
                    if cond.message and cond.status != "True":
                        output.append(f"    Message: {cond.message}")
            
            # Last scale time
            if status.last_scale_time:
                output.append(f"Last Scale Time: {status.last_scale_time}")
            
            output.append("")
        
        return "\n".join(output)
        
    except ApiException as e:
        if e.status == 404:
            return f"HPA '{name}' not found in namespace '{namespace}'."
        return f"Error getting HPA: {e.reason}"
    except Exception as e:
        logger.error(f"Error in k8s_get_hpa: {e}", exc_info=True)
        return f"Error: {str(e)}"


@tool
def k8s_get_pod_disruption_budget(namespace: str, name: Optional[str] = None) -> str:
    """Get PodDisruptionBudget status and allowed disruptions.
    
    PDBs can block rollouts and node drains. Use this to diagnose:
    - Rollout stuck because PDB prevents pod eviction
    - Node drain failing during cluster upgrade
    - Too restrictive minAvailable/maxUnavailable settings
    
    Args:
        namespace: Kubernetes namespace
        name: Optional PDB name (if omitted, lists all PDBs in namespace)
    """
    try:
        policy_v1 = client.PolicyV1Api()
        
        if name:
            pdbs = [policy_v1.read_namespaced_pod_disruption_budget(name, namespace)]
        else:
            pdbs = policy_v1.list_namespaced_pod_disruption_budget(namespace).items
        
        if not pdbs:
            return f"No PodDisruptionBudgets found in namespace '{namespace}'."
        
        output = []
        
        for pdb in pdbs:
            output.append(f"═══ PDB: {pdb.metadata.name} ═══")
            
            spec = pdb.spec
            status = pdb.status
            
            # Selector
            if spec.selector and spec.selector.match_labels:
                labels = ", ".join([f"{k}={v}" for k, v in spec.selector.match_labels.items()])
                output.append(f"Selector: {labels}")
            
            # Policy settings
            if spec.min_available is not None:
                output.append(f"Min Available: {spec.min_available}")
            if spec.max_unavailable is not None:
                output.append(f"Max Unavailable: {spec.max_unavailable}")
            
            # Current status
            output.append(f"Current Healthy: {status.current_healthy}")
            output.append(f"Desired Healthy: {status.desired_healthy}")
            output.append(f"Expected Pods: {status.expected_pods}")
            output.append(f"Disruptions Allowed: {status.disruptions_allowed}")
            
            # Warning if no disruptions allowed
            if status.disruptions_allowed == 0:
                output.append("⚠️  WARNING: No disruptions allowed - this may block rollouts/drains!")
                if status.current_healthy < status.desired_healthy:
                    output.append(f"   Currently unhealthy pods: {status.desired_healthy - status.current_healthy}")
            
            # Conditions (if any)
            if status.conditions:
                output.append("Conditions:")
                for cond in status.conditions:
                    status_icon = "✓" if cond.status == "True" else "✗"
                    output.append(f"  {status_icon} {cond.type}: {cond.reason}")
            
            output.append("")
        
        return "\n".join(output)
        
    except ApiException as e:
        if e.status == 404:
            return f"PDB '{name}' not found in namespace '{namespace}'."
        return f"Error getting PDB: {e.reason}"
    except Exception as e:
        logger.error(f"Error in k8s_get_pod_disruption_budget: {e}", exc_info=True)
        return f"Error: {str(e)}"


@tool
def k8s_get_limit_ranges(namespace: str) -> str:
    """Get LimitRange constraints for a namespace.
    
    LimitRanges can silently modify or reject pod specs:
    - Pod rejected because request exceeds LimitRange max
    - Default limits applied causing OOM
    - Container request/limit ratios enforced
    
    Args:
        namespace: Kubernetes namespace
    """
    try:
        core_v1 = _get_core_v1()
        
        limit_ranges = core_v1.list_namespaced_limit_range(namespace).items
        
        if not limit_ranges:
            return f"No LimitRanges found in namespace '{namespace}'. Resources are unconstrained."
        
        output = []
        
        for lr in limit_ranges:
            output.append(f"═══ LimitRange: {lr.metadata.name} ═══")
            
            for limit in lr.spec.limits:
                output.append(f"\nType: {limit.type}")
                
                if limit.default:
                    output.append(f"  Default Limits:")
                    for resource, value in limit.default.items():
                        output.append(f"    {resource}: {value}")
                
                if limit.default_request:
                    output.append(f"  Default Requests:")
                    for resource, value in limit.default_request.items():
                        output.append(f"    {resource}: {value}")
                
                if limit.min:
                    output.append(f"  Minimum:")
                    for resource, value in limit.min.items():
                        output.append(f"    {resource}: {value}")
                
                if limit.max:
                    output.append(f"  Maximum:")
                    for resource, value in limit.max.items():
                        output.append(f"    {resource}: {value}")
                
                if limit.max_limit_request_ratio:
                    output.append(f"  Max Limit/Request Ratio:")
                    for resource, value in limit.max_limit_request_ratio.items():
                        output.append(f"    {resource}: {value}")
            
            output.append("")
        
        return "\n".join(output)
        
    except ApiException as e:
        return f"Error getting LimitRanges: {e.reason}"
    except Exception as e:
        logger.error(f"Error in k8s_get_limit_ranges: {e}", exc_info=True)
        return f"Error: {str(e)}"


# =============================================================================
# Phase 3 Tools - Advanced Diagnostics
# =============================================================================

@tool
def k8s_get_jobs(namespace: str, include_completed: bool = False, name_pattern: Optional[str] = None) -> str:
    """List Jobs and their completion status.
    
    Jobs are often part of deployment workflows:
    - Database migration jobs failing
    - Pre-deploy/post-deploy hooks
    - Helm hooks
    - CronJob children
    
    Args:
        namespace: Kubernetes namespace
        include_completed: Whether to include completed/successful jobs (default: False)
        name_pattern: Optional job name pattern to filter by
    """
    try:
        batch_v1 = client.BatchV1Api()
        
        jobs = batch_v1.list_namespaced_job(namespace).items
        
        if name_pattern:
            import re
            pattern = re.compile(name_pattern, re.IGNORECASE)
            jobs = [j for j in jobs if pattern.search(j.metadata.name)]
        
        if not include_completed:
            # Filter to only show active, failed, or recently completed jobs
            def is_relevant(job):
                status = job.status
                # Active jobs
                if status.active and status.active > 0:
                    return True
                # Failed jobs
                if status.failed and status.failed > 0:
                    return True
                # Succeeded but within last hour
                if status.succeeded and status.completion_time:
                    from datetime import datetime, timezone, timedelta
                    completion = status.completion_time
                    if completion.tzinfo is None:
                        completion = completion.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) - completion < timedelta(hours=1):
                        return True
                return False
            
            jobs = [j for j in jobs if is_relevant(j)]
        
        if not jobs:
            msg = f"No jobs found in namespace '{namespace}'"
            if not include_completed:
                msg += " (excluding completed jobs)"
            return msg + "."
        
        output = []
        core_v1 = _get_core_v1()
        
        for job in jobs:
            status = job.status
            spec = job.spec
            
            # Determine job status
            if status.active and status.active > 0:
                job_status = "🔄 Running"
            elif status.failed and status.failed > 0:
                job_status = "❌ Failed"
            elif status.succeeded and status.succeeded > 0:
                job_status = "✅ Completed"
            else:
                job_status = "⏳ Pending"
            
            output.append(f"═══ Job: {job.metadata.name} ═══")
            output.append(f"Status: {job_status}")
            output.append(f"Completions: {status.succeeded or 0}/{spec.completions or 1}")
            output.append(f"Parallelism: {spec.parallelism or 1}")
            
            if status.active:
                output.append(f"Active: {status.active}")
            if status.failed:
                output.append(f"Failed: {status.failed}")
            
            # Timing
            if status.start_time:
                output.append(f"Started: {status.start_time}")
            if status.completion_time:
                output.append(f"Completed: {status.completion_time}")
            
            # Backoff limit
            if spec.backoff_limit:
                output.append(f"Backoff Limit: {spec.backoff_limit}")
            
            # Conditions
            if status.conditions:
                for cond in status.conditions:
                    if cond.type == "Failed" and cond.status == "True":
                        output.append(f"Failure Reason: {cond.reason}")
                        output.append(f"Failure Message: {cond.message}")
            
            # For failed jobs, get pod logs
            if status.failed and status.failed > 0:
                try:
                    # Find pods for this job
                    label_selector = f"job-name={job.metadata.name}"
                    pods = core_v1.list_namespaced_pod(namespace, label_selector=label_selector).items
                    
                    for pod in pods[-1:]:  # Just last pod
                        if pod.status.phase == "Failed":
                            output.append(f"\nLast Pod ({pod.metadata.name}) Logs:")
                            try:
                                logs = core_v1.read_namespaced_pod_log(
                                    pod.metadata.name, namespace, tail_lines=20
                                )
                                for line in logs.strip().split('\n')[-10:]:
                                    output.append(f"  {line}")
                            except:
                                output.append("  (logs unavailable)")
                except:
                    pass
            
            output.append("")
        
        return "\n".join(output)
        
    except ApiException as e:
        return f"Error getting jobs: {e.reason}"
    except Exception as e:
        logger.error(f"Error in k8s_get_jobs: {e}", exc_info=True)
        return f"Error: {str(e)}"


@tool
def k8s_get_priority_classes() -> str:
    """List PriorityClasses in the cluster.
    
    Priority affects scheduling order and preemption:
    - Low-priority pods being preempted unexpectedly
    - High-priority pods not getting scheduled
    - Understanding pod scheduling order
    """
    try:
        scheduling_v1 = client.SchedulingV1Api()
        
        pcs = scheduling_v1.list_priority_class().items
        
        if not pcs:
            return "No PriorityClasses found in the cluster."
        
        # Sort by value descending
        pcs.sort(key=lambda x: x.value, reverse=True)
        
        output = ["PriorityClasses (sorted by value, highest first):", ""]
        
        for pc in pcs:
            default_marker = " (DEFAULT)" if pc.global_default else ""
            preemption = pc.preemption_policy or "PreemptLowerPriority"
            
            output.append(f"═══ {pc.metadata.name}{default_marker} ═══")
            output.append(f"Value: {pc.value}")
            output.append(f"Preemption Policy: {preemption}")
            if pc.description:
                output.append(f"Description: {pc.description}")
            output.append("")
        
        return "\n".join(output)
        
    except ApiException as e:
        return f"Error getting PriorityClasses: {e.reason}"
    except Exception as e:
        logger.error(f"Error in k8s_get_priority_classes: {e}", exc_info=True)
        return f"Error: {str(e)}"


@tool
def k8s_get_cronjobs(namespace: str, name_pattern: Optional[str] = None) -> str:
    """List CronJobs and their status including recent job history.
    
    Useful for diagnosing:
    - CronJobs not triggering on schedule
    - Failed job runs
    - Suspended CronJobs
    - Concurrency policy issues
    
    Args:
        namespace: Kubernetes namespace
        name_pattern: Optional name pattern to filter by
    """
    try:
        batch_v1 = client.BatchV1Api()
        
        cronjobs = batch_v1.list_namespaced_cron_job(namespace).items
        
        if name_pattern:
            import re
            pattern = re.compile(name_pattern, re.IGNORECASE)
            cronjobs = [cj for cj in cronjobs if pattern.search(cj.metadata.name)]
        
        if not cronjobs:
            return f"No CronJobs found in namespace '{namespace}'."
        
        output = []
        
        for cj in cronjobs:
            spec = cj.spec
            status = cj.status
            
            suspended_marker = " ⏸️ SUSPENDED" if spec.suspend else ""
            output.append(f"═══ CronJob: {cj.metadata.name}{suspended_marker} ═══")
            output.append(f"Schedule: {spec.schedule}")
            output.append(f"Concurrency Policy: {spec.concurrency_policy or 'Allow'}")
            
            if spec.starting_deadline_seconds:
                output.append(f"Starting Deadline: {spec.starting_deadline_seconds}s")
            
            if spec.successful_jobs_history_limit:
                output.append(f"Successful History Limit: {spec.successful_jobs_history_limit}")
            if spec.failed_jobs_history_limit:
                output.append(f"Failed History Limit: {spec.failed_jobs_history_limit}")
            
            # Last schedule
            if status.last_schedule_time:
                output.append(f"Last Scheduled: {status.last_schedule_time}")
            if status.last_successful_time:
                output.append(f"Last Successful: {status.last_successful_time}")
            
            # Active jobs
            if status.active:
                output.append(f"Active Jobs: {len(status.active)}")
                for job_ref in status.active:
                    output.append(f"  • {job_ref.name}")
            
            output.append("")
        
        return "\n".join(output)
        
    except ApiException as e:
        return f"Error getting CronJobs: {e.reason}"
    except Exception as e:
        logger.error(f"Error in k8s_get_cronjobs: {e}", exc_info=True)
        return f"Error: {str(e)}"


@tool
def k8s_check_image_pull_status(namespace: str, pod: Optional[str] = None) -> str:
    """Check image pull status for pods, useful for debugging ImagePullBackOff.
    
    Diagnoses:
    - ImagePullBackOff errors
    - Missing or incorrect pull secrets
    - Private registry authentication issues
    - Image tag typos
    
    Args:
        namespace: Kubernetes namespace
        pod: Optional specific pod name (if omitted, checks all pods with image pull issues)
    """
    try:
        core_v1 = _get_core_v1()
        
        if pod:
            pods = [core_v1.read_namespaced_pod(pod, namespace)]
        else:
            pods = core_v1.list_namespaced_pod(namespace).items
        
        output = []
        issues_found = False
        
        for p in pods:
            pod_name = p.metadata.name
            
            # Check for image pull issues in container statuses
            all_statuses = (p.status.container_statuses or []) + (p.status.init_container_statuses or [])
            
            for cs in all_statuses:
                waiting = cs.state.waiting if cs.state else None
                if waiting and waiting.reason in ["ImagePullBackOff", "ErrImagePull", "ErrImageNeverPull"]:
                    issues_found = True
                    output.append(f"═══ Pod: {pod_name}, Container: {cs.name} ═══")
                    output.append(f"Image: {cs.image}")
                    output.append(f"Status: {waiting.reason}")
                    output.append(f"Message: {waiting.message}")
                    
                    # Check if pull secrets are configured
                    pull_secrets = p.spec.image_pull_secrets or []
                    if pull_secrets:
                        output.append(f"Pull Secrets: {', '.join([s.name for s in pull_secrets])}")
                    else:
                        output.append("Pull Secrets: NONE CONFIGURED")
                        
                        # Check if image is from a private registry
                        image = cs.image
                        if not image.startswith(("docker.io/", "gcr.io/", "ghcr.io/")) and "/" in image:
                            output.append("⚠️  Image appears to be from a private registry but no pull secret is configured!")
                    
                    # Get events for this pod related to image pulling
                    try:
                        events = core_v1.list_namespaced_event(
                            namespace,
                            field_selector=f"involvedObject.name={pod_name},reason=Failed"
                        ).items
                        
                        image_events = [e for e in events if "image" in (e.message or "").lower() or "pull" in (e.message or "").lower()]
                        if image_events:
                            output.append("Related Events:")
                            for e in image_events[-3:]:
                                output.append(f"  • {e.message}")
                    except:
                        pass
                    
                    output.append("")
        
        if not issues_found:
            if pod:
                return f"No image pull issues found for pod '{pod}'."
            return f"No image pull issues found in namespace '{namespace}'."
        
        return "\n".join(output)
        
    except ApiException as e:
        if e.status == 404:
            return f"Pod '{pod}' not found in namespace '{namespace}'."
        return f"Error checking image pull status: {e.reason}"
    except Exception as e:
        logger.error(f"Error in k8s_check_image_pull_status: {e}", exc_info=True)
        return f"Error: {str(e)}"


@tool
def k8s_get_service_mesh_status(namespace: str, pod: Optional[str] = None) -> str:
    """Check Istio/service mesh sidecar status for pods.
    
    Useful for diagnosing:
    - Sidecar injection failures
    - Istio-proxy not ready
    - mTLS configuration issues
    - Traffic routing problems
    
    Args:
        namespace: Kubernetes namespace
        pod: Optional specific pod name
    """
    try:
        core_v1 = _get_core_v1()
        
        # Check namespace labels for injection
        ns = core_v1.read_namespace(namespace)
        ns_labels = ns.metadata.labels or {}
        
        output = [f"═══ Service Mesh Status for {namespace} ═══", ""]
        
        # Check Istio injection label
        istio_injection = ns_labels.get("istio-injection", "not set")
        istio_rev = ns_labels.get("istio.io/rev", "not set")
        
        output.append("Namespace Configuration:")
        output.append(f"  istio-injection: {istio_injection}")
        output.append(f"  istio.io/rev: {istio_rev}")
        
        if istio_injection != "enabled" and istio_rev == "not set":
            output.append("  ⚠️  Istio sidecar injection is NOT enabled for this namespace")
        
        output.append("")
        
        # Check pods
        if pod:
            pods = [core_v1.read_namespaced_pod(pod, namespace)]
        else:
            pods = core_v1.list_namespaced_pod(namespace).items
        
        pods_with_sidecar = 0
        pods_without_sidecar = 0
        sidecar_issues = []
        
        for p in pods:
            containers = [c.name for c in p.spec.containers]
            has_istio = "istio-proxy" in containers
            
            if has_istio:
                pods_with_sidecar += 1
                
                # Check istio-proxy status
                for cs in (p.status.container_statuses or []):
                    if cs.name == "istio-proxy":
                        if not cs.ready:
                            sidecar_issues.append({
                                "pod": p.metadata.name,
                                "ready": cs.ready,
                                "state": cs.state,
                                "restart_count": cs.restart_count
                            })
            else:
                pods_without_sidecar += 1
                
                # Check if injection was explicitly disabled
                annotations = p.metadata.annotations or {}
                if annotations.get("sidecar.istio.io/inject") == "false":
                    pass  # Intentionally disabled
                elif istio_injection == "enabled" or istio_rev != "not set":
                    # Should have sidecar but doesn't
                    sidecar_issues.append({
                        "pod": p.metadata.name,
                        "issue": "Missing sidecar despite namespace injection being enabled"
                    })
        
        output.append("Pod Summary:")
        output.append(f"  Pods with istio-proxy sidecar: {pods_with_sidecar}")
        output.append(f"  Pods without sidecar: {pods_without_sidecar}")
        
        if sidecar_issues:
            output.append("")
            output.append("Issues Detected:")
            for issue in sidecar_issues:
                output.append(f"  • Pod: {issue['pod']}")
                if "issue" in issue:
                    output.append(f"    {issue['issue']}")
                else:
                    output.append(f"    Ready: {issue['ready']}, Restarts: {issue['restart_count']}")
                    if issue['state']:
                        if issue['state'].waiting:
                            output.append(f"    Waiting: {issue['state'].waiting.reason}")
                        elif issue['state'].terminated:
                            output.append(f"    Terminated: {issue['state'].terminated.reason}")
        
        return "\n".join(output)
        
    except ApiException as e:
        if e.status == 404:
            return f"Namespace '{namespace}' or pod '{pod}' not found."
        return f"Error checking service mesh status: {e.reason}"
    except Exception as e:
        logger.error(f"Error in k8s_get_service_mesh_status: {e}", exc_info=True)
        return f"Error: {str(e)}"


@tool  
def k8s_get_resource_quotas_usage(namespace: str) -> str:
    """Get detailed ResourceQuota usage showing what's consumed vs limits.
    
    Useful for diagnosing:
    - Pod creation failures due to quota exhaustion
    - Understanding resource constraints
    - Capacity planning
    
    Args:
        namespace: Kubernetes namespace
    """
    try:
        core_v1 = _get_core_v1()
        
        quotas = core_v1.list_namespaced_resource_quota(namespace).items
        
        if not quotas:
            return f"No ResourceQuotas found in namespace '{namespace}'."
        
        output = []
        
        for quota in quotas:
            output.append(f"═══ ResourceQuota: {quota.metadata.name} ═══")
            
            status = quota.status
            hard = status.hard or {}
            used = status.used or {}
            
            output.append(f"{'Resource':<30} {'Used':<15} {'Hard Limit':<15} {'%Used':<10}")
            output.append("-" * 70)
            
            for resource in sorted(hard.keys()):
                hard_val = hard.get(resource, "N/A")
                used_val = used.get(resource, "0")
                
                # Calculate percentage if possible
                try:
                    # Parse values (handle memory/cpu units)
                    def parse_quantity(val):
                        if isinstance(val, (int, float)):
                            return float(val)
                        val = str(val)
                        if val.endswith('m'):
                            return float(val[:-1]) / 1000
                        if val.endswith('Ki'):
                            return float(val[:-2]) * 1024
                        if val.endswith('Mi'):
                            return float(val[:-2]) * 1024 * 1024
                        if val.endswith('Gi'):
                            return float(val[:-2]) * 1024 * 1024 * 1024
                        return float(val)
                    
                    hard_num = parse_quantity(hard_val)
                    used_num = parse_quantity(used_val)
                    pct = (used_num / hard_num * 100) if hard_num > 0 else 0
                    pct_str = f"{pct:.1f}%"
                    
                    # Warning if near limit
                    if pct >= 90:
                        pct_str += " ⚠️"
                    elif pct >= 75:
                        pct_str += " ⚡"
                except:
                    pct_str = "N/A"
                
                output.append(f"{resource:<30} {used_val:<15} {hard_val:<15} {pct_str:<10}")
            
            output.append("")
        
        return "\n".join(output)
        
    except ApiException as e:
        return f"Error getting ResourceQuotas: {e.reason}"
    except Exception as e:
        logger.error(f"Error in k8s_get_resource_quotas_usage: {e}", exc_info=True)
        return f"Error: {str(e)}"


@tool
def k8s_check_service_connectivity(service: str, namespace: str) -> str:
    """Check if a service is properly configured and has healthy endpoints.
    
    Diagnoses:
    - Service selector not matching any pods
    - All endpoints unhealthy
    - Port configuration mismatches
    - Service type issues
    
    Args:
        service: Service name
        namespace: Kubernetes namespace
    """
    try:
        core_v1 = _get_core_v1()
        
        output = [f"═══ Service Connectivity Check: {service} ═══", ""]
        
        # Get service
        try:
            svc = core_v1.read_namespaced_service(service, namespace)
        except ApiException as e:
            if e.status == 404:
                return f"Service '{service}' not found in namespace '{namespace}'."
            raise
        
        # Service info
        output.append("Service Configuration:")
        output.append(f"  Type: {svc.spec.type}")
        output.append(f"  Cluster IP: {svc.spec.cluster_ip}")
        
        if svc.spec.external_i_ps:
            output.append(f"  External IPs: {', '.join(svc.spec.external_i_ps)}")
        
        if svc.spec.type == "LoadBalancer":
            ingress = svc.status.load_balancer.ingress or []
            if ingress:
                lbs = [i.ip or i.hostname for i in ingress]
                output.append(f"  Load Balancer: {', '.join(lbs)}")
            else:
                output.append("  Load Balancer: PENDING")
        
        # Ports
        output.append("  Ports:")
        for port in svc.spec.ports:
            target = port.target_port
            output.append(f"    {port.port}/{port.protocol} -> {target}")
        
        # Selector
        selector = svc.spec.selector
        if selector:
            selector_str = ", ".join([f"{k}={v}" for k, v in selector.items()])
            output.append(f"  Selector: {selector_str}")
        else:
            output.append("  Selector: NONE (external service or headless)")
            output.append("  ⚠️  No selector means no automatic endpoint discovery")
        
        output.append("")
        
        # Check endpoints
        output.append("Endpoint Status:")
        try:
            eps = core_v1.read_namespaced_endpoints(service, namespace)
            subsets = eps.subsets or []
            
            total_ready = 0
            total_not_ready = 0
            
            for subset in subsets:
                addresses = subset.addresses or []
                not_ready = subset.not_ready_addresses or []
                total_ready += len(addresses)
                total_not_ready += len(not_ready)
                
                if addresses:
                    output.append(f"  Ready endpoints ({len(addresses)}):")
                    for addr in addresses[:5]:  # Limit output
                        target_ref = addr.target_ref
                        if target_ref:
                            output.append(f"    • {addr.ip} ({target_ref.name})")
                        else:
                            output.append(f"    • {addr.ip}")
                    if len(addresses) > 5:
                        output.append(f"    ... and {len(addresses) - 5} more")
                
                if not_ready:
                    output.append(f"  NotReady endpoints ({len(not_ready)}):")
                    for addr in not_ready[:3]:
                        target_ref = addr.target_ref
                        if target_ref:
                            output.append(f"    • {addr.ip} ({target_ref.name})")
            
            if total_ready == 0 and total_not_ready == 0:
                output.append("  ⚠️  NO ENDPOINTS - Service has no backing pods!")
                
                # Try to find pods that might match
                if selector:
                    label_selector = ",".join([f"{k}={v}" for k, v in selector.items()])
                    pods = core_v1.list_namespaced_pod(namespace, label_selector=label_selector).items
                    if pods:
                        output.append(f"  Found {len(pods)} pods matching selector, but none are ready:")
                        for p in pods[:3]:
                            output.append(f"    • {p.metadata.name}: {p.status.phase}")
                    else:
                        output.append("  No pods found matching the selector!")
                        output.append(f"  Check if pods exist with labels: {selector_str}")
            elif total_not_ready > total_ready:
                output.append(f"  ⚠️  Most endpoints are not ready ({total_not_ready}/{total_ready + total_not_ready})")
            else:
                output.append(f"  ✓ {total_ready} healthy endpoint(s)")
                
        except ApiException:
            output.append("  Unable to retrieve endpoints")
        
        return "\n".join(output)
        
    except ApiException as e:
        return f"Error checking service connectivity: {e.reason}"
    except Exception as e:
        logger.error(f"Error in k8s_check_service_connectivity: {e}", exc_info=True)
        return f"Error: {str(e)}"

