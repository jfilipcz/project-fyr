# Project Fyr

Project Fyr is an agentic AI assistant that watches Kubernetes deployments, inspects failures, enriches them with CI metadata, and posts concise guidance to Slack. It contains:

- **Watcher/Analyzer** – streams deployment events, captures rollout context (pods, events, logs) and runs the LangChain-based analyzer.
- **GitLab Ingestor** – FastAPI service that receives CI metadata (project, commit, pipeline URL, Slack channel, team) and stores it with the rollout for later notifications.
- **Slack Notifier** – built into the analyzer loop; formats summaries with severity, probable cause, and next steps.
- **Helm chart** – deploys the watcher plus the GitLab ingestor with configurable commands/arguments, ConfigMap-driven settings, and optional RBAC.

## Local Development

### Requirements
- Python 3.10+
- SQLite (default) or another SQLAlchemy-compatible database URL
- Access to a Kubernetes cluster/context for the watcher

### Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Run the watcher/analyzer loop (defaults to SQLite file `project_fyr.db`):
```bash
python -m project_fyr.service
```

Run the GitLab ingestor locally (listens on `localhost:8000`):
```bash
uvicorn project_fyr.gitlab_api:app --reload --port 8000
```

Use `pytest`/`ruff` from the optional `dev` extras for testing and linting.

## Configuration Reference

All services read settings via the `PROJECT_FYR_*` environment variables:

| Variable | Description | Default |
| --- | --- | --- |
| `PROJECT_FYR_DATABASE_URL` | SQLAlchemy URL (MySQL/Postgres recommended in production) | `sqlite:///./project_fyr.db` |
| `PROJECT_FYR_K8S_CLUSTER_NAME` | Human-readable label for alerts | `ci-cluster` |
| `PROJECT_FYR_ROLLOUT_TIMEOUT_SECONDS` | Max rollout age before marking failed | `900` |
| `PROJECT_FYR_SLACK_BOT_TOKEN` | Bot token for Slack notifications | empty |
| `PROJECT_FYR_SLACK_DEFAULT_CHANNEL` | Fallback Slack channel | empty |
| `PROJECT_FYR_OPENAI_API_KEY` | Enables LangChain analyzer | empty (analyzer falls back to heuristic text) |
| `PROJECT_FYR_LANGCHAIN_MODEL_NAME` | LLM to call through LangChain | `gpt-4o-mini` |
| `PROJECT_FYR_LOG_TAIL_SECONDS` | Log window for context collection | `300` |
| `PROJECT_FYR_MAX_LOG_LINES` | Max log lines per rollout | `200` |
| `PROJECT_FYR_REDUCER_MAX_EVENTS` | Max distinct events kept | `20` |
| `PROJECT_FYR_REDUCER_MAX_CLUSTERS` | Max log clusters kept | `8` |

GitLab payloads should POST JSON shaped like `project_fyr.models.DeploymentNotifyRequest` to `/deployments` (or `/ingest` for backward compatibility).

## Helm Deployment

The chart in `helm/project-fyr` deploys the watcher/analyzer and, optionally, the GitLab ingestor.

### Quick start
```bash
helm upgrade --install project-fyr ./helm/project-fyr \
  --set config.databaseUrl="mysql+pymysql://fyr:secret@mysql/fyr" \
  --set config.slackBotToken="$SLACK_TOKEN" \
  --set config.slackDefaultChannel="#deployments" \
  --set config.openaiApiKey="$OPENAI_API_KEY"
```

Key values:
- `watcher.*` – replica count, command/args, scheduling hints for the watcher.
- `gitlabIngestor.*` – enable/disable, image overrides, service type/port, runtime command (defaults to `uvicorn project_fyr.gitlab_api:app`).
- `config.*` – populates the ConfigMap consumed by both pods, covering every `PROJECT_FYR_*` setting.
- `serviceAccount.*` – watcher RBAC identity (set `create=false` + `name` to reuse an existing SA bound to the necessary cluster roles).

Mount production secrets via external `Secret` objects and reference them using `envFrom`/`extraEnv` patches if desired—the chart keeps ConfigMap values simple for local testing.

