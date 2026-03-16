
from datetime import timedelta
from sqlalchemy import select
from project_fyr import utcnow
from project_fyr.models import (
    RolloutStatus,
    AnalysisStatus,
    NotifyStatus,
    NamespaceCaseStatus,
    IssueScope,
    IssueStatus,
    WorkItemKind,
    WorkItemStatus,
)
from project_fyr.db import (
    AnalysisRecord,
    IssueObservationRecord,
    NamespaceCaseRecord,
    NamespaceCaseRepo,
    RolloutStatusTransition,
    WorkItemRepo,
    init_db,
)
from unittest.mock import MagicMock, patch

def test_get_stats_empty(repo):
    stats = repo.get_stats(hours=24)
    assert stats["total"] == 0
    assert stats["success"] == 0
    assert stats["failed"] == 0
    assert stats["success_rate"] == 0

def test_get_stats_mixed(repo, session):
    now = utcnow()

    # Success within window
    repo.create(cluster="c1", namespace="n1", deployment="d1", generation=1,
                status=RolloutStatus.SUCCESS, started_at=now - timedelta(hours=1))

    # Failed within window
    repo.create(cluster="c1", namespace="n1", deployment="d2", generation=1,
                status=RolloutStatus.FAILED, started_at=now - timedelta(hours=2))

    # Old rollout (outside window)
    repo.create(cluster="c1", namespace="n1", deployment="d3", generation=1,
                status=RolloutStatus.SUCCESS, started_at=now - timedelta(hours=25))

    stats = repo.get_stats(hours=24)
    assert stats["total"] == 2
    assert stats["success"] == 1
    assert stats["failed"] == 1
    assert stats["success_rate"] == 50.0


def test_get_operational_stats_dashboard_metrics(repo):
    now = utcnow()

    # Pending analysis queue (oldest + recent)
    repo.create(
        cluster="c1",
        namespace="queue-old",
        deployment="d1",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=now - timedelta(hours=3),
        failed_at=now - timedelta(hours=2),
        analysis_status=AnalysisStatus.PENDING,
    )
    repo.create(
        cluster="c1",
        namespace="queue-new",
        deployment="d2",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=now - timedelta(minutes=25),
        failed_at=now - timedelta(minutes=20),
        analysis_status=AnalysisStatus.PENDING,
    )

    # Repeated failures in one namespace (for top namespace metric)
    repo.create(
        cluster="c1",
        namespace="repeat-ns",
        deployment="api",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=now - timedelta(minutes=12),
        failed_at=now - timedelta(minutes=10),
        analysis_status=AnalysisStatus.DONE,
    )
    repo.create(
        cluster="c1",
        namespace="repeat-ns",
        deployment="worker",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=now - timedelta(minutes=7),
        failed_at=now - timedelta(minutes=5),
        analysis_status=AnalysisStatus.DONE,
    )

    # Single failed namespace (should not count as repeated namespace)
    repo.create(
        cluster="c1",
        namespace="single-ns",
        deployment="once",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=now - timedelta(minutes=90),
        failed_at=now - timedelta(minutes=80),
        analysis_status=AnalysisStatus.DONE,
    )

    ops = repo.get_operational_stats(hours=24)

    assert ops["pending_analyses"] == 2
    assert ops["oldest_pending_age_minutes"] >= 119
    assert ops["new_failures_60m"] == 3
    assert ops["noisy_namespaces"] == 1
    assert ops["top_noisy_namespace"] == "repeat-ns"
    assert ops["top_noisy_namespace_failures"] == 2


def test_get_recent_failures(repo, session):
    now = utcnow()

    # Failed with analysis
    r1 = repo.create(cluster="c1", namespace="n1", deployment="d1", generation=1,
                     status=RolloutStatus.FAILED, started_at=now,
                     analysis_status=AnalysisStatus.DONE)

    # Actually we can use append_analysis logic or just manual insert
    ar = AnalysisRecord(rollout_id=r1.id, model_name="test", prompt_version="v1",
                        reduced_context={}, analysis={"summary": "test failure"})
    session.add(ar)
    session.flush()

    # Re-fetch or merge r1 to attach to this session
    r1 = session.merge(r1)
    r1.analysis_id = ar.id
    session.commit()

    # Failed without analysis
    repo.create(cluster="c1", namespace="n1", deployment="d2", generation=1,
                status=RolloutStatus.FAILED, started_at=now,
                analysis_status=AnalysisStatus.PENDING)

    failures = repo.get_recent_failures(hours=24)
    assert len(failures) == 1
    assert failures[0][0].id == r1.id
    assert failures[0][1].analysis["summary"] == "test failure"


def test_get_namespace_recent_failures(repo, session):
    now = utcnow()

    # Matching namespace with analysis
    r1 = repo.create(
        cluster="c1",
        namespace="target-ns",
        deployment="api",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=now,
        analysis_status=AnalysisStatus.DONE,
    )
    ar1 = AnalysisRecord(
        rollout_id=r1.id,
        model_name="test",
        prompt_version="v1",
        reduced_context={},
        analysis={"summary": "timeout", "likely_cause": "db timeout", "severity": "high"},
    )
    session.add(ar1)
    session.flush()
    r1 = session.merge(r1)
    r1.analysis_id = ar1.id
    session.commit()

    # Different namespace should be excluded
    r2 = repo.create(
        cluster="c1",
        namespace="other-ns",
        deployment="worker",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=now,
        analysis_status=AnalysisStatus.DONE,
    )
    ar2 = AnalysisRecord(
        rollout_id=r2.id,
        model_name="test",
        prompt_version="v1",
        reduced_context={},
        analysis={"summary": "oom"},
    )
    session.add(ar2)
    session.flush()
    r2 = session.merge(r2)
    r2.analysis_id = ar2.id
    session.commit()

    failures = repo.get_namespace_recent_failures(namespace="target-ns", hours=24)
    assert len(failures) == 1
    assert failures[0][0].namespace == "target-ns"
    assert failures[0][1].analysis["likely_cause"] == "db timeout"


def test_init_db_enables_connection_recovery_options():
    fake_engine = MagicMock()
    with patch("project_fyr.db.create_engine", return_value=fake_engine) as mock_create_engine, \
         patch("project_fyr.db.Base.metadata.create_all") as mock_create_all:
        engine = init_db("mysql+pymysql://user:pass@mysql/projectfyr")

    assert engine is fake_engine
    _, kwargs = mock_create_engine.call_args
    assert kwargs["future"] is True
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 300
    mock_create_all.assert_called_once_with(fake_engine)


def test_discard_analysis_marks_rollout_discarded_with_reason(repo):
    rollout = repo.create(
        cluster="c1",
        namespace="n1",
        deployment="d1",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=utcnow(),
        analysis_status=AnalysisStatus.PENDING,
    )

    repo.discard_analysis(rollout.id, reason="Namespace deleted")
    stored = repo.get_by_id(rollout.id)

    assert stored is not None
    assert stored.status == RolloutStatus.FAILED
    assert stored.analysis_status == AnalysisStatus.DISCARDED
    assert stored.notify_status.name == "SENT"
    assert stored.metadata_json["discard_reason"] == "Namespace deleted"


def test_discard_analysis_can_mark_rollout_success(repo):
    rollout = repo.create(
        cluster="c1",
        namespace="n1",
        deployment="d2",
        generation=1,
        status=RolloutStatus.PENDING,
        started_at=utcnow(),
        analysis_status=AnalysisStatus.NOT_NEEDED,
    )

    repo.discard_analysis(
        rollout.id,
        reason="Deployment no longer exists",
        mark_success=True,
    )
    stored = repo.get_by_id(rollout.id)

    assert stored is not None
    assert stored.status == RolloutStatus.SUCCESS
    assert stored.analysis_status == AnalysisStatus.DISCARDED
    assert stored.completed_at is not None


def test_update_metadata_preserves_existing_trigger_context(repo):
    rollout = repo.create(
        cluster="c1",
        namespace="n1",
        deployment="d3",
        generation=1,
        status=RolloutStatus.ROLLING_OUT,
        started_at=utcnow(),
        metadata_json={
            "trigger_context": {"trigger_reason": "transient_failure_observed"},
            "discard_reason": "kept-for-history",
        },
    )

    repo.update_metadata(
        rollout.id,
        metadata_json={"project-fyr.io/enabled": "true"},
    )
    stored = repo.get_by_id(rollout.id)

    assert stored is not None
    assert stored.metadata_json["project-fyr.io/enabled"] == "true"
    assert stored.metadata_json["trigger_context"]["trigger_reason"] == "transient_failure_observed"
    assert stored.metadata_json["discard_reason"] == "kept-for-history"


def test_update_status_records_status_transition(repo):
    rollout = repo.create(
        cluster="c1",
        namespace="n1",
        deployment="d4",
        generation=1,
        status=RolloutStatus.PENDING,
        started_at=utcnow(),
    )

    repo.update_status(rollout.id, RolloutStatus.FAILED, failed_at=utcnow())

    with repo.session() as s:
        transitions = list(
            s.scalars(
                select(RolloutStatusTransition)
                .where(RolloutStatusTransition.rollout_id == rollout.id)
                .order_by(RolloutStatusTransition.id.asc())
            )
        )

    assert len(transitions) == 1
    assert transitions[0].from_status == RolloutStatus.PENDING
    assert transitions[0].to_status == RolloutStatus.FAILED


def test_discard_analysis_mark_success_records_status_transition(repo):
    rollout = repo.create(
        cluster="c1",
        namespace="n1",
        deployment="d5",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=utcnow(),
        analysis_status=AnalysisStatus.PENDING,
    )

    repo.discard_analysis(
        rollout.id,
        reason="Deployment no longer exists",
        mark_success=True,
    )

    with repo.session() as s:
        transitions = list(
            s.scalars(
                select(RolloutStatusTransition)
                .where(RolloutStatusTransition.rollout_id == rollout.id)
                .order_by(RolloutStatusTransition.id.asc())
            )
        )

    assert len(transitions) == 1
    assert transitions[0].from_status == RolloutStatus.FAILED
    assert transitions[0].to_status == RolloutStatus.SUCCESS
    assert transitions[0].reason == "Deployment no longer exists"


def test_set_rollout_notification_state_persists_metadata(repo):
    now = utcnow()
    rollout = repo.create(
        cluster="c1",
        namespace="n1",
        deployment="d6",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=now - timedelta(minutes=10),
        failed_at=now - timedelta(minutes=8),
        analysis_status=AnalysisStatus.DONE,
        metadata_json={"trigger_context": {"failure_type": "unschedulable"}},
    )

    due_at = now + timedelta(minutes=5)
    repo.set_rollout_notification_state(
        rollout.id,
        state="deferred",
        classification="unknown",
        reason="quiet-first policy",
        deferred_until=due_at,
    )

    stored = repo.get_by_id(rollout.id)
    assert stored is not None
    assert stored.notify_status == NotifyStatus.PENDING
    assert stored.metadata_json["trigger_context"]["failure_type"] == "unschedulable"
    assert stored.metadata_json["notification_state"] == "deferred"
    assert stored.metadata_json["notification_classification"] == "unknown"
    assert stored.metadata_json["notification_decision_reason"] == "quiet-first policy"
    assert stored.metadata_json["deferred_until"] == due_at.isoformat()

    repo.set_rollout_notification_state(
        rollout.id,
        state="suppressed",
        classification="transient",
        reason="recovered before notification",
        recovered_before_notification=True,
        notify_status=NotifyStatus.SENT,
    )

    stored = repo.get_by_id(rollout.id)
    assert stored is not None
    assert stored.notify_status == NotifyStatus.SENT
    assert stored.metadata_json["notification_state"] == "suppressed"
    assert stored.metadata_json["notification_classification"] == "transient"
    assert stored.metadata_json["recovered_before_notification"] is True


def test_list_deferred_rollout_notifications_due_filters_by_metadata(repo):
    now = utcnow()
    due_rollout = repo.create(
        cluster="c1",
        namespace="n1",
        deployment="due",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=now - timedelta(minutes=20),
        failed_at=now - timedelta(minutes=15),
        analysis_status=AnalysisStatus.DONE,
    )
    not_due_rollout = repo.create(
        cluster="c1",
        namespace="n1",
        deployment="not-due",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=now - timedelta(minutes=20),
        failed_at=now - timedelta(minutes=15),
        analysis_status=AnalysisStatus.DONE,
    )

    repo.set_rollout_notification_state(
        due_rollout.id,
        state="deferred",
        classification="unknown",
        reason="waiting for quiet window",
        deferred_until=now - timedelta(seconds=1),
    )
    repo.set_rollout_notification_state(
        not_due_rollout.id,
        state="deferred",
        classification="unknown",
        reason="waiting for quiet window",
        deferred_until=now + timedelta(minutes=1),
    )

    due = repo.list_deferred_rollout_notifications_due("c1", now=now)

    assert [rollout.id for rollout in due] == [due_rollout.id]


def test_namespace_case_hierarchy_tables_persist_records(engine):
    case_repo = NamespaceCaseRepo(engine)
    work_repo = WorkItemRepo(engine)

    case = case_repo.create_namespace_case(cluster="c1", namespace="ns1")
    issue = case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="api",
        cause_family="other",
        issue_type="rollout_failed",
    )
    observation = case_repo.append_issue_observation(
        issue.id,
        source="watcher",
        signal_type="trigger",
        payload_json={"failure_type": "crashloop"},
    )
    work_item = work_repo.enqueue_work_item(
        kind=WorkItemKind.ISSUE_INVESTIGATION,
        issue_id=issue.id,
        namespace_case_id=case.id,
    )

    with case_repo.session() as s:
        stored_case = s.get(NamespaceCaseRecord, case.id)
        stored_issue = s.get(type(issue), issue.id)
        stored_observation = s.get(IssueObservationRecord, observation.id)

    assert stored_case is not None
    assert stored_case.status == NamespaceCaseStatus.OPEN
    assert stored_issue is not None
    assert stored_issue.namespace_case_id == case.id
    assert stored_issue.status == IssueStatus.ACTIVE
    assert stored_observation is not None
    assert stored_observation.issue_id == issue.id
    assert work_item.kind == WorkItemKind.ISSUE_INVESTIGATION
    assert work_item.status == WorkItemStatus.PENDING


def test_get_or_create_open_case_reuses_open_and_quieting_case(engine):
    case_repo = NamespaceCaseRepo(engine)

    first = case_repo.get_or_create_open_case("c1", "ns1")
    second = case_repo.get_or_create_open_case("c1", "ns1")

    assert first.id == second.id

    case_repo.update_case_status(first.id, NamespaceCaseStatus.QUIETING)
    third = case_repo.get_or_create_open_case("c1", "ns1")
    assert third.id == first.id

    case_repo.update_case_status(first.id, NamespaceCaseStatus.CLOSED)
    fourth = case_repo.get_or_create_open_case("c1", "ns1")
    assert fourth.id != first.id


def test_get_or_create_issue_updates_existing_active_issue(engine):
    case_repo = NamespaceCaseRepo(engine)
    case = case_repo.create_namespace_case(cluster="c1", namespace="ns1")

    first = case_repo.get_or_create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="api",
        rollout_id=12,
        cause_family="other",
        issue_type="rollout_failed",
    )
    second = case_repo.get_or_create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="api",
        rollout_id=12,
        cause_family="shared_dependency_failure",
        issue_type="rollout_failed",
        metadata_json={"dependency_target": "mysql"},
    )

    assert first.id == second.id
    assert second.cause_family == "shared_dependency_failure"
    assert second.metadata_json["dependency_target"] == "mysql"


def test_claim_work_item_transitions_oldest_pending_to_running(engine):
    case_repo = NamespaceCaseRepo(engine)
    work_repo = WorkItemRepo(engine)

    case = case_repo.create_namespace_case(cluster="c1", namespace="ns1")
    issue = case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="api",
        cause_family="other",
        issue_type="rollout_failed",
    )

    first = work_repo.enqueue_work_item(
        kind=WorkItemKind.CASE_RECHECK,
        namespace_case_id=case.id,
    )
    second = work_repo.enqueue_work_item(
        kind=WorkItemKind.ISSUE_INVESTIGATION,
        namespace_case_id=case.id,
        issue_id=issue.id,
    )
    third = work_repo.enqueue_work_item(
        kind=WorkItemKind.ISSUE_INVESTIGATION,
        namespace_case_id=case.id,
        issue_id=issue.id,
    )

    claimed = work_repo.claim_work_item(WorkItemKind.ISSUE_INVESTIGATION)

    assert claimed is not None
    assert claimed.id == second.id
    assert claimed.status == WorkItemStatus.RUNNING
    assert claimed.started_at is not None

    pending = work_repo.claim_work_item(WorkItemKind.CASE_NOTIFICATION)
    assert pending is None

    with work_repo.session() as s:
        stored_first = s.get(type(first), first.id)
        stored_second = s.get(type(second), second.id)
        stored_third = s.get(type(third), third.id)

    assert stored_first.status == WorkItemStatus.PENDING
    assert stored_second.status == WorkItemStatus.RUNNING
    assert stored_third.status == WorkItemStatus.PENDING


def test_list_active_issues_returns_only_active_and_investigating(engine):
    case_repo = NamespaceCaseRepo(engine)
    case = case_repo.create_namespace_case(cluster="c1", namespace="ns1")

    active = case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="api",
        cause_family="other",
        issue_type="rollout_failed",
    )
    investigating = case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.NAMESPACE,
        resource_kind="Namespace",
        resource_name="ns1",
        cause_family="shared_storage_failure",
        issue_type="namespace_storage_failure",
        status=IssueStatus.INVESTIGATING,
    )
    case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="worker",
        cause_family="other",
        issue_type="rollout_failed",
        status=IssueStatus.RESOLVED,
    )

    active_issues = case_repo.list_active_issues(case.id)

    assert [issue.id for issue in active_issues] == [active.id, investigating.id]


def test_get_case_overview_stats_counts_open_quieting_closed_cases(engine):
    case_repo = NamespaceCaseRepo(engine)

    open_case = case_repo.get_or_create_open_case("c1", "open-ns")
    quieting_case = case_repo.get_or_create_open_case("c1", "quieting-ns")
    closed_case = case_repo.get_or_create_open_case("c1", "closed-ns")

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
        namespace_case_id=quieting_case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="worker",
        cause_family="capacity_autoscale_pending",
        issue_type="unschedulable",
        status=IssueStatus.RESOLVED,
        metadata_json={"recovered_before_notification": True},
    )

    case_repo.update_case_status(quieting_case.id, NamespaceCaseStatus.QUIETING)
    case_repo.update_case_status(closed_case.id, NamespaceCaseStatus.CLOSED)

    stats = case_repo.get_case_overview_stats()

    assert stats["open_cases"] == 1
    assert stats["quieting_cases"] == 1
    assert stats["closed_cases"] == 1
    assert stats["active_child_issues"] == 1
    assert stats["recovered_without_slack"] == 1
    assert stats["cases_awaiting_notification_decision"] == 1
    assert stats["top_cause_family"] == "shared_dependency_failure"


def test_case_rows_do_not_mark_active_case_as_recovered_quietly(engine):
    case_repo = NamespaceCaseRepo(engine)
    case = case_repo.get_or_create_open_case("c1", "mixed-ns")
    case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="api",
        cause_family="shared_dependency_failure",
        issue_type="crashloop",
        status=IssueStatus.ACTIVE,
    )
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

    rows = case_repo.list_cases(include_closed=False)

    assert len(rows) == 1
    assert rows[0]["active_issue_count"] == 1
    assert rows[0]["recovered_quietly"] is False
    assert rows[0]["slack_state"] == "notification_pending_window"


def test_get_case_detail_returns_active_and_resolved_issues_and_observations(engine):
    case_repo = NamespaceCaseRepo(engine)
    case = case_repo.get_or_create_open_case("c1", "demo")
    active_issue = case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="api",
        cause_family="shared_dependency_failure",
        issue_type="crashloop",
        status=IssueStatus.ACTIVE,
    )
    resolved_issue = case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.NAMESPACE,
        resource_kind="Namespace",
        resource_name="demo",
        cause_family="shared_storage_failure",
        issue_type="correlated_namespace_issue",
        status=IssueStatus.RESOLVED,
    )
    case_repo.append_issue_observation(
        active_issue.id,
        source="watcher",
        signal_type="permanent_failure_detected",
        payload_json={"deployment": "api"},
    )

    detail = case_repo.get_case_detail(case.id)

    assert detail["case"].id == case.id
    assert [issue.id for issue in detail["active_issues"]] == [active_issue.id]
    assert [issue.id for issue in detail["resolved_issues"]] == [resolved_issue.id]
    assert len(detail["observations"]) == 1


def test_get_parent_case_for_rollout_returns_case_and_issue_context(engine):
    from project_fyr.db import RolloutRepo

    rollout_repo = RolloutRepo(engine, annotation_prefix="project-fyr.io")
    case_repo = NamespaceCaseRepo(engine)

    rollout = rollout_repo.create(
        cluster="c1",
        namespace="demo",
        deployment="api",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=utcnow(),
    )
    case = case_repo.get_or_create_open_case("c1", "demo")
    issue = case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="api",
        rollout_id=rollout.id,
        cause_family="app_config_or_secret",
        issue_type="config_error",
        status=IssueStatus.ACTIVE,
    )

    parent = case_repo.get_parent_case_for_rollout(rollout.id)

    assert parent is not None
    assert parent["case"].id == case.id
    assert parent["issue"].id == issue.id
