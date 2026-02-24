"""Tests for project_fyr.triage — keyword-based failure triage."""

from project_fyr.triage import triage_failure, TriageTeam
from project_fyr.models import ReducedContext, Analysis, EventSummary, LogCluster


def _ctx(**overrides):
    """Build a minimal ReducedContext."""
    defaults = dict(
        namespace="default",
        deployment="my-app",
        generation=1,
        summary="Pod failed",
        phase="CrashLoopBackOff",
        failing_pods=["my-app-abc-123"],
        log_clusters=[],
        events=[],
    )
    defaults.update(overrides)
    return ReducedContext(**defaults)


def _analysis(**overrides):
    """Build a minimal Analysis."""
    defaults = dict(
        summary="Pod crashed on startup",
        likely_cause="NullPointerException",
        recommended_steps=["Fix the bug"],
        severity="medium",
    )
    defaults.update(overrides)
    return Analysis(**defaults)


def test_triage_security_keywords():
    ctx = _ctx(summary="rbac FORBIDDEN policy violation on service account")
    analysis = _analysis(likely_cause="Service account missing RBAC permission")
    result = triage_failure(ctx, analysis)
    assert result.team == TriageTeam.SECURITY
    assert "Security" in result.reason


def test_triage_infra_scheduling():
    ctx = _ctx(
        summary="Pod stuck in Pending",
        events=[
            EventSummary(
                reason="FailedScheduling",
                message_template="0/3 nodes available: insufficient memory",
                count=5,
                last_timestamp="2025-01-01T00:00:00Z",
            )
        ],
    )
    analysis = _analysis(likely_cause="Insufficient cluster resources")
    result = triage_failure(ctx, analysis)
    assert result.team == TriageTeam.INFRA


def test_triage_infra_image_pull():
    ctx = _ctx(summary="ImagePullBackOff for registry.example.com/app:latest")
    analysis = _analysis(likely_cause="Image not found in registry")
    result = triage_failure(ctx, analysis)
    assert result.team == TriageTeam.INFRA


def test_triage_defaults_to_application():
    ctx = _ctx(summary="Pod restarted with exit code 1")
    analysis = _analysis(likely_cause="Unhandled exception in main loop")
    result = triage_failure(ctx, analysis)
    assert result.team == TriageTeam.APPLICATION
    assert "default" in result.reason.lower()


def test_triage_security_takes_precedence():
    """When both security and infra keywords are present, security wins."""
    ctx = _ctx(summary="Certificate rotation failed on storage PVC")
    analysis = _analysis()
    result = triage_failure(ctx, analysis)
    assert result.team == TriageTeam.SECURITY


def test_triage_from_log_clusters():
    ctx = _ctx(
        log_clusters=[
            LogCluster(
                pod="my-app-1",
                container="app",
                template="connection timeout to <HOST>",
                example="connection timeout to 10.0.0.1",
                count=12,
            )
        ],
    )
    analysis = _analysis()
    result = triage_failure(ctx, analysis)
    assert result.team == TriageTeam.INFRA
