from project_fyr.issue_classifier import classify_issue_signal


def test_classify_capacity_autoscale_pending_from_pure_capacity_shortage():
    result = classify_issue_signal(
        signal_type="watcher",
        trigger_context={
            "failure_type": "unschedulable",
            "observed_failures": [
                "0/51 nodes are available: 1 Insufficient cpu, 4 Insufficient memory",
            ],
        },
    )

    assert result.cause_family == "capacity_autoscale_pending"
    assert result.issue_type == "unschedulable"
    assert result.dependency_target is None


def test_classify_placement_mismatch_from_taint_and_affinity_mismatch():
    result = classify_issue_signal(
        signal_type="watcher",
        trigger_context={
            "failure_type": "unschedulable",
            "observed_failures": [
                "0/28 nodes are available: 1 node(s) had untolerated taint {dedicated: smart}, 24 node(s) didn't match Pod's node affinity/selector",
            ],
        },
    )

    assert result.cause_family == "placement_mismatch"
    assert "untolerated taint" in " ".join(result.reasons)


def test_classify_shared_dependency_failure_from_mysql_unready_signal():
    result = classify_issue_signal(
        signal_type="analysis",
        analysis_text=(
            "The init container is crashing because database migrations cannot connect. "
            "MySQL is not ready yet and connection refused is returned during startup."
        ),
    )

    assert result.cause_family == "shared_dependency_failure"
    assert result.dependency_target == "mysql"


def test_classify_shared_storage_failure_from_nfs_and_pvc_errors():
    result = classify_issue_signal(
        signal_type="analysis",
        analysis_text=(
            "The pod is stuck in CreateContainerError due to a stale NFS file handle on PVC shared-storage. "
            "subPath mount preparation fails before the main container starts."
        ),
    )

    assert result.cause_family == "shared_storage_failure"
    assert result.dependency_target == "shared-storage"


def test_classify_app_config_or_secret_from_config_error():
    result = classify_issue_signal(
        signal_type="watcher",
        trigger_context={
            "failure_type": "config_error",
            "observed_failures": [
                "CreateContainerConfigError - secret app-config not found",
            ],
        },
    )

    assert result.cause_family == "app_config_or_secret"
    assert result.issue_type == "config_error"
