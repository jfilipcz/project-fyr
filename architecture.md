# Project Fyr Architecture

## High-Level Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Kubernetes Cluster                              │
│                                                                         │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐          │
│  │ Deployment A │     │ Deployment B │     │ Deployment C │          │
│  │  (Rolling    │     │  (Failing    │     │  (Pending)   │          │
│  │   Update)    │     │   Rollout)   │     │              │          │
│  └──────┬───────┘     └──────┬───────┘     └──────┬───────┘          │
│         │                     │                     │                  │
│         └─────────────────────┼─────────────────────┘                  │
│                               │                                        │
│                   ┌───────────▼───────────┐                           │
│                   │   Project Fyr         │                           │
│                   │   ┌─────────────┐     │                           │
│                   │   │  Watcher    │◄────┼─── Watches Deployments   │
│                   │   └─────┬───────┘     │                           │
│                   │         │             │                           │
│                   │         ▼             │                           │
│                   │   ┌─────────────┐     │                           │
│                   │   │  Database   │     │                           │
│                   │   │  (Rollouts) │     │                           │
│                   │   └─────┬───────┘     │                           │
│                   │         │             │                           │
│                   │         ▼             │                           │
│                   │   ┌─────────────┐     │                           │
│                   │   │  Analyzer   │     │                           │
│                   │   │  (AI Agent) │────┼───► Investigates Failures │
│                   │   └─────┬───────┘     │                           │
│                   │         │             │                           │
│                   │         ▼             │                           │
│                   │   ┌─────────────┐     │                           │
│                   │   │  Dashboard  │     │                           │
│                   │   │  (Web UI)   │     │                           │
│                   │   └─────────────┘     │                           │
│                   └───────────────────────┘                           │
└─────────────────────────────────────────────────────────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
                    ▼                     ▼
            ┌───────────────┐     ┌──────────────┐
            │  Slack        │     │  Engineers   │
            │  Notifications│     │  (Dashboard) │
            └───────────────┘     └──────────────┘
```

**Flow:**
1. **Watcher** monitors Kubernetes Deployments for rollout events
2. Records rollout metadata in **Database** (namespace, deployment, status, timestamps)
3. **Analyzer** picks up failed rollouts and investigates using AI agent
4. AI agent queries cluster (pods, events, logs, metrics) to diagnose issues
5. Analysis results stored in database and sent to **Slack**
6. **Dashboard** provides web UI for viewing rollouts and triggering investigations

---

## Detailed Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              Kubernetes Cluster                                         │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐ │
│  │                         Namespaces with Annotations                               │ │
│  │  project-fyr/slack-channel="#team-alerts"                                        │ │
│  │  project-fyr/team="Platform Team"                                                │ │
│  │                                                                                   │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │ │
│  │  │ Deployment 1 │  │ Deployment 2 │  │ Deployment 3 │  │ Deployment N │       │ │
│  │  │              │  │              │  │              │  │              │       │ │
│  │  │ ┌──────────┐ │  │ ┌──────────┐ │  │ ┌──────────┐ │  │ ┌──────────┐ │       │ │
│  │  │ │ Pod      │ │  │ │ Pod      │ │  │ │ Pod      │ │  │ │ Pod      │ │       │ │
│  │  │ │ (Ready)  │ │  │ │(CrashLoop)│ │  │ │(Pending) │ │  │ │ (Ready)  │ │       │ │
│  │  │ └──────────┘ │  │ └──────────┘ │  │ └──────────┘ │  │ └──────────┘ │       │ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘       │ │
│  └───────────────────────────────────────────────────────────────────────────────────┘ │
│                                         │                                               │
│                                         │ Watch API                                     │
│                                         ▼                                               │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                          Project Fyr System                                     │   │
│  │                                                                                 │   │
│  │  ┌────────────────────────────────────────────────────────────────────────┐    │   │
│  │  │                    WATCHER SERVICE                                      │    │   │
│  │  │  ┌──────────────────────────────────────────────────────────────────┐  │    │   │
│  │  │  │ Kubernetes Watch API                                             │  │    │   │
│  │  │  │  • Monitors Deployment events (ADDED, MODIFIED, DELETED)        │  │    │   │
│  │  │  │  • Tracks rollout progress (readyReplicas vs replicas)          │  │    │   │
│  │  │  │  • Detects failures (timeout, unavailableReplicas)              │  │    │   │
│  │  │  └──────────────────────────────────────────────────────────────────┘  │    │   │
│  │  │                            │                                            │    │   │
│  │  │                            ▼                                            │    │   │
│  │  │  ┌──────────────────────────────────────────────────────────────────┐  │    │   │
│  │  │  │ Namespace Enrichment                                             │  │    │   │
│  │  │  │  • Reads namespace annotations (project-fyr/*)                   │  │    │   │
│  │  │  │  • Extracts metadata (slack-channel, team, custom fields)       │  │    │   │
│  │  │  └──────────────────────────────────────────────────────────────────┘  │    │   │
│  │  │                            │                                            │    │   │
│  │  │                            ▼                                            │    │   │
│  │  │  ┌──────────────────────────────────────────────────────────────────┐  │    │   │
│  │  │  │ Rollout Record Creation                                          │  │    │   │
│  │  │  │  • Creates database entry with:                                  │  │    │   │
│  │  │  │    - namespace, deployment, revision                             │  │    │   │
│  │  │  │    - status (in_progress, succeeded, failed)                     │  │    │   │
│  │  │  │    - timestamps (started_at, completed_at)                       │  │    │   │
│  │  │  │    - namespace metadata JSON blob                                │  │    │   │
│  │  │  └──────────────────────────────────────────────────────────────────┘  │    │   │
│  │  └────────────────────────────────────────────────────────────────────────┘    │   │
│  │                                                                                 │   │
│  │                                    │                                            │   │
│  │                                    ▼                                            │   │
│  │  ┌────────────────────────────────────────────────────────────────────────┐    │   │
│  │  │                       DATABASE (MySQL/SQLite)                          │    │   │
│  │  │  ┌──────────────────────────────────────────────────────────────────┐  │    │   │
│  │  │  │ rollouts table:                                                   │  │    │   │
│  │  │  │  • id, namespace, deployment_name, revision                      │  │    │   │
│  │  │  │  • status, started_at, completed_at                              │  │    │   │
│  │  │  │  • namespace_metadata (JSON)                                     │  │    │   │
│  │  │  └──────────────────────────────────────────────────────────────────┘  │    │   │
│  │  │  ┌──────────────────────────────────────────────────────────────────┐  │    │   │
│  │  │  │ analysis_records table:                                          │  │    │   │
│  │  │  │  • rollout_id (FK), summary, likely_cause                        │  │    │   │
│  │  │  │  • recommended_steps (JSON), severity                            │  │    │   │
│  │  │  │  • triage_team, triage_reason                                    │  │    │   │
│  │  │  └──────────────────────────────────────────────────────────────────┘  │    │   │
│  │  └────────────────────────────────────────────────────────────────────────┘    │   │
│  │                                                                                 │   │
│  │                                    │                                            │   │
│  │                                    ▼                                            │   │
│  │  ┌────────────────────────────────────────────────────────────────────────┐    │   │
│  │  │                    ANALYZER SERVICE                                     │    │   │
│  │  │  ┌──────────────────────────────────────────────────────────────────┐  │    │   │
│  │  │  │ Polling Loop                                                     │  │    │   │
│  │  │  │  • Queries database for failed rollouts                          │  │    │   │
│  │  │  │  • WHERE status='failed' AND analysis_id IS NULL                 │  │    │   │
│  │  │  │  • Sleeps between polls                                          │  │    │   │
│  │  │  └──────────────────────────────────────────────────────────────────┘  │    │   │
│  │  │                            │                                            │    │   │
│  │  │                            ▼                                            │    │   │
│  │  │  ┌──────────────────────────────────────────────────────────────────┐  │    │   │
│  │  │  │ LangChain Agent (InvestigatorAgent)                              │  │    │   │
│  │  │  │                                                                  │  │    │   │
│  │  │  │  LLM: Azure OpenAI / OpenAI (gpt-4o-mini)                       │  │    │   │
│  │  │  │                                                                  │  │    │   │
│  │  │  │  Tools Available:                                                │  │    │   │
│  │  │  │  ┌────────────────────────────────────────────────────────────┐ │  │    │   │
│  │  │  │  │ • k8s_get_resources (list pods, deployments, services)     │ │  │    │   │
│  │  │  │  │ • k8s_describe (detailed resource info)                    │ │  │    │   │
│  │  │  │  │ • k8s_logs (fetch pod logs, previous logs)                 │ │  │    │   │
│  │  │  │  │ • k8s_events (namespace events with filters)               │ │  │    │   │
│  │  │  │  │ • k8s_get_configmap / k8s_get_secret_structure             │ │  │    │   │
│  │  │  │  │ • k8s_get_network (services, ingresses, endpoints)         │ │  │    │   │
│  │  │  │  │ • k8s_get_nodes (node status, taints, capacity)            │ │  │    │   │
│  │  │  │  │ • k8s_get_storage (PVCs, volumes)                          │ │  │    │   │
│  │  │  │  │ • k8s_check_rbac (permission checks)                       │ │  │    │   │
│  │  │  │  │ • k8s_list_helm_releases / k8s_get_argocd_application      │ │  │    │   │
│  │  │  │  │ • k8s_query_prometheus (restarts, OOM, CPU, memory)        │ │  │    │   │
│  │  │  │  └────────────────────────────────────────────────────────────┘ │  │    │   │
│  │  │  │                                                                  │  │    │   │
│  │  │  │  Investigation Process:                                          │  │    │   │
│  │  │  │  1. List pods for deployment                                     │  │    │   │
│  │  │  │  2. Check events for errors                                      │  │    │   │
│  │  │  │  3. Inspect pod logs (including previous)                        │  │    │   │
│  │  │  │  4. Check resource constraints (nodes, storage, network)         │  │    │   │
│  │  │  │  5. Query Prometheus for metrics                                 │  │    │   │
│  │  │  │  6. Generate structured analysis                                 │  │    │   │
│  │  │  └──────────────────────────────────────────────────────────────────┘  │    │   │
│  │  │                            │                                            │    │   │
│  │  │                            ▼                                            │    │   │
│  │  │  ┌──────────────────────────────────────────────────────────────────┐  │    │   │
│  │  │  │ Analysis Processing                                              │  │    │   │
│  │  │  │  • Parses agent output (summary, likely_cause, steps, severity) │  │    │   │
│  │  │  │  • Runs triage heuristics (infra/security/application)          │  │    │   │
│  │  │  │  • Saves analysis_record to database                            │  │    │   │
│  │  │  │  • Links to rollout via rollout_id                              │  │    │   │
│  │  │  └──────────────────────────────────────────────────────────────────┘  │    │   │
│  │  │                            │                                            │    │   │
│  │  │                            ▼                                            │    │   │
│  │  │  ┌──────────────────────────────────────────────────────────────────┐  │    │   │
│  │  │  │ Slack Notification                                               │  │    │   │
│  │  │  │  • Retrieves slack-channel from rollout metadata                │  │    │   │
│  │  │  │  • Formats message blocks with:                                 │  │    │   │
│  │  │  │    - Rollout info (namespace/deployment/revision)               │  │    │   │
│  │  │  │    - Severity level                                             │  │    │   │
│  │  │  │    - Summary and likely cause                                   │  │    │   │
│  │  │  │    - Recommended steps (bulleted)                               │  │    │   │
│  │  │  │    - Triage team assignment                                     │  │    │   │
│  │  │  │  • Posts to Slack API                                           │  │    │   │
│  │  │  └──────────────────────────────────────────────────────────────────┘  │    │   │
│  │  │                            │                                            │    │   │
│  │  │                            ▼                                            │    │   │
│  │  │  ┌──────────────────────────────────────────────────────────────────┐  │    │   │
│  │  │  │ Prometheus Metrics Export                                        │  │    │   │
│  │  │  │  • project_fyr_agent_iterations (histogram)                      │  │    │   │
│  │  │  │  • project_fyr_agent_investigations_total (counter by status)    │  │    │   │
│  │  │  └──────────────────────────────────────────────────────────────────┘  │    │   │
│  │  └────────────────────────────────────────────────────────────────────────┘    │   │
│  │                                                                                 │   │
│  │  ┌────────────────────────────────────────────────────────────────────────┐    │   │
│  │  │                    DASHBOARD SERVICE (FastAPI)                         │    │   │
│  │  │  ┌──────────────────────────────────────────────────────────────────┐  │    │   │
│  │  │  │ HTTP Endpoints:                                                  │  │    │   │
│  │  │  │  • GET /                  - Recent rollouts list                 │  │    │   │
│  │  │  │  • GET /rollout/{id}      - Detailed analysis view               │  │    │   │
│  │  │  │  • GET /investigate       - On-demand investigation form         │  │    │   │
│  │  │  │  • POST /api/investigate  - Trigger investigation                │  │    │   │
│  │  │  └──────────────────────────────────────────────────────────────────┘  │    │   │
│  │  │  ┌──────────────────────────────────────────────────────────────────┐  │    │   │
│  │  │  │ Features:                                                        │  │    │   │
│  │  │  │  • Lists all namespaces and deployments                          │  │    │   │
│  │  │  │  • Shows deployment status (ready/desired replicas)              │  │    │   │
│  │  │  │  • Filter: show only failing deployments                         │  │    │   │
│  │  │  │  • Triggers synchronous investigation via InvestigatorAgent      │  │    │   │
│  │  │  │  • Displays analysis without Slack notification                  │  │    │   │
│  │  │  └──────────────────────────────────────────────────────────────────┘  │    │   │
│  │  │  ┌──────────────────────────────────────────────────────────────────┐  │    │   │
│  │  │  │ Templates (Jinja2):                                              │  │    │   │
│  │  │  │  • index.html      - Rollout list with status indicators         │  │    │   │
│  │  │  │  • detail.html     - Full analysis report                        │  │    │   │
│  │  │  │  • investigate.html - On-demand investigation UI                 │  │    │   │
│  │  │  └──────────────────────────────────────────────────────────────────┘  │    │   │
│  │  └────────────────────────────────────────────────────────────────────────┘    │   │
│  │                                                                                 │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                         │
│  ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│  │                        RBAC Configuration                                       │   │
│  │  ClusterRole permissions:                                                       │   │
│  │  • deployments: get, list, watch                                                │   │
│  │  • namespaces: get, list                                                        │   │
│  │  • pods: get, list                                                              │   │
│  │  • pods/log: get                                                                │   │
│  │  • events: get, list                                                            │   │
│  │  • services, configmaps, secrets: get, list                                     │   │
│  │  • nodes, persistentvolumeclaims: get, list                                     │   │
│  └─────────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                            ┌─────────────┴─────────────┐
                            │                           │
                            ▼                           ▼
                ┌───────────────────────┐   ┌──────────────────────┐
                │  Slack API            │   │  Engineers           │
                │  • Post messages      │   │  • View dashboard    │
                │  • Format blocks      │   │  • Trigger analysis  │
                │  • Channel routing    │   │  • Review history    │
                └───────────────────────┘   └──────────────────────┘
                            │
                            ▼
                ┌───────────────────────┐
                │  Prometheus           │
                │  • Scrapes metrics    │
                │  • Query by agent     │
                │  • Alert on failures  │
                └───────────────────────┘

External Dependencies:
  • Azure OpenAI / OpenAI API - LLM inference for agent
  • Slack Bot Token - Notification delivery
  • Prometheus (optional) - Metrics queries and export
  • MySQL/PostgreSQL - Production database (SQLite for dev)
```

---

## Key Design Decisions

### 1. **Separation of Concerns**
- **Watcher**: Pure Kubernetes event monitoring, no AI logic
- **Analyzer**: Stateless investigation engine, polls for work
- **Dashboard**: Read-mostly UI with optional on-demand triggers

### 2. **Database as State Store**
- Single source of truth for rollout history
- Enables async processing (watcher → analyzer)
- Supports historical analysis and reporting

### 3. **Namespace Annotations for Configuration**
- Declarative: metadata lives with the resource
- No external config files or GitOps repos needed
- Easy per-namespace customization (Slack channels, teams)

### 4. **LangChain Agent Architecture**
- Autonomous: decides which tools to use based on context
- Iterative: can make multiple tool calls to dig deeper
- Structured output: enforces schema (summary, cause, steps, severity)
- Metrics: tracks iterations and success rate

### 5. **Triage Heuristics**
- Lightweight keyword matching post-analysis
- Routes to infra/security/application teams
- Extensible: easy to add new patterns or ML-based routing

### 6. **Prometheus Integration**
- **Query side**: Agent uses Prometheus to check for OOMKills, restarts, throttling
- **Export side**: Analyzer exposes metrics for monitoring investigation health

### 7. **Dashboard for Human-in-the-Loop**
- Not all failures warrant auto-investigation
- Engineers can manually trigger analysis
- Provides audit trail and searchable history

---

## Data Flow Example

**Scenario: Deployment fails due to missing ConfigMap**

1. **Watcher** detects deployment update in `payments` namespace
2. Reads annotation: `project-fyr/slack-channel="#payments-alerts"`
3. Tracks rollout, sees pods stuck in `CrashLoopBackOff` after 15 minutes
4. Marks rollout as `failed`, saves to database

5. **Analyzer** polls database, finds new failed rollout
6. Initializes LangChain agent with rollout context
7. Agent calls:
   - `k8s_get_resources("Pod", "payments")` → finds crashing pod
   - `k8s_events("payments")` → sees "Error: configmap 'app-config' not found"
   - `k8s_logs(pod_name)` → confirms missing ConfigMap error
   - `k8s_get_configmap("app-config")` → 404 Not Found

8. Agent generates analysis:
   - **Summary**: Deployment failed due to missing ConfigMap
   - **Likely Cause**: ConfigMap 'app-config' referenced but not found
   - **Steps**: ["Create ConfigMap 'app-config'", "Or remove reference from deployment"]
   - **Severity**: high

9. **Triage**: Detects "configmap" keyword → assigns to **application** team

10. **Slack**: Posts formatted message to `#payments-alerts` channel

11. **Prometheus**: Records 1 successful investigation, 4 agent iterations

12. **Dashboard**: Engineers view full analysis history at `/rollout/{id}`

---

## Scaling Considerations

- **Watcher**: Single replica (Kubernetes Watch API handles reconnection)
- **Analyzer**: Can run multiple replicas (polling uses database locks)
- **Dashboard**: Stateless, can scale horizontally
- **Database**: Bottleneck for high-volume clusters (recommend managed MySQL/Postgres)
- **LLM Rate Limits**: Analyzer implements exponential backoff, metrics track throttling
