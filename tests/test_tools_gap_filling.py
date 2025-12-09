from unittest.mock import MagicMock, patch
from project_fyr.tools import k8s_get_nodes, k8s_check_rbac, k8s_get_network_policies, k8s_get_endpoints

@patch("project_fyr.tools._get_core_v1")
def test_k8s_get_nodes(mock_get_core):
    mock_api = MagicMock()
    mock_get_core.return_value = mock_api
    
    mock_node = MagicMock()
    mock_node.metadata.name = "node-1"
    mock_node.status.conditions = [MagicMock(type="Ready", status="True")]
    mock_node.metadata.labels = {"node-role.kubernetes.io/worker": ""}
    mock_node.spec.taints = None
    mock_node.status.allocatable = {"cpu": "4", "memory": "16Gi"}
    
    mock_api.list_node.return_value.items = [mock_node]
    
    result = k8s_get_nodes.invoke({})
    assert "node-1 (worker): Ready" in result
    assert "CPU: 4" in result

@patch("project_fyr.tools._get_auth_v1")
def test_k8s_check_rbac(mock_get_auth):
    mock_api = MagicMock()
    mock_get_auth.return_value = mock_api
    
    mock_response = MagicMock()
    mock_response.status.allowed = True
    mock_response.status.reason = "RBAC allowed"
    mock_api.create_subject_access_review.return_value = mock_response
    
    result = k8s_check_rbac.invoke({
        "service_account": "default",
        "namespace": "default",
        "verb": "get",
        "resource": "pods"
    })
    
    assert "ALLOWED" in result
    assert "RBAC allowed" in result

@patch("project_fyr.tools._get_networking_v1")
def test_k8s_get_network_policies(mock_get_net):
    mock_api = MagicMock()
    mock_get_net.return_value = mock_api
    
    mock_np = MagicMock()
    mock_np.metadata.name = "deny-all"
    mock_np.spec.pod_selector.match_labels = {}
    mock_np.spec.policy_types = ["Ingress"]
    
    mock_api.list_namespaced_network_policy.return_value.items = [mock_np]
    
    result = k8s_get_network_policies.invoke({"namespace": "default"})
    assert "deny-all" in result
    assert "Ingress" in result

@patch("project_fyr.tools._get_core_v1")
def test_k8s_get_endpoints(mock_get_core):
    mock_api = MagicMock()
    mock_get_core.return_value = mock_api
    
    mock_eps = MagicMock()
    mock_subset = MagicMock()
    
    mock_addr = MagicMock()
    mock_addr.ip = "10.1.1.1"
    mock_subset.addresses = [mock_addr]
    
    mock_port = MagicMock()
    mock_port.port = 80
    mock_port.protocol = "TCP"
    mock_subset.ports = [mock_port]
    
    mock_eps.subsets = [mock_subset]
    mock_api.read_namespaced_endpoints.return_value = mock_eps
    
    result = k8s_get_endpoints.invoke({"service_name": "my-svc", "namespace": "default"})
    assert "Ready IPs (80/TCP): 10.1.1.1" in result
