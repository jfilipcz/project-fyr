"""FastAPI endpoint for GitLab pipeline metadata."""

from __future__ import annotations

from fastapi import FastAPI

from .config import Settings
from .db import RolloutRepo, init_db
from .models import DeploymentNotifyRequest

app = FastAPI(title="Project Fyr GitLab Ingestor")

settings = Settings()
engine = init_db(settings.database_url)
repo = RolloutRepo(engine)


def _store_metadata(payload: DeploymentNotifyRequest):
    rollout = repo.upsert_gitlab_metadata(payload)
    return {"status": "ok", "rollout_id": rollout.id}


@app.post("/deployments")
def notify_deployment(payload: DeploymentNotifyRequest):
    return _store_metadata(payload)


@app.post("/ingest")
def ingest(payload: DeploymentNotifyRequest):
    return _store_metadata(payload)
