# Implementation Plan: System Namespaces Filter & On-Demand Namespace Analysis

**Date:** January 12, 2026  
**Status:** Proposal  
**Priority:** Medium

---

## Feature 1: System Namespace Filtering

### Overview
Implement an opt-out mechanism for "system" namespaces that should be excluded from Fyr monitoring, analysis, and dashboard display. This is configured on the Fyr side rather than requiring namespace-level opt-in.

### Requirements
- Configuration-based exclusion list
- Filter at multiple levels: watcher, database queries, UI
- Sensible defaults for common system namespaces
- Easy to extend/customize per deployment

### Implementation Details

#### 1.1 Configuration (config.py)
```python
# System namespaces to exclude from monitoring
system_namespaces: list[str] = Field(
    default=[
        "kube-system",
        "kube-public",
        "kube-node-lease",
        "default",
        "monitoring",
        "logging",
        "ingress-nginx",
        "cert-manager",
        "flux-system",
        "argocd",
        "project-fyr",
        "istio-system",
        "elastic-system"
    ],
    env="PROJECT_FYR_SYSTEM_NAMESPACES"
)

# Allow comma-separated string from env
@field_validator('system_namespaces', mode='before')
@classmethod
def parse_system_namespaces(cls, v):
    if isinstance(v, str):
        return [ns.strip() for ns in v.split(',') if ns.strip()]
    return v
```

#### 1.2 Watcher Service (watcher_service.py)
- Filter out system namespaces when detecting rollouts
- Skip events from excluded namespaces entirely
```python
def should_monitor_namespace(self, namespace: str) -> bool:
    """Check if namespace should be monitored."""
    if namespace in settings.system_namespaces:
        logger.debug(f"Skipping system namespace: {namespace}")
        return False
    
    # Existing opt-in/opt-out logic
    if settings.watch_all_namespaces:
        return namespace not in settings.excluded_namespaces
    else:
        return namespace in settings.watched_namespaces
    
    return True
```

#### 1.3 Database Repository (db.py)
Add filtering methods:
```python
def list_recent(self, limit: int = 50, exclude_system: bool = True):
    """List recent rollouts, optionally excluding system namespaces."""
    query = self.session.query(Rollout).order_by(Rollout.started_at.desc())
    if exclude_system:
        query = query.filter(~Rollout.namespace.in_(settings.system_namespaces))
    return query.limit(limit).all()

# Update all existing query methods:
# - list_by_status()
# - list_by_namespace()
# - list_by_status_and_namespace()
# - get_stats()
# - get_recent_failures()
```

#### 1.4 Dashboard API (dashboard.py)
- `/api/rollouts` - exclude system namespaces by default
- `/api/investigate/deployments` - exclude system namespaces from dropdown
- `/api/rollouts?include_system=true` - admin override to include system namespaces
- `/api/investigate/deployments?include_system=true` - admin override for deployments

#### 1.5 Configuration via Helm
```yaml
# values.yaml
config:
  systemNamespaces:
    - kube-system
    - kube-public
    - kube-node-lease
    - monitoring
    # ... customizable per deployment
```

### Testing Strategy
1. Deploy test apps in system vs non-system namespaces
2. Verify watcher ignores system namespaces
3. Verify UI doesn't show system namespace rollouts
4. Test override parameter for admin access
5. Test configuration via environment variables

### Migration Considerations
- Existing rollouts in system namespaces will remain in DB
- They'll be filtered from queries but not deleted
- Add migration note in changelog

---

## Feature 2: On-Demand Namespace Analysis

### Overview
Enable users to perform proactive analysis of a namespace even without detected failures. This helps troubleshooting and allows future extension to application-level analysis.

### Requirements
- Analyze entire namespace health, not just single deployment
- Work even when no failures detected
- Provide actionable insights
- Extensible architecture for future app-level analysis

### Implementation Details

#### 2.1 New Namespace Analyzer Agent (namespace_analyzer.py)
```python
class NamespaceAnalyzer:
    """Perform comprehensive namespace-level analysis."""
    
    def analyze_namespace(self, namespace: str, cluster: str = None) -> NamespaceAnalysis:
        """
        Analyze overall namespace health.
        
        Returns comprehensive analysis including:
        - Deployment health summary
        - Resource utilization
        - Recent events
        - Common issues detected
        - Recommendations
        """
        # 1. Get all deployments in namespace
        deployments = self._get_deployments(namespace)
        
        # 2. Analyze deployment health
        deployment_health = self._analyze_deployments(deployments)
        
        # 3. Check resource quotas and limits
        resource_status = self._check_resources(namespace)
        
        # 4. Scan recent events
        events = self._get_recent_events(namespace)
        critical_events = self._analyze_events(events)
        
        # 5. Check for common issues
        issues = self._detect_common_issues(namespace, deployments)
        
        # 6. Use LLM to synthesize findings
        synthesis = self._llm_analyze(
            deployment_health=deployment_health,
            resources=resource_status,
            events=critical_events,
            issues=issues
        )
        
        return NamespaceAnalysis(
            namespace=namespace,
            cluster=cluster,
            timestamp=datetime.utcnow(),
            deployment_summary=deployment_health,
            resource_status=resource_status,
            critical_events=critical_events,
            detected_issues=issues,
            analysis=synthesis,
            recommendations=self._generate_recommendations(synthesis)
        )
```

#### 2.2 Analysis Components

**Deployment Health:**
```python
def _analyze_deployments(self, deployments) -> DeploymentHealthSummary:
    """Analyze health of all deployments in namespace."""
    return {
        "total": len(deployments),
        "healthy": count_healthy,
        "degraded": count_degraded,
        "failing": count_failing,
        "details": [
            {
                "name": dep.name,
                "replicas": {"desired": X, "ready": Y, "available": Z},
                "status": "healthy|degraded|failing",
                "issues": ["ImagePullBackOff on pod-123", ...]
            }
            for dep in deployments
        ]
    }
```

**Resource Analysis:**
```python
def _check_resources(self, namespace) -> ResourceStatus:
    """Check resource quotas and usage."""
    quotas = get_resource_quotas(namespace)
    usage = get_current_usage(namespace)
    
    return {
        "quotas": quotas,
        "usage": usage,
        "utilization_percent": calculate_utilization(quotas, usage),
        "approaching_limits": check_threshold(usage, quotas, threshold=0.8),
        "warnings": ["CPU usage at 85% of quota", ...]
    }
```

**Event Analysis:**
```python
def _analyze_events(self, events) -> List[CriticalEvent]:
    """Extract and categorize critical events."""
    critical = []
    for event in events:
        if event.type == "Warning":
            critical.append({
                "type": event.reason,
                "message": event.message,
                "object": f"{event.involved_object.kind}/{event.involved_object.name}",
                "count": event.count,
                "last_seen": event.last_timestamp
            })
    
    # Group by type/pattern
    return group_similar_events(critical)
```

**Common Issues Detection:**
```python
def _detect_common_issues(self, namespace, deployments) -> List[DetectedIssue]:
    """Detect common Kubernetes issues."""
    issues = []
    
    # ImagePullBackOff
    image_issues = check_image_pull_errors(namespace)
    if image_issues:
        issues.append(DetectedIssue(
            type="ImagePullBackOff",
            severity="high",
            affected_objects=image_issues,
            description="Pods cannot pull container images"
        ))
    
    # CrashLoopBackOff
    crash_loops = check_crash_loops(namespace)
    
    # Pending pods (unschedulable)
    pending = check_pending_pods(namespace)
    
    # Resource constraints
    resource_pressure = check_resource_pressure(namespace)
    
    # PVC issues
    pvc_issues = check_pvc_status(namespace)
    
    return issues
```

#### 2.3 LLM Integration
```python
def _llm_analyze(self, deployment_health, resources, events, issues) -> str:
    """Use LLM to synthesize namespace analysis."""
    
    prompt = f"""
    Analyze this Kubernetes namespace health summary:
    
    DEPLOYMENTS:
    - Total: {deployment_health['total']}
    - Healthy: {deployment_health['healthy']}
    - Degraded: {deployment_health['degraded']}
    - Failing: {deployment_health['failing']}
    
    RESOURCE UTILIZATION:
    - CPU: {resources['utilization_percent']['cpu']}%
    - Memory: {resources['utilization_percent']['memory']}%
    
    CRITICAL EVENTS (last 30 min):
    {format_events(events)}
    
    DETECTED ISSUES:
    {format_issues(issues)}
    
    Provide:
    1. Overall namespace health assessment
    2. Root cause analysis of any issues
    3. Prioritized recommendations
    4. Proactive suggestions for improvement
    """
    
    return llm_chain.invoke(prompt)
```

#### 2.4 Data Models (models.py)
```python
class NamespaceAnalysis(BaseModel):
    """Results of namespace-level analysis."""
    namespace: str
    cluster: Optional[str]
    timestamp: datetime
    deployment_summary: Dict[str, Any]
    resource_status: Dict[str, Any]
    critical_events: List[Dict[str, Any]]
    detected_issues: List[Dict[str, Any]]
    analysis: str  # LLM synthesis
    recommendations: List[str]
    overall_health: str  # "healthy", "degraded", "critical"
    health_score: int  # 0-100

class DetectedIssue(BaseModel):
    type: str  # "ImagePullBackOff", "CrashLoopBackOff", etc.
    severity: str  # "low", "medium", "high", "critical"
    affected_objects: List[str]
    description: str
    first_seen: Optional[datetime]
    count: int
```

#### 2.5 API Endpoint (dashboard.py)
```python
@app.post("/api/analyze/namespace")
async def analyze_namespace(request: Request):
    """Perform on-demand namespace analysis."""
    data = await request.json()
    namespace = data.get("namespace")
    cluster = data.get("cluster", settings.cluster_name)
    
    if not namespace:
        raise HTTPException(status_code=400, detail="namespace required")
    
    # Check if namespace is system namespace
    if namespace in settings.system_namespaces:
        raise HTTPException(
            status_code=403,
            detail=f"Analysis not available for system namespace: {namespace}"
        )
    
    analyzer = NamespaceAnalyzer(
        model_name=settings.langchain_model_name,
        api_key=settings.openai_api_key,
        api_base=settings.openai_api_base,
        api_version=settings.openai_api_version,
        azure_deployment=settings.azure_deployment
    )
    
    try:
        analysis = analyzer.analyze_namespace(namespace, cluster)
        
        # Optionally save to database for history
        save_namespace_analysis(analysis)
        
        return analysis.model_dump()
    except Exception as e:
        logger.error(f"Namespace analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

#### 2.6 UI Component - Extend Existing On-Demand Page
**Reuse existing `/investigate` page** - add namespace-level analysis alongside deployment analysis

```html
<!-- Update investigate.html template -->
<div class="container">
    <h1>On-Demand Investigation</h1>
    
    <!-- Add analysis type selector -->
    <div class="form-group">
        <label>Analysis Type:</label>
        <div class="radio-group">
            <input type="radio" id="deploymentAnalysis" name="analysisType" value="deployment" checked>
            <label for="deploymentAnalysis">Single Deployment</label>
            
            <input type="radio" id="namespaceAnalysis" name="analysisType" value="namespace">
            <label for="namespaceAnalysis">Entire Namespace</label>
        </div>
    </div>
    
    <!-- Existing deployment selection (show when deploymentAnalysis selected) -->
    <div id="deploymentSection">
        <div class="form-group">
            <label for="namespace">Namespace</label>
            <select id="namespace" onchange="updateDeployments()">
                <!-- Populated dynamically, exclude system namespaces -->
            </select>
        </div>
        <div class="form-group">
            <label for="deployment">Deployment</label>
            <select id="deployment">
                <option value="">Select Namespace First</option>
            </select>
        </div>
        <button onclick="runDeploymentInvestigation()">Investigate Deployment</button>
    </div>
    
    <!-- New namespace analysis section (show when namespaceAnalysis selected) -->
    <div id="namespaceSection" style="display: none;">
        <div class="form-group">
            <label>Select Namespace:</label>
            <select id="namespaceForAnalysis">
                <!-- Populated dynamically, exclude system namespaces -->
            </select>
        </div>
        <button onclick="runNamespaceAnalysis()">Analyze Namespace</button>
    </div>
    
    <!-- Results container - shows either deployment or namespace analysis -->
    <div id="results" class="analysis-results">
        <!-- For deployment analysis: existing format -->
        <div id="deploymentResults" style="display: none;">
            <!-- Existing deployment investigation results -->
        </div>
        
        <!-- For namespace analysis: new format -->
        <div id="namespaceResults" style="display: none;">
        <!-- Health Overview Card -->
        <div class="health-card">
            <h2>Overall Health: <span class="health-badge">Healthy</span></h2>
            <div class="health-score">Score: 85/100</div>
        </div>
        
        <!-- Deployment Summary -->
        <div class="section">
            <h3>Deployments</h3>
            <div class="stats">
                <div class="stat healthy">5 Healthy</div>
                <div class="stat degraded">1 Degraded</div>
                <div class="stat failing">0 Failing</div>
            </div>
        </div>
        
        <!-- Resource Status -->
        <div class="section">
            <h3>Resource Utilization</h3>
            <div class="resource-bars">
                <div class="resource">
                    <span>CPU</span>
                    <div class="progress-bar">
                        <div class="progress" style="width: 65%">65%</div>
                    </div>
                </div>
                <div class="resource">
                    <span>Memory</span>
                    <div class="progress-bar">
                        <div class="progress" style="width: 82%">82%</div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Critical Events -->
        <div class="section">
            <h3>Recent Events (30 min)</h3>
            <ul class="events-list">
                <li class="event warning">
                    <span class="event-type">Warning</span>
                    <span class="event-msg">BackOff: pod-xyz failed</span>
                    <span class="event-time">5m ago</span>
                </li>
            </ul>
        </div>
        
        <!-- AI Analysis -->
        <div class="section analysis">
            <h3>AI Analysis</h3>
            <div class="analysis-text">
                <!-- LLM-generated analysis -->
            </div>
        </div>
        
        <!-- Recommendations -->
        <div class="section recommendations">
            <h3>Recommendations</h3>
            <ol>
                <li>Increase memory limit for deployment-xyz</li>
                <li>Review recent config changes in ConfigMap-abc</li>
                <li>Consider adding liveness probe to service-123</li>
            </ol>
        </div>
        
        <!-- Detected Issues (if any) -->
        <div class="section issues">
            <h3>Detected Issues</h3>
            <div class="issue-card high">
                <div class="issue-header">
                    <span class="severity">HIGH</span>
        </div> <!-- End namespaceResults -->
    </div>
</div>

<script>
    // Toggle between deployment and namespace analysis modes
    document.querySelectorAll('input[name="analysisType"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
            const isDeployment = e.target.value === 'deployment';
            document.getElementById('deploymentSection').style.display = isDeployment ? 'block' : 'none';
            document.getElementById('namespaceSection').style.display = isDeployment ? 'none' : 'block';
            document.getElementById('results').style.display = 'none';
        });
    });
    
    async function runNamespaceAnalysis() {
        const namespace = document.getElementById('namespaceForAnalysis').value;
        if (!namespace) return;
        
        // Show loading state
        document.getElementById('results').style.display = 'block';
        document.getElementById('namespaceResults').style.display = 'block';
        document.getElementById('deploymentResults').style.display = 'none';
        
        // Call API
        const response = await fetch('/api/analyze/namespace', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({namespace: namespace})
        });
        
        const analysis = await response.json();
        // Populate namespaceResults with analysis data
        displayNamespaceAnalysis(analysis);
    }
</script>
```

**Benefits of reusing existing page:**
- Single entry point for all on-demand investigations
- Familiar UI for users
- Natural workflow: analyze namespace → drill down to specific deployment
- Less code duplication
- Consistent navigation experience             <div class="issue-details">
                    Affected: pod-xyz (deployment: api-service)
                    <br>Count: 15 restarts in last 30 minutes
                </div>
            </div>
        </div>
    </div>
</div>
```
**Analysis Storage Decision:**
- ✅ **No database storage in Phase 1** - Treat as real-time/ephemeral analysis
- Analysis results returned directly to UI
- Simpler implementation, faster to ship
- Can add history tracking in Phase 3 if needed

#### 2.7 UI Enhancements

**Link from Rollout Detail Page:**
Add "Analyze Namespace" button on rollout detail page for easy discovery:

```html
<!-- In detail.html -->
<div class="actions">
    <a href="/investigate?namespace={{ rollout.namespace }}&type=namespace" 
       class="btn btn-secondary">
        🔍 Analyze Entire {{ rollout.namespace }} Namespace
    </a>
</div>
```

This creates a natural workflow: investigate specific failure → broaden to namespace-level analysis. )
```

---

## Implementation Phases

### Phase 1: System Namespace Filtering (Week 1)
**Priority: High** - Reduces noise, improves UX

1. Add configuration to config.py
2. Update watcher service filtering logic
3. Update database repository methods
4. Update dashboard API endpoints
5. Test with system namespaces
6. Update Helm chart
7. Documentation

**Estimated Effort:** 2-3 days

### Phase 2: Basic Namespace Analysis (Week 2)
**Priority: Medium** - New capability

1. Create NamespaceAnalyzer class
2. Implement deployment health checks
3. Implement event collection
4. Add basic LLM integration
5. Create API endpoint
6. Basic UI page
7. Testing

**Estimated Effort:** 4-5 days

### Phase 3: Enhanced Analysis (Week 3)
**Priority: Low** - Polish & extend

1. Add resource quota analysis
2. Add common issue detection
3. Improve LLM prompts
4. Add analysis history storage
5. Enhanced UI with visualizations
6. Update existing On-Demand UI page
7. Add "Analyze Namespace" link from rollout detail page
8. Testing

**Estimated Effort:** 4-5 days

### Phase 3: Enhanced Analysis (Week 3)
**Priority: Low** - Polish & extend

1. Add resource quota analysis
2. Add common issue detection
3. Improve LLM prompts
4. Add health scoring algorithm
5. Enhanced UI with visualizations
6. Add caching mechanism (15-30 min TTL)
7. *Optional:* Add analysis history storage to database
### Security
- Ensure RBAC permissions for namespace access
- Validate namespace exists before analysis
- Log all analysis requests for audit
- Consider restricting to authenticated users only

### Scalability
- Namespace analysis is synchronous - could be slow
- Consider background job queue for long-running analysis
- Add timeout protection (max 2-3 minutes)
- Graceful degradation if some data unavailable

### User Experience
- Show loading states with progress indicators
- Allow partial results if some checks fail
- Export analysis as PDF/markdown report
- Link from rollout details to namespace analysis
- Show last analysis timestamp, allow refresh

---

## Configuration Examples

### Helm values.yaml
```yaml
config:
  # Feature 1: System Namespace Filtering
  systemNamespaces:
    - kube-system
    - kube-public
    - kube-node-lease
    - monitoring
    - logging
    - flux-system
  
  # Feature 2: Namespace Analysis Settings
  namespaceAnalysis:
    enabled: true
    cacheTTL: 1800  # 30 minutes
    timeout: 180  # 3 minutes
    maxConcurrent: 3
    rateLimit:
      default
    - monitoring
    - logging
    - flux-system
    - project-fyr
    - istio-systemkube-node-lease,default,monitoring,logging,project-fyr,istio-system,elastic-system"

# Namespace analysis
PROJECT_FYR_NAMESPACE_ANALYSIS_ENABLED=true
PROJECT_FYR_NAMESPACE_ANALYSIS_CACHE_TTL=1800
PROJECT_FYR_NAMESPACE_ANALYSIS_TIMEOUT=180
```

---

## Confirmed Decisions

Based on project requirements, the following decisions have been finalized:

### System Namespaces
- ✅ **Include:** `project-fyr`, `istio-system`, `elastic-system` in default list
- ✅ **Exclude:** `default` namespace from monitoring
- ✅ **Admin Override:** Support `?include_system=true` query parameter

### Namespace Analysis Storage
- ✅ **Phase 1:** No database storage (real-time/ephemeral)
- ✅ **Future:** Can add history tracking in Phase 3 if needed

### UI Integration
- ✅ **Reuse:** Extend existing `/investigate` page with radio toggle
- ✅ **Cross-link:** Add "Analyze Namespace" button on rollout detail pagesJECT_FYR_SYSTEM_NAMESPACES="kube-system,kube-public,monitoring,logging"

# Namespace analysis
PROJECT_FYR_NAMESPACE_ANALYSIS_ENABLED=true
PROJECT_FYR_NAMESPACE_ANALYSIS_CACHE_TTL=1800
PROJECT_FYR_NAMESPACE_ANALYSIS_TIMEOUT=180
```

---

## Success Metrics

### Feature 1: System Namespace Filtering
- Reduction in noise on dashboard (target: 30-50% fewer rollouts shown)
- Faster page load times (less data to process)
- Improved user focus on relevant namespaces
- Positive user feedback on cleaner interface

### Feature 2: On-Demand Namespace Analysis
- Adoption rate (% of users using feature weekly)
- Time to insight (how quickly issues are identified)
- User satisfaction score for analysis quality
- Reduction in MTTR for namespace-level issues
- Number of proactive issues caught before failure

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| System namespace list incomplete | Low | Make easily configurable, document well |
| Namespace analysis too slow | High | Add timeout, caching, async processing |
| LLM costs increase significantly | Medium | Rate limiting, caching, optimize prompts |
| Users confused by two analysis types | Medium | Clear UI/UX, good naming, documentation |
| Analysis missing critical issues | High | Comprehensive testing, user feedback loop |

---

## Open Questions

1. **System Namespaces:**
   - Should we allow per-cluster customization of system namespaces?
   - Should there be a UI to view/edit the system namespace list?
   - Should system namespace rollouts be soft-deleted or just filtered?

2. **Namespace Analysis:**
   - Should analysis be saved to database for historical tracking?
   - Should we send analysis results to Slack/email?
   - How to handle very large namespaces (100+ deployments)?
   - Should analysis be scheduled periodically (daily health check)?
   - Integration with existing deployment-level investigation?

3. **Future Extensions:**
   - When to add application-level log analysis?
   - Should we analyze based on labels/annotations (e.g., team ownership)?
   - Multi-cluster namespace analysis?
   - Cost analysis integration?

---

## Next Steps

1. **Review & Approve** this plan
2. **Prioritize** features (can implement independently)
3. **Refine** technical design based on feedback
4. **Prototype** namespace analyzer core logic
5. **Implement** Phase 1 (system namespaces)
6. **Test** with real cluster data
7. **Deploy** to staging environment
8. **Gather** user feedback
9. **Iterate** based on learnings

---

## Resources

- Kubernetes API: Deployments, Events, ResourceQuotas
- Python Kubernetes Client: https://github.com/kubernetes-client/python
- LangChain: Prompt engineering for analysis
- Similar tools: Kuberhealthy, Popeye, KubeScore (for inspiration)
