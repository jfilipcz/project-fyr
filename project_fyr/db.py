"""Database models and repository helpers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

from sqlalchemy import JSON, DateTime, Enum as SAEnum, Integer, String, create_engine, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .models import Analysis, AnalysisStatus, NotifyStatus, ReducedContext, RolloutStatus


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
    context_summary: Mapped[str] = mapped_column(String)  # JSON or text summary
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
    type: Mapped[str] = mapped_column(String(50))  # rollout | alert
    status: Mapped[str] = mapped_column(String(50), default="pending")
    
    # Polymorphic-ish FKs
    rollout_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    alert_batch_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    analysis_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)



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

