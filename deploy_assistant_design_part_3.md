# Project Fyr – Design Document (Part 3)

This part provides **implementation guidance for Codex or other code generators**. It focuses on:

- Recommended folder/package layout
- Shared utilities
- Database schema + SQLAlchemy models
- Service initialization patterns
- Error handling conventions
- Observability (logging/metrics)
- Deployment notes (K8s manifests, RBAC, secrets)

It serves as a *bridge* between the conceptual design in Parts 1–2 and actual code implementation.

---

# 16. Recommended Repository Structure

A clean monorepo structure for the four services:

```
project-fyr/
├── services/
│   ├── watcher/
│   │   ├── main.py
│   │   ├── k8s.py
│   │   ├── logic.py
│   │   └── db.py
│   ├── gitlab-ingestor/
│   │   ├── main.py
│   │   ├── api.py
│   │   └── db.py
│   ├── analyzer/
│   │   ├── main.py
│   │   ├── collector.py
│   │   ├── reduction.py
│   │   ├── chains.py
│   │   └── db.py
│   └── notifier/
│       ├── main.py
│       ├── slack_client.py
│       └── db.py
│
├── shared/
│   ├── models/
│   │   ├── rollout.py
│   │   ├── analysis.py
│   │   ├── context.py
│   │   └── reduction.py
│   ├── db/
│   │   ├── base.py
│   │   └── engine.py
│   ├── logging.py
│   └── settings.py
│
├── migrations/
├── charts/ (optional Helm chart)
├── scripts/ (local dev tools)
└── README.md
```

**Notes:**
- Each service is fully isolated in its own folder → one Docker image per service.
- Shared code goes into `shared/` and is imported by each service.
- Database schemas/models live in `shared/models`, so all services use the same definitions.
- `shared/settings.py` uses environment-driven configuration (`pydantic-settings` recommended).

---

# 17. Database Schema & SQLAlchemy Models

The system uses **MySQL** with a small number of tables:

- `rollouts`
- `analyses`
- (optional) `analysis_metrics` for LLM cost tracking

## 17.1. SQL Schema

### Table: rollouts

```
id BIGINT PRIMARY KEY AUTO_INCREMENT,
cluster VARCHAR(255) NOT NULL,
namespace VARCHAR(255) NOT NULL,
deployment VARCHAR(255) NOT NULL,
generation INT NOT NULL,
status ENUM('PENDING','ROLLING_OUT','SUCCESS','FAILED') NOT NULL,
origin ENUM('k8s','gitlab','mixed') DEFAULT 'k8s',
started_at DATETIME NULL,
completed_at DATETIME NULL,
failed_at DATETIME NULL,
analysis_id BIGINT NULL,
analysis_status ENUM('PENDING','DONE','FAILED') NULL,
notify_status ENUM('PENDING','SENT','FAILED') NULL,

-- Git metadata
git_project VARCHAR(255) NULL,
git_commit VARCHAR(255) NULL,
pipeline_url TEXT NULL,
mr_url TEXT NULL,
team VARCHAR(255) NULL,
slack_channel VARCHAR(255) NULL,

INDEX idx_lookup(cluster, namespace, deployment, generation),
INDEX idx_status(cluster, status)
```

### Table: analyses

```
id BIGINT PRIMARY KEY AUTO_INCREMENT,
rollout_id BIGINT NOT NULL,
model_name VARCHAR(255) NOT NULL,
prompt_version VARCHAR(255) NOT NULL,
reduced_context JSON NOT NULL,
analysis JSON NOT NULL,
created_at DATETIME NOT NULL,
INDEX idx_rollout(rollout_id)
```

---

## 17.2. SQLAlchemy Models

Recommended approach: **SQLAlchemy 2.0 declarative models**.

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Enum, JSON, DateTime, String, Integer
from datetime import datetime


class Base(DeclarativeBase):
    pass


class Rollout(Base):
    __tablename__ = "rollouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster: Mapped[str]
    namespace: Mapped[str]
    deployment: Mapped[str]
    generation: Mapped[int]

    status: Mapped[str]
    origin: Mapped[str] = mapped_column(default="k8s")

    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    failed_at: Mapped[datetime | None]

    analysis_id: Mapped[int | None]
    analysis_status: Mapped[str | None]
    notify_status: Mapped[str | None]

    # Git metadata
    git_project: Mapped[str | None]
    git_commit: Mapped[str | None]
    pipeline_url: Mapped[str | None]
    mr_url: Mapped[str | None]
    team: Mapped[str | None]
    slack_channel: Mapped[str | None]


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    rollout_id: Mapped[int]
    model_name: Mapped[str]
    prompt_version: Mapped[str]
    reduced_context: Mapped[dict] = mapped_column(JSON)
    analysis: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
```

---

# 18. Shared Configuration Layer

Use `pydantic-settings` for environment variables.

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    slack_token: str | None = None
    cluster_name: str = "ci-cluster"
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
```

Each service loads:

```python
settings = Settings()
```

---

# 19. Logging & Observability

Use `structlog` for consistent JSON logging.

Example shared logging setup:

```python
import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)

logger = structlog.get_logger()
```

Each service then logs:

```python
logger.info("rollout_failed", rollout_id=r.id, reason="crashloop")
logger.error("notify_failed", rollout_id=r.id, error=str(e))
```

---

# 20. Error Handling Conventions

Consistent rule across all services:

- **NEVER crash the main loop** → always catch exceptions.
- **Short backoff** on recoverable errors.
- **Long backoff** on repeated failures.
- Mark rollout analysis/notification as `FAILED` when appropriate.

Patterns:

```python
try:
    ...
except Exception as e:
    logger.exception("analysis_failed", error=str(e))
    rollout_repo.mark_analysis_failed(r.id, error=str(e))
    continue
```

---

# 21. Deployment Considerations (Kubernetes)

Each service runs in its own Deployment with its own Docker image.

## 21.1. RBAC

### Watcher requires:
- `get`, `list`, `watch` on:
  - `deployments`, `replicasets`, `pods`, `events`

### Analyzer requires:
- `get`, `list` on same resources
- `get` on `pods/log`

### Ingestor & Notifier:
- No Kubernetes permissions needed.

---

## 21.2. Secrets

- `MYSQL_URL`
- `SLACK_BOT_TOKEN`
- `GITLAB_INGESTOR_TOKEN`
- `OPENAI_API_KEY`

Mounted as env vars via K8s `Secret`.

---

## 21.3. Horizontal Scaling

### Watcher:
- **1 replica only** (multiple watchers cause duplicate rollout creation).

### Analyzer:
- Can scale out **if** using DB row-level locking when picking work.

### Notifier:
- Can scale out safely (idempotent if DB update is atomic).

---

# 22. Testing Strategy

## 22.1. Unit tests
- Reduction pipeline
- Chains (with dummy models)
- Rollout state machine
- DB repositories (using sqlite in-memory)

## 22.2. Integration tests
- Fake K8s clusters using `kind` or `k3d`
- GitLab webhook simulation

## 22.3. Load tests
- Analyzer throughput
- Slack notification rate limits

---

This completes **Part 3** of the Project Fyr design: a full implementation-facing guide for Codex or human developers.

