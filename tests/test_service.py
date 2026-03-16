from project_fyr import utcnow
from project_fyr.models import Analysis, RolloutStatus
from project_fyr.service import evaluate_deployment_phase, analyze_pod_failures, should_fail_early, PodFailureSignals
import pytest
from datetime import timedelta
from unittest.mock import MagicMock, patch

def test_evaluate_deployment_phase_stable():
    dep = MagicMock()
    dep.status.available_replicas = 3
    dep.spec.replicas = 3
    dep.status.conditions = []
    assert evaluate_deployment_phase(dep) == "STABLE"

def test_evaluate_deployment_phase_failed_progress():
    dep = MagicMock()
    dep.status.available_replicas = 0
    dep.status.availableReplicas = 0
    dep.spec.replicas = 3
    condition = MagicMock()
    condition.type = "Progressing"
    condition.status = "False"
    dep.status.conditions = [condition]
    assert evaluate_deployment_phase(dep) == "FAILED_PROGRESS"

def test_evaluate_deployment_phase_pending():
    dep = MagicMock()
    dep.status.available_replicas = 0
    dep.status.availableReplicas = 0
    dep.spec.replicas = 3
    condition = MagicMock()
    condition.type = "Available"
    condition.status = "False"
    dep.status.conditions = [condition]
    assert evaluate_deployment_phase(dep) == "PENDING"

def test_repo_create_rollout(repo):
    rollout = repo.create(
        cluster="test-cluster",
        namespace="default",
        deployment="web",
        generation=1,
        status=RolloutStatus.PENDING,
        started_at=utcnow()
    )
    assert rollout.id is not None
    assert rollout.deployment == "web"

def test_analyze_pod_failures():
    pod1 = MagicMock()
    pod1.metadata.name = "pod1"
    pod1.status.phase = "Running"
    pod1.status.container_statuses = []
    pod1.status.init_container_statuses = []
    pod1.status.conditions = []

    pod2 = MagicMock()
    pod2.metadata.name = "pod2"
    pod2.status.phase = "Running"
    cs = MagicMock()
    cs.state.waiting.reason = "CrashLoopBackOff"
    cs.state.waiting.message = "Back-off restarting failed container"
    pod2.status.container_statuses = [cs]
    pod2.status.init_container_statuses = []
    pod2.status.conditions = []

    pod3 = MagicMock()
    pod3.metadata.name = "pod3"
    pod3.status.phase = "Pending"
    pod3.status.container_statuses = []
    pod3.status.init_container_statuses = []
    # Add Unschedulable condition
    condition = MagicMock()
    condition.type = "PodScheduled"
    condition.status = "False"
    condition.reason = "Unschedulable"
    condition.message = "0/3 nodes are available: insufficient cpu"
    pod3.status.conditions = [condition]

    pods = [pod1, pod2, pod3]
    signals = analyze_pod_failures(pods)

    assert signals.total_pods == 3
    assert signals.crashloop_pods == 1
    assert signals.unschedulable_pods == 1


def test_analyze_pod_failures_preserves_full_unschedulable_message():
    pod = MagicMock()
    pod.metadata.name = "pod-unschedulable"
    pod.status.phase = "Pending"
    pod.status.container_statuses = []
    pod.status.init_container_statuses = []

    condition = MagicMock()
    condition.type = "PodScheduled"
    condition.status = "False"
    condition.reason = "Unschedulable"
    condition.message = (
        "0/61 nodes are available: 1 Insufficient memory, 4 Too many pods, "
        "57 node(s) didn't match Pod's node affinity/selector."
    )
    pod.status.conditions = [condition]

    signals = analyze_pod_failures([pod])

    assert signals.failure_reasons == [
        "pod-unschedulable: Unschedulable - "
        "0/61 nodes are available: 1 Insufficient memory, 4 Too many pods, "
        "57 node(s) didn't match Pod's node affinity/selector."
    ]


def test_analyze_pod_failures_config_errors():
    """Test detection of CreateContainerConfigError (missing Secret/ConfigMap)."""
    pod = MagicMock()
    pod.metadata.name = "config-error-pod"
    pod.status.phase = "Pending"
    pod.status.init_container_statuses = []
    pod.status.conditions = []

    cs = MagicMock()
    cs.state.waiting.reason = "CreateContainerConfigError"
    cs.state.waiting.message = "secret 'db-credentials' not found"
    pod.status.container_statuses = [cs]

    signals = analyze_pod_failures([pod])

    assert signals.config_error_pods == 1
    assert signals.total_failing == 1
    assert len(signals.failure_reasons) == 1
    assert "CreateContainerConfigError" in signals.failure_reasons[0]


def test_analyze_pod_failures_image_pull():
    """Test detection of various image pull errors."""
    pods = []

    for reason in ["ImagePullBackOff", "ErrImagePull", "InvalidImageName"]:
        pod = MagicMock()
        pod.metadata.name = f"pod-{reason}"
        pod.status.phase = "Pending"
        pod.status.init_container_statuses = []
        pod.status.conditions = []
        cs = MagicMock()
        cs.state.waiting.reason = reason
        cs.state.waiting.message = "test message"
        pod.status.container_statuses = [cs]
        pods.append(pod)

    signals = analyze_pod_failures(pods)

    assert signals.image_pull_pods == 3
    assert signals.total_failing == 3


def test_analyze_pod_failures_init_container():
    """Test detection of init container failures."""
    pod = MagicMock()
    pod.metadata.name = "init-crash-pod"
    pod.status.phase = "Pending"
    pod.status.container_statuses = []
    pod.status.conditions = []

    # Init container in CrashLoopBackOff
    init_cs = MagicMock()
    init_cs.state.waiting.reason = "CrashLoopBackOff"
    init_cs.state.waiting.message = "Init container failed"
    pod.status.init_container_statuses = [init_cs]

    signals = analyze_pod_failures([pod])

    assert signals.crashloop_pods == 1
    assert signals.total_failing == 1


def test_should_fail_early():
    # Crashloop is a permanent failure, should fail early
    signals = PodFailureSignals(total_pods=5, crashloop_pods=3, permanent_failure_pods=3)
    assert should_fail_early(signals) is True

    # One crashloop is still a permanent failure
    signals = PodFailureSignals(total_pods=5, crashloop_pods=1, permanent_failure_pods=1)
    assert should_fail_early(signals) is True


def test_should_fail_early_transient_vs_permanent():
    """Test that transient failures don't trigger early failure."""
    # Transient-only failures (image pull, may be QPS rate limit) - should NOT fail early
    signals = PodFailureSignals(
        total_pods=5,
        image_pull_pods=3,
        transient_failure_pods=3,
        permanent_failure_pods=0,
    )
    assert signals.has_only_transient_failures is True
    assert should_fail_early(signals) is False

    # QPS exceeded pattern - definitely transient
    signals = PodFailureSignals(
        total_pods=5,
        image_pull_pods=3,
        transient_failure_pods=3,
        qps_exceeded_pods=3,
        permanent_failure_pods=0,
    )
    assert signals.is_likely_qps_issue is True
    assert should_fail_early(signals) is False

    # Mixed transient + permanent - should fail early due to permanent
    signals = PodFailureSignals(
        total_pods=5,
        image_pull_pods=2,
        config_error_pods=1,
        transient_failure_pods=2,
        permanent_failure_pods=1,
    )
    assert signals.has_only_transient_failures is False
    assert should_fail_early(signals) is True


def test_should_fail_early_combined_failures():
    """Test that different permanent failure types trigger early failure."""
    # Config error is permanent - should fail early
    signals = PodFailureSignals(
        total_pods=5,
        config_error_pods=1,
        permanent_failure_pods=1,
    )
    assert should_fail_early(signals) is True

    # Unschedulable is permanent - should fail early
    signals = PodFailureSignals(
        total_pods=5,
        unschedulable_pods=2,
        permanent_failure_pods=2,
    )
    assert should_fail_early(signals) is True

    # Container error is permanent - should fail early
    signals = PodFailureSignals(
        total_pods=5,
        container_error_pods=1,
        permanent_failure_pods=1,
    )
    assert should_fail_early(signals) is True


def test_handle_deployment_event_with_watch_all_namespaces():
    """Test that watch_all_namespaces=True monitors all deployments."""
    from project_fyr.service import handle_deployment_event
    from project_fyr.config import Settings

    dep = MagicMock()
    dep.metadata.namespace = "test-ns"
    dep.metadata.name = "test-app"
    dep.metadata.generation = 1
    dep.metadata.labels = {}  # No labels
    dep.metadata.annotations = {}
    dep.status.conditions = []
    dep.status.available_replicas = 0
    dep.status.availableReplicas = 0
    dep.spec.replicas = 1

    repo = MagicMock()
    repo.get_by_key.return_value = None

    config = Settings(watch_all_namespaces=True)

    # Should create rollout even without labels
    handle_deployment_event(dep, "ADDED", repo, "test-cluster", namespace_metadata={}, config=config)

    repo.create.assert_called_once()


def test_handle_deployment_event_with_namespace_annotation():
    """Test that namespace-level project-fyr/enabled annotation works."""
    from project_fyr.service import handle_deployment_event
    from project_fyr.config import Settings

    dep = MagicMock()
    dep.metadata.namespace = "test-ns"
    dep.metadata.name = "test-app"
    dep.metadata.generation = 1
    dep.metadata.labels = {}  # No deployment label
    dep.metadata.annotations = {}
    dep.status.conditions = []
    dep.status.available_replicas = 0
    dep.status.availableReplicas = 0
    dep.spec.replicas = 1

    repo = MagicMock()
    repo.get_by_key.return_value = None

    # Namespace has the annotation
    ns_meta = {
        "metadata_json": {
            "project-fyr.io/enabled": "true"
        }
    }

    config = Settings(namespace_label_enabled=True, watch_all_namespaces=False)

    # Should create rollout because namespace has annotation
    handle_deployment_event(dep, "ADDED", repo, "test-cluster", namespace_metadata=ns_meta, config=config)

    repo.create.assert_called_once()


def test_handle_deployment_event_requires_opt_in():
    """Test that deployments without labels/annotations are ignored in default mode."""
    from project_fyr.service import handle_deployment_event
    from project_fyr.config import Settings

    dep = MagicMock()
    dep.metadata.namespace = "test-ns"
    dep.metadata.name = "test-app"
    dep.metadata.generation = 1
    dep.metadata.labels = {}  # No labels
    dep.metadata.annotations = {}

    repo = MagicMock()

    # No namespace annotation either
    ns_meta = {"metadata_json": {}}

    config = Settings(namespace_label_enabled=True, watch_all_namespaces=False)

    # Should NOT create rollout
    handle_deployment_event(dep, "ADDED", repo, "test-cluster", namespace_metadata=ns_meta, config=config)

    repo.create.assert_not_called()
    repo.get_by_key.assert_not_called()


def test_handle_deployment_event_with_deployment_label():
    """Test that deployment label still works."""
    from project_fyr.service import handle_deployment_event
    from project_fyr.config import Settings

    dep = MagicMock()
    dep.metadata.namespace = "test-ns"
    dep.metadata.name = "test-app"
    dep.metadata.generation = 1
    dep.metadata.labels = {"project-fyr.io/enabled": "true"}  # Deployment has label
    dep.metadata.annotations = {}
    dep.status.conditions = []
    dep.status.available_replicas = 0
    dep.status.availableReplicas = 0
    dep.spec.replicas = 1

    repo = MagicMock()
    repo.get_by_key.return_value = None

    config = Settings(namespace_label_enabled=False, watch_all_namespaces=False)

    # Should create rollout because deployment has label
    handle_deployment_event(dep, "ADDED", repo, "test-cluster", namespace_metadata={}, config=config)

    repo.create.assert_called_once()


def test_rollout_transient_policy_config_parses_cluster_settings():
    from project_fyr.config import Settings

    config = Settings(
        rollout_transient_classification_mode="allowlist",
        rollout_transient_investigation_mode="delayed",
        rollout_transient_slack_mode="actionable_only",
        rollout_transient_persistence_window_seconds=123,
        rollout_transient_failure_types="unschedulable, transient_image_pull",
        rollout_transient_observation_patterns="connection refused, mysql not ready",
        rollout_immediate_actionable_failure_types="crashloop",
    )

    assert config.rollout_transient_classification_mode == "allowlist"
    assert config.rollout_transient_investigation_mode == "delayed"
    assert config.rollout_transient_slack_mode == "actionable_only"
    assert config.rollout_transient_persistence_window_seconds == 123
    assert config.rollout_transient_failure_types == ["unschedulable", "transient_image_pull"]
    assert config.rollout_transient_observation_patterns == ["connection refused", "mysql not ready"]
    assert config.rollout_immediate_actionable_failure_types == ["crashloop"]


def test_rollout_transient_policy_config_parses_env_csv_settings(monkeypatch):
    from project_fyr.config import Settings

    monkeypatch.setenv(
        "PROJECT_FYR_ROLLOUT_TRANSIENT_FAILURE_TYPES",
        "unschedulable, transient_image_pull",
    )
    monkeypatch.setenv(
        "PROJECT_FYR_ROLLOUT_TRANSIENT_OBSERVATION_PATTERNS",
        "connection refused, mysql not ready",
    )
    monkeypatch.setenv(
        "PROJECT_FYR_ROLLOUT_IMMEDIATE_ACTIONABLE_FAILURE_TYPES",
        "crashloop",
    )

    config = Settings()

    assert config.rollout_transient_failure_types == ["unschedulable", "transient_image_pull"]
    assert config.rollout_transient_observation_patterns == ["connection refused", "mysql not ready"]
    assert config.rollout_immediate_actionable_failure_types == ["crashloop"]


def test_reconcile_loop_survives_transient_repo_failure():
    """Reconcile loop should continue after transient DB/repo errors."""
    from project_fyr.service import WatcherService

    svc = WatcherService.__new__(WatcherService)
    svc._config = MagicMock(rollout_timeout_seconds=60)
    svc._repo = MagicMock()
    svc._repo.list_active.side_effect = [Exception("db down"), []]

    with patch("project_fyr.service.client.AppsV1Api", return_value=MagicMock()), \
         patch("project_fyr.service.client.CoreV1Api", return_value=MagicMock()), \
         patch("project_fyr.service.time.sleep", side_effect=[None, RuntimeError("stop-loop")]):
        with pytest.raises(RuntimeError, match="stop-loop"):
            svc._reconcile_loop("dev-cluster")

    assert svc._repo.list_active.call_count >= 2


def test_analysis_worker_loop_survives_transient_processing_failure():
    """Worker loop should continue when one processing pass fails."""
    from project_fyr.service import AnalysisWorker

    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker._process_rollouts = MagicMock(side_effect=[Exception("db down"), None])
    worker._process_alert_jobs = MagicMock()
    worker._process_namespace_jobs = MagicMock()

    with patch("project_fyr.service.time.sleep", side_effect=[None, RuntimeError("stop-loop")]):
        with pytest.raises(RuntimeError, match="stop-loop"):
            worker.loop()

    assert worker._process_rollouts.call_count >= 2


def test_reconcile_loop_discards_rollout_when_deployment_missing():
    """404 on deployment read should discard stale active rollout and continue."""
    from project_fyr.service import WatcherService
    from kubernetes.client.rest import ApiException

    rollout = MagicMock()
    rollout.id = 42
    rollout.namespace = "n1"
    rollout.deployment = "d1"

    apps_api = MagicMock()
    apps_api.read_namespaced_deployment.side_effect = ApiException(status=404, reason="Not Found")

    svc = WatcherService.__new__(WatcherService)
    svc._config = MagicMock(rollout_timeout_seconds=60)
    svc._repo = MagicMock()
    svc._repo.list_active.side_effect = [[rollout], []]

    with patch("project_fyr.service.client.AppsV1Api", return_value=apps_api), \
         patch("project_fyr.service.client.CoreV1Api", return_value=MagicMock()), \
         patch("project_fyr.service.time.sleep", side_effect=[None, RuntimeError("stop-loop")]):
        with pytest.raises(RuntimeError, match="stop-loop"):
            svc._reconcile_loop("dev-cluster")

    svc._repo.discard_analysis.assert_called_once()
    args, kwargs = svc._repo.discard_analysis.call_args
    assert args[0] == rollout.id
    assert kwargs["mark_success"] is True


def test_process_rollouts_discards_when_namespace_deleted_during_processing():
    """Namespace can disappear after batch pre-check but before investigation starts."""
    from project_fyr.service import AnalysisWorker
    from kubernetes.client.rest import ApiException

    rollout = MagicMock()
    rollout.id = 43927
    rollout.namespace = "ephemeral-ns"
    rollout.deployment = "test-app"

    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker._cluster = "ci-cluster"
    worker._repo = MagicMock()
    worker._repo.list_failed.return_value = [rollout]
    worker._core_v1 = MagicMock()
    # Batch check says namespace exists; per-rollout re-check says namespace is now gone.
    worker._core_v1.read_namespace.side_effect = [None, ApiException(status=404, reason="Not Found")]
    worker._is_deployment_healthy_now = MagicMock(return_value=False)
    worker._investigate_rollout = MagicMock()

    worker._process_rollouts()

    worker._repo.discard_analysis.assert_called_once()
    args, kwargs = worker._repo.discard_analysis.call_args
    assert args[0] == rollout.id
    assert "Namespace deleted" in kwargs["reason"]
    worker._investigate_rollout.assert_not_called()


def test_process_rollouts_discards_when_namespace_is_terminating():
    """Rollouts in terminating namespaces should be discarded, not analyzed."""
    from project_fyr.service import AnalysisWorker

    rollout = MagicMock()
    rollout.id = 46433
    rollout.namespace = "terminating-ns"
    rollout.deployment = "test-app"

    namespace_obj = MagicMock()
    namespace_obj.status.phase = "Terminating"

    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker._cluster = "ci-cluster"
    worker._repo = MagicMock()
    worker._repo.list_failed.return_value = [rollout]
    worker._core_v1 = MagicMock()
    worker._core_v1.read_namespace.return_value = namespace_obj
    worker._is_deployment_healthy_now = MagicMock(return_value=False)
    worker._investigate_rollout = MagicMock()

    worker._process_rollouts()

    worker._repo.discard_analysis.assert_called_once()
    _, kwargs = worker._repo.discard_analysis.call_args
    assert "terminating" in kwargs["reason"].lower()
    worker._investigate_rollout.assert_not_called()


def test_process_rollouts_defers_healthy_rollout_within_grace_window():
    """Healthy pre-check within grace window should defer (not investigate, not discard)."""
    from project_fyr.service import AnalysisWorker

    rollout = MagicMock()
    rollout.id = 51929
    rollout.namespace = "admin-ns"
    rollout.deployment = "web"
    rollout.failed_at = utcnow() - timedelta(seconds=30)
    rollout.started_at = utcnow() - timedelta(seconds=40)

    namespace_obj = MagicMock()
    namespace_obj.status.phase = "Active"

    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker._cluster = "ci-cluster"
    worker._config = MagicMock(analysis_healthy_recheck_delay_seconds=90)
    worker._repo = MagicMock()
    worker._repo.list_failed.return_value = [rollout]
    worker._core_v1 = MagicMock()
    worker._core_v1.read_namespace.return_value = namespace_obj
    worker._is_deployment_healthy_now = MagicMock(return_value=True)
    worker._investigate_rollout = MagicMock()

    worker._process_rollouts()

    worker._repo.discard_analysis.assert_not_called()
    worker._investigate_rollout.assert_not_called()


def test_process_alert_jobs_ignores_pending_namespace_jobs(engine):
    """Alert processing must not consume namespace investigation jobs."""
    from project_fyr.db import AlertRepo, InvestigationJob
    from project_fyr.service import AnalysisWorker

    alert_repo = AlertRepo(engine)
    with alert_repo.session() as s:
        s.add(
            InvestigationJob(
                type="namespace",
                status="pending",
                namespace_incident_id=123,
            )
        )
        s.commit()

    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker._alert_repo = alert_repo
    worker._investigate_alert_batch = MagicMock()

    worker._process_alert_jobs()

    worker._investigate_alert_batch.assert_not_called()


def test_process_rollouts_discards_healthy_rollout_after_grace_window():
    """Healthy pre-check after grace window should discard analysis as recovered."""
    from project_fyr.service import AnalysisWorker

    rollout = MagicMock()
    rollout.id = 51930
    rollout.namespace = "admin-ns"
    rollout.deployment = "api"
    rollout.failed_at = utcnow() - timedelta(seconds=240)
    rollout.started_at = utcnow() - timedelta(seconds=260)

    namespace_obj = MagicMock()
    namespace_obj.status.phase = "Active"

    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker._cluster = "ci-cluster"
    worker._config = MagicMock(analysis_healthy_recheck_delay_seconds=90)
    worker._repo = MagicMock()
    worker._repo.list_failed.return_value = [rollout]
    worker._core_v1 = MagicMock()
    worker._core_v1.read_namespace.return_value = namespace_obj
    worker._is_deployment_healthy_now = MagicMock(return_value=True)
    worker._investigate_rollout = MagicMock()

    worker._process_rollouts()

    worker._repo.discard_analysis.assert_called_once()
    _, kwargs = worker._repo.discard_analysis.call_args
    assert "Recovered before analysis" in kwargs["reason"]
    worker._investigate_rollout.assert_not_called()


def test_process_rollouts_delays_known_transient_candidate_when_configured():
    from project_fyr.config import Settings
    from project_fyr.service import AnalysisWorker

    rollout = MagicMock()
    rollout.id = 51931
    rollout.namespace = "admin-ns"
    rollout.deployment = "api"
    rollout.failed_at = utcnow() - timedelta(seconds=60)
    rollout.started_at = utcnow() - timedelta(seconds=80)
    rollout.metadata_json = {
        "trigger_context": {
            "failure_type": "unschedulable",
            "observed_failures": ["0/3 nodes available"],
        }
    }

    namespace_obj = MagicMock()
    namespace_obj.status.phase = "Active"

    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker._cluster = "ci-cluster"
    worker._config = Settings(
        analysis_healthy_recheck_delay_seconds=90,
        rollout_transient_investigation_mode="delayed",
        rollout_transient_persistence_window_seconds=300,
        rollout_transient_failure_types="unschedulable",
    )
    worker._repo = MagicMock()
    worker._repo.list_failed.return_value = [rollout]
    worker._core_v1 = MagicMock()
    worker._core_v1.read_namespace.return_value = namespace_obj
    worker._is_deployment_healthy_now = MagicMock(return_value=False)
    worker._investigate_rollout = MagicMock()

    worker._process_rollouts()

    worker._investigate_rollout.assert_not_called()


def test_classify_rollout_notification_decision_marks_transient_by_failure_type():
    from project_fyr.config import Settings
    from project_fyr.service import classify_rollout_notification_decision

    config = Settings(
        rollout_transient_failure_types="unschedulable,transient_image_pull",
        rollout_immediate_actionable_failure_types="",
        rollout_transient_observation_patterns="",
    )
    analysis = Analysis(
        summary="test",
        likely_cause="cluster added nodes after brief shortage",
        recommended_steps=["wait"],
    )

    decision = classify_rollout_notification_decision(
        trigger_context={"failure_type": "unschedulable", "observed_failures": ["0/3 nodes available"]},
        analysis=analysis,
        config=config,
    )

    assert decision.classification == "transient"
    assert decision.notify_immediately is False
    assert "failure_type" in decision.reason


def test_classify_rollout_notification_decision_marks_transient_by_pattern():
    from project_fyr.config import Settings
    from project_fyr.service import classify_rollout_notification_decision

    config = Settings(
        rollout_transient_failure_types="",
        rollout_immediate_actionable_failure_types="",
        rollout_transient_observation_patterns="mysql not ready,connection refused",
    )
    analysis = Analysis(
        summary="migration startup race",
        likely_cause="Migration job failed because mysql not ready and connection refused during startup",
        recommended_steps=["retry"],
    )

    decision = classify_rollout_notification_decision(
        trigger_context={"failure_type": "crashloop", "observed_failures": ["Back-off restarting failed container"]},
        analysis=analysis,
        config=config,
    )

    assert decision.classification == "transient"
    assert decision.notify_immediately is False
    assert "pattern" in decision.reason


def test_classify_rollout_notification_decision_marks_immediate_actionable():
    from project_fyr.config import Settings
    from project_fyr.service import classify_rollout_notification_decision

    config = Settings(
        rollout_transient_failure_types="unschedulable",
        rollout_immediate_actionable_failure_types="crashloop",
        rollout_transient_observation_patterns="",
    )
    analysis = Analysis(
        summary="persistent crashloop",
        likely_cause="Application keeps crashing on startup",
        recommended_steps=["inspect logs"],
    )

    decision = classify_rollout_notification_decision(
        trigger_context={"failure_type": "crashloop", "observed_failures": ["CrashLoopBackOff"]},
        analysis=analysis,
        config=config,
    )

    assert decision.classification == "immediate_actionable"
    assert decision.notify_immediately is True


def test_classify_rollout_notification_decision_marks_unknown_quiet_first():
    from project_fyr.config import Settings
    from project_fyr.service import classify_rollout_notification_decision

    config = Settings(
        rollout_transient_failure_types="unschedulable",
        rollout_immediate_actionable_failure_types="",
        rollout_transient_observation_patterns="mysql not ready",
    )
    analysis = Analysis(
        summary="generic failure",
        likely_cause="Deployment failed for an uncategorized reason",
        recommended_steps=["inspect deployment"],
    )

    decision = classify_rollout_notification_decision(
        trigger_context={"failure_type": "config_error", "observed_failures": ["CreateContainerConfigError"]},
        analysis=analysis,
        config=config,
    )

    assert decision.classification == "unknown"
    assert decision.notify_immediately is False


def test_investigate_rollout_defers_notification_by_default_after_persisting_analysis():
    from project_fyr.config import Settings
    from project_fyr.service import AnalysisWorker

    rollout = MagicMock()
    rollout.id = 7001
    rollout.namespace = "demo"
    rollout.deployment = "api"
    rollout.generation = 3
    rollout.team = "platform"
    rollout.slack_channel = "#deployments"
    rollout.metadata_json = {
        "trigger_context": {
            "failure_type": "config_error",
            "observed_failures": ["CreateContainerConfigError"],
        }
    }

    config = Settings(
        k8s_cluster_name="ci-cluster",
        langchain_model_name="gpt-4o-mini",
        rollout_transient_persistence_window_seconds=300,
        rollout_transient_failure_types="unschedulable",
        rollout_immediate_actionable_failure_types="",
        rollout_transient_observation_patterns="",
    )

    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker._config = config
    worker._agent = MagicMock()
    worker._repo = MagicMock()
    worker._slack = MagicMock()
    worker._build_slack_routing_metadata = MagicMock(return_value={})
    worker._agent.investigate.return_value = Analysis(
        summary="analysis",
        likely_cause="uncategorized startup failure",
        recommended_steps=["inspect"],
    )

    events: list[str] = []
    worker._repo.append_analysis.side_effect = lambda *args, **kwargs: events.append("append")
    worker._repo.set_rollout_notification_state.side_effect = lambda *args, **kwargs: events.append(f"state:{kwargs['state']}")

    with patch("project_fyr.service.triage_failure", return_value=MagicMock(team="platform", reason="test")):
        worker._investigate_rollout(rollout)

    worker._slack.send_analysis.assert_not_called()
    worker._repo.append_analysis.assert_called_once()
    worker._repo.set_rollout_notification_state.assert_called_once()
    _, kwargs = worker._repo.set_rollout_notification_state.call_args
    assert kwargs["state"] == "deferred"
    assert kwargs["classification"] == "unknown"
    assert kwargs["deferred_until"] is not None
    assert events == ["append", "state:deferred"]


def test_investigate_rollout_sends_immediate_actionable_after_persisting_analysis():
    from project_fyr.config import Settings
    from project_fyr.service import AnalysisWorker

    rollout = MagicMock()
    rollout.id = 7002
    rollout.namespace = "demo"
    rollout.deployment = "api"
    rollout.generation = 4
    rollout.team = "platform"
    rollout.slack_channel = "#deployments"
    rollout.metadata_json = {
        "trigger_context": {
            "failure_type": "crashloop",
            "observed_failures": ["CrashLoopBackOff"],
        }
    }

    config = Settings(
        k8s_cluster_name="ci-cluster",
        langchain_model_name="gpt-4o-mini",
        rollout_transient_failure_types="unschedulable",
        rollout_immediate_actionable_failure_types="crashloop",
        rollout_transient_observation_patterns="",
    )

    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker._config = config
    worker._agent = MagicMock()
    worker._repo = MagicMock()
    worker._slack = MagicMock()
    worker._build_slack_routing_metadata = MagicMock(return_value={})
    worker._agent.investigate.return_value = Analysis(
        summary="analysis",
        likely_cause="persistent crash loop",
        recommended_steps=["inspect logs"],
    )
    worker._slack.send_analysis.return_value = True

    events: list[str] = []
    worker._repo.append_analysis.side_effect = lambda *args, **kwargs: events.append("append")
    worker._slack.send_analysis.side_effect = lambda **kwargs: events.append("slack") or True
    worker._repo.set_rollout_notification_state.side_effect = lambda *args, **kwargs: events.append(f"state:{kwargs['state']}")

    with patch("project_fyr.service.triage_failure", return_value=MagicMock(team="platform", reason="test")):
        worker._investigate_rollout(rollout)

    worker._repo.append_analysis.assert_called_once()
    worker._slack.send_analysis.assert_called_once()
    assert events.index("append") < events.index("slack")
    _, kwargs = worker._repo.set_rollout_notification_state.call_args
    assert kwargs["state"] == "sent"


def test_process_deferred_rollout_notifications_suppresses_recovered_rollout():
    from project_fyr.config import Settings
    from project_fyr.models import NotifyStatus
    from project_fyr.service import AnalysisWorker

    rollout = MagicMock()
    rollout.id = 7101
    rollout.namespace = "demo"
    rollout.deployment = "api"
    rollout.generation = 5
    rollout.team = "platform"
    rollout.slack_channel = "#deployments"
    rollout.metadata_json = {
        "notification_state": "deferred",
        "notification_classification": "unknown",
        "notification_decision_reason": "quiet-first policy",
    }

    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker._config = Settings(k8s_cluster_name="ci-cluster", langchain_model_name="gpt-4o-mini")
    worker._repo = MagicMock()
    worker._repo.list_deferred_rollout_notifications_due.return_value = [rollout]
    worker._slack = MagicMock()
    worker._build_slack_routing_metadata = MagicMock(return_value={})
    worker._is_deployment_healthy_now = MagicMock(return_value=True)

    worker._process_deferred_rollout_notifications()

    worker._slack.send_analysis.assert_not_called()
    worker._repo.set_rollout_notification_state.assert_called_once()
    _, kwargs = worker._repo.set_rollout_notification_state.call_args
    assert kwargs["state"] == "suppressed"
    assert kwargs["notify_status"] == NotifyStatus.SENT
    assert kwargs["recovered_before_notification"] is True


def test_process_deferred_rollout_notifications_sends_when_still_actionable():
    from project_fyr.config import Settings
    from project_fyr.models import Analysis, NotifyStatus
    from project_fyr.service import AnalysisWorker

    rollout = MagicMock()
    rollout.id = 7102
    rollout.namespace = "demo"
    rollout.deployment = "api"
    rollout.generation = 6
    rollout.team = "platform"
    rollout.slack_channel = "#deployments"
    rollout.analysis_id = 55
    rollout.metadata_json = {
        "notification_state": "deferred",
        "notification_classification": "unknown",
        "notification_decision_reason": "quiet-first policy",
    }

    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker._config = Settings(k8s_cluster_name="ci-cluster", langchain_model_name="gpt-4o-mini")
    worker._repo = MagicMock()
    worker._repo.list_deferred_rollout_notifications_due.return_value = [rollout]
    worker._repo.get_analysis.return_value = MagicMock(
        analysis=Analysis(
            summary="analysis",
            likely_cause="still failing",
            recommended_steps=["check logs"],
        ).model_dump(mode="json")
    )
    worker._slack = MagicMock()
    worker._slack.send_analysis.return_value = True
    worker._build_slack_routing_metadata = MagicMock(return_value={})
    worker._is_deployment_healthy_now = MagicMock(return_value=False)

    worker._process_deferred_rollout_notifications()

    worker._slack.send_analysis.assert_called_once()
    _, kwargs = worker._repo.set_rollout_notification_state.call_args
    assert kwargs["state"] == "sent"
    assert kwargs["notify_status"] == NotifyStatus.SENT


def test_namespace_case_ingestion_records_rollout_failure(engine, repo):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from project_fyr.cases import CaseIngestionService
    from project_fyr.db import IssueObservationRecord, IssueRecord, NamespaceCaseRecord, WorkItemRecord
    from project_fyr.models import IssueScope, RolloutStatus, WorkItemKind

    rollout = repo.create(
        cluster="ci-cluster",
        namespace="demo",
        deployment="api",
        generation=1,
        status=RolloutStatus.FAILED,
        started_at=utcnow() - timedelta(minutes=3),
        failed_at=utcnow() - timedelta(minutes=2),
        slack_channel="#deployments",
        team="platform",
        metadata_json={
            "trigger_context": {
                "trigger_reason": "permanent_failure_detected",
                "failure_type": "unschedulable",
                "observed_failures": ["0/3 nodes are available: insufficient cpu"],
            }
        },
    )

    ingestor = CaseIngestionService(engine)
    case, issue = ingestor.record_rollout_failure(
        rollout=rollout,
        trigger_reason="permanent_failure_detected",
        trigger_context=(rollout.metadata_json or {}).get("trigger_context"),
    )

    with Session(engine) as s:
        stored_case = s.scalar(select(NamespaceCaseRecord).where(NamespaceCaseRecord.id == case.id))
        stored_issue = s.scalar(select(IssueRecord).where(IssueRecord.id == issue.id))
        observations = list(s.scalars(select(IssueObservationRecord).where(IssueObservationRecord.issue_id == issue.id)))
        work_items = list(
            s.scalars(
                select(WorkItemRecord)
                .where(WorkItemRecord.namespace_case_id == case.id)
                .order_by(WorkItemRecord.id.asc())
            )
        )

    assert stored_case is not None
    assert stored_case.namespace == "demo"
    assert stored_case.slack_channel == "#deployments"
    assert stored_issue is not None
    assert stored_issue.namespace_case_id == case.id
    assert stored_issue.scope == IssueScope.DEPLOYMENT
    assert stored_issue.resource_kind == "Deployment"
    assert stored_issue.resource_name == "api"
    assert stored_issue.rollout_id == rollout.id
    assert stored_issue.cause_family == "capacity_autoscale_pending"
    assert len(observations) == 1
    assert observations[0].signal_type == "permanent_failure_detected"
    assert observations[0].payload_json["trigger_context"]["failure_type"] == "unschedulable"
    assert [item.kind for item in work_items] == [
        WorkItemKind.ISSUE_INVESTIGATION,
        WorkItemKind.CASE_RECHECK,
    ]


def test_reconcile_rollout_routes_failed_signal_into_namespace_case_ingestion(repo):
    from project_fyr.service import reconcile_rollout

    dep = MagicMock()
    dep.metadata.namespace = "demo"
    dep.metadata.name = "api"
    dep.status.available_replicas = 0
    dep.status.availableReplicas = 0
    dep.spec.replicas = 1
    condition = MagicMock()
    condition.type = "Progressing"
    condition.status = "False"
    dep.status.conditions = [condition]

    rollout = repo.create(
        cluster="ci-cluster",
        namespace="demo",
        deployment="api",
        generation=1,
        status=RolloutStatus.ROLLING_OUT,
        started_at=utcnow() - timedelta(minutes=4),
    )
    ingestor = MagicMock()

    reconcile_rollout(
        dep,
        rollout,
        utcnow(),
        repo,
        timedelta(minutes=10),
        case_ingestor=ingestor,
    )

    ingestor.record_rollout_failure.assert_called_once()
    _, kwargs = ingestor.record_rollout_failure.call_args
    assert kwargs["rollout"].id == rollout.id
    assert kwargs["trigger_reason"] == "deployment_progress_failed"
    assert kwargs["trigger_context"]["failure_type"] == "failed_progress"


def test_terminating_stuck_case_routes_into_namespace_case(engine):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from project_fyr.cases import CaseIngestionService
    from project_fyr.db import IssueObservationRecord, IssueRecord, NamespaceCaseRecord, WorkItemRecord
    from project_fyr.models import IssueScope, WorkItemKind
    from project_fyr.service import WatcherService

    service = WatcherService.__new__(WatcherService)
    service._engine = engine
    service._config = MagicMock(
        namespace_terminating_threshold_minutes=5,
        max_investigations_per_namespace_per_hour=10,
        max_investigations_per_cluster_per_hour=40,
    )
    service._case_ingestor = CaseIngestionService(engine)
    service._check_rate_limits = MagicMock(return_value=True)

    namespace = MagicMock()
    namespace.metadata.deletion_timestamp = utcnow() - timedelta(minutes=12)
    namespace.metadata.finalizers = ["kubernetes"]

    service._check_terminating_stuck(
        "ci-cluster",
        "demo",
        namespace,
        team="platform",
        slack_channel="#deployments",
    )

    with Session(engine) as s:
        stored_case = s.scalar(select(NamespaceCaseRecord))
        stored_issue = s.scalar(select(IssueRecord))
        observations = list(s.scalars(select(IssueObservationRecord)))
        work_items = list(s.scalars(select(WorkItemRecord).order_by(WorkItemRecord.id.asc())))

    assert stored_case is not None
    assert stored_case.namespace == "demo"
    assert stored_case.slack_channel == "#deployments"
    assert stored_issue is not None
    assert stored_issue.scope == IssueScope.NAMESPACE
    assert stored_issue.resource_kind == "Namespace"
    assert stored_issue.resource_name == "demo"
    assert stored_issue.issue_type == "terminating_stuck"
    assert len(observations) == 1
    assert observations[0].signal_type == "terminating_stuck"
    assert observations[0].payload_json["metadata"]["finalizers"] == ["kubernetes"]
    assert [item.kind for item in work_items] == [
        WorkItemKind.ISSUE_INVESTIGATION,
        WorkItemKind.CASE_RECHECK,
    ]


def test_issue_investigation_worker_persists_issue_analysis_without_slack(engine, repo):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from project_fyr.cases import CaseIngestionService
    from project_fyr.db import IssueRecord, WorkItemRecord
    from project_fyr.models import Analysis, IssueStatus, RolloutStatus, WorkItemKind, WorkItemStatus
    from project_fyr.service import AnalysisWorker

    rollout = repo.create(
        cluster="ci-cluster",
        namespace="demo",
        deployment="api",
        generation=2,
        status=RolloutStatus.FAILED,
        started_at=utcnow() - timedelta(minutes=6),
        failed_at=utcnow() - timedelta(minutes=5),
        metadata_json={
            "trigger_context": {
                "trigger_reason": "permanent_failure_detected",
                "failure_type": "crashloop",
                "observed_failures": ["Back-off restarting failed container"],
            }
        },
    )

    ingestor = CaseIngestionService(engine)
    _, issue = ingestor.record_rollout_failure(
        rollout=rollout,
        trigger_reason="permanent_failure_detected",
        trigger_context=(rollout.metadata_json or {}).get("trigger_context"),
    )

    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker._repo = repo
    worker._case_repo = None
    worker._work_item_repo = None
    worker._cluster = "ci-cluster"
    worker._config = MagicMock(langchain_model_name="gpt-4o-mini")
    worker._agent = MagicMock()
    worker._agent.investigate.return_value = Analysis(
        summary="api restart loop",
        likely_cause="MySQL dependency was not ready and connection refused during startup",
        recommended_steps=["check mysql readiness"],
    )
    worker._slack = MagicMock()
    worker._is_deployment_healthy_now = MagicMock(return_value=False)

    worker._process_issue_work_items()

    with Session(engine) as s:
        stored_issue = s.scalar(select(IssueRecord).where(IssueRecord.id == issue.id))
        work_items = list(
            s.scalars(select(WorkItemRecord).order_by(WorkItemRecord.id.asc()))
        )

    assert stored_issue is not None
    assert stored_issue.analysis_id is not None
    assert stored_issue.status == IssueStatus.ACTIVE
    assert stored_issue.cause_family == "shared_dependency_failure"
    worker._is_deployment_healthy_now.assert_called_once_with("api", "demo")
    worker._slack.send_analysis.assert_not_called()
    assert any(
        item.kind == WorkItemKind.ISSUE_INVESTIGATION and item.status == WorkItemStatus.COMPLETED
        for item in work_items
    )
    assert sum(1 for item in work_items if item.kind == WorkItemKind.CASE_RECHECK) == 2


def test_case_recheck_synthesizes_namespace_issue_for_repeated_deployment_failures(engine):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from project_fyr.db import IssueRecord, NamespaceCaseRepo, WorkItemRepo
    from project_fyr.models import IssueScope, IssueStatus, WorkItemKind
    from project_fyr.service import AnalysisWorker

    case_repo = NamespaceCaseRepo(engine)
    work_repo = WorkItemRepo(engine)
    case = case_repo.get_or_create_open_case("ci-cluster", "demo")
    for name in ("api", "worker", "jobs"):
        case_repo.create_issue(
            namespace_case_id=case.id,
            scope=IssueScope.DEPLOYMENT,
            resource_kind="Deployment",
            resource_name=name,
            cause_family="capacity_autoscale_pending",
            issue_type="unschedulable",
            status=IssueStatus.ACTIVE,
        )
    work_repo.enqueue_work_item(kind=WorkItemKind.CASE_RECHECK, namespace_case_id=case.id)

    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker._repo = MagicMock(_engine=engine)
    worker._case_repo = case_repo
    worker._work_item_repo = work_repo
    worker._config = MagicMock(namespace_case_auto_close_seconds=900)

    worker._process_case_recheck_work_items()

    with Session(engine) as s:
        issues = list(s.scalars(select(IssueRecord).order_by(IssueRecord.id.asc())))

    namespace_issues = [issue for issue in issues if issue.scope == IssueScope.NAMESPACE]
    assert len(namespace_issues) == 1
    assert namespace_issues[0].cause_family == "capacity_autoscale_pending"
    assert namespace_issues[0].status == IssueStatus.ACTIVE


def test_case_recheck_quiets_then_closes_case_after_quiet_window(engine):
    from sqlalchemy.orm import Session

    from project_fyr.db import NamespaceCaseRepo, WorkItemRepo
    from project_fyr.models import IssueScope, IssueStatus, NamespaceCaseStatus, WorkItemKind
    from project_fyr.service import AnalysisWorker

    case_repo = NamespaceCaseRepo(engine)
    work_repo = WorkItemRepo(engine)
    case = case_repo.get_or_create_open_case("ci-cluster", "demo")
    issue = case_repo.create_issue(
        namespace_case_id=case.id,
        scope=IssueScope.DEPLOYMENT,
        resource_kind="Deployment",
        resource_name="api",
        cause_family="other",
        issue_type="timeout",
        status=IssueStatus.ACTIVE,
    )
    case_repo.update_issue_status(issue.id, IssueStatus.RESOLVED)
    work_repo.enqueue_work_item(kind=WorkItemKind.CASE_RECHECK, namespace_case_id=case.id)

    worker = AnalysisWorker.__new__(AnalysisWorker)
    worker._repo = MagicMock(_engine=engine)
    worker._case_repo = case_repo
    worker._work_item_repo = work_repo
    worker._config = MagicMock(namespace_case_auto_close_seconds=900)

    worker._process_case_recheck_work_items()
    quieting_case = case_repo.get_case_by_id(case.id)
    assert quieting_case.status == NamespaceCaseStatus.QUIETING

    with Session(engine) as s:
        quieting_case = s.get(type(quieting_case), case.id)
        quieting_case.quieting_at = utcnow() - timedelta(minutes=16)
        s.commit()

    work_repo.enqueue_work_item(kind=WorkItemKind.CASE_RECHECK, namespace_case_id=case.id)
    worker._process_case_recheck_work_items()

    closed_case = case_repo.get_case_by_id(case.id)
    assert closed_case.status == NamespaceCaseStatus.CLOSED
