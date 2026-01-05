
import pytest
from datetime import datetime, timedelta
from project_fyr.models import RolloutStatus, AnalysisStatus
from project_fyr.db import Rollout, AnalysisRecord

def test_get_stats_empty(repo):
    stats = repo.get_stats(hours=24)
    assert stats["total"] == 0
    assert stats["success"] == 0
    assert stats["failed"] == 0
    assert stats["success_rate"] == 0

def test_get_stats_mixed(repo, session):
    now = datetime.utcnow()
    
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

def test_get_recent_failures(repo, session):
    now = datetime.utcnow()
    
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
