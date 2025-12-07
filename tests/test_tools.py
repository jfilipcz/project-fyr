from unittest.mock import MagicMock, patch
from project_fyr.tools import k8s_get_resources, k8s_describe, k8s_logs, k8s_events

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
        "metadata": {"name": "test-pod", "namespace": "default"},
        "spec": {"containers": [{"name": "c1"}]}
    }
    
    mock_api.read_namespaced_pod.return_value = mock_pod
    
    result = k8s_describe.invoke({"kind": "Pod", "name": "test-pod", "namespace": "default"})
    assert "name: test-pod" in result
    assert "containers" in result

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
