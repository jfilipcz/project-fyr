"""Database models and repository helpers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Iterator, Optional, Any

from sqlalchemy import JSON, DateTime, Enum as SAEnum, Integer, String, create_engine, select, update, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .models import Analysis, AnalysisStatus, NotifyStatus, ReducedContext, RolloutStatus, NamespaceIncidentType, NamespaceIncidentStatus


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
        SAEnum(AnalysisStatus), default=AnalysisStatus.PENDING
    )
    notify_status: Mapped[NotifyStatus] = mapped_column(
        SAEnum(NotifyStatus), default=NotifyStatus.PENDING
    )
    team: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    slack_channel: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class AnalysisRecord(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rollout_id: Mapped[int] = mapped_column(Integer, index=True)
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


def init_db(database_url: str):
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    return engine


class RolloutRepo:
    def __init__(self, engine):
        self._engine = engine

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
        stmt = select(Rollout).where(
            Rollout.cluster == cluster,
            Rollout.status == RolloutStatus.FAILED,
            Rollout.analysis_status != AnalysisStatus.DONE,
        )
        with self.session() as s:
            return list(s.scalars(stmt))

    def list_recent(self, limit: int = 50) -> list[Rollout]:
        stmt = select(Rollout).order_by(Rollout.id.desc()).limit(limit)
        with self.session() as s:
            return list(s.scalars(stmt))

    def list_by_status(self, status: str, limit: int = 50) -> list[Rollout]:
        """List rollouts filtered by status."""
        # Convert string to RolloutStatus enum
        try:
            status_enum = RolloutStatus[status.upper()]
        except (KeyError, AttributeError):
            # If invalid status, return empty list
            return []
        
        stmt = select(Rollout).where(
            Rollout.status == status_enum
        ).order_by(Rollout.id.desc()).limit(limit)
        with self.session() as s:
            return list(s.scalars(stmt))

    def list_by_namespace(self, namespace: str, limit: int = 50) -> list[Rollout]:
        """List rollouts filtered by namespace."""
        stmt = select(Rollout).where(
            Rollout.namespace == namespace
        ).order_by(Rollout.id.desc()).limit(limit)
        with self.session() as s:
            return list(s.scalars(stmt))

    def get_stats(self, hours: int = 24) -> dict[str, int]:
        """Get rollout statistics for the last N hours."""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        
        # Total rollouts in window
        stmt_total = select(Rollout).where(Rollout.started_at >= cutoff)
        
        # Success count
        stmt_success = select(Rollout).where(
            Rollout.started_at >= cutoff,
            Rollout.status == RolloutStatus.SUCCESS
        )
        
        # Failed count
        stmt_failed = select(Rollout).where(
            Rollout.started_at >= cutoff,
            Rollout.status == RolloutStatus.FAILED
        )

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

    def get_recent_failures(self, limit: int = 50, hours: int = 24) -> list[tuple[Rollout, Optional[AnalysisRecord]]]:
        """Get failed rollouts with their analysis records for the last N hours."""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        
        # Join Rollout with AnalysisRecord
        stmt = (
            select(Rollout, AnalysisRecord)
            .outerjoin(AnalysisRecord, Rollout.analysis_id == AnalysisRecord.id)
            .where(
                Rollout.status == RolloutStatus.FAILED,
                Rollout.started_at >= cutoff,
                Rollout.analysis_status == AnalysisStatus.DONE
            )
            .order_by(Rollout.id.desc())
            .limit(limit)
        )
        
        with self.session() as s:
            # Result is a list of Row objects (tuples)
            results = s.execute(stmt).all()
            # Convert to list of tuples for easier consumption
            return [(r.Rollout, r.AnalysisRecord) for r in results]

    def list_by_status_and_namespace(self, status: str, namespace: str, limit: int = 50) -> list[Rollout]:
        """List rollouts filtered by both status and namespace."""
        try:
            status_enum = RolloutStatus[status.upper()]
        except (KeyError, AttributeError):
            return []
        
        stmt = select(Rollout).where(
            Rollout.status == status_enum,
            Rollout.namespace == namespace
        ).order_by(Rollout.id.desc()).limit(limit)
        with self.session() as s:
            return list(s.scalars(stmt))

    def get_by_id(self, rollout_id: int) -> Optional[Rollout]:
        stmt = select(Rollout).where(Rollout.id == rollout_id)
        with self.session() as s:
            return s.scalars(stmt).first()

    def get_analysis(self, analysis_id: int) -> Optional[AnalysisRecord]:
        stmt = select(AnalysisRecord).where(AnalysisRecord.id == analysis_id)
        with self.session() as s:
            return s.scalars(stmt).first()

    def update_status(self, rollout_id: int, new_status: RolloutStatus, **timestamps) -> None:
        stmt = (
            update(Rollout)
            .where(Rollout.id == rollout_id)
            .values(status=new_status, **timestamps)
        )
        with self.session() as s:
            s.execute(stmt)
            s.commit()

    def update_notify_status(self, rollout_id: int, new_status: NotifyStatus) -> None:
        stmt = update(Rollout).where(Rollout.id == rollout_id).values(notify_status=new_status)
        with self.session() as s:
            s.execute(stmt)
            s.commit()

    def append_analysis(
        self,
        rollout_id: int,
        *,
        reduced_context: ReducedContext,
        analysis: Analysis,
        model_name: str,
        prompt_version: str = "v1",
    ) -> None:
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
            status_stmt = (
                update(Rollout)
                .where(Rollout.id == rollout_id)
                .values(
                    analysis_id=record.id,
                    analysis_status=AnalysisStatus.DONE,
                    completed_at=analysis.created_at,
                )
            )
            s.execute(status_stmt)
            s.commit()
    def update_metadata(
        self,
        rollout_id: int,
        *,
        metadata_json: Optional[dict] = None,
        team: Optional[str] = None,
        slack_channel: Optional[str] = None,
    ) -> None:
        values: dict[str, Any] = {}
        if metadata_json is not None:
            values["metadata_json"] = metadata_json
        if team is not None:
            values["team"] = team
        if slack_channel is not None:
            values["slack_channel"] = slack_channel
        if not values:
            return
        stmt = update(Rollout).where(Rollout.id == rollout_id).values(**values)
        with self.session() as s:
            s.execute(stmt)
            s.commit()


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
                resolved_at=datetime.utcnow()
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
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        
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



