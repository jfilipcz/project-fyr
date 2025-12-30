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
    NAMESPACE = "namespace"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class NamespaceIncidentType(str, Enum):
    TERMINATING_STUCK = "terminating_stuck"
    QUOTA_EXCEEDED = "quota_exceeded"
    HIGH_EVICTION_RATE = "high_eviction_rate"
    HIGH_RESTART_RATE = "high_restart_rate"


class NamespaceIncidentStatus(str, Enum):
    ACTIVE = "active"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"


class AlertState(BaseModel):
    fingerprint: str
    status: str
    last_received_at: datetime
    last_investigated_at: Optional[datetime] = None
    created_at: datetime


class NamespaceIncident(BaseModel):
    id: int
    cluster: str
    namespace: str
    incident_type: NamespaceIncidentType
    status: NamespaceIncidentStatus
    started_at: datetime
    resolved_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    analysis_id: Optional[int] = None


class NamespaceContext(BaseModel):
    """Context data for namespace investigation."""
    namespace: str
    cluster: str
    incident_type: str
    namespace_status: dict[str, Any]
    pod_summary: dict[str, Any]
    events: list[EventSummary]
    quotas: Optional[dict[str, Any]] = None
    recent_evictions: list[dict[str, Any]] = Field(default_factory=list)
    recent_restarts: list[dict[str, Any]] = Field(default_factory=list)
