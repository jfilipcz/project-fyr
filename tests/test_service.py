from unittest.mock import MagicMock
from project_fyr.service import evaluate_deployment_phase, analyze_pod_failures, should_fail_early, PodFailureSignals

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
