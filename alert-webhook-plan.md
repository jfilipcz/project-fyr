# Project Fyr — Alert Webhook & Correlated Investigations Plan

Prepared for: extending Project Fyr with a third investigation trigger (Grafana/Alertmanager webhook).  
Audience: Codex / Claude Sonnet 4.5 friendly, implementation-focused.

## Goals
- Accept Grafana/Alertmanager alerts via webhook as a new investigation trigger.
- Reuse alert context in analysis and Slack output.
- Correlate multiple alerts within a time window to produce a richer, single investigation.

## Scope (deliverables)
- Schema additions for alerts, batches, and generic investigation jobs.
- Webhook endpoint with auth + validation + persistence.
- Correlation/batching worker.
- Analyzer path for alert-driven jobs (prompt enrichment, triage, Slack).
- Dashboard views for alert batches and re-run controls.
- Helm/config knobs + metrics + tests.

## Architecture Impact (where code changes land)
- API: new router (e.g., `project_fyr/alert_webhook.py`) mounted in FastAPI app.
- Persistence: new models/repos in `project_fyr/db.py` + Pydantic models in `project_fyr/models.py`.
- Background: extend `AnalyzerService` with alert batching worker and job picker.
- Agent: extend `InvestigatorAgent.investigate` to accept `alert_context`.
- Slack: augment `SlackNotifier` blocks to render alert batches.
- UI: new templates/routes in `project_fyr/dashboard.py` + `project_fyr/templates/*`.
- Helm: values + service/ingress for webhook; config/env in `project_fyr/config.py`.

## Data Model (DB)
- `alerts` table  
  - `id`, `source` (alertmanager|grafana), `fingerprint`, `status`, `labels` (JSON), `annotations` (JSON), `starts_at`, `ends_at`, `value`, `namespace`, `service`, `node`, `payload` (raw), `received_at`, `batched` (bool), `batch_id` (FK).
  - Indexes: `fingerprint`, `(received_at)`, `(batch_id)`.
- `alert_batches` table  
  - `id`, `primary_fingerprint`, `namespace`, `service`, `window_start`, `window_end`, `alerts` (JSON list of alert ids/keys), `context_summary` (text), `created_at`.
- `investigation_jobs` table  
  - `id`, `type` (rollout|alert), `rollout_id` (nullable FK), `alert_batch_id` (nullable FK), `status` (pending|running|done|failed), `notify_status`, `created_at`, `started_at`, `completed_at`.
- Leave `rollouts` untouched; keep separation of concerns.

## Config / Env (add to `project_fyr/config.py`)
- `PROJECT_FYR_ALERT_WEBHOOK_SECRET`
- `PROJECT_FYR_ALERT_CORRELATION_WINDOW_SECONDS` (default 300–600)
- `PROJECT_FYR_ALERT_BATCH_MIN_COUNT` (default 1–2)
- `PROJECT_FYR_ALERT_SOURCE_ALLOWLIST` (csv)
- `PROJECT_FYR_ALERT_SLACK_DEFAULT_CHANNEL`
- `PROJECT_FYR_ALERT_RATE_LIMIT_PER_MIN`

## Webhook Contract
- Endpoint: `POST /webhook/alert`
- Auth: shared secret header `X-Alert-Token` or HMAC (`X-Signature-256`) over body; reject if missing/mismatch.
- Payload: Alertmanager v2 compatible (`status`, `alerts[] {status, labels, annotations, startsAt, endsAt, fingerprint, generatorURL, value}`); accept Grafana’s shape (store raw if fields missing).
- Validation: size guard, required fields (`alerts` array).
- Response: 202 accepted + ids; 400/401 on validation/auth failure.

## Ingestion Flow
1) Webhook receives payload, verifies auth.  
2) Normalizes each alert (extract namespace/app/node labels when present).  
3) Writes `alerts` rows; marks `batched=false`.  
4) Enqueues `investigation_jobs` stub? (defer until batcher assigns batch_id).  
5) Emits metric `alert_webhook_requests_total{status}`.

## Correlation / Batching Worker
- Runs in analyzer process (new thread) every N seconds.
- Fetches unbatched alerts with `received_at` within correlation window.
- Grouping heuristics (ordered fallback):
  1. same namespace + `app`/`service` label,
  2. same node/host label,
  3. same `alertname` + `fingerprint`,
  4. otherwise singleton batch.
- Create `alert_batches` with `alerts` list and `context_summary` (alert names, top labels, value, time range).
- Mark alerts as `batched=true` and set `batch_id`.
- Create `investigation_jobs(type=alert, alert_batch_id=…)` for each new batch.
- Metrics: `alert_batches_created_total`, `alert_batch_size_histogram`.

## Analysis Path for Alerts
- Extend `InvestigationJob` picker: if job.type == alert, load batch + member alerts.
- Build `alert_context`:
  - primary namespace/service/node,
  - list of alerts (name, value, startsAt, labels, annotations),
  - time window,
  - hypotheses to check (CPU, latency, errors) derived from alertname/labels.
- Agent prompt tweak: prepend alert context to `AGENT_SYSTEM_PROMPT`; ask to cross-check with Prometheus/k8s tools; encourage linking alerts (e.g., “latency + node CPU”).
- ReducedContext placeholder: keep current schema but fill with alert-derived summary; note missing rollout fields are expected.
- Run triage with extended keyword set (SLO, latency, CPU, disk, packet loss).

## Notifications
- Slack: reuse `SlackNotifier`; new block format for alerts:
  - header with `Alert batch`, severity (from alert labels or agent output), namespace/service.
  - bullets of top alerts (name, value, duration).
  - triage team/reason, metadata (labels subset), correlation window shown.
- Channel resolution: alert label `slack_channel` > namespace annotations > `alert_slack_default_channel` > global default.

## Dashboard UX
- Add tab/section “Alerts” listing recent batches with status, namespace, primary alert, created_at.
- Detail page shows member alerts, labels, value, analysis summary, Slack link, and “Re-run investigation” button (calls `/api/investigate` with `type=alert`, `batch_id`).
- Keep rollouts view untouched.

## Helm / Ops
- Helm values: enableWebhook, webhook.secret, service/ingress, rateLimit, correlationWindow, batchMinCount, sourceAllowlist.
- ServiceMonitor scrape for new metrics.
- Document sample Alertmanager receiver config and Grafana contact point pointing to webhook URL.

## Observability & Safety
- Metrics: webhook requests, batch sizes, investigations by status/type, agent iterations by job_type, Slack success/fail.
- Rate limiting & payload size limits to protect analyzer.
- Logging: redact secrets, avoid dumping full payloads.

## Testing
- Unit: webhook auth/parse/normalize; repo insert/query; correlation grouping; prompt builder for alert_context; triage keywords.
- Integration (FastAPI TestClient + sqlite): POST webhook → alerts → batch → job creation; Slack mock file output for alerts.
- Load test (optional): synthetic burst to validate rate-limit handling.

## Open Questions / Decisions Needed
1) Preferred auth: shared secret header vs HMAC?  
2) Default correlation window and minimum batch size?  
3) Channel routing precedence acceptable?  
4) Expected alert volume (decides SQLite vs MySQL/Postgres for prod)?  
5) Should flapping alerts update existing batch instead of new batch creation?  
6) Auto-run specific PromQLs per alertname family (e.g., latency → CPU/mem/network) before LLM?
