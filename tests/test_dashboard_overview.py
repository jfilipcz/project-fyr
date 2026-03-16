
import asyncio
from fastapi import HTTPException
import pytest
from starlette.requests import Request
from project_fyr.dashboard import (
    get_namespace_analysis_details,
    get_overview_insights,
    index,
    insights_cache,
)
from unittest.mock import MagicMock, patch
from pathlib import Path
from sqlalchemy import select

from project_fyr.db import NamespaceCaseRepo
from project_fyr.models import IssueScope, IssueStatus


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

def test_overview_page(repo):
    """GET / serves overview with stats cards and AI Insights section."""
    case_repo = NamespaceCaseRepo(repo._engine)
    response = asyncio.run(index(_request("/"), repo=repo, case_repo=case_repo))
    assert response.status_code == 200
    html = response.body.decode()
    assert "Total Deployments" in html
    assert "AI Insights" in html


def test_overview_page_includes_namespace_markdown_renderer():
    """Namespace analysis modal should support markdown rendering."""
    template = (Path(__file__).resolve().parents[1] / "project_fyr" / "templates" / "overview.html").read_text()
    assert "function renderMarkdown(" in template
    assert "marked.parse" in template


def test_overview_page_supports_shareable_namespace_analysis_links():
    """Namespace analysis modal should expose deep-link and copy-link hooks."""
    template = (Path(__file__).resolve().parents[1] / "project_fyr" / "templates" / "overview.html").read_text()
    assert "Copy Link" in template
    assert "namespaceAnalysis" in template
    assert "function copyNamespaceAnalysisLink()" in template
    assert "initialNamespaceAnalysis" in template


def test_overview_page_uses_operational_top_panels():
    """Overview should keep rollout metrics in the lower rollout telemetry section."""
    template = (Path(__file__).resolve().parents[1] / "project_fyr" / "templates" / "overview.html").read_text()
    assert "Open Cases" in template
    assert "Recovered Without Slack" in template
    assert "Pending Analyses" in template
    assert "Oldest Pending" in template
    assert "New Failures (60m)" in template
    assert "Repeated Namespaces" in template
    assert "Rollout Telemetry" in template
    assert template.index("Rollout Telemetry") < template.index("Pending Analyses")


def test_overview_page_is_case_first(repo):
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

    response = asyncio.run(index(_request("/"), repo=repo, case_repo=case_repo))
    assert response.status_code == 200
    html = response.body.decode()
    assert "Open Cases" in html
    assert "Active Cases" in html
    assert "demo" in html


def test_overview_page_shows_recovered_quietly_section(repo):
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

    response = asyncio.run(index(_request("/"), repo=repo, case_repo=case_repo))
    assert response.status_code == 200
    html = response.body.decode()
    assert "Recovered Quietly" in html
    assert "quiet-demo" in html


def test_overview_page_active_cases_namespace_links_to_case_detail(repo):
    case_repo = NamespaceCaseRepo(repo._engine)
    case = case_repo.get_or_create_open_case("c1", "linked-demo")
    case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="api",
        cause_family="shared_dependency_failure",
        issue_type="crashloop",
        status=IssueStatus.ACTIVE,
    )

    response = asyncio.run(index(_request("/"), repo=repo, case_repo=case_repo))

    assert response.status_code == 200
    html = response.body.decode()
    assert f'href="/case/{case.id}"' in html
    assert "linked-demo" in html


def test_overview_page_omits_recovered_quiet_case_from_active_cases(repo):
    case_repo = NamespaceCaseRepo(repo._engine)
    case = case_repo.get_or_create_open_case("c1", "quiet-only")
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

    response = asyncio.run(index(_request("/"), repo=repo, case_repo=case_repo))

    assert response.status_code == 200
    html = response.body.decode()
    assert "No active cases in this window." in html
    assert "quiet-only" in html

@patch("project_fyr.aggregator.IssueAggregator")
def test_api_insights(mock_aggregator_cls, repo):
    insights_cache["data"] = None
    insights_cache["timestamp"] = None
    insights_cache["hours"] = None
    insights_cache["include_system"] = None

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
    from project_fyr import utcnow

    r = repo.create(cluster="c1", namespace="n1", deployment="d1", generation=1,
                    status=RolloutStatus.FAILED, started_at=utcnow(),
                    analysis_status=AnalysisStatus.DONE)
    with repo.session() as s:
        ar = AnalysisRecord(rollout_id=r.id, model_name="test", prompt_version="v1",
                           reduced_context={}, analysis={"summary": "fail"})
        s.add(ar)
        s.flush()
        r.analysis_id = ar.id
        s.commit()

    data = asyncio.run(get_overview_insights(hours=24, include_system=False, repo=repo))
    assert data["summary"] == "Everything is burning"
    assert len(data["top_issues"]) == 1


def test_namespace_analysis_endpoint_returns_rollout_analyses(repo):
    from project_fyr.models import RolloutStatus, AnalysisStatus
    from project_fyr.db import AnalysisRecord
    from project_fyr import utcnow

    r = repo.create(
        cluster="c1",
        namespace="n1",
        deployment="d1",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=utcnow(),
        analysis_status=AnalysisStatus.DONE,
    )

    with repo.session() as s:
        ar = AnalysisRecord(
            rollout_id=r.id,
            model_name="test-model",
            prompt_version="v1",
            reduced_context={},
            analysis={
                "summary": "Rollout failed due to dependency timeout",
                "likely_cause": "Database connection timeout",
                "severity": "high",
                "recommended_steps": ["Increase DB connection timeout"],
            },
        )
        s.add(ar)
        s.flush()
        r = s.merge(r)
        r.analysis_id = ar.id
        s.commit()

    payload = asyncio.run(
        get_namespace_analysis_details(namespace="n1", hours=24, repo=repo)
    )

    assert payload["namespace"] == "n1"
    assert payload["count"] == 1
    assert payload["failures"][0]["deployment"] == "d1"
    assert payload["failures"][0]["severity"] == "high"
    assert "Database connection timeout" in payload["failures"][0]["likely_cause"]


def test_namespace_analysis_endpoint_requires_namespace(repo):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(get_namespace_analysis_details(namespace="", hours=24, repo=repo))
    assert excinfo.value.status_code == 400


@patch("project_fyr.aggregator.IssueAggregator")
def test_api_insights_uses_db_cache_when_memory_cache_cleared(mock_aggregator_cls, repo):
    insights_cache["data"] = None
    insights_cache["timestamp"] = None
    insights_cache["hours"] = None
    insights_cache["include_system"] = None

    from project_fyr.models import RolloutStatus, AnalysisStatus
    from project_fyr.db import AnalysisRecord, AggregatedInsight
    from project_fyr import utcnow

    r = repo.create(
        cluster="c1",
        namespace="n1",
        deployment="d1",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=utcnow(),
        analysis_status=AnalysisStatus.DONE,
    )
    with repo.session() as s:
        ar = AnalysisRecord(
            rollout_id=r.id,
            model_name="test",
            prompt_version="v1",
            reduced_context={},
            analysis={"summary": "fail"},
        )
        s.add(ar)
        s.flush()
        r.analysis_id = ar.id
        s.commit()

    mock_agg = MagicMock()
    mock_agg.aggregate_issues.return_value = {
        "top_issues": [{"cause": "OOM", "count": 1, "description": "Mem", "affected_namespaces": ["n1"]}],
        "summary": "Cached summary",
    }
    mock_aggregator_cls.return_value = mock_agg

    first_payload = asyncio.run(
        get_overview_insights(hours=24, include_system=False, repo=repo)
    )
    assert first_payload["summary"] == "Cached summary"

    with repo.session() as s:
        persisted = list(s.scalars(select(AggregatedInsight)))
    assert len(persisted) == 1
    assert persisted[0].failure_count == 1

    insights_cache["data"] = None
    insights_cache["timestamp"] = None
    insights_cache["hours"] = None
    insights_cache["include_system"] = None

    second_payload = asyncio.run(
        get_overview_insights(hours=24, include_system=False, repo=repo)
    )
    assert second_payload["summary"] == "Cached summary"
    # Aggregator called only for first request; second request should use DB cache.
    mock_agg.aggregate_issues.assert_called_once()
