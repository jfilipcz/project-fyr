
from fastapi.testclient import TestClient
from project_fyr.dashboard import app, get_repo
from unittest.mock import MagicMock, patch

client = TestClient(app)

def test_overview_page(repo):
    app.dependency_overrides[get_repo] = lambda: repo
    response = client.get("/overview")
    assert response.status_code == 200
    assert "Total Deployments" in response.text
    assert "AI Analysis" in response.text

@patch("project_fyr.aggregator.IssueAggregator")
def test_api_insights(mock_aggregator_cls, repo):
    app.dependency_overrides[get_repo] = lambda: repo
    
    # Mock aggregator instance
    mock_agg = MagicMock()
    mock_agg.aggregate_issues.return_value = {
        "top_issues": [{"cause": "OOM", "count": 1, "description": "Mem", "affected_namespaces": ["n1"]}],
        "summary": "Everything is burning"
    }
    mock_aggregator_cls.return_value = mock_agg
    
    # Need failures in DB for aggregator to be called
    # Mock get_recent_failures return? Or insert real data?
    # Let's insert real data to test full integration except LLM
    from project_fyr.models import RolloutStatus, AnalysisStatus
    from project_fyr.db import AnalysisRecord
    from datetime import datetime
    
    r = repo.create(cluster="c1", namespace="n1", deployment="d1", generation=1, 
                    status=RolloutStatus.FAILED, started_at=datetime.utcnow(),
                    analysis_status=AnalysisStatus.DONE)
    with repo.session() as s:
        ar = AnalysisRecord(rollout_id=r.id, model_name="test", prompt_version="v1", 
                           reduced_context={}, analysis={"summary": "fail"})
        s.add(ar)
        s.flush()
        r.analysis_id = ar.id
        s.commit()
    
    response = client.get("/api/overview/insights")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == "Everything is burning"
    assert len(data["top_issues"]) == 1
