from project_fyr import utcnow
from project_fyr.models import RolloutStatus
from project_fyr.service import evaluate_deployment_phase, analyze_pod_failures, should_fail_early, PodFailureSignals
from unittest.mock import MagicMock

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

