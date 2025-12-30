from datetime import datetime
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
        started_at=datetime.utcnow()
    )
    assert rollout.id is not None
    assert rollout.deployment == "web"

def test_analyze_pod_failures():
    pod1 = MagicMock()
    pod1.status.phase = "Running"
    pod1.status.container_statuses = []
    
    pod2 = MagicMock()
    pod2.status.phase = "Running"
    cs = MagicMock()
    cs.state.waiting.reason = "CrashLoopBackOff"
    pod2.status.container_statuses = [cs]

    pod3 = MagicMock()
    pod3.status.phase = "Pending"
    pod3.status.container_statuses = []

    pods = [pod1, pod2, pod3]
    signals = analyze_pod_failures(pods)
    
    assert signals.total_pods == 3
    assert signals.crashloop_pods == 1
    assert signals.pending_scheduling_pods == 1

def test_should_fail_early():
    signals = PodFailureSignals(total_pods=5, crashloop_pods=3)
    assert should_fail_early(signals) is True

    signals = PodFailureSignals(total_pods=5, crashloop_pods=1)
    assert should_fail_early(signals) is False


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
            "project-fyr/enabled": "true"
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
    dep.metadata.labels = {"project-fyr/enabled": "true"}  # Deployment has label
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

