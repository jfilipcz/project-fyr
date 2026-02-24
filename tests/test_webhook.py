"""Tests for project_fyr.webhook — Alert webhook endpoint."""

from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

import project_fyr.webhook as webhook_mod
from project_fyr.webhook import router

# Create a minimal test app with the webhook router
app = FastAPI()
app.include_router(router)

# Override the dependency to avoid real DB init
mock_repo = MagicMock()
app.dependency_overrides[webhook_mod.get_alert_repo] = lambda: mock_repo
client = TestClient(app)


def test_webhook_rejects_when_no_secret():
    """Webhook rejects requests when no secret is configured."""
    with patch.object(webhook_mod, "settings") as mock_settings:
        mock_settings.alert_webhook_secret = ""
        response = client.post(
            "/webhook/alert",
            json={"alerts": []},
        )
    assert response.status_code == 403


def test_webhook_rejects_bad_token():
    """Invalid token returns 401."""
    with patch.object(webhook_mod, "settings") as mock_settings:
        mock_settings.alert_webhook_secret = "correct-secret"
        response = client.post(
            "/webhook/alert",
            json={"alerts": []},
            headers={"X-Alert-Token": "wrong-secret"},
        )
    assert response.status_code == 401


def test_webhook_accepts_valid_alert():
    """Valid request with correct token is accepted (202)."""
    mock_repo.get_by_fingerprint.return_value = None
    mock_repo.get_state.return_value = None  # no prior state
    mock_repo.create.return_value = MagicMock(id=1)
    mock_repo.upsert_state.return_value = None

    with patch.object(webhook_mod, "settings") as mock_settings:
        mock_settings.alert_webhook_secret = "my-secret"
        mock_settings.alert_auto_investigate = False
        payload = {
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "HighMemory", "namespace": "prod"},
                    "annotations": {"summary": "Memory is high"},
                    "startsAt": "2025-01-01T00:00:00Z",
                    "fingerprint": "abc123",
                }
            ]
        }
        response = client.post(
            "/webhook/alert",
            json=payload,
            headers={"X-Alert-Token": "my-secret"},
        )
    assert response.status_code == 202
    data = response.json()
    assert data["count"] >= 0


def test_webhook_invalid_json():
    """Malformed body returns 400."""
    with patch.object(webhook_mod, "settings") as mock_settings:
        mock_settings.alert_webhook_secret = "my-secret"
        response = client.post(
            "/webhook/alert",
            content=b"not json",
            headers={
                "X-Alert-Token": "my-secret",
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 400
