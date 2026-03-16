from unittest.mock import MagicMock, patch
from project_fyr.tools import (
    _clean_metadata,
    k8s_get_resources,
    k8s_describe,
    k8s_logs,
    k8s_events,
)

@patch("project_fyr.tools._get_core_v1")
def test_k8s_get_resources_pod(mock_get_core):
    mock_api = MagicMock()
    mock_get_core.return_value = mock_api

    mock_pod = MagicMock()
    mock_pod.metadata.name = "test-pod"
    mock_pod.status.phase = "Running"
    mock_pod.status.container_statuses = []

    mock_api.list_namespaced_pod.return_value.items = [mock_pod]

    result = k8s_get_resources.invoke({"kind": "Pod", "namespace": "default"})
    assert "test-pod" in result
    assert "Running" in result

@patch("project_fyr.tools._get_apps_v1")
def test_k8s_get_resources_deployment(mock_get_apps):
    mock_api = MagicMock()
    mock_get_apps.return_value = mock_api

    mock_dep = MagicMock()
    mock_dep.metadata.name = "test-dep"
    mock_dep.status.ready_replicas = 1
    mock_dep.status.replicas = 1

    mock_api.list_namespaced_deployment.return_value.items = [mock_dep]

    result = k8s_get_resources.invoke({"kind": "Deployment", "namespace": "default"})
    assert "test-dep" in result
    assert "Ready: 1/1" in result

@patch("project_fyr.tools._get_core_v1")
def test_k8s_describe_pod(mock_get_core):
    mock_api = MagicMock()
    mock_get_core.return_value = mock_api

    mock_pod = MagicMock()
    mock_pod.to_dict.return_value = {
        "api_version": "v1",
        "kind": "Pod",
        "metadata": {"name": "test-pod", "namespace": "default"},
        "spec": {"containers": [{"name": "c1", "image": "repo/c1:1.0"}]},
        "status": {"phase": "Running"}
    }

    mock_api.read_namespaced_pod.return_value = mock_pod

    result = k8s_describe.invoke({"kind": "Pod", "name": "test-pod", "namespace": "default"})
    assert "name: test-pod" in result
    assert "kind: Pod" in result
    assert "phase: Running" in result
    assert "containers" in result
    assert "image: repo/c1:1.0" in result


def test_clean_metadata_removes_snake_case_noise():
    cleaned = _clean_metadata(
        {
            "metadata": {
                "name": "test-pod",
                "managed_fields": [{"manager": "x"}],
                "uid": "123",
                "resource_version": "42",
                "generation": 7,
                "creation_timestamp": "2026-01-01T00:00:00Z",
                "annotations": {
                    "kubectl.kubernetes.io/last-applied-configuration": "very-long",
                    "team": "platform",
                },
            }
        }
    )
    meta = cleaned["metadata"]
    assert "managed_fields" not in meta
    assert "uid" not in meta
    assert "resource_version" not in meta
    assert "generation" not in meta
    assert "creation_timestamp" not in meta
    assert "kubectl.kubernetes.io/last-applied-configuration" not in meta["annotations"]
    assert meta["annotations"]["team"] == "platform"


@patch("project_fyr.tools._get_core_v1")
def test_k8s_describe_pod_omits_noisy_metadata(mock_get_core):
    mock_api = MagicMock()
    mock_get_core.return_value = mock_api

    mock_pod = MagicMock()
    mock_pod.to_dict.return_value = {
        "api_version": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "test-pod",
            "namespace": "default",
            "managed_fields": [{"manager": "kcm"}],
            "uid": "abc",
            "resource_version": "123",
            "annotations": {
                "kubectl.kubernetes.io/last-applied-configuration": "huge-payload",
                "owner": "team-a",
            },
            "labels": {"app": "demo"},
        },
        "spec": {
            "node_name": "node-1",
            "containers": [
                {
                    "name": "api",
                    "image": "repo/api:1.2.3",
                    "env": [{"name": "ENV_A", "value": "x"}],
                }
            ],
        },
        "status": {
            "phase": "Running",
            "container_statuses": [
                {
                    "name": "api",
                    "ready": True,
                    "restart_count": 1,
                    "state": {"running": {"started_at": "2026-03-03T08:00:00Z"}},
                }
            ],
        },
    }
    mock_api.read_namespaced_pod.return_value = mock_pod

    result = k8s_describe.invoke({"kind": "Pod", "name": "test-pod", "namespace": "default"})
    assert "owner: team-a" in result
    assert "node_name: node-1" in result
    assert "restart_count: 1" in result
    assert "managed_fields" not in result
    assert "uid:" not in result
    assert "resource_version" not in result
    assert "kubectl.kubernetes.io/last-applied-configuration" not in result

@patch("project_fyr.tools._get_core_v1")
def test_k8s_logs(mock_get_core):
    mock_api = MagicMock()
    mock_get_core.return_value = mock_api

    mock_api.read_namespaced_pod_log.return_value = "Error: connection refused"

    result = k8s_logs.invoke({"name": "test-pod", "namespace": "default"})
    assert "Error: connection refused" in result

@patch("project_fyr.tools._get_core_v1")
def test_k8s_events(mock_get_core):
    mock_api = MagicMock()
    mock_get_core.return_value = mock_api

    mock_event = MagicMock()
    mock_event.last_timestamp = "2023-01-01T00:00:00Z"
    mock_event.type = "Warning"
    mock_event.reason = "Failed"
    mock_event.message = "Failed to pull image"
    mock_event.involved_object.kind = "Pod"
    mock_event.involved_object.name = "test-pod"

    mock_api.list_namespaced_event.return_value.items = [mock_event]

    result = k8s_events.invoke({"namespace": "default", "involved_object_name": "test-pod"})
    assert "Failed to pull image" in result
