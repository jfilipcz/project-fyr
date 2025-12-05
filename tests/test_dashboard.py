from fastapi.testclient import TestClient
from project_fyr.dashboard import app, get_repo
from project_fyr.models import RolloutStatus

client = TestClient(app)

def test_index_empty(repo):
    app.dependency_overrides[get_repo] = lambda: repo
    response = client.get("/")
    assert response.status_code == 200
    assert "Project Fyr Rollouts" in response.text
    assert "No rollouts found" not in response.text # We don't have this text, but table should be empty

def test_index_with_rollouts(repo):
    app.dependency_overrides[get_repo] = lambda: repo
    repo.create(
        cluster="test-cluster",
        namespace="default",
        deployment="test-dep",
        generation=1,
        status=RolloutStatus.SUCCESS
    )
    response = client.get("/")
    assert response.status_code == 200
    assert "test-dep" in response.text
    assert "SUCCESS" in response.text

def test_detail_found(repo):
    app.dependency_overrides[get_repo] = lambda: repo
    r = repo.create(
        cluster="test-cluster",
        namespace="default",
        deployment="test-dep-detail",
        generation=1,
        status=RolloutStatus.FAILED
    )
    response = client.get(f"/rollout/{r.id}")
    assert response.status_code == 200
    assert "Rollout #" in response.text
    assert "test-dep-detail" in response.text

def test_detail_not_found(repo):
    app.dependency_overrides[get_repo] = lambda: repo
    response = client.get("/rollout/999")
    assert response.status_code == 404
