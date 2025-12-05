from datetime import datetime
from project_fyr.models import RolloutStatus
from project_fyr.service import evaluate_deployment_phase
from unittest.mock import MagicMock

def test_evaluate_deployment_phase_stable():
    dep = MagicMock()
    dep.status.available_replicas = 3
    dep.spec.replicas = 3
    dep.status.conditions = []
    assert evaluate_deployment_phase(dep) == "STABLE"

def test_evaluate_deployment_phase_pending():
    dep = MagicMock()
    dep.status.available_replicas = 0
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
