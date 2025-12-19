
import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from project_fyr.webhook import router, get_alert_repo
from project_fyr.db import Base, AlertRepo, AlertStateRecord
from project_fyr.config import settings

from sqlalchemy.pool import StaticPool

# Setup in-memory DB for testing
engine = create_engine(
    "sqlite:///:memory:", 
    connect_args={"check_same_thread": False}, 
    poolclass=StaticPool
)
# All models imported above via project_fyr.db
Base.metadata.create_all(engine)

def override_get_alert_repo():
    yield AlertRepo(engine)

# Create a FastAPI app to include the router
from fastapi import FastAPI
app = FastAPI()
app.include_router(router)
app.dependency_overrides[get_alert_repo] = override_get_alert_repo

client = TestClient(app)

def test_stateful_alert_flow():
    # 1. New Alert -> Should be Accepted
    payload_1 = {
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {"alertname": "TestAlert"},
            "fingerprint": "fp-123",
            "startsAt": datetime.utcnow().isoformat() + "Z"
        }]
    }
    
    resp = client.post("/webhook/alert", json=payload_1)
    assert resp.status_code == 202
    data = resp.json()
    assert data["triggered"] == 1
    assert data["count"] == 1
    
    # Check DB state
    with Session(engine) as s:
        state = s.query(AlertStateRecord).filter_by(fingerprint="fp-123").first()
        assert state is not None
        assert state.status == "firing"
        assert state.last_investigated_at is not None
        first_investigated_at = state.last_investigated_at

    # 2. Same Alert 5 mins later -> Should be Throttled (triggered=0)
    # We send the same payload
    resp = client.post("/webhook/alert", json=payload_1)
    assert resp.status_code == 202
    data = resp.json()
    assert data["triggered"] == 0  # Throttled
    assert data["count"] == 1      # But saved
    
    # 3. Simulate 25 hours passing
    with Session(engine) as s:
        state = s.query(AlertStateRecord).filter_by(fingerprint="fp-123").first()
        state.last_investigated_at = datetime.utcnow() - timedelta(hours=25)
        s.commit()
        
    # 4. Same Alert again -> Should be Accepted again
    resp = client.post("/webhook/alert", json=payload_1)
    assert resp.status_code == 202
    data = resp.json()
    assert data["triggered"] == 1
    
    # 5. Resolved Alert -> Should be Ignored (triggered=0)
    payload_resolved = {
        "status": "resolved",
        "alerts": [{
            "status": "resolved",
            "labels": {"alertname": "TestAlert"},
            "fingerprint": "fp-123",
            "startsAt": datetime.utcnow().isoformat() + "Z"
        }]
    }
    resp = client.post("/webhook/alert", json=payload_resolved)
    assert resp.status_code == 202
    data = resp.json()
    assert data["triggered"] == 0
    
    # Check DB state is resolved
    with Session(engine) as s:
        state = s.query(AlertStateRecord).filter_by(fingerprint="fp-123").first()
        assert state.status == "resolved"

    # 6. Flaky Alert: Firing again immediately after resolve (within 24h of last investigation)
    # The last investigation was at step 4 (just now).
    # Even though it's "resolved" -> "firing", it should be suppressed because it was investigated recently.
    resp = client.post("/webhook/alert", json=payload_1)
    assert resp.status_code == 202
    data = resp.json()
    assert data["triggered"] == 0  # Throttled due to sticky throttling


