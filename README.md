# Project Fyr

Project Fyr is an agentic AI assistant that watches Kubernetes deployments, inspects failures, enriches them using namespace annotations, and posts concise guidance to Slack. It contains:

- **Watcher** – streams deployment events and tracks rollout status.
- **Analyzer** – an autonomous LangChain agent that investigates failures by actively querying the cluster (Pods, Events, Logs, ArgoCD, Helm, Prometheus).
- **Dashboard** – a FastAPI web UI for browsing rollouts, viewing analyses, and triggering on-demand investigations.
- **Slack Notifier** – posts the agent's analysis (summary, root cause, remediation) to Slack.
- **Namespace annotations** – opt-in metadata (Slack channel, owning team, etc.) stored on the Kubernetes namespace.
- **Triage helper** – heuristics to suggest the responsible team (infra, security, application).
- **Helm chart** – deploys all three services (watcher, analyzer, dashboard) with configurable settings.

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

A single `Dockerfile` builds all three services:

- **Watcher**: `python -m project_fyr.watcher_service`
- **Analyzer**: `python -m project_fyr.analyzer_service`
- **Dashboard**: `python -m project_fyr.dashboard`

You can use the provided `Makefile`:

```bash
make build TAG=dev
make push TAG=1.0.0 REGISTRY=ghcr.io/my-org
# cross-compile for specific platform
make build TAG=dev PLATFORM=linux/amd64
```

Example build/run:

```bash
docker build -t project-fyr:latest .

# Run watcher
docker run --rm --name watcher \
  -e PROJECT_FYR_DATABASE_URL="sqlite:////data/project_fyr.db" \
  -v $(pwd)/data:/data \
  project-fyr:latest \
  python -m project_fyr.watcher_service

# Run analyzer
docker run --rm --name analyzer \
  -e PROJECT_FYR_DATABASE_URL="sqlite:////data/project_fyr.db" \
  -e PROJECT_FYR_SLACK_BOT_TOKEN="$SLACK_TOKEN" \
  -e PROJECT_FYR_OPENAI_API_KEY="$OPENAI_API_KEY" \
  project-fyr:latest \
  python -m project_fyr.analyzer_service

# Run dashboard
docker run --rm --name dashboard \
  -e PROJECT_FYR_DATABASE_URL="sqlite:////data/project_fyr.db" \
  -p 8000:8000 \
  project-fyr:latest \
  python -m project_fyr.dashboard
```

Override environment variables (or inject secrets) to point at your production database and tokens.

#### Building explicit x86_64 images

Use `docker buildx` (or an equivalent builder) to force `linux/amd64` output even on Apple Silicon:

```bash
docker buildx build --platform linux/amd64 \
  -t ghcr.io/example/project-fyr:amd64 . --push
```

#### Local development

Run services locally (defaults to SQLite file `project_fyr.db`):

```bash
# Watcher
python -m project_fyr.watcher_service

# Analyzer
python -m project_fyr.analyzer_service

# Dashboard (http://localhost:8000)
python -m project_fyr.dashboard
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
| `PROJECT_FYR_SLACK_API_URL` | Override Slack API URL (for testing with mock) | empty |
| `PROJECT_FYR_OPENAI_API_KEY` | Required for the Investigator Agent | empty |
| `PROJECT_FYR_OPENAI_API_BASE` | Azure OpenAI endpoint URL | empty |
| `PROJECT_FYR_OPENAI_API_VERSION` | Azure OpenAI API version | empty |
| `PROJECT_FYR_AZURE_DEPLOYMENT` | Azure OpenAI deployment name | empty |
| `PROJECT_FYR_LANGCHAIN_MODEL_NAME` | LLM to use for the agent | `gpt-4o-mini` |
| `PROJECT_FYR_PROMETHEUS_URL` | Prometheus server URL for metrics queries | empty |


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

### Deployment opt-in

**Important:** Project Fyr only monitors deployments that have the label `project-fyr/enabled=true`. This is an opt-in mechanism to avoid monitoring all deployments in your cluster.

Add the label to deployments you want to monitor:

```bash
# Label an existing deployment
kubectl label deployment my-app -n my-namespace project-fyr/enabled=true

# Or add it to your deployment manifest
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
  namespace: my-namespace
  labels:
    project-fyr/enabled: "true"
spec:
  # ... rest of deployment spec
```

Without this label, the watcher will ignore the deployment even if the namespace has proper annotations.

## Dashboard Web UI

The dashboard provides a web interface for:
- **Browsing rollouts** – view recent deployments with their status and analysis
- **Detailed analysis** – see the full investigation report, likely cause, and recommended steps
- **On-demand investigations** – manually trigger analysis for any deployment in any namespace
- **Status filtering** – filter by failing deployments to focus on active issues

Access the dashboard at the configured Ingress hostname or via port-forward:
```bash
kubectl port-forward -n project-fyr svc/project-fyr-dashboard 8000:8000
# Open http://localhost:8000
```

The dashboard requires the same database connection as the watcher and analyzer but does not need LLM API keys unless you trigger on-demand investigations.

## Helm Deployment

The chart in `helm/project-fyr` deploys three services:
- **Watcher** – monitors deployments and creates rollout records
- **Analyzer** – investigates failures using the LangChain agent
- **Dashboard** – web UI for browsing rollouts and triggering investigations

An optional MySQL dependency is available for dev/test clusters.

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
- `analyzer.*` – replica count, command/args, scheduling hints for the analyzer.
- `dashboard.*` – replica count, command/args, scheduling hints for the dashboard.
- `ingress.*` – expose the dashboard externally with optional TLS.
- `config.*` – populates the ConfigMap consumed by all services, covering every `PROJECT_FYR_*` setting.
- `serviceAccount.*` – RBAC identity (set `create=false` + `name` to reuse an existing SA).
- `rbac.create` – automatically create ClusterRole and ClusterRoleBinding with required permissions (default: `true`).
- `secrets.existingSecret` – reference to a Secret managed by External Secret Operator that injects sensitive `PROJECT_FYR_*` values.
- `metrics.serviceMonitor.*` – enable Prometheus ServiceMonitor for metrics discovery (requires Prometheus Operator).

Mount production secrets via external `Secret` objects and reference them using `envFrom`/`extraEnv` patches if desired—the chart keeps ConfigMap values simple for local testing. Namespace annotations control Slack routing/metadata.

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

## Prometheus Metrics

The analyzer service exposes Prometheus metrics on port 8000 at `/metrics`:

| Metric | Type | Description |
| --- | --- | --- |
| `project_fyr_agent_iterations` | Histogram | Number of LLM iterations per investigation (buckets: 1-1000) |
| `project_fyr_agent_investigations_total` | Counter | Total investigations by status (success, error, mock, disabled) |

### ServiceMonitor (Prometheus Operator)

If you're using Prometheus Operator, enable the ServiceMonitor in your Helm values:

```yaml
metrics:
  serviceMonitor:
    enabled: true
    # Add labels that match your Prometheus operator's serviceMonitorSelector
    additionalLabels:
      prometheus: kube-prometheus
    interval: 30s
    scrapeTimeout: 10s
```

The ServiceMonitor will automatically configure Prometheus to scrape the analyzer metrics endpoint.

### Manual Prometheus Configuration

If not using Prometheus Operator, add this to your Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: 'project-fyr-analyzer'
    static_configs:
      - targets: ['project-fyr-analyzer-metrics:8000']
```

### Using the Metrics

These metrics help you:
- Monitor investigation costs (iterations ≈ API calls)
- Track investigation success rate
- Set up alerts for investigation failures
- Understand typical investigation complexity

Example PromQL queries:

```promql
# Average iterations per investigation
rate(project_fyr_agent_iterations_sum[5m]) / rate(project_fyr_agent_iterations_count[5m])

# Investigation success rate
rate(project_fyr_agent_investigations_total{status="success"}[5m]) / 
rate(project_fyr_agent_investigations_total[5m]) * 100

# 95th percentile iteration count
histogram_quantile(0.95, rate(project_fyr_agent_iterations_bucket[5m]))
```

## Triage heuristics

After the analyzer produces its summary, a lightweight heuristic classifier labels each failure with a next-investigator team:

- **application** – default when no infra/security signals are found.
- **infra** – scheduling/resource/network/storage issues (keywords such as `FailedScheduling`, `Insufficient`, `CNI`, `PersistentVolume`).
- **security** – permission/secret/TLS problems (keywords such as `Forbidden`, `Unauthorized`, `certificate`, `secret`).

The triage team and rationale are included in the Slack notification metadata. Extend `project_fyr/triage.py` if you need richer routing or additional teams.
