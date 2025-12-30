# Project Fyr

Project Fyr is an agentic AI assistant that watches Kubernetes deployments and namespaces, inspects failures, enriches them using namespace annotations, and posts concise guidance to Slack. It contains:

- **Watcher** – streams deployment events, tracks rollout status, and monitors namespace-level incidents (stuck terminating, quota violations, high eviction/restart rates).
- **Analyzer** – an autonomous LangChain agent that investigates failures by actively querying the cluster (Pods, Events, Logs, ArgoCD, Helm, Prometheus, Namespace details, Resource Quotas).
- **Dashboard** – a FastAPI web UI for browsing rollouts, viewing analyses, and triggering on-demand investigations.
- **Slack Notifier** – posts the agent's analysis (summary, root cause, remediation) to Slack.
- **Namespace annotations** – opt-in metadata (Slack channel, owning team, etc.) stored on the Kubernetes namespace.
- **Namespace investigations** – automatic detection and analysis of namespace-level issues including stuck terminating states, quota violations, pod eviction storms, and high restart rates.
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
| `PROJECT_FYR_WATCH_ALL_NAMESPACES` | Monitor all deployments without labels/annotations | `false` |
| `PROJECT_FYR_NAMESPACE_LABEL_ENABLED` | Allow namespace-level opt-in annotation | `true` |
| `PROJECT_FYR_NAMESPACE_MONITORING_ENABLED` | Enable namespace-level incident detection | `true` |
| `PROJECT_FYR_NAMESPACE_MONITORING_INTERVAL_SECONDS` | Check interval for namespace issues | `300` |
| `PROJECT_FYR_NAMESPACE_TERMINATING_THRESHOLD_MINUTES` | Minutes before namespace considered stuck | `5` |
| `PROJECT_FYR_NAMESPACE_EVICTION_THRESHOLD` | Pod evictions to trigger investigation | `5` |
| `PROJECT_FYR_NAMESPACE_EVICTION_WINDOW_MINUTES` | Time window for eviction counting | `5` |
| `PROJECT_FYR_NAMESPACE_RESTART_THRESHOLD` | Container restarts to trigger investigation | `10` |
| `PROJECT_FYR_NAMESPACE_RESTART_WINDOW_MINUTES` | Time window for restart counting | `5` |
| `PROJECT_FYR_MAX_INVESTIGATIONS_PER_NAMESPACE_PER_HOUR` | Rate limit per namespace | `2` |
| `PROJECT_FYR_MAX_INVESTIGATIONS_PER_CLUSTER_PER_HOUR` | Rate limit cluster-wide | `20` |
| `PROJECT_FYR_ALERT_WEBHOOK_SECRET` | Secret for alert webhook authentication | empty |
| `PROJECT_FYR_ALERT_CORRELATION_WINDOW_SECONDS` | Time window for alert batching | `300` |
| `PROJECT_FYR_ALERT_BATCH_MIN_COUNT` | Minimum alerts to trigger batch investigation | `1` |


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

### Deployment Monitoring Options

Project Fyr offers flexible monitoring options through labels and annotations:

#### Option 1: Deployment-level opt-in (default)

Add the label `project-fyr/enabled=true` to individual deployments you want to monitor:

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

#### Option 2: Namespace-level opt-in

Enable monitoring for **all deployments** in a namespace by adding an annotation to the namespace:

```bash
# Enable Fyr for all deployments in the namespace
kubectl annotate namespace my-namespace project-fyr/enabled=true
```

This is useful for namespaces where you want to monitor everything without labeling each deployment individually.

**Note:** You can disable namespace-level checking by setting the environment variable `PROJECT_FYR_NAMESPACE_LABEL_ENABLED=false` in the watcher deployment.

#### Option 3: Watch all namespaces (global monitoring)

To monitor **all deployments across all namespaces** without any labels or annotations, set this environment variable in the watcher deployment:

```yaml
env:
  - name: PROJECT_FYR_WATCH_ALL_NAMESPACES
    value: "true"
```

**⚠️ Warning:** This option monitors every deployment in your cluster. Use with caution in large clusters as it may generate significant data and analysis load.

### Monitoring Behavior Summary

| Configuration | Behavior |
|---------------|----------|
| Default (no env vars) | Only deployments with `project-fyr/enabled=true` label **OR** in namespaces with `project-fyr/enabled=true` annotation |
| `NAMESPACE_LABEL_ENABLED=false` | Only deployments with `project-fyr/enabled=true` label (namespace annotations ignored) |
| `WATCH_ALL_NAMESPACES=true` | All deployments in all namespaces (ignores all labels and annotations) |

## Namespace-Level Monitoring

In addition to deployment rollout monitoring, Project Fyr can automatically detect and investigate namespace-level issues:

### Supported Incident Types

1. **Stuck Terminating Namespaces**
   - Detects namespaces stuck in `Terminating` state beyond a threshold
   - Investigates finalizers and resources preventing deletion
   - Default threshold: 5 minutes

2. **Resource Quota Violations**
   - Monitors namespaces hitting quota limits
   - Identifies which resources are constrained
   - Recommends quota adjustments or resource cleanup

3. **Pod Eviction Storms**
   - Detects high rates of pod evictions
   - Investigates memory/disk pressure or resource constraints
   - Default: 5+ evictions in 5 minutes

4. **High Container Restart Rates**
   - Monitors excessive container restarts across namespace
   - Identifies failing pods and restart patterns
   - Default: 10+ restarts in 5 minutes

### Enabling Namespace Monitoring

Namespace monitoring is **enabled by default** for namespaces with the `project-fyr/enabled=true` annotation:

```bash
# Enable namespace-level monitoring
kubectl annotate namespace my-namespace project-fyr/enabled=true
```

The same annotation enables both deployment rollout monitoring and namespace incident detection.

### Rate Limiting

To prevent investigation storms, namespace investigations are rate-limited:
- **Per-namespace limit**: 2 investigations per hour (default)
- **Cluster-wide limit**: 20 investigations per hour (default)

These limits apply to the total of rollout investigations + namespace incident investigations.

### Configuration

Tune namespace monitoring behavior via environment variables:

```yaml
env:
  # Enable/disable namespace monitoring
  - name: PROJECT_FYR_NAMESPACE_MONITORING_ENABLED
    value: "true"
  
  # Check interval (seconds)
  - name: PROJECT_FYR_NAMESPACE_MONITORING_INTERVAL_SECONDS
    value: "300"
  
  # Stuck terminating threshold (minutes)
  - name: PROJECT_FYR_NAMESPACE_TERMINATING_THRESHOLD_MINUTES
    value: "5"
  
  # Eviction detection
  - name: PROJECT_FYR_NAMESPACE_EVICTION_THRESHOLD
    value: "5"
  - name: PROJECT_FYR_NAMESPACE_EVICTION_WINDOW_MINUTES
    value: "5"
  
  # Restart detection
  - name: PROJECT_FYR_NAMESPACE_RESTART_THRESHOLD
    value: "10"
  - name: PROJECT_FYR_NAMESPACE_RESTART_WINDOW_MINUTES
    value: "5"
  
  # Rate limiting
  - name: PROJECT_FYR_MAX_INVESTIGATIONS_PER_NAMESPACE_PER_HOUR
    value: "2"
  - name: PROJECT_FYR_MAX_INVESTIGATIONS_PER_CLUSTER_PER_HOUR
    value: "20"
```


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
