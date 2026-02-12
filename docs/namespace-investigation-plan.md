# Namespace-Level Investigation Implementation Plan

## Overview
Extend Project Fyr to support namespace-level investigations, not just deployment failures. This enables investigating stuck namespaces (e.g., stuck in Terminating state), resource quota issues, network policies, RBAC problems, etc.

## Current Architecture Analysis

### Existing Components
1. **WatcherService** - Watches Deployment objects only
2. **AnalyzerService** - Processes investigation jobs from queue
3. **Rollout model** - Deployment-specific (cluster, namespace, deployment, generation)
4. **Tools** - Kubernetes API calls focused on deployment context
5. **Dashboard** - Shows deployment rollouts only

### Current Flow
```
Deployment Event → Rollout Record → Investigation Job → Analysis → Slack Notification
```

---

## Design Decisions to Make

### 1. **Data Model Design**
**Decision: Should we reuse `Rollout` table or create a separate `NamespaceIssue` table?**

**Option A: Unified "Investigation" table**
- Single table for all investigation types (deployment, namespace, pod, etc.)
- Polymorphic approach with `investigation_type` enum
- Pros: Single dashboard, unified workflow, easier to extend to pods/services/etc.
- Cons: Some fields won't apply to all types, potential confusion

**Option B: Separate tables per resource type**
- `rollouts` (existing), `namespace_issues`, potentially `pod_issues`, etc.
- Pros: Clear schema, type-specific fields, no null pollution
- Cons: Duplicate code, separate dashboards, harder to get unified view

**Option C: Hybrid - Shared investigation tracking, separate resource tables**
- `investigations` table (id, type, status, analysis_id, created_at)
- Type-specific tables: `rollouts`, `namespace_incidents`
- Link via investigation_id
- Pros: Best of both worlds, clean separation, unified workflow tracking
- Cons: More complex joins

**Recommendation: Option C** - Allows clean evolution and unified tracking

### 2. **Namespace Monitoring Strategy**
**Decision: How do we detect namespace issues?**

**Option A: Periodic polling**
- Check all namespaces every N minutes for stuck states
- Look for: Terminating > X minutes, resource pressure, quota violations
- Pros: Simple, catches all issues
- Cons: Polling overhead, delayed detection

**Option B: Watch namespace events**
- Watch Namespace objects for status changes
- Trigger on phase changes or specific conditions
- Pros: Real-time detection, efficient
- Cons: May miss gradual degradation

**Option C: Hybrid - Watch + Periodic checks**
- Watch for phase changes
- Poll for stuck states and resource issues
- Pros: Best detection coverage
- Cons: Most complex

**Recommendation: Option C** - Start with watch, add polling for stuck detection

### 3. **Opt-in Mechanism**
**Decision: How do users enable namespace-level monitoring?**

**✅ DECIDED: Option A - Reuse deployment labels/annotations**
- `project-fyr/enabled: "true"` on namespace enables both deployment AND namespace monitoring
- Pros: Simple, consistent with existing approach, one annotation to rule them all
- Cons: Can't separate deployment vs namespace monitoring (acceptable tradeoff)

### 4. **Investigation Triggers**
**Decision: What namespace conditions trigger investigations?**

**✅ DECIDED: Phase 1 Triggers (All Must-Have):**
- Namespace stuck in Terminating for > 5 minutes
- Resource quota exceeded (pods pending due to quota)
- High pod eviction rate (> 5 pods evicted in 5 min)
- High pod restart rate across namespace (> 10 restarts in 5 min)

**Future Triggers:**
- Network policy blocking traffic (many connection timeouts)
- RBAC errors (many Forbidden API calls)
- Image pull failures across multiple deployments

**Configuration:**
```yaml
namespaceMonitoring:
  enabled: true
  rateLimit:
    maxInvestigationsPerNamespacePerHour: 2
    maxInvestigationsPerClusterPerHour: 20
  triggers:
    terminatingThresholdMinutes: 5
    evictionThreshold: 5
    evictionWindowMinutes: 5
    restartThreshold: 10
    restartWindowMinutes: 5
```

### 5. **Context Gathering**
**Decision: What data do we collect for namespace investigations?**

**Core Data:**
- Namespace object (status, labels, annotations, finalizers)
- Events in namespace (last 1 hour)
- Resource quotas and limits
- Network policies
- All pods summary (counts by phase, restart counts)
- Failed/Pending pods details
- Recent deployments/statefulsets status

**Extended Data (optional):**
- RBAC roles/rolebindings
- ConfigMaps/Secrets count
- Service endpoints
- Ingress status
- PVC status

**Recommendation:** Start with core data, add extended based on trigger type

### 6. **Tool Architecture**
**Decision: How do we structure tools for namespace context?**

**Option A: New namespace-specific tools**
```python
@tool
def get_namespace_status(namespace: str) -> dict:
    """Get namespace object and status"""

@tool  
def list_namespace_events(namespace: str) -> list:
    """Get events in namespace"""

@tool
def get_namespace_resource_usage(namespace: str) -> dict:
    """Get quotas, limits, current usage"""
```

**Option B: Extend existing tools with optional namespace-only mode**
- Modify `get_pod_details` to work without deployment filter
- Add `namespace_only=True` parameter

**Option C: Unified resource tools**
```python
@tool
def investigate_resource(resource_type: str, namespace: str, name: str = None) -> dict:
    """Generic investigation tool for any k8s resource"""
```

**Recommendation: Option A** - Clear, focused tools for LLM

### 7. **Prompt Engineering**
**Decision: How do we prompt the LLM for namespace investigations?**

**Key Differences from Deployment Prompts:**
- Broader scope - multiple resources
- Different failure modes - stuck finalizers, quota issues, network problems
- Less obvious "root cause" - may need correlation across resources

**New Prompt Sections:**
- Namespace lifecycle issues (finalizers, operators)
### 8. **Dashboard Integration**
**Decision: How do we show namespace issues in the UI?**

**✅ DECIDED: Option A - Unified dashboard with type filter**
- Add "Type" column (Deployment | Namespace)
- Filter: All | Deployments | Namespaces
- Unified investigation queue depth metrics visible on dashboard
- Group by namespace
- Show both deployment failures and namespace issues under each namespace

**Recommendation: Option A** - Simpler, unified view

### 9. **Manual Trigger UI**
**Decision: Should users be able to manually trigger namespace investigations?**

**Current State:**
- "New Investigation" button exists (routes to `/investigate`)
- Presumably for manual deployment investigation

**Enhancement:**
```
[New Investigation ▼]
  → Deployment Investigation
  → Namespace Investigation
  → Custom Investigation
```

Form for namespace investigation:
- Namespace selector (autocomplete from cluster)
- Optional: specific concern (Terminating stuck, Resource issues, Network problems)

**Recommendation: Yes** - Add dropdown to existing button

### 10. **Notification Strategy**
**Decision: How/when do we notify teams about namespace issues?**

**Considerations:**
- Namespace issues may affect multiple teams
- Severity varies (stuck terminating = critical, quota warning = info)
- May want different channels (platform team vs app teams)

**Approach:**
```yaml
annotations:
  project-fyr/watch-namespace: "true"
  project-fyr/namespace-alert-channel: "#platform-alerts"
**Approach:**
```yaml
annotations:
  project-fyr/enabled: "true"  # Enables both deployment and namespace monitoring
  project-fyr/team: "platform"
  project-fyr/slack-channel: "#platform-alerts"
```

All namespace incidents treated as important → Immediate Slack notification

---

## Rate Limiting & Queue Management

### Investigation Rate Limits
To prevent investigation storms and control costs:

**Per-Namespace Limits:**
- Max 2 investigations per namespace per hour (configurable)
- Applies to both deployment rollouts and namespace incidents
- Track via investigation history in database

**Cluster-Wide Limits:**
- Max 20 investigations per cluster per hour (configurable)
- Protects against cluster-wide issues triggering mass investigations

**Queue Depth Metrics:**
```
project_fyr_investigation_queue_depth{type="rollout|namespace"} - Current jobs pending
project_fyr_investigation_queue_oldest_seconds - Age of oldest pending job
project_fyr_investigations_rate_limited_total{type="rollout|namespace",reason="namespace_limit|cluster_limit"} - Counter
project_fyr_investigations_completed_total{type="rollout|namespace",status="success|failed"} - Counter
project_fyr_investigation_duration_seconds{type="rollout|namespace"} - Histogram
```

**Dashboard Display:**
- Show queue depth badge: "Queue: 5 pending (3 rollouts, 2 namespaces)"
- Warning if queue > 10: "High investigation load - some investigations may be delayed"
- Show rate limit status: "3/20 investigations this hour"

### Implementation
```python
class RateLimiter:
    def can_investigate(self, namespace: str, cluster: str, investigation_type: str) -> tuple[bool, str]:
        """Check if investigation is allowed, return (allowed, reason)"""
        # Check namespace limit
        recent = count_investigations_in_window(namespace, cluster, hours=1)
        if recent >= config.max_per_namespace_per_hour:
            return False, "namespace_limit"
        
        # Check cluster limit
        cluster_recent = count_investigations_in_window(cluster=cluster, hours=1)
        if cluster_recent >= config.max_per_cluster_per_hour:
            return False, "cluster_limit"
        
        return True, ""
```

---

### Phase 1: Core Infrastructure (Week 1)
**Goal:** Basic namespace issue tracking

**Tasks:**
1. Create `namespace_incidents` table
   - Fields: id, cluster, namespace, incident_type, status, started_at, resolved_at, metadata_json, analysis_id
2. Create `NamespaceIncident` DB model and repository methods
3. Add `namespace` to `InvestigationJob.type` enum
4. Create namespace watcher in `WatcherService`
5. Basic trigger: Detect namespace in Terminating > 5min

**Deliverable:** Namespace incidents stored in DB, visible in logs

### Phase 2: Context Gathering (Week 1-2)
**Goal:** Collect relevant data for namespace investigations

**Tasks:**
1. Implement namespace-specific tools:
   - `get_namespace_details()`
   - `get_namespace_events()`
   - `get_namespace_resource_quotas()`
   - `list_namespace_pods_summary()`
   - `get_namespace_network_policies()`
2. Create `NamespaceContext` model
3. Build context reducer for namespace data
4. Test data collection with stuck namespace

**Deliverable:** Rich context available for namespace investigations

### Phase 3: LLM Integration (Week 2)
**Goal:** AI-powered namespace troubleshooting

**Tasks:**
1. Create namespace investigation prompt
2. Update agent to handle namespace investigation type
3. Extend `AnalyzerService` to process namespace jobs
4. Test with real stuck namespace scenario
5. Iterate on prompt based on quality

**Deliverable:** Working AI investigations for namespaces

### Phase 4: Dashboard & UI (Week 2-3)
**Goal:** Visualize namespace issues

**Tasks:**
1. Add "Type" column to dashboard
2. Add type filter (All | Deployments | Namespaces)
3. Create namespace detail page
4. Update "New Investigation" to support namespace selection
5. Add namespace search/autocomplete

**Deliverable:** UI shows both deployment and namespace issues

### Phase 5: Notifications (Week 3)
**Goal:** Alert teams about namespace problems

**Tasks:**
1. Add namespace annotation parsing for alert config
2. Extend Slack notifier for namespace incidents
3. Implement severity-based notification logic
4. Add notification templates for common namespace issues

**Deliverable:** Teams get notified about namespace issues

### Phase 6: Advanced Triggers (Week 3-4)
**Goal:** Detect more namespace issue types

**Tasks:**
1. Add resource quota violation detection
2. Add high pod eviction rate detection
3. Add pod restart storm detection
4. Add configurable trigger thresholds
5. Add periodic "health check" for watched namespaces

**Deliverable:** Comprehensive namespace monitoring

---

## Database Schema Changes

### New Table: `namespace_incidents`
```sql
CREATE TABLE namespace_incidents (
    id INTEGER PRIMARY KEY,
    cluster VARCHAR(255) NOT NULL,
    namespace VARCHAR(255) NOT NULL,
    incident_type VARCHAR(50) NOT NULL,  -- terminating_stuck, quota_exceeded, high_eviction_rate, etc.
    status VARCHAR(50) NOT NULL,  -- active, resolved, investigating
    started_at DATETIME NOT NULL,
    resolved_at DATETIME,
    metadata_json JSON,  -- annotations, quotas, pod counts, etc.
    analysis_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_cluster_namespace (cluster, namespace),
    INDEX idx_status (status),
    INDEX idx_started_at (started_at)
);
```

### Update `investigation_jobs` type enum
```python
type VARCHAR(50)  # rollout | alert | namespace (new)
namespace_incident_id INTEGER  # new FK
```

---

## Configuration Changes

### Helm values.yaml
```yaml
config:
  # Existing
  watchAllNamespaces: false
  namespaceLabelEnabled: true
  
  # New
  namespaceMonitoring:
    enabled: true
    triggers:
      terminatingThresholdMinutes: 5
      evictionThreshold: 5
      quotaViolationThreshold: 3
    reconcileIntervalSeconds: 300  # Check for stuck states every 5 min
```

### Environment variables
```
PROJECT_FYR_NAMESPACE_MONITORING_ENABLED=true
PROJECT_FYR_NS_TERMINATING_THRESHOLD_MINUTES=5
PROJECT_FYR_NS_EVICTION_THRESHOLD=5
PROJECT_FYR_NS_RECONCILE_INTERVAL=300
```

---

## API Changes

### New Endpoints
```
GET /namespace-incidents          # List namespace incidents
GET /namespace-incidents/{id}     # Detail view
POST /investigate/namespace       # Manual trigger
```

---

## Testing Strategy

### Unit Tests
- NamespaceWatcher event handling
- Trigger condition detection
- Context gathering tools
- Repository methods

### Integration Tests
1. Create test namespace with finalizer → detect stuck terminating
2. Exceed quota → detect quota violation
3. Evict many pods → detect eviction storm
4. Manual investigation via API

### E2E Test Scenario
1. Deploy namespace with `project-fyr/watch-namespace: "true"`
2. Add finalizer: `kubectl patch ns test-ns -p '{"metadata":{"finalizers":["test-finalizer"]}}'`
3. Delete namespace: `kubectl delete ns test-ns`
4. Wait 5+ minutes
5. Verify: Incident created, investigation triggered, analysis generated, Slack sent

---

## Risks & Mitigations

### Risk 1: Too many incidents
**Impact:** Alert fatigue, high costs
**Mitigation:** 
- Conservative default triggers (5+ min for terminating)
- Severity-based notification (only critical instant)
- Rate limiting (max 1 investigation per namespace per hour)

### Risk 2: Namespace context too large
**Impact:** Token limit exceeded, slow analysis
**Mitigation:**
- Limit events to last 1 hour
- Summarize pod list (counts, not full objects)
- Only fetch failed/pending pod details
- Implement context size checks

### Risk 3: False positives
**Impact:** Wasted investigations, distrust
**Mitigation:**
- Require multiple signals (e.g., terminating + finalizers + events)
- Allow users to dismiss/suppress certain incident types
- Track investigation quality metrics

### Risk 4: Performance impact on watchers
**Impact:** High CPU/memory from watching namespaces
**Mitigation:**
- Use label selector even for namespace watching
- Periodic polling instead of watching for some triggers
- Separate watcher process for namespace monitoring

---

## Success Metrics

1. **Detection Rate:** % of stuck namespaces detected within 6 minutes
2. **Investigation Quality:** User rating of namespace analyses (thumbs up/down)
3. **Time to Resolution:** Median time from incident detection to resolution
4. **False Positive Rate:** % of investigations that were not actual issues
5. **Adoption:** % of namespaces with `watch-namespace` enabled

---

## Open Questions

1. **Should we watch all namespaces or only labeled ones for critical issues (like stuck terminating)?**
   - Stuck terminating is often an accident - they may not have the label
   - Could watch all, but only auto-investigate labeled ones?

2. **How do we handle namespace deletion investigations?**
   - Once namespace is gone, can't fetch context
   - Need to capture context before deletion?
   - Or rely on events still in etcd?

3. **Should platform teams get different insights than app teams?**
   - Platform: cluster-level issues, quota tuning, node problems
   - App teams: why their specific namespace is stuck
   - Different prompts based on annotation?

4. **Do we need cross-namespace correlation?**
   - Multiple namespaces stuck → cluster-level issue?
   - "You're not alone, 5 other namespaces also stuck" insight?

5. **What's the story for multi-cluster?**
   - Do namespace incidents roll up across clusters?
   - Separate dashboard per cluster or unified?

---

## Next Steps

1. **Review & Approve:** Discuss this plan with team, make decisions on open items
2. **Prioritize:** Agree on phase order (can we skip/defer any phases?)
3. **Spike:** 2-day investigation:
   - Manually create stuck namespace
   - Test what data is available
   - Validate LLM can diagnose with collected context
4. **Implement Phase 1:** Get basic tracking working
