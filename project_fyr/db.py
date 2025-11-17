"""Database models and repository helpers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from sqlalchemy import JSON, Column, DateTime, Enum as SAEnum, Integer, String, create_engine, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .models import (
    Analysis,
    AnalysisStatus,
    DeploymentNotifyRequest,
    NotifyStatus,
    ReducedContext,
    RolloutStatus,
)


class Base(DeclarativeBase):
    pass


class Rollout(Base):
    __tablename__ = "rollouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster: Mapped[str] = mapped_column(String, index=True)
    namespace: Mapped[str] = mapped_column(String, index=True)
    deployment: Mapped[str] = mapped_column(String, index=True)
    generation: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(SAEnum(RolloutStatus), default=RolloutStatus.PENDING)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    origin: Mapped[str] = mapped_column(String, default="k8s")
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, default=dict)
    analysis_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analysis_status: Mapped[AnalysisStatus] = mapped_column(
        SAEnum(AnalysisStatus), default=AnalysisStatus.PENDING
    )
    notify_status: Mapped[NotifyStatus] = mapped_column(
        SAEnum(NotifyStatus), default=NotifyStatus.PENDING
    )
    git_project: Mapped[str | None] = mapped_column(String, nullable=True)
    git_commit: Mapped[str | None] = mapped_column(String, nullable=True)
    pipeline_url: Mapped[str | None] = mapped_column(String, nullable=True)
    mr_url: Mapped[str | None] = mapped_column(String, nullable=True)
    team: Mapped[str | None] = mapped_column(String, nullable=True)
    slack_channel: Mapped[str | None] = mapped_column(String, nullable=True)


class AnalysisRecord(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rollout_id: Mapped[int] = mapped_column(Integer, index=True)
    model_name: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str] = mapped_column(String)
    reduced_context: Mapped[dict] = mapped_column(JSON)
    analysis: Mapped[dict] = mapped_column(JSON)
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

    def get_by_key(self, cluster: str, namespace: str, deployment: str, generation: int) -> Rollout | None:
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

    def update_status(self, rollout_id: int, new_status: RolloutStatus, **timestamps) -> None:
        stmt = (
            update(Rollout)
            .where(Rollout.id == rollout_id)
            .values(status=new_status, **timestamps)
        )
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
                reduced_context=reduced_context.model_dump(),
                analysis=analysis.model_dump(),
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

    def upsert_gitlab_metadata(self, payload: DeploymentNotifyRequest) -> Rollout:
        """Persist GitLab metadata, creating a rollout row if needed."""
        git = payload.git
        git_fields = {
            "git_project": git.project,
            "git_commit": git.commit,
            "pipeline_url": git.pipeline_url,
            "mr_url": git.mr_url,
        }
        extra_fields = {
            "team": payload.team,
            "slack_channel": payload.slack_channel,
        }
        metadata_payload = {
            "git": git.model_dump(),
            "team": payload.team,
            "slack_channel": payload.slack_channel,
        }
        with self.session() as s:
            stmt = select(Rollout).where(
                Rollout.cluster == payload.cluster,
                Rollout.namespace == payload.namespace,
                Rollout.deployment == payload.deployment,
                Rollout.generation == payload.generation,
            )
            rollout = s.scalars(stmt).first()
            now = datetime.utcnow()
            if rollout:
                for field, value in {**git_fields, **extra_fields}.items():
                    if value is not None:
                        setattr(rollout, field, value)
                metadata = rollout.metadata_json or {}
                metadata.update({k: v for k, v in metadata_payload.items() if v is not None})
                rollout.metadata_json = metadata
            else:
                rollout = Rollout(
                    cluster=payload.cluster,
                    namespace=payload.namespace,
                    deployment=payload.deployment,
                    generation=payload.generation,
                    status=RolloutStatus.PENDING,
                    started_at=now,
                    origin="gitlab",
                    metadata_json={k: v for k, v in metadata_payload.items() if v is not None},
                    **{k: v for k, v in {**git_fields, **extra_fields}.items() if v is not None},
                )
                s.add(rollout)
            s.commit()
            s.refresh(rollout)
            return rollout
