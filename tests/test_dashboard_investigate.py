from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from project_fyr.dashboard import app

client = TestClient(app)

@patch("project_fyr.dashboard.settings")
@patch("project_fyr.agent.InvestigatorAgent")
def test_investigate_api(mock_agent_cls, mock_settings):
    mock_settings.langchain_model_name = "mock-model"
    mock_settings.openai_api_key = "mock-key"
    
    mock_agent = MagicMock()
    mock_agent_cls.return_value = mock_agent
    
    mock_analysis = MagicMock()
    mock_analysis.model_dump.return_value = {
        "summary": "Test Summary",
        "likely_cause": "Test Cause",
        "recommended_steps": ["Step 1"],
        "severity": "medium"
    }
    mock_agent.investigate.return_value = mock_analysis
    
    response = client.post("/api/investigate", json={"deployment": "dep", "namespace": "ns"})
    
    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == "Test Summary"
    mock_agent.investigate.assert_called_with("dep", "ns")

def test_investigate_api_missing_params():
    response = client.post("/api/investigate", json={})
    assert response.status_code == 400

@patch("kubernetes.config.load_kube_config")
@patch("kubernetes.client.AppsV1Api")
@patch("kubernetes.client.CoreV1Api")
def test_investigate_page(mock_core, mock_apps, mock_config):
    mock_core_instance = MagicMock()
    mock_core.return_value = mock_core_instance
    
    mock_ns = MagicMock()
    mock_ns.metadata.name = "default"
    mock_core_instance.list_namespace.return_value.items = [mock_ns]
    
    mock_apps_instance = MagicMock()
    mock_apps.return_value = mock_apps_instance
    
    mock_dep = MagicMock()
    mock_dep.metadata.name = "nginx"
    # Fix: Set numeric values for replicas to avoid MagicMock comparison
    mock_dep.status.ready_replicas = 3
    mock_dep.spec.replicas = 3
    mock_apps_instance.list_namespaced_deployment.return_value.items = [mock_dep]
    
    response = client.get("/investigate")
    assert response.status_code == 200
    assert "On-Demand Investigation" in response.text
    assert "nginx" in response.text
