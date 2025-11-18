# Deploy Assistant – Design Document (Part 2)

This file contains **Sections 11–15** of the full design, split out for length and clarity.

---

## 11. Pod-Level Early Failure Detection

The watcher can optionally mark rollouts as FAILED earlier by inspecting pod states associated with the new Deployment generation. This is useful for common hard failures such as `CrashLoopBackOff`, `ImagePullBackOff`, or scheduling issues.

### 11.1. Fetching Pods for a Deployment

```python
def list_deployment_pods(core_v1, dep) -> list:
    ns = dep.metadata.namespace
    selector = dep.spec.selector.match_labels or {}
    label_selector = ",".join(f"{k}={v}" for k, v in selector.items())

    pods = core_v1.list_namespaced_pod(
        namespace=ns,
        label_selector=label_selector,
    )
    return pods.items
```

### 11.2. Detecting Pod-Level Failure Signals

```python
@dataclass
class PodFailureSignals:
    crashloop_pods: int = 0
    image_pull_pods: int = 0
    pending_scheduling_pods: int = 0
    total_pods: int = 0


def analyze_pod_failures(pods: list) -> PodFailureSignals:
    signals = PodFailureSignals(total_pods=len(pods))

    for pod in pods:
        phase = (pod.status.phase or "").upper()

        # CrashLoopBackOff / ImagePullBackOff
        for cs in pod.status.container_statuses or []:
            waiting = (cs.state.waiting or None)
            if not waiting:
                continue

            reason = waiting.reason or ""
            if reason == "CrashLoopBackOff":
                signals.crashloop_pods += 1
            if reason in ("ImagePullBackOff", "ErrImagePull"):
                signals.image_pull_pods += 1

        # Pending + scheduling issues
        if phase == "PENDING":
            signals.pending_scheduling_pods += 1

    return signals
```

A simple policy to determine early failure:

```python
def should_fail_early(signals: PodFailureSignals, min_pods: int = 1) -> bool:
    if signals.total_pods < min_pods:
        return False

    failing = signals.crashloop_pods + signals.image_pull_pods
    if failing >= max(1, signals.total_pods // 2):
        return True

    return False
```

### 11.3. Integrating Early Failure into Reconciliation

```python
def reconcile_rollout(dep, rollout, now, repo, timeout):
    phase = evaluate_deployment_phase(dep)

    started_at = rollout.started_at or now
    age = now - started_at

    core_v1 = k8s_client.CoreV1Api()
    pods = list_deployment_pods(core_v1, dep)
    signals = analyze_pod_failures(pods)

    if should_fail_early(signals, min_pods=1):
        repo.update_status(rollout.id, RolloutStatus.FAILED, failed_at=now)
        return

    # then normal logic ...
```

---

## 12. GitLab Ingestor Service

The GitLab ingestor (`deploy-assistant-gitlab-ingestor`) receives metadata from CI pipelines and updates rollout records.

### 12.1. Responsibilities

- `POST /deployments` endpoint
- Bearer token authentication
- Enrich rollout records with Git metadata
- Create `PENDING` rollout if watcher hasn't seen the deployment yet

### 12.2. Request Model

```python
class GitContext(BaseModel):
    project: str
    commit: str
    pipeline_url: str | None = None
    mr_url: str | None = None


class DeploymentNotifyRequest(BaseModel):
    cluster: str
    namespace: str
    deployment: str
    git: GitContext
    team: str | None = None
    slack_channel: str | None = None
```

### 12.3. FastAPI Endpoint Skeleton

```python
@app.post("/deployments")
async def notify_deployment(body: DeploymentNotifyRequest, ...):
    rollout = repo.get_latest_for_deployment(...)
    fields = {...}

    if rollout is None:
        repo.create(...)
    else:
        repo.update_fields(...)

    return {"ok": True}
```

---

## 13. Analyzer Service

### 13.1. Responsibilities

- Poll for rollouts with `status = FAILED` and no analysis yet
- Collect Kubernetes resources
- Build `RawContext` → `ReducedContext`
- Run analysis chain → `Analysis`
- Persist results in MySQL

### 13.2. Collecting RawContext

(includes deployment, pods, events, logs)

### 13.3. Analyzer Loop

```python
while True:
    failed = rollout_repo.list_failed_without_analysis()
    for r in failed:
        raw = collect_raw_context(...)
        reduced = build_reduced_context(raw, git_context)
        analysis = analysis_chain.invoke({"context_json": ...})
        analysis_repo.create(...)
        rollout_repo.mark_analysis_done(...)
    time.sleep(10)
```

---

## 14. Notifier Service

### 14.1. Responsibilities

- Poll for rollouts whose analysis is done and not yet notified
- Resolve target Slack channel
- Format Slack message based on `Analysis`
- Send message via Slack API

### 14.2. Slack Message Format

Designed for developers with limited K8s knowledge. Emphasis on:
- Summary
- Likely cause
- Next steps
- Optional technical details

### 14.3. Notifier Loop

```python
while True:
    ready = rollout_repo.list_ready_to_notify()
    for r in ready:
        analysis = analysis_repo.get_by_id(r.analysis_id)
        text = format_slack_message(r, analysis)
        slack.post_message(channel, text)
        rollout_repo.mark_notified(r.id)
    time.sleep(10)
```

---

## 15. LangChain Prompts & Chains

### 15.1. Main Analysis Chain

Produces JSON `{summary, likely_cause, next_steps, technical_details, severity}`.

### 15.2. Failure Classification Chain

Classifies snapshot → `{failure_class, confidence, rationale}`.

### 15.3. Reduction Plan Chain

Selects reduction ops based on failure class.

### 15.4. Log/Event Summary Chains

Short natural-language summaries of log and event templates.

---

This completes **Part 2** of the design document (Sections 11–15).

