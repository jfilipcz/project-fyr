import pytest
import asyncio
from fastapi import HTTPException
from starlette.requests import Request

from project_fyr.dashboard import case_detail, cases_list, detail, get_rollouts_data, index, rollouts_list
from project_fyr.db import NamespaceCaseRepo
from project_fyr.models import (
    Analysis,
    AnalysisStatus,
    IssueScope,
    IssueStatus,
    ReducedContext,
    RolloutStatus,
)


def _request(path: str = "/") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "scheme": "http",
            "http_version": "1.1",
        }
    )


def test_index_empty(repo):
    """GET / serves the overview dashboard with stats cards."""
    response = asyncio.run(index(_request("/"), repo=repo))
    assert response.status_code == 200
    assert "Dashboard" in response.body.decode()
    assert "Total Deployments" in response.body.decode()


def test_index_with_rollouts(repo):
    """Stats on the overview dashboard reflect database contents."""
    repo.create(
        cluster="test-cluster",
        namespace="default",
        deployment="test-dep",
        generation=1,
        status=RolloutStatus.SUCCESS
    )
    response = asyncio.run(index(_request("/"), repo=repo))
    assert response.status_code == 200
    # Overview page renders stats server-side
    assert "Total Deployments" in response.body.decode()


def test_rollouts_page(repo):
    """GET /rollouts serves the rollouts list page (data loaded via JS API)."""
    response = asyncio.run(rollouts_list(_request("/rollouts")))
    assert response.status_code == 200
    assert "Rollouts" in response.body.decode()


def test_rollouts_api(repo):
    """API endpoint returns rollout data as JSON."""
    repo.create(
        cluster="test-cluster",
        namespace="my-app",
        deployment="test-dep",
        generation=1,
        status=RolloutStatus.SUCCESS,
    )
    data = asyncio.run(get_rollouts_data(repo=repo))
    assert data["count"] == 1
    assert data["rollouts"][0]["deployment"] == "test-dep"
    assert data["rollouts"][0]["status"] == "SUCCESS"


def test_detail_found(repo):
    r = repo.create(
        cluster="test-cluster",
        namespace="default",
        deployment="test-dep-detail",
        generation=1,
        status=RolloutStatus.FAILED
    )
    response = asyncio.run(detail(_request(f"/rollout/{r.id}"), r.id, repo))
    assert response.status_code == 200
    html = response.body.decode()
    assert "Rollout #" in html
    assert "test-dep-detail" in html


def test_detail_not_found(repo):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(detail(_request("/rollout/999"), 999, repo))
    assert excinfo.value.status_code == 404


def test_detail_shows_quiet_rollout_notification_state(repo):
    rollout = repo.create(
        cluster="test-cluster",
        namespace="default",
        deployment="test-dep-quiet",
        generation=1,
        status=RolloutStatus.FAILED,
        analysis_status=AnalysisStatus.PENDING,
        metadata_json={
            "notification_state": "suppressed",
            "recovered_before_notification": True,
        },
    )
    repo.append_analysis(
        rollout.id,
        reduced_context=ReducedContext(
            namespace="default",
            deployment="test-dep-quiet",
            generation=1,
            summary="Agentic Investigation",
            phase="FAILED",
            failing_pods=[],
            log_clusters=[],
            events=[],
            argocd_status=None,
        ),
        analysis=Analysis(
            summary="quiet investigation",
            likely_cause="Recovered during quiet-first window",
            recommended_steps=["No action required"],
        ),
        model_name="test-model",
    )
    repo.set_rollout_notification_state(
        rollout.id,
        state="suppressed",
        classification="unknown",
        reason="recovered before notification",
        recovered_before_notification=True,
    )

    response = asyncio.run(detail(_request(f"/rollout/{rollout.id}"), rollout.id, repo))

    assert response.status_code == 200
    assert "Recovered before notification" in response.body.decode()


def test_cases_page_renders_case_rows(repo):
    case_repo = NamespaceCaseRepo(repo._engine)
    case = case_repo.get_or_create_open_case("c1", "demo")
    case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="api",
        cause_family="shared_dependency_failure",
        issue_type="crashloop",
        status=IssueStatus.ACTIVE,
    )

    response = asyncio.run(cases_list(_request("/cases"), case_repo=case_repo))

    assert response.status_code == 200
    html = response.body.decode()
    assert "Namespace Cases" in html
    assert "demo" in html
    assert "shared_dependency_failure" in html


def test_cases_page_accepts_status_and_cause_family_filters(repo):
    case_repo = NamespaceCaseRepo(repo._engine)
    open_case = case_repo.get_or_create_open_case("c1", "open-demo")
    quiet_case = case_repo.get_or_create_open_case("c1", "quiet-demo")
    case_repo.create_issue(
        namespace_case_id=open_case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="api",
        cause_family="shared_dependency_failure",
        issue_type="crashloop",
        status=IssueStatus.ACTIVE,
    )
    case_repo.create_issue(
        namespace_case_id=quiet_case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="worker",
        cause_family="capacity_autoscale_pending",
        issue_type="unschedulable",
        status=IssueStatus.ACTIVE,
    )
    from project_fyr.models import NamespaceCaseStatus
    case_repo.update_case_status(quiet_case.id, NamespaceCaseStatus.QUIETING)

    response = asyncio.run(
        cases_list(
            _request("/cases"),
            status="QUIETING",
            cause_family="capacity_autoscale_pending",
            case_repo=case_repo,
        )
    )

    assert response.status_code == 200
    html = response.body.decode()
    assert "quiet-demo" in html
    assert "open-demo" not in html


def test_case_detail_page_renders_header_active_issues_and_timeline(repo):
    case_repo = NamespaceCaseRepo(repo._engine)
    case = case_repo.get_or_create_open_case("c1", "demo")
    issue = case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="api",
        cause_family="shared_dependency_failure",
        issue_type="crashloop",
        status=IssueStatus.ACTIVE,
    )
    case_repo.append_issue_observation(
        issue.id,
        source="watcher",
        signal_type="permanent_failure_detected",
        payload_json={"deployment": "api"},
    )

    response = asyncio.run(case_detail(_request(f"/case/{case.id}"), case.id, case_repo=case_repo))

    assert response.status_code == 200
    html = response.body.decode()
    assert "Namespace Case" in html
    assert "Active Issues" in html
    assert "Observation Timeline" in html
    assert "api" in html


def test_case_detail_page_shows_recovered_before_notification(repo):
    case_repo = NamespaceCaseRepo(repo._engine)
    case = case_repo.get_or_create_open_case("c1", "quiet-demo")
    case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="worker",
        cause_family="capacity_autoscale_pending",
        issue_type="unschedulable",
        status=IssueStatus.RESOLVED,
        metadata_json={"recovered_before_notification": True},
    )

    response = asyncio.run(case_detail(_request(f"/case/{case.id}"), case.id, case_repo=case_repo))

    assert response.status_code == 200
    assert "Recovered before notification" in response.body.decode()


def test_rollout_detail_shows_parent_case_block(repo):
    case_repo = NamespaceCaseRepo(repo._engine)
    rollout = repo.create(
        cluster="c1",
        namespace="demo",
        deployment="api",
        generation=3,
        status=RolloutStatus.FAILED,
    )
    case = case_repo.get_or_create_open_case("c1", "demo")
    case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="api",
        rollout_id=rollout.id,
        cause_family="shared_dependency_failure",
        issue_type="crashloop",
        status=IssueStatus.ACTIVE,
    )

    response = asyncio.run(detail(_request(f"/rollout/{rollout.id}"), rollout.id, repo, case_repo))

    assert response.status_code == 200
    html = response.body.decode()
    assert "Parent Case" in html
    assert f"/case/{case.id}" in html
    assert "Part of namespace case" in html


def test_rollout_detail_shows_issue_context(repo):
    case_repo = NamespaceCaseRepo(repo._engine)
    rollout = repo.create(
        cluster="c1",
        namespace="demo",
        deployment="worker",
        generation=4,
        status=RolloutStatus.FAILED,
    )
    case = case_repo.get_or_create_open_case("c1", "demo")
    case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="worker",
        rollout_id=rollout.id,
        cause_family="app_config_or_secret",
        issue_type="config_error",
        status=IssueStatus.ACTIVE,
    )

    response = asyncio.run(detail(_request(f"/rollout/{rollout.id}"), rollout.id, repo, case_repo))

    assert response.status_code == 200
    html = response.body.decode()
    assert "Issue Context" in html
    assert "app_config_or_secret" in html
    assert "config_error" in html
