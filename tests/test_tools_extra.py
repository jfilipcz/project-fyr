from unittest.mock import MagicMock, patch
from project_fyr.tools import k8s_get_argocd_application, k8s_list_helm_releases

@patch("project_fyr.tools._get_custom_objects_api")
def test_k8s_get_argocd_application(mock_get_custom):
    mock_api = MagicMock()
    mock_get_custom.return_value = mock_api
    
    mock_app = {
        "status": {
            "health": {"status": "Healthy"},
            "sync": {"status": "Synced"},
            "conditions": [{"type": "SharedResourceWarning", "message": "Resource already exists"}]
        }
    }
    
    mock_api.get_namespaced_custom_object.return_value = mock_app
    
    result = k8s_get_argocd_application.invoke({"name": "guestbook", "namespace": "argocd"})
    
    assert "ArgoCD Application: guestbook" in result
    assert "Health: Healthy" in result
    assert "Sync Status: Synced" in result
    assert "SharedResourceWarning" in result

@patch("project_fyr.tools._get_core_v1")
def test_k8s_list_helm_releases(mock_get_core):
    mock_api = MagicMock()
    mock_get_core.return_value = mock_api
    
    # Mock a Helm secret
    mock_secret = MagicMock()
    mock_secret.metadata.labels = {
        "name": "my-release",
        "status": "deployed",
        "version": "1"
    }
    mock_secret.metadata.creation_timestamp = "2023-01-01T00:00:00Z"
    
    mock_api.list_namespaced_secret.return_value.items = [mock_secret]
    
    result = k8s_list_helm_releases.invoke({"namespace": "default"})
    
    assert "Helm Releases:" in result
    assert "my-release" in result
    assert "Rev: 1" in result
    assert "Status: deployed" in result

@patch("project_fyr.tools._get_core_v1")
def test_k8s_list_helm_releases_empty(mock_get_core):
    mock_api = MagicMock()
    mock_get_core.return_value = mock_api
    mock_api.list_namespaced_secret.return_value.items = []
    
    result = k8s_list_helm_releases.invoke({"namespace": "default"})
    assert "No Helm releases found" in result
