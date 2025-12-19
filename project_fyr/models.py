"""Core models shared across components."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class RolloutStatus(str, Enum):
    PENDING = "PENDING"
    ROLLING_OUT = "ROLLING_OUT"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class AnalysisStatus(str, Enum):
    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"


class NotifyStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class RawContext(BaseModel):
    deployment: dict[str, Any]
    pods: list[dict[str, Any]]
    events: list[dict[str, Any]]
    logs: dict[str, list[str]]
    argocd_app: Optional[dict[str, Any]] = None


class LogCluster(BaseModel):
    pod: str
    container: str
    template: str
    example: str
    count: int
    last_timestamp: Optional[str] = None


class EventSummary(BaseModel):
    reason: str
    message_template: str
    count: int
    last_timestamp: str


class ReducedContext(BaseModel):
    namespace: str
    deployment: str
    generation: int
    summary: str
    phase: str
    failing_pods: list[str]
    log_clusters: list[LogCluster]
    events: list[EventSummary]
    argocd_status: Optional[dict[str, Any]] = None


class Analysis(BaseModel):
    summary: str
    likely_cause: str
    recommended_steps: list[str]
    severity: str = Field(default="medium")
    details: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    triage_team: Optional[str] = Field(default=None)
    triage_reason: Optional[str] = Field(default=None)


class Alert(BaseModel):
    fingerprint: str
    status: str
    starts_at: datetime
    ends_at: Optional[datetime] = None
    labels: dict[str, str]
    annotations: dict[str, str]
    generator_url: Optional[str] = None


class AlertBatch(BaseModel):
    id: int
    summary: str
    alerts: list[Alert]
    created_at: datetime


class JobType(str, Enum):
    ROLLOUT = "rollout"
    ALERT = "alert"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class AlertState(BaseModel):
    fingerprint: str
    status: str
    last_received_at: datetime
    last_investigated_at: Optional[datetime] = None
    created_at: datetime
