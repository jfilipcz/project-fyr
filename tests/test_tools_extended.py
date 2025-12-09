from unittest.mock import MagicMock, patch
from project_fyr.tools import k8s_get_configmap, k8s_get_secret_structure, k8s_get_storage, k8s_get_network

@patch("project_fyr.tools._get_core_v1")
def test_k8s_get_configmap(mock_get_core):
    mock_api = MagicMock()
    mock_get_core.return_value = mock_api
    
    mock_cm = MagicMock()
    mock_cm.data = {"key": "value"}
    mock_api.read_namespaced_config_map.return_value = mock_cm
    
    result = k8s_get_configmap.invoke({"name": "my-cm", "namespace": "default"})
    assert "key: value" in result

@patch("project_fyr.tools._get_core_v1")
def test_k8s_get_secret_structure(mock_get_core):
    mock_api = MagicMock()
    mock_get_core.return_value = mock_api
    
    mock_secret = MagicMock()
    mock_secret.data = {"password": "redacted"}
    mock_api.read_namespaced_secret.return_value = mock_secret
    
    result = k8s_get_secret_structure.invoke({"name": "my-secret", "namespace": "default"})
    assert "contains keys: password" in result
    assert "redacted" not in result # Ensure value is not shown directly (though mock has it)

@patch("project_fyr.tools._get_core_v1")
def test_k8s_get_storage(mock_get_core):
    mock_api = MagicMock()
    mock_get_core.return_value = mock_api
    
    mock_pvc = MagicMock()
    mock_pvc.metadata.name = "my-pvc"
    mock_pvc.status.phase = "Bound"
    mock_pvc.status.capacity = {"storage": "10Gi"}
    mock_pvc.spec.volume_name = "pv-123"
    
    mock_api.list_namespaced_persistent_volume_claim.return_value.items = [mock_pvc]
    
    result = k8s_get_storage.invoke({"namespace": "default"})
    assert "my-pvc: Bound" in result
    assert "Capacity: 10Gi" in result

@patch("project_fyr.tools._get_core_v1")
@patch("project_fyr.tools._get_networking_v1")
def test_k8s_get_network(mock_get_net, mock_get_core):
    mock_core = MagicMock()
    mock_net = MagicMock()
    mock_get_core.return_value = mock_core
    mock_get_net.return_value = mock_net
    
    # Mock Service
    mock_svc = MagicMock()
    mock_svc.metadata.name = "my-svc"
    mock_svc.spec.type = "ClusterIP"
    mock_svc.spec.cluster_ip = "10.0.0.1"
    mock_port = MagicMock()
    mock_port.port = 80
    mock_port.protocol = "TCP"
    mock_svc.spec.ports = [mock_port]
    mock_core.list_namespaced_service.return_value.items = [mock_svc]
    
    # Mock Ingress
    mock_ing = MagicMock()
    mock_ing.metadata.name = "my-ing"
    mock_rule = MagicMock()
    mock_rule.host = "example.com"
    mock_path = MagicMock()
    mock_path.path = "/"
    mock_rule.http.paths = [mock_path]
    mock_ing.spec.rules = [mock_rule]
    mock_net.list_namespaced_ingress.return_value.items = [mock_ing]
    
    result = k8s_get_network.invoke({"namespace": "default"})
    assert "my-svc (ClusterIP): 10.0.0.1 [80/TCP]" in result
    assert "my-ing: example.com['/']" in result
