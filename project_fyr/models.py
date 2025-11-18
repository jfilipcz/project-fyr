"""Core models shared across components."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

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


class LogCluster(BaseModel):
    pod: str
    container: str
    template: str
    example: str
    count: int
    last_timestamp: str | None = None


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


class Analysis(BaseModel):
    summary: str
    likely_cause: str
    recommended_steps: list[str]
    severity: str = Field(default="medium")
    details: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

