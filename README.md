# Project Fyr

Project Fyr is an agentic AI assistant that watches Kubernetes deployments, inspects failures, enriches them using namespace annotations, and posts concise guidance to Slack. It contains:

- **Watcher** – streams deployment events and tracks rollout status.
- **Investigator Agent** – an autonomous LangChain agent that investigates failures by actively querying the cluster (Pods, Events, Logs, ArgoCD, Helm).
- **Slack Notifier** – posts the agent's analysis (summary, root cause, remediation) to Slack.
- **Namespace annotations** – opt-in metadata (Slack channel, owning team, etc.) stored on the Kubernetes namespace.
- **Triage helper** – heuristics to suggest the responsible team (infra, security, application).
- **Helm chart** – deploys the watcher/analyzer with configurable settings.

## Local Development

### Requirements
- Python 3.10+
- SQLite (default) or another SQLAlchemy-compatible database URL
- Access to a Kubernetes cluster/context

### Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

### Container images

Two Dockerfiles are available:

- `Dockerfile` – watcher/reconciler service (`python -m project_fyr.watcher_service`).
- `Dockerfile.analyzer` – analyzer/agent service (`python -m project_fyr.analyzer_service`).

You can also use the provided `Makefile`:

```bash
make build-watcher TAG=dev
make build-analyzer TAG=dev
# cross-compile + push to registry
make push-watcher TAG=1.0.0 REGISTRY=ghcr.io/my-org PLATFORM=linux/amd64
```

Example build/run:

```bash
docker build -f Dockerfile -t project-fyr-watcher:latest .
docker build -f Dockerfile.analyzer -t project-fyr-analyzer:latest .

docker run --rm --name watcher \
  -e PROJECT_FYR_DATABASE_URL="sqlite:////data/project_fyr.db" \
  -v $(pwd)/data:/data \
  project-fyr-watcher:latest

docker run --rm --name analyzer \
  -e PROJECT_FYR_DATABASE_URL="sqlite:////data/project_fyr.db" \
  -e PROJECT_FYR_SLACK_BOT_TOKEN="$SLACK_TOKEN" \
  -e PROJECT_FYR_OPENAI_API_KEY="$OPENAI_API_KEY" \
  project-fyr-analyzer:latest
```

Override environment variables (or inject secrets) to point at your production database and tokens.

#### Building explicit x86_64 images

Use `docker buildx` (or an equivalent builder) to force `linux/amd64` output even on Apple Silicon:

```bash
docker buildx build --platform linux/amd64 \
  -f Dockerfile \
  -t ghcr.io/example/project-fyr-watcher:amd64 .

docker buildx build --platform linux/amd64 \
  -f Dockerfile.analyzer \
  -t ghcr.io/example/project-fyr-analyzer:amd64 .
```

Add `--push` to publish to your registry once the builds succeed.

Run the watcher service (defaults to SQLite file `project_fyr.db`):
```bash
python -m project_fyr.watcher_service
```

Run the analyzer/agent:
```bash
python -m project_fyr.analyzer_service
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
| `PROJECT_FYR_OPENAI_API_KEY` | Required for the Investigator Agent | empty |
| `PROJECT_FYR_LANGCHAIN_MODEL_NAME` | LLM to use for the agent | `gpt-4o-mini` |


When deploying with External Secret Operator, set `secrets.existingSecret` (Helm value) so the watcher pod pulls credentials/keys from that Secret via `envFrom`.

### Namespace metadata

Add annotations to each namespace you want monitored:

| Annotation | Purpose |
| --- | --- |
| `project-fyr/slack-channel` | Slack channel for rollout notifications. |
| `project-fyr/team` | Owning team (included in Slack summaries and metadata). |

You can add any other annotations with the `project-fyr/` prefix; they are captured in the rollout metadata blob for later use.

Example:
```bash
kubectl annotate namespace payments \
  project-fyr/slack-channel="#payments-deploys" \
  project-fyr/team="Payments SRE" --overwrite
```

## Helm Deployment

The chart in `helm/project-fyr` deploys the watcher/analyzer (with an optional MySQL dependency for dev/test clusters).

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
- `config.*` – populates the ConfigMap consumed by the watcher, covering every `PROJECT_FYR_*` setting.
- `serviceAccount.*` – watcher RBAC identity (set `create=false` + `name` to reuse an existing SA bound to the necessary cluster roles).
- `rbac.create` – automatically create ClusterRole and ClusterRoleBinding with required permissions (default: `true`).
- `secrets.existingSecret` – reference to a Secret managed by External Secret Operator (or any other controller) that injects sensitive `PROJECT_FYR_*` values.

Mount production secrets via external `Secret` objects and reference them using `envFrom`/`extraEnv` patches if desired—the chart keeps ConfigMap values simple for local testing. Namespace annotations control Slack routing/metadata instead of a dedicated GitLab service.

> The analyzer runs as a separate deployment/workload. Use `Dockerfile.analyzer` (or your own manifest) to run `python -m project_fyr.analyzer_service` pointing at the same database.

### Optional MySQL dependency

For development and demo clusters you can enable the bundled Bitnami MySQL chart:

```bash
cat <<'VALUES' > dev-values.yaml
mysql:
  enabled: true
  auth:
    username: projectfyr
    password: projectfyr
    database: projectfyr

config:
  # The chart renders config strings with `tpl`, so you can reference release metadata.
  databaseUrl: >-
    {{ printf "mysql+pymysql://%s:%s@%s-mysql:3306/%s" .Values.mysql.auth.username .Values.mysql.auth.password .Release.Name .Values.mysql.auth.database }}
VALUES

helm upgrade --install project-fyr ./helm/project-fyr -f dev-values.yaml
```

The dependency is disabled by default; in production you should continue pointing `PROJECT_FYR_DATABASE_URL` at your managed database and rely on `secrets.existingSecret` (ESO) to mount credentials.

## Triage heuristics

After the analyzer produces its summary, a lightweight heuristic classifier labels each failure with a next-investigator team:

- **application** – default when no infra/security signals are found.
- **infra** – scheduling/resource/network/storage issues (keywords such as `FailedScheduling`, `Insufficient`, `CNI`, `PersistentVolume`).
- **security** – permission/secret/TLS problems (keywords such as `Forbidden`, `Unauthorized`, `certificate`, `secret`).

The triage team and rationale are included in the Slack notification metadata. Extend `project_fyr/triage.py` if you need richer routing or additional teams.
