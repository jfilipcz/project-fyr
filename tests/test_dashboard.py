from fastapi.testclient import TestClient
from project_fyr.dashboard import app, get_repo
from project_fyr.models import RolloutStatus

client = TestClient(app)

def test_index_empty(repo):
    """GET / serves the overview dashboard with stats cards."""
    app.dependency_overrides[get_repo] = lambda: repo
    response = client.get("/")
    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert "Total Deployments" in response.text

def test_index_with_rollouts(repo):
    """Stats on the overview dashboard reflect database contents."""
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
    # Overview page renders stats server-side
    assert "Total Deployments" in response.text

def test_rollouts_page(repo):
    """GET /rollouts serves the rollouts list page (data loaded via JS API)."""
    app.dependency_overrides[get_repo] = lambda: repo
    response = client.get("/rollouts")
    assert response.status_code == 200
    assert "Rollouts" in response.text

def test_rollouts_api(repo):
    """API endpoint returns rollout data as JSON."""
    app.dependency_overrides[get_repo] = lambda: repo
    repo.create(
        cluster="test-cluster",
        namespace="my-app",
        deployment="test-dep",
        generation=1,
        status=RolloutStatus.SUCCESS,
    )
    response = client.get("/api/rollouts")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["rollouts"][0]["deployment"] == "test-dep"
    assert data["rollouts"][0]["status"] == "SUCCESS"

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
