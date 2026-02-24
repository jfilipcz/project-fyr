from project_fyr import utcnow

from project_fyr.models import RolloutStatus


def _create_rollout(repo, *, namespace, deployment, status, requestor=None):
    metadata_json = {}
    if requestor:
        metadata_json["project-fyr.io/requestor-email"] = requestor
    return repo.create(
        cluster="test-cluster",
        namespace=namespace,
        deployment=deployment,
        generation=1,
        status=status,
        started_at=utcnow(),
        metadata_json=metadata_json,
    )


def test_list_recent_filters_by_requestor(repo):
    _create_rollout(repo, namespace="app-ns", deployment="app-a", status=RolloutStatus.FAILED, requestor="a@example.com")
    _create_rollout(repo, namespace="app-ns", deployment="app-b", status=RolloutStatus.SUCCESS, requestor="b@example.com")
    _create_rollout(repo, namespace="app-ns", deployment="app-c", status=RolloutStatus.SUCCESS)

    results = repo.list_recent(limit=50, requestor="a@example.com")
    assert [r.deployment for r in results] == ["app-a"]


def test_list_by_status_filters_by_requestor(repo):
    _create_rollout(repo, namespace="app-ns", deployment="app-a", status=RolloutStatus.FAILED, requestor="a@example.com")
    _create_rollout(repo, namespace="app-ns", deployment="app-b", status=RolloutStatus.FAILED, requestor="b@example.com")
    _create_rollout(repo, namespace="app-ns", deployment="app-c", status=RolloutStatus.FAILED)

    results = repo.list_by_status("failed", limit=50, requestor="b@example.com", exclude_system=False)
    assert [r.deployment for r in results] == ["app-b"]


def test_list_by_namespace_filters_by_requestor(repo):
    _create_rollout(repo, namespace="team-a", deployment="app-a", status=RolloutStatus.SUCCESS, requestor="a@example.com")
    _create_rollout(repo, namespace="team-a", deployment="app-b", status=RolloutStatus.SUCCESS, requestor="b@example.com")
    _create_rollout(repo, namespace="team-b", deployment="app-c", status=RolloutStatus.SUCCESS, requestor="a@example.com")

    results = repo.list_by_namespace("team-a", limit=50, requestor="a@example.com")
    assert [r.deployment for r in results] == ["app-a"]


def test_list_by_status_and_namespace_filters_by_requestor(repo):
    _create_rollout(repo, namespace="team-a", deployment="app-a", status=RolloutStatus.SUCCESS, requestor="a@example.com")
    _create_rollout(repo, namespace="team-a", deployment="app-b", status=RolloutStatus.SUCCESS, requestor="b@example.com")
    _create_rollout(repo, namespace="team-a", deployment="app-c", status=RolloutStatus.FAILED, requestor="a@example.com")

    results = repo.list_by_status_and_namespace("success", "team-a", limit=50, requestor="a@example.com")
    assert [r.deployment for r in results] == ["app-a"]
