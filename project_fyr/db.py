# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Database models and repository helpers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from project_fyr import utcnow
from typing import Iterator, Optional, Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    create_engine,
    func,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .config import settings
from .models import (
    Analysis,
    AnalysisStatus,
    IssueScope,
    IssueStatus,
    NamespaceCaseStatus,
    NamespaceIncidentStatus,
    NamespaceIncidentType,
    NotifyStatus,
    ReducedContext,
    RolloutStatus,
    WorkItemKind,
    WorkItemStatus,
)


class Base(DeclarativeBase):
    pass


class Rollout(Base):
    __tablename__ = "rollouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster: Mapped[str] = mapped_column(String(255), index=True)
    namespace: Mapped[str] = mapped_column(String(255), index=True)
    deployment: Mapped[str] = mapped_column(String(255), index=True)
    generation: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(SAEnum(RolloutStatus), default=RolloutStatus.PENDING)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    origin: Mapped[str] = mapped_column(String(50), default="k8s")
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON, default=dict)
    analysis_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    analysis_status: Mapped[AnalysisStatus] = mapped_column(
        SAEnum(AnalysisStatus), default=AnalysisStatus.NOT_NEEDED
    )
    notify_status: Mapped[NotifyStatus] = mapped_column(
        SAEnum(NotifyStatus), default=NotifyStatus.PENDING
    )
    team: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    slack_channel: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class RolloutStatusTransition(Base):
    __tablename__ = "rollout_status_transitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rollout_id: Mapped[int] = mapped_column(Integer, index=True)
    from_status: Mapped[Optional[RolloutStatus]] = mapped_column(
        SAEnum(RolloutStatus), nullable=True
    )
    to_status: Mapped[RolloutStatus] = mapped_column(SAEnum(RolloutStatus))
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class AnalysisRecord(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rollout_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    model_name: Mapped[str] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(50))
    reduced_context: Mapped[dict] = mapped_column(JSON)
    analysis: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AlertBatchRecord(Base):
    __tablename__ = "alert_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    primary_fingerprint: Mapped[str] = mapped_column(String(255), index=True)
    namespace: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    service: Mapped[Optional[str]] = mapped_column(String(255))
    window_start: Mapped[datetime] = mapped_column(DateTime)
    window_end: Mapped[datetime] = mapped_column(DateTime)
    context_summary: Mapped[str] = mapped_column(String(2000))  # JSON or text summary
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AlertRecord(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(50))
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    labels: Mapped[dict] = mapped_column(JSON)
    annotations: Mapped[dict] = mapped_column(JSON)
    payload: Mapped[dict] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Batching
    batched: Mapped[bool] = mapped_column(Integer, default=0)  # SQLite bool
    batch_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)


class InvestigationJob(Base):
    __tablename__ = "investigation_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(50))  # rollout | alert | namespace
    status: Mapped[str] = mapped_column(String(50), default="pending")

    # Polymorphic-ish FKs
    rollout_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    alert_batch_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    namespace_incident_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    analysis_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)



class AlertStateRecord(Base):
    __tablename__ = "alert_states"

    fingerprint: Mapped[str] = mapped_column(String(255), primary_key=True)
    status: Mapped[str] = mapped_column(String(50))
    last_received_at: Mapped[datetime] = mapped_column(DateTime)
    last_investigated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NamespaceIncidentRecord(Base):
    __tablename__ = "namespace_incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster: Mapped[str] = mapped_column(String(255), index=True)
    namespace: Mapped[str] = mapped_column(String(255), index=True)
    incident_type: Mapped[str] = mapped_column(SAEnum(NamespaceIncidentType))
    status: Mapped[str] = mapped_column(SAEnum(NamespaceIncidentStatus), default=NamespaceIncidentStatus.ACTIVE)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON, default=dict)
    analysis_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    analysis_status: Mapped[AnalysisStatus] = mapped_column(
        SAEnum(AnalysisStatus), default=AnalysisStatus.PENDING
    )
    notify_status: Mapped[NotifyStatus] = mapped_column(
        SAEnum(NotifyStatus), default=NotifyStatus.PENDING
    )
    team: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    slack_channel: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NamespaceCaseRecord(Base):
    __tablename__ = "namespace_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster: Mapped[str] = mapped_column(String(255), index=True)
    namespace: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[NamespaceCaseStatus] = mapped_column(
        SAEnum(NamespaceCaseStatus), default=NamespaceCaseStatus.OPEN, index=True
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    quieting_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    slack_channel: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    slack_thread_ts: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IssueRecord(Base):
    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    namespace_case_id: Mapped[int] = mapped_column(
        ForeignKey("namespace_cases.id"), index=True
    )
    scope: Mapped[IssueScope] = mapped_column(SAEnum(IssueScope), index=True)
    resource_kind: Mapped[str] = mapped_column(String(100))
    resource_name: Mapped[str] = mapped_column(String(255), index=True)
    rollout_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("rollouts.id"), nullable=True, index=True
    )
    cause_family: Mapped[str] = mapped_column(String(100), index=True)
    issue_type: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[IssueStatus] = mapped_column(
        SAEnum(IssueStatus), default=IssueStatus.ACTIVE, index=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    analysis_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("analyses.id"), nullable=True, index=True
    )
    notify_status: Mapped[NotifyStatus] = mapped_column(
        SAEnum(NotifyStatus), default=NotifyStatus.PENDING
    )
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IssueObservationRecord(Base):
    __tablename__ = "issue_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    source: Mapped[str] = mapped_column(String(50), index=True)
    signal_type: Mapped[str] = mapped_column(String(100), index=True)
    payload_json: Mapped[Optional[dict]] = mapped_column("payload", JSON, default=dict)


class WorkItemRecord(Base):
    __tablename__ = "work_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[WorkItemKind] = mapped_column(SAEnum(WorkItemKind), index=True)
    namespace_case_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("namespace_cases.id"), nullable=True, index=True
    )
    issue_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("issues.id"), nullable=True, index=True
    )
    status: Mapped[WorkItemStatus] = mapped_column(
        SAEnum(WorkItemStatus), default=WorkItemStatus.PENDING, index=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)


class AggregatedInsight(Base):
    """Cached AI-generated insights for the overview dashboard."""
    __tablename__ = "aggregated_insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster: Mapped[str] = mapped_column(String(255), index=True)
    hours: Mapped[int] = mapped_column(Integer)  # Time window in hours
    insights: Mapped[dict] = mapped_column(JSON)  # The aggregated insights result
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    failure_count: Mapped[int] = mapped_column(Integer)  # Number of failures analyzed


def init_db(database_url: str):
    engine = create_engine(
        database_url,
        future=True,
        pool_pre_ping=True,
        pool_recycle=300,
    )
    Base.metadata.create_all(engine)
    return engine


class RolloutRepo:
    def __init__(self, engine, annotation_prefix: str = "project-fyr.io"):
        self._engine = engine
        self._annotation_prefix = annotation_prefix

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self._engine) as session:
            yield session

    def create(self, **kwargs) -> Rollout:
        rollout = Rollout(**kwargs)
        with self.session() as s:
            s.add(rollout)
            s.commit()
            s.refresh(rollout)
        return rollout

    def get_by_key(self, cluster: str, namespace: str, deployment: str, generation: int) -> Optional[Rollout]:
        stmt = select(Rollout).where(
            Rollout.cluster == cluster,
            Rollout.namespace == namespace,
            Rollout.deployment == deployment,
            Rollout.generation == generation,
        )
        with self.session() as s:
            return s.scalars(stmt).first()

    def list_active(self, cluster: str) -> list[Rollout]:
        stmt = select(Rollout).where(
            Rollout.cluster == cluster,
            Rollout.status.in_([RolloutStatus.PENDING, RolloutStatus.ROLLING_OUT]),
        )
        with self.session() as s:
            return list(s.scalars(stmt))

    def list_failed(self, cluster: str) -> list[Rollout]:
        """List FAILED rollouts that need analysis (PENDING only, not DISCARDED)."""
        stmt = select(Rollout).where(
            Rollout.cluster == cluster,
            Rollout.status == RolloutStatus.FAILED,
            Rollout.analysis_status == AnalysisStatus.PENDING,
        ).order_by(Rollout.id.asc())  # FIFO order
        with self.session() as s:
            return list(s.scalars(stmt))

    def list_for_speculative_analysis(self, cluster: str, grace_seconds: int) -> list[Rollout]:
        """Find ROLLING_OUT rollouts past grace period that haven't been analyzed yet."""
        threshold_time = utcnow() - timedelta(seconds=grace_seconds)
        stmt = select(Rollout).where(
            Rollout.cluster == cluster,
            Rollout.status == RolloutStatus.ROLLING_OUT,
            Rollout.started_at < threshold_time,
            Rollout.analysis_status == AnalysisStatus.NOT_NEEDED,
        ).order_by(Rollout.id.asc())  # FIFO order
        with self.session() as s:
            return list(s.scalars(stmt))

    def list_speculative_now_failed(self, cluster: str) -> list[Rollout]:
        """Find rollouts with speculative analysis that are now FAILED (ready for notification)."""
        stmt = select(Rollout).where(
            Rollout.cluster == cluster,
            Rollout.status == RolloutStatus.FAILED,
            Rollout.analysis_status == AnalysisStatus.SPECULATIVE,
        ).order_by(Rollout.id.asc())
        with self.session() as s:
            return list(s.scalars(stmt))

    def list_speculative_now_success(self, cluster: str) -> list[Rollout]:
        """Find rollouts with speculative analysis that succeeded (should discard analysis)."""
        stmt = select(Rollout).where(
            Rollout.cluster == cluster,
            Rollout.status == RolloutStatus.SUCCESS,
            Rollout.analysis_status == AnalysisStatus.SPECULATIVE,
        )
        with self.session() as s:
            return list(s.scalars(stmt))

    def list_stuck_pending(self, cluster: str, threshold_seconds: int) -> list[Rollout]:
        """Find PENDING rollouts that have been pending for longer than threshold."""
        from datetime import timedelta
        threshold_time = utcnow() - timedelta(seconds=threshold_seconds)
        stmt = select(Rollout).where(
            Rollout.cluster == cluster,
            Rollout.status == RolloutStatus.PENDING,
            Rollout.started_at < threshold_time,
            Rollout.analysis_status.in_([AnalysisStatus.NOT_NEEDED, AnalysisStatus.PENDING]),
        ).order_by(Rollout.id.asc())
        with self.session() as s:
            return list(s.scalars(stmt))

    def list_recent(
        self,
        limit: int = 50,
        exclude_system: bool = True,
        requestor: Optional[str] = None,
    ) -> list[Rollout]:
        stmt = select(Rollout)
        if exclude_system:
            stmt = stmt.where(Rollout.namespace.notin_(settings.system_namespaces))
        stmt = self._apply_requestor_filter(stmt, requestor)
        stmt = stmt.order_by(Rollout.id.desc()).limit(limit)
        with self.session() as s:
            return list(s.scalars(stmt))

    def list_by_status(
        self,
        status: str,
        limit: int = 50,
        exclude_system: bool = True,
        requestor: Optional[str] = None,
    ) -> list[Rollout]:
        """List rollouts filtered by status."""
        # Convert string to RolloutStatus enum
        try:
            status_enum = RolloutStatus[status.upper()]
        except (KeyError, AttributeError):
            # If invalid status, return empty list
            return []

        stmt = select(Rollout).where(
            Rollout.status == status_enum
        )
        if exclude_system:
            stmt = stmt.where(Rollout.namespace.notin_(settings.system_namespaces))
        stmt = self._apply_requestor_filter(stmt, requestor)
        stmt = stmt.order_by(Rollout.id.desc()).limit(limit)
        with self.session() as s:
            return list(s.scalars(stmt))

    def list_by_namespace(
        self,
        namespace: str,
        limit: int = 50,
        requestor: Optional[str] = None,
    ) -> list[Rollout]:
        """List rollouts filtered by namespace."""
        stmt = select(Rollout).where(
            Rollout.namespace == namespace
        ).order_by(Rollout.id.desc()).limit(limit)
        stmt = self._apply_requestor_filter(stmt, requestor)
        with self.session() as s:
            return list(s.scalars(stmt))

    def get_stats(self, hours: int = 24, exclude_system: bool = True) -> dict[str, int]:
        """Get rollout statistics for the last N hours."""
        cutoff = utcnow() - timedelta(hours=hours)

        # Total rollouts in window
        stmt_total = select(Rollout).where(Rollout.started_at >= cutoff)
        if exclude_system:
            stmt_total = stmt_total.where(Rollout.namespace.notin_(settings.system_namespaces))

        # Success count
        stmt_success = select(Rollout).where(
            Rollout.started_at >= cutoff,
            Rollout.status == RolloutStatus.SUCCESS
        )
        if exclude_system:
            stmt_success = stmt_success.where(Rollout.namespace.notin_(settings.system_namespaces))

        # Failed count
        stmt_failed = select(Rollout).where(
            Rollout.started_at >= cutoff,
            Rollout.status == RolloutStatus.FAILED
        )
        if exclude_system:
            stmt_failed = stmt_failed.where(Rollout.namespace.notin_(settings.system_namespaces))

        with self.session() as s:
            total = len(list(s.scalars(stmt_total)))
            success = len(list(s.scalars(stmt_success)))
            failed = len(list(s.scalars(stmt_failed)))

        return {
            "total": total,
            "success": success,
            "failed": failed,
            "success_rate": round((success / total) * 100, 1) if total > 0 else 0
        }

    def get_operational_stats(self, hours: int = 24, exclude_system: bool = True) -> dict[str, Any]:
        """Get operational metrics for dashboard triage."""
        now = utcnow()
        cutoff = now - timedelta(hours=hours)
        recent_cutoff = now - timedelta(minutes=60)

        pending_stmt = select(
            func.count(Rollout.id),
            func.min(Rollout.failed_at),
        ).where(
            Rollout.status == RolloutStatus.FAILED,
            Rollout.analysis_status == AnalysisStatus.PENDING,
        )

        recent_failures_stmt = select(func.count(Rollout.id)).where(
            Rollout.status == RolloutStatus.FAILED,
            Rollout.failed_at >= recent_cutoff,
        )

        repeated_namespaces_stmt = (
            select(Rollout.namespace, func.count(Rollout.id).label("failure_count"))
            .where(
                Rollout.status == RolloutStatus.FAILED,
                Rollout.failed_at >= cutoff,
            )
            .group_by(Rollout.namespace)
            .having(func.count(Rollout.id) >= 2)
            .order_by(func.count(Rollout.id).desc(), Rollout.namespace.asc())
        )

        if exclude_system:
            system_filter = Rollout.namespace.notin_(settings.system_namespaces)
            pending_stmt = pending_stmt.where(system_filter)
            recent_failures_stmt = recent_failures_stmt.where(system_filter)
            repeated_namespaces_stmt = repeated_namespaces_stmt.where(system_filter)

        with self.session() as s:
            pending_count, oldest_pending_failed_at = s.execute(pending_stmt).one()
            recent_failures = s.scalar(recent_failures_stmt) or 0
            repeated_namespaces = s.execute(repeated_namespaces_stmt).all()

        oldest_pending_age_minutes = None
        if oldest_pending_failed_at is not None:
            oldest_pending_age_minutes = max(
                0,
                int((now - oldest_pending_failed_at).total_seconds() // 60),
            )

        top_namespace = repeated_namespaces[0] if repeated_namespaces else None

        return {
            "pending_analyses": int(pending_count or 0),
            "oldest_pending_age_minutes": oldest_pending_age_minutes,
            "new_failures_60m": int(recent_failures),
            "noisy_namespaces": len(repeated_namespaces),
            "top_noisy_namespace": top_namespace[0] if top_namespace else None,
            "top_noisy_namespace_failures": int(top_namespace[1]) if top_namespace else 0,
        }

    def get_recent_failures(self, limit: int = 50, hours: int = 24, exclude_system: bool = True) -> list[tuple[Rollout, Optional[AnalysisRecord]]]:
        """Get failed rollouts with their analysis records for the last N hours."""
        cutoff = utcnow() - timedelta(hours=hours)

        # Join Rollout with AnalysisRecord
        stmt = (
            select(Rollout, AnalysisRecord)
            .outerjoin(AnalysisRecord, Rollout.analysis_id == AnalysisRecord.id)
            .where(
                Rollout.status == RolloutStatus.FAILED,
                Rollout.started_at >= cutoff,
                Rollout.analysis_status == AnalysisStatus.DONE
            )
        )
        if exclude_system:
            stmt = stmt.where(Rollout.namespace.notin_(settings.system_namespaces))
        stmt = stmt.order_by(Rollout.id.desc()).limit(limit)

        with self.session() as s:
            # Result is a list of Row objects (tuples)
            results = s.execute(stmt).all()
            # Convert to list of tuples for easier consumption
            return [(r.Rollout, r.AnalysisRecord) for r in results]

    def get_namespace_recent_failures(
        self,
        namespace: str,
        *,
        limit: int = 50,
        hours: int = 24,
        exclude_system: bool = True,
    ) -> list[tuple[Rollout, Optional[AnalysisRecord]]]:
        """Get failed rollouts with analysis for a specific namespace in the last N hours."""
        cutoff = utcnow() - timedelta(hours=hours)

        stmt = (
            select(Rollout, AnalysisRecord)
            .outerjoin(AnalysisRecord, Rollout.analysis_id == AnalysisRecord.id)
            .where(
                Rollout.namespace == namespace,
                Rollout.status == RolloutStatus.FAILED,
                Rollout.started_at >= cutoff,
                Rollout.analysis_status == AnalysisStatus.DONE,
            )
        )
        if exclude_system:
            stmt = stmt.where(Rollout.namespace.notin_(settings.system_namespaces))
        stmt = stmt.order_by(Rollout.id.desc()).limit(limit)

        with self.session() as s:
            results = s.execute(stmt).all()
            return [(r.Rollout, r.AnalysisRecord) for r in results]

    def list_by_status_and_namespace(
        self,
        status: str,
        namespace: str,
        limit: int = 50,
        requestor: Optional[str] = None,
    ) -> list[Rollout]:
        """List rollouts filtered by both status and namespace."""
        try:
            status_enum = RolloutStatus[status.upper()]
        except (KeyError, AttributeError):
            return []

        stmt = select(Rollout).where(
            Rollout.status == status_enum,
            Rollout.namespace == namespace
        ).order_by(Rollout.id.desc()).limit(limit)
        stmt = self._apply_requestor_filter(stmt, requestor)
        with self.session() as s:
            return list(s.scalars(stmt))

    def _apply_requestor_filter(self, stmt, requestor: Optional[str]):
        if not requestor:
            return stmt
        return stmt.where(
            Rollout.metadata_json[f"{self._annotation_prefix}/requestor-email"].as_string() == requestor
        )

    def get_by_id(self, rollout_id: int) -> Optional[Rollout]:
        stmt = select(Rollout).where(Rollout.id == rollout_id)
        with self.session() as s:
            return s.scalars(stmt).first()

    def get_analysis(self, analysis_id: int) -> Optional[AnalysisRecord]:
        stmt = select(AnalysisRecord).where(AnalysisRecord.id == analysis_id)
        with self.session() as s:
            return s.scalars(stmt).first()

    @staticmethod
    def _as_rollout_status(status: RolloutStatus | str | None) -> RolloutStatus | None:
        if status is None:
            return None
        if isinstance(status, RolloutStatus):
            return status
        try:
            return RolloutStatus(status)
        except ValueError:
            return RolloutStatus[status]

    def _append_status_transition(
        self,
        s: Session,
        *,
        rollout_id: int,
        from_status: RolloutStatus | str | None,
        to_status: RolloutStatus | str | None,
        reason: str | None = None,
    ) -> None:
        old_status = self._as_rollout_status(from_status)
        new_status = self._as_rollout_status(to_status)
        if new_status is None or old_status == new_status:
            return
        s.add(
            RolloutStatusTransition(
                rollout_id=rollout_id,
                from_status=old_status,
                to_status=new_status,
                reason=reason,
            )
        )

    def update_status(self, rollout_id: int, new_status: RolloutStatus, **timestamps) -> None:
        with self.session() as s:
            rollout = s.get(Rollout, rollout_id)
            if rollout is None:
                return

            previous_status = rollout.status
            rollout.status = self._as_rollout_status(new_status)
            for key, value in timestamps.items():
                setattr(rollout, key, value)

            self._append_status_transition(
                s,
                rollout_id=rollout_id,
                from_status=previous_status,
                to_status=rollout.status,
            )
            s.commit()

    def queue_for_analysis(self, rollout_id: int) -> None:
        """Mark rollout as needing analysis (analysis_status=PENDING)."""
        stmt = (
            update(Rollout)
            .where(Rollout.id == rollout_id)
            .values(analysis_status=AnalysisStatus.PENDING)
        )
        with self.session() as s:
            s.execute(stmt)
            s.commit()

    def mark_speculative(self, rollout_id: int) -> None:
        """Mark rollout as having speculative analysis in progress."""
        stmt = (
            update(Rollout)
            .where(Rollout.id == rollout_id)
            .values(analysis_status=AnalysisStatus.SPECULATIVE)
        )
        with self.session() as s:
            s.execute(stmt)
            s.commit()

    def discard_analysis(
        self,
        rollout_id: int,
        *,
        reason: str | None = None,
        mark_success: bool = False,
    ) -> None:
        """Discard analysis and optionally mark rollout as successful.

        Used for scenarios where analysis is no longer actionable, such as deleted
        ephemeral resources.
        """
        with self.session() as s:
            rollout = s.get(Rollout, rollout_id)
            if rollout is None:
                return

            rollout.analysis_status = AnalysisStatus.DISCARDED
            rollout.notify_status = NotifyStatus.SENT

            if reason:
                metadata = dict(rollout.metadata_json or {})
                metadata["discard_reason"] = reason
                rollout.metadata_json = metadata

            if mark_success:
                previous_status = rollout.status
                rollout.status = RolloutStatus.SUCCESS
                rollout.completed_at = utcnow()
                self._append_status_transition(
                    s,
                    rollout_id=rollout.id,
                    from_status=previous_status,
                    to_status=rollout.status,
                    reason=reason,
                )

            s.commit()

    def discard_speculative(self, rollout_id: int) -> None:
        """Discard speculative analysis (rollout succeeded)."""
        self.discard_analysis(rollout_id)

    def promote_speculative(self, rollout_id: int) -> None:
        """Promote speculative analysis to DONE (rollout confirmed failed, ready for notification)."""
        stmt = (
            update(Rollout)
            .where(Rollout.id == rollout_id)
            .values(analysis_status=AnalysisStatus.DONE)
        )
        with self.session() as s:
            s.execute(stmt)
            s.commit()

    def update_notify_status(self, rollout_id: int, new_status: NotifyStatus) -> None:
        stmt = update(Rollout).where(Rollout.id == rollout_id).values(notify_status=new_status)
        with self.session() as s:
            s.execute(stmt)
            s.commit()

    def set_rollout_notification_state(
        self,
        rollout_id: int,
        *,
        state: str,
        classification: str | None = None,
        reason: str | None = None,
        deferred_until: datetime | None = None,
        recovered_before_notification: bool | None = None,
        notify_status: NotifyStatus | None = None,
        policy: dict[str, Any] | None = None,
    ) -> None:
        with self.session() as s:
            rollout = s.get(Rollout, rollout_id)
            if rollout is None:
                return

            metadata = dict(rollout.metadata_json or {})
            metadata["notification_state"] = state
            if classification is not None:
                metadata["notification_classification"] = classification
            if reason is not None:
                metadata["notification_decision_reason"] = reason
            if deferred_until is not None:
                metadata["deferred_until"] = deferred_until.isoformat()
            if recovered_before_notification is not None:
                metadata["recovered_before_notification"] = recovered_before_notification
            if policy is not None:
                metadata["notification_policy"] = policy

            rollout.metadata_json = metadata
            if notify_status is not None:
                rollout.notify_status = notify_status
            s.commit()

    def list_deferred_rollout_notifications_due(
        self,
        cluster: str,
        *,
        now: datetime | None = None,
    ) -> list[Rollout]:
        reference = now or utcnow()
        stmt = select(Rollout).where(
            Rollout.cluster == cluster,
            Rollout.analysis_status == AnalysisStatus.DONE,
            Rollout.notify_status == NotifyStatus.PENDING,
        ).order_by(Rollout.id.asc())
        due: list[Rollout] = []
        with self.session() as s:
            rollouts = list(s.scalars(stmt))
            for rollout in rollouts:
                metadata = dict(rollout.metadata_json or {})
                if metadata.get("notification_state") != "deferred":
                    continue
                deferred_until = metadata.get("deferred_until")
                if not deferred_until:
                    continue
                try:
                    due_at = datetime.fromisoformat(str(deferred_until))
                except ValueError:
                    continue
                if due_at <= reference:
                    due.append(rollout)
        return due

    def append_analysis(
        self,
        rollout_id: int,
        *,
        reduced_context: ReducedContext,
        analysis: Analysis,
        model_name: str,
        prompt_version: str = "v1",
        speculative: bool = False,
    ) -> None:
        """Save analysis results.

        Args:
            speculative: If True, sets analysis_status to SPECULATIVE instead of DONE.
                        This allows holding notification until rollout status is confirmed.
        """
        with self.session() as s:
            record = AnalysisRecord(
                rollout_id=rollout_id,
                model_name=model_name,
                prompt_version=prompt_version,
                reduced_context=reduced_context.model_dump(mode="json"),
                analysis=analysis.model_dump(mode="json"),
            )
            s.add(record)
            s.flush()

            new_status = AnalysisStatus.SPECULATIVE if speculative else AnalysisStatus.DONE
            status_stmt = (
                update(Rollout)
                .where(Rollout.id == rollout_id)
                .values(
                    analysis_id=record.id,
                    analysis_status=new_status,
                    completed_at=analysis.created_at if not speculative else None,
                )
            )
            s.execute(status_stmt)
            s.commit()

    def append_trigger_context(
        self,
        rollout_id: int,
        trigger_reason: str,
        failure_observations: list[str],
        *,
        is_transient: bool = False,
        time_to_failure_seconds: int | None = None,
        failure_type: str | None = None,
    ) -> None:
        """Append trigger context to rollout metadata for investigation enrichment."""
        with self.session() as s:
            rollout = s.get(Rollout, rollout_id)
            if not rollout:
                return

            metadata = dict(rollout.metadata_json or {})
            trigger_context = metadata.get("trigger_context", {})

            # Set or update trigger reason
            trigger_context["trigger_reason"] = trigger_reason

            # Append to observed failures (keeping history)
            existing_failures = trigger_context.get("observed_failures", [])
            for obs in failure_observations:
                if obs not in existing_failures:
                    existing_failures.append(obs)
            trigger_context["observed_failures"] = existing_failures[-10:]  # Keep last 10

            # Track transient failures
            if is_transient:
                trigger_context["transient_failures_detected"] = True

            if time_to_failure_seconds is not None:
                trigger_context["time_to_failure_seconds"] = time_to_failure_seconds

            if failure_type is not None:
                trigger_context["failure_type"] = failure_type

            metadata["trigger_context"] = trigger_context

            stmt = update(Rollout).where(Rollout.id == rollout_id).values(metadata_json=metadata)
            s.execute(stmt)
            s.commit()

    def update_metadata(
        self,
        rollout_id: int,
        *,
        metadata_json: Optional[dict] = None,
        team: Optional[str] = None,
        slack_channel: Optional[str] = None,
    ) -> None:
        with self.session() as s:
            rollout = s.get(Rollout, rollout_id)
            if rollout is None:
                return

            if metadata_json is not None:
                # Preserve previously captured investigation context (trigger_context,
                # discard_reason, etc.) while refreshing namespace/deployment metadata.
                merged_metadata = dict(rollout.metadata_json or {})
                merged_metadata.update(metadata_json)
                rollout.metadata_json = merged_metadata
            if team is not None:
                rollout.team = team
            if slack_channel is not None:
                rollout.slack_channel = slack_channel

            s.commit()

    def get_cached_insights(self, cluster: str, hours: int, ttl_minutes: int) -> Optional[AggregatedInsight]:
        """Get cached aggregated insights if they exist and are fresh."""
        cutoff = utcnow() - timedelta(minutes=ttl_minutes)

        stmt = (
            select(AggregatedInsight)
            .where(
                AggregatedInsight.cluster == cluster,
                AggregatedInsight.hours == hours,
                AggregatedInsight.generated_at >= cutoff
            )
            .order_by(AggregatedInsight.generated_at.desc())
            .limit(1)
        )

        with self.session() as s:
            return s.scalars(stmt).first()

    def save_cached_insights(
        self,
        cluster: str,
        hours: int,
        insights: dict[str, Any],
        failure_count: int
    ) -> AggregatedInsight:
        """Save newly generated insights to cache."""
        cached = AggregatedInsight(
            cluster=cluster,
            hours=hours,
            insights=insights,
            failure_count=failure_count,
            generated_at=utcnow()
        )

        with self.session() as s:
            s.add(cached)
            s.commit()
            s.refresh(cached)

        return cached

    def cleanup_old_insights(self, days: int = 7) -> int:
        """Delete cached insights older than specified days."""
        cutoff = utcnow() - timedelta(days=days)

        stmt = select(AggregatedInsight).where(AggregatedInsight.generated_at < cutoff)

        with self.session() as s:
            old_insights = list(s.scalars(stmt))
            count = len(old_insights)

            for insight in old_insights:
                s.delete(insight)

            s.commit()

        return count


class AlertRepo:
    def __init__(self, engine):
        self._engine = engine

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self._engine) as session:
            yield session

    def create_alert(self, **kwargs) -> AlertRecord:
        alert = AlertRecord(**kwargs)
        with self.session() as s:
            s.add(alert)
            s.commit()
            s.refresh(alert)
        return alert

    def get_unbatched_alerts(self, window_start: datetime) -> list[AlertRecord]:
        # Get alerts received after window_start that are not yet batched
        stmt = select(AlertRecord).where(
            AlertRecord.batched == 0,
            AlertRecord.received_at >= window_start
        ).order_by(AlertRecord.received_at.asc())

        with self.session() as s:
            return list(s.scalars(stmt))

    def create_batch(self, alerts: list[AlertRecord], summary: str, **kwargs) -> AlertBatchRecord:
        with self.session() as s:
            batch = AlertBatchRecord(context_summary=summary, **kwargs)
            s.add(batch)
            s.flush()

            # Update alerts
            alert_ids = [a.id for a in alerts]
            stmt = update(AlertRecord).where(AlertRecord.id.in_(alert_ids)).values(
                batched=True,
                batch_id=batch.id
            )
            s.execute(stmt)

            # Create job
            job = InvestigationJob(
                type="alert",
                alert_batch_id=batch.id,
                status="pending"
            )
            s.add(job)

            s.commit()
            s.refresh(batch)
            return batch

    def get_pending_jobs(self) -> list[InvestigationJob]:
        stmt = select(InvestigationJob).where(InvestigationJob.status == "pending")
        with self.session() as s:
            return list(s.scalars(stmt))

    def get_pending_alert_jobs(self) -> list[InvestigationJob]:
        stmt = select(InvestigationJob).where(
            InvestigationJob.status == "pending",
            InvestigationJob.type == "alert",
        )
        with self.session() as s:
            return list(s.scalars(stmt))

    def get_pending_namespace_jobs(self) -> list[InvestigationJob]:
        """Get pending investigation jobs for namespace incidents."""
        stmt = select(InvestigationJob).where(
            InvestigationJob.status == "pending",
            InvestigationJob.type == "namespace"
        )
        with self.session() as s:
            return list(s.scalars(stmt))

    def get_batch(self, batch_id: int) -> Optional[AlertBatchRecord]:
        stmt = select(AlertBatchRecord).where(AlertBatchRecord.id == batch_id)
        with self.session() as s:
            return s.scalars(stmt).first()

    def get_batch_alerts(self, batch_id: int) -> list[AlertRecord]:
        stmt = select(AlertRecord).where(AlertRecord.batch_id == batch_id)
        with self.session() as s:
            return list(s.scalars(stmt))

    def update_job_status(self, job_id: int, status: str, **timestamps) -> None:
        stmt = update(InvestigationJob).where(InvestigationJob.id == job_id).values(status=status, **timestamps)
        with self.session() as s:
            s.execute(stmt)
            s.commit()

    def get_state(self, fingerprint: str) -> Optional[AlertStateRecord]:
        stmt = select(AlertStateRecord).where(AlertStateRecord.fingerprint == fingerprint)
        with self.session() as s:
            return s.scalars(stmt).first()

    def update_state(
        self,
        fingerprint: str,
        status: str,
        now: datetime,
        investigated: bool = False
    ) -> AlertStateRecord:
        with self.session() as s:
            state = s.scalars(
                select(AlertStateRecord).where(AlertStateRecord.fingerprint == fingerprint)
            ).first()

            if not state:
                state = AlertStateRecord(
                    fingerprint=fingerprint,
                    status=status,
                    last_received_at=now,
                    last_investigated_at=now if investigated else None
                )
                s.add(state)
            else:
                state.status = status
                state.last_received_at = now
                if investigated:
                    state.last_investigated_at = now

            s.commit()
            s.refresh(state)
            return state


class NamespaceIncidentRepo:
    def __init__(self, engine):
        self._engine = engine

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self._engine) as session:
            yield session

    def create(self, **kwargs) -> NamespaceIncidentRecord:
        incident = NamespaceIncidentRecord(**kwargs)
        with self.session() as s:
            s.add(incident)
            s.commit()
            s.refresh(incident)
        return incident

    def get_active_incident(
        self, cluster: str, namespace: str, incident_type: str
    ) -> Optional[NamespaceIncidentRecord]:
        """Get active incident of a specific type for a namespace."""
        from .models import NamespaceIncidentType
        try:
            incident_type_enum = NamespaceIncidentType[incident_type.upper()]
        except (KeyError, AttributeError):
            return None

        stmt = select(NamespaceIncidentRecord).where(
            NamespaceIncidentRecord.cluster == cluster,
            NamespaceIncidentRecord.namespace == namespace,
            NamespaceIncidentRecord.incident_type == incident_type_enum,
            NamespaceIncidentRecord.status.in_([
                NamespaceIncidentStatus.ACTIVE,
                NamespaceIncidentStatus.INVESTIGATING
            ]),
        )
        with self.session() as s:
            return s.scalars(stmt).first()

    def list_active(self, cluster: str) -> list[NamespaceIncidentRecord]:
        """List all active incidents in a cluster."""
        stmt = select(NamespaceIncidentRecord).where(
            NamespaceIncidentRecord.cluster == cluster,
            NamespaceIncidentRecord.status.in_([
                NamespaceIncidentStatus.ACTIVE,
                NamespaceIncidentStatus.INVESTIGATING
            ]),
        )
        with self.session() as s:
            return list(s.scalars(stmt))

    def list_recent(self, limit: int = 50) -> list[NamespaceIncidentRecord]:
        stmt = select(NamespaceIncidentRecord).order_by(NamespaceIncidentRecord.id.desc()).limit(limit)
        with self.session() as s:
            return list(s.scalars(stmt))

    def get_by_id(self, incident_id: int) -> Optional[NamespaceIncidentRecord]:
        stmt = select(NamespaceIncidentRecord).where(NamespaceIncidentRecord.id == incident_id)
        with self.session() as s:
            return s.scalars(stmt).first()

    def resolve(self, incident_id: int) -> None:
        """Mark incident as resolved."""
        stmt = (
            update(NamespaceIncidentRecord)
            .where(NamespaceIncidentRecord.id == incident_id)
            .values(
                status=NamespaceIncidentStatus.RESOLVED,
                resolved_at=utcnow()
            )
        )
        with self.session() as s:
            s.execute(stmt)
            s.commit()

    def update_status(self, incident_id: int, new_status: NamespaceIncidentStatus) -> None:
        stmt = (
            update(NamespaceIncidentRecord)
            .where(NamespaceIncidentRecord.id == incident_id)
            .values(status=new_status)
        )
        with self.session() as s:
            s.execute(stmt)
            s.commit()

    def update_notify_status(self, incident_id: int, new_status: NotifyStatus) -> None:
        stmt = update(NamespaceIncidentRecord).where(
            NamespaceIncidentRecord.id == incident_id
        ).values(notify_status=new_status)
        with self.session() as s:
            s.execute(stmt)
            s.commit()

    def append_analysis(
        self,
        incident_id: int,
        *,
        reduced_context: dict,
        analysis: Analysis,
        model_name: str,
        prompt_version: str = "v1",
    ) -> None:
        with self.session() as s:
            # We could create a separate NamespaceAnalysisRecord table,
            # but for now reuse AnalysisRecord with rollout_id = None
            # and store incident_id in metadata
            record = AnalysisRecord(
                rollout_id=incident_id,  # Reuse this field temporarily
                model_name=model_name,
                prompt_version=prompt_version,
                reduced_context=reduced_context,
                analysis=analysis.model_dump(mode="json"),
            )
            s.add(record)
            s.flush()
            status_stmt = (
                update(NamespaceIncidentRecord)
                .where(NamespaceIncidentRecord.id == incident_id)
                .values(
                    analysis_id=record.id,
                    analysis_status=AnalysisStatus.DONE,
                )
            )
            s.execute(status_stmt)
            s.commit()

    def count_investigations_in_window(
        self,
        cluster: str,
        namespace: Optional[str] = None,
        hours: int = 1
    ) -> int:
        """Count investigations (rollouts + incidents) in time window for rate limiting."""
        from datetime import timedelta
        cutoff = utcnow() - timedelta(hours=hours)

        with self.session() as s:
            # Count rollout investigations
            rollout_stmt = select(Rollout).where(
                Rollout.cluster == cluster,
                Rollout.started_at >= cutoff
            )
            if namespace:
                rollout_stmt = rollout_stmt.where(Rollout.namespace == namespace)
            rollout_count = len(list(s.scalars(rollout_stmt)))

            # Count namespace incident investigations
            incident_stmt = select(NamespaceIncidentRecord).where(
                NamespaceIncidentRecord.cluster == cluster,
                NamespaceIncidentRecord.started_at >= cutoff
            )
            if namespace:
                incident_stmt = incident_stmt.where(NamespaceIncidentRecord.namespace == namespace)
            incident_count = len(list(s.scalars(incident_stmt)))

            return rollout_count + incident_count


class NamespaceCaseRepo:
    def __init__(self, engine):
        self._engine = engine

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self._engine) as session:
            yield session

    def create_namespace_case(self, **kwargs) -> NamespaceCaseRecord:
        case = NamespaceCaseRecord(**kwargs)
        with self.session() as s:
            s.add(case)
            s.commit()
            s.refresh(case)
        return case

    def get_or_create_open_case(
        self,
        cluster: str,
        namespace: str,
        *,
        slack_channel: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> NamespaceCaseRecord:
        stmt = (
            select(NamespaceCaseRecord)
            .where(
                NamespaceCaseRecord.cluster == cluster,
                NamespaceCaseRecord.namespace == namespace,
                NamespaceCaseRecord.status.in_(
                    [NamespaceCaseStatus.OPEN, NamespaceCaseStatus.QUIETING]
                ),
            )
            .order_by(NamespaceCaseRecord.id.desc())
        )
        with self.session() as s:
            existing = s.scalars(stmt).first()
            now = utcnow()
            if existing:
                existing.last_activity_at = now
                if slack_channel and not existing.slack_channel:
                    existing.slack_channel = slack_channel
                if metadata_json:
                    merged_metadata = dict(existing.metadata_json or {})
                    merged_metadata.update(metadata_json)
                    existing.metadata_json = merged_metadata
                s.commit()
                s.refresh(existing)
                return existing

            case = NamespaceCaseRecord(
                cluster=cluster,
                namespace=namespace,
                status=NamespaceCaseStatus.OPEN,
                opened_at=now,
                last_activity_at=now,
                slack_channel=slack_channel,
                metadata_json=metadata_json or {},
            )
            s.add(case)
            s.commit()
            s.refresh(case)
            return case

    def update_case_status(self, case_id: int, new_status: NamespaceCaseStatus) -> None:
        values: dict[str, Any] = {"status": new_status}
        now = utcnow()
        if new_status == NamespaceCaseStatus.OPEN:
            values["last_activity_at"] = now
            values["quieting_at"] = None
        elif new_status == NamespaceCaseStatus.QUIETING:
            values["quieting_at"] = now
        elif new_status == NamespaceCaseStatus.CLOSED:
            values["closed_at"] = now
        stmt = (
            update(NamespaceCaseRecord)
            .where(NamespaceCaseRecord.id == case_id)
            .values(**values)
        )
        with self.session() as s:
            s.execute(stmt)
            s.commit()

    def get_case_by_id(self, case_id: int) -> Optional[NamespaceCaseRecord]:
        stmt = select(NamespaceCaseRecord).where(NamespaceCaseRecord.id == case_id)
        with self.session() as s:
            return s.scalars(stmt).first()

    @staticmethod
    def _is_recovered_without_slack(issue: IssueRecord) -> bool:
        metadata = dict(issue.metadata_json or {})
        if metadata.get("recovered_before_notification") is True:
            return True
        if issue.status in {IssueStatus.RESOLVED, IssueStatus.SUPPRESSED} and metadata.get(
            "resolution_reason", ""
        ).lower().startswith("deployment recovered"):
            return True
        return False

    def _case_rows(
        self,
        *,
        statuses: list[NamespaceCaseStatus] | None = None,
        cause_family: str | None = None,
        include_closed: bool = True,
    ) -> list[dict[str, Any]]:
        stmt = select(NamespaceCaseRecord)
        if statuses:
            stmt = stmt.where(NamespaceCaseRecord.status.in_(statuses))
        elif not include_closed:
            stmt = stmt.where(NamespaceCaseRecord.status != NamespaceCaseStatus.CLOSED)
        stmt = stmt.order_by(
            NamespaceCaseRecord.last_activity_at.desc(),
            NamespaceCaseRecord.id.desc(),
        )
        rows: list[dict[str, Any]] = []
        with self.session() as s:
            cases = list(s.scalars(stmt))
            for case in cases:
                issues = list(
                    s.scalars(
                        select(IssueRecord)
                        .where(IssueRecord.namespace_case_id == case.id)
                        .order_by(IssueRecord.last_seen_at.desc(), IssueRecord.id.desc())
                    )
                )
                active_issues = [
                    issue for issue in issues
                    if issue.status in {IssueStatus.ACTIVE, IssueStatus.INVESTIGATING}
                ]
                if cause_family and not any(issue.cause_family == cause_family for issue in issues):
                    continue
                cause_counts: dict[str, int] = {}
                for issue in active_issues or issues:
                    cause_counts[issue.cause_family] = cause_counts.get(issue.cause_family, 0) + 1
                dominant_cause_family = None
                if cause_counts:
                    dominant_cause_family = sorted(
                        cause_counts.items(),
                        key=lambda item: (-item[1], item[0]),
                    )[0][0]
                recovered_quietly = (not active_issues) and any(
                    self._is_recovered_without_slack(issue) for issue in issues
                )
                if case.first_notified_at:
                    slack_state = "notified"
                elif active_issues:
                    slack_state = "notification_pending_window"
                elif recovered_quietly:
                    slack_state = "dashboard_only"
                else:
                    slack_state = "not_notified"
                rows.append(
                    {
                        "case": case,
                        "issues": issues,
                        "active_issues": active_issues,
                        "active_issue_count": len(active_issues),
                        "synthesized_namespace_issue_count": sum(
                            1
                            for issue in active_issues
                            if issue.scope == IssueScope.NAMESPACE
                        ),
                        "dominant_cause_family": dominant_cause_family,
                        "recovered_quietly": recovered_quietly,
                        "slack_state": slack_state,
                    }
                )
        return rows

    def get_case_overview_stats(self) -> dict[str, Any]:
        rows = self._case_rows()
        active_cause_counts: dict[str, int] = {}
        for row in rows:
            for issue in row["active_issues"]:
                active_cause_counts[issue.cause_family] = (
                    active_cause_counts.get(issue.cause_family, 0) + 1
                )
        top_cause_family = None
        if active_cause_counts:
            top_cause_family = sorted(
                active_cause_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[0][0]
        return {
            "open_cases": sum(1 for row in rows if row["case"].status == NamespaceCaseStatus.OPEN),
            "quieting_cases": sum(
                1 for row in rows if row["case"].status == NamespaceCaseStatus.QUIETING
            ),
            "closed_cases": sum(
                1 for row in rows if row["case"].status == NamespaceCaseStatus.CLOSED
            ),
            "active_child_issues": sum(row["active_issue_count"] for row in rows),
            "recovered_without_slack": sum(1 for row in rows if row["recovered_quietly"]),
            "cases_awaiting_notification_decision": sum(
                1 for row in rows if row["slack_state"] == "notification_pending_window"
            ),
            "top_cause_family": top_cause_family,
        }

    def list_cases(
        self,
        *,
        statuses: list[NamespaceCaseStatus] | None = None,
        cause_family: str | None = None,
        include_closed: bool = True,
    ) -> list[dict[str, Any]]:
        return self._case_rows(
            statuses=statuses,
            cause_family=cause_family,
            include_closed=include_closed,
        )

    def create_issue(self, **kwargs) -> IssueRecord:
        issue = IssueRecord(**kwargs)
        with self.session() as s:
            s.add(issue)
            s.commit()
            s.refresh(issue)
        return issue

    def get_or_create_issue(
        self,
        *,
        namespace_case_id: int,
        scope: IssueScope,
        resource_kind: str,
        resource_name: str,
        rollout_id: int | None = None,
        cause_family: str,
        issue_type: str,
        status: IssueStatus = IssueStatus.ACTIVE,
        metadata_json: dict[str, Any] | None = None,
    ) -> IssueRecord:
        stmt = (
            select(IssueRecord)
            .where(
                IssueRecord.namespace_case_id == namespace_case_id,
                IssueRecord.scope == scope,
                IssueRecord.resource_kind == resource_kind,
                IssueRecord.resource_name == resource_name,
                IssueRecord.rollout_id == rollout_id,
                IssueRecord.status.in_([IssueStatus.ACTIVE, IssueStatus.INVESTIGATING]),
            )
            .order_by(IssueRecord.id.desc())
        )
        with self.session() as s:
            existing = s.scalars(stmt).first()
            now = utcnow()
            if existing:
                existing.cause_family = cause_family
                existing.issue_type = issue_type
                existing.status = status
                existing.last_seen_at = now
                if metadata_json:
                    merged_metadata = dict(existing.metadata_json or {})
                    merged_metadata.update(metadata_json)
                    existing.metadata_json = merged_metadata
                s.commit()
                s.refresh(existing)
                return existing

            issue = IssueRecord(
                namespace_case_id=namespace_case_id,
                scope=scope,
                resource_kind=resource_kind,
                resource_name=resource_name,
                rollout_id=rollout_id,
                cause_family=cause_family,
                issue_type=issue_type,
                status=status,
                first_seen_at=now,
                last_seen_at=now,
                metadata_json=metadata_json or {},
            )
            s.add(issue)
            s.commit()
            s.refresh(issue)
            return issue

    def append_issue_observation(
        self,
        issue_id: int,
        *,
        source: str,
        signal_type: str,
        payload_json: dict[str, Any] | None = None,
    ) -> IssueObservationRecord:
        observation = IssueObservationRecord(
            issue_id=issue_id,
            source=source,
            signal_type=signal_type,
            payload_json=payload_json or {},
        )
        with self.session() as s:
            s.add(observation)
            s.commit()
            s.refresh(observation)
        return observation

    def get_issue_by_id(self, issue_id: int) -> Optional[IssueRecord]:
        stmt = select(IssueRecord).where(IssueRecord.id == issue_id)
        with self.session() as s:
            return s.scalars(stmt).first()

    def update_issue_status(
        self,
        issue_id: int,
        new_status: IssueStatus,
        *,
        metadata_json: dict[str, Any] | None = None,
    ) -> None:
        with self.session() as s:
            issue = s.get(IssueRecord, issue_id)
            if issue is None:
                return
            issue.status = new_status
            issue.last_seen_at = utcnow()
            if new_status in {IssueStatus.RESOLVED, IssueStatus.SUPPRESSED}:
                issue.resolved_at = utcnow()
            if metadata_json:
                merged_metadata = dict(issue.metadata_json or {})
                merged_metadata.update(metadata_json)
                issue.metadata_json = merged_metadata
            s.commit()

    def append_issue_analysis(
        self,
        issue_id: int,
        *,
        analysis: Analysis,
        model_name: str,
        prompt_version: str = "v1",
        reduced_context: ReducedContext | dict[str, Any] | None = None,
    ) -> Optional[AnalysisRecord]:
        with self.session() as s:
            issue = s.get(IssueRecord, issue_id)
            if issue is None:
                return None
            if reduced_context is None:
                reduced_payload: dict[str, Any] = {
                    "issue_id": issue.id,
                    "scope": issue.scope.value,
                    "resource_kind": issue.resource_kind,
                    "resource_name": issue.resource_name,
                }
            elif isinstance(reduced_context, ReducedContext):
                reduced_payload = reduced_context.model_dump(mode="json")
            else:
                reduced_payload = dict(reduced_context)

            record = AnalysisRecord(
                rollout_id=issue.rollout_id,
                model_name=model_name,
                prompt_version=prompt_version,
                reduced_context=reduced_payload,
                analysis=analysis.model_dump(mode="json"),
            )
            s.add(record)
            s.flush()
            issue.analysis_id = record.id
            issue.last_seen_at = utcnow()
            s.commit()
            s.refresh(record)
            return record

    def list_active_issues(self, namespace_case_id: int) -> list[IssueRecord]:
        stmt = (
            select(IssueRecord)
            .where(
                IssueRecord.namespace_case_id == namespace_case_id,
                IssueRecord.status.in_([IssueStatus.ACTIVE, IssueStatus.INVESTIGATING]),
            )
            .order_by(IssueRecord.id.asc())
        )
        with self.session() as s:
            return list(s.scalars(stmt))

    def list_case_issues(self, namespace_case_id: int) -> list[IssueRecord]:
        stmt = (
            select(IssueRecord)
            .where(IssueRecord.namespace_case_id == namespace_case_id)
            .order_by(IssueRecord.id.asc())
        )
        with self.session() as s:
            return list(s.scalars(stmt))

    def get_case_detail(self, case_id: int) -> dict[str, Any]:
        case = self.get_case_by_id(case_id)
        if case is None:
            return {
                "case": None,
                "active_issues": [],
                "resolved_issues": [],
                "observations": [],
                "linked_rollouts": [],
            }
        issues = self.list_case_issues(case_id)
        issue_ids = [issue.id for issue in issues]
        active_issues = [
            issue for issue in issues
            if issue.status in {IssueStatus.ACTIVE, IssueStatus.INVESTIGATING}
        ]
        resolved_issues = [
            issue for issue in issues
            if issue.status in {IssueStatus.RESOLVED, IssueStatus.SUPPRESSED}
        ]
        observations: list[IssueObservationRecord] = []
        linked_rollouts: list[Rollout] = []
        with self.session() as s:
            if issue_ids:
                observations = list(
                    s.scalars(
                        select(IssueObservationRecord)
                        .where(IssueObservationRecord.issue_id.in_(issue_ids))
                        .order_by(
                            IssueObservationRecord.observed_at.asc(),
                            IssueObservationRecord.id.asc(),
                        )
                    )
                )
            rollout_ids = [issue.rollout_id for issue in issues if issue.rollout_id is not None]
            if rollout_ids:
                linked_rollouts = list(
                    s.scalars(
                        select(Rollout)
                        .where(Rollout.id.in_(rollout_ids))
                        .order_by(Rollout.id.asc())
                    )
                )
        return {
            "case": case,
            "active_issues": active_issues,
            "resolved_issues": resolved_issues,
            "observations": observations,
            "linked_rollouts": linked_rollouts,
        }

    def get_parent_case_for_rollout(self, rollout_id: int) -> Optional[dict[str, Any]]:
        stmt = (
            select(IssueRecord)
            .where(IssueRecord.rollout_id == rollout_id)
            .order_by(IssueRecord.id.desc())
        )
        with self.session() as s:
            issue = s.scalars(stmt).first()
            if issue is None:
                return None
            case = s.get(NamespaceCaseRecord, issue.namespace_case_id)
            if case is None:
                return None
            return {"case": case, "issue": issue}


class WorkItemRepo:
    def __init__(self, engine):
        self._engine = engine

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self._engine) as session:
            yield session

    def enqueue_work_item(self, **kwargs) -> WorkItemRecord:
        work_item = WorkItemRecord(**kwargs)
        with self.session() as s:
            s.add(work_item)
            s.commit()
            s.refresh(work_item)
        return work_item

    def claim_work_item(self, kind: WorkItemKind) -> Optional[WorkItemRecord]:
        stmt = (
            select(WorkItemRecord)
            .where(
                WorkItemRecord.kind == kind,
                WorkItemRecord.status == WorkItemStatus.PENDING,
            )
            .order_by(WorkItemRecord.scheduled_at.asc(), WorkItemRecord.id.asc())
        )
        with self.session() as s:
            work_item = s.scalars(stmt).first()
            if work_item is None:
                return None
            work_item.status = WorkItemStatus.RUNNING
            work_item.started_at = utcnow()
            work_item.attempts = (work_item.attempts or 0) + 1
            s.commit()
            s.refresh(work_item)
            return work_item

    def update_work_item_status(
        self,
        work_item_id: int,
        status: WorkItemStatus,
        *,
        error: str | None = None,
    ) -> None:
        with self.session() as s:
            work_item = s.get(WorkItemRecord, work_item_id)
            if work_item is None:
                return
            work_item.status = status
            if status in {WorkItemStatus.COMPLETED, WorkItemStatus.FAILED}:
                work_item.completed_at = utcnow()
            if error is not None:
                work_item.error = error
            s.commit()
