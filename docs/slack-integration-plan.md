# Fyr Slack Integration Plan

## Overview

This document outlines the Slack integration strategy for Project Fyr, including architecture decisions, use cases, and implementation approach.

**Key Constraint:** Fyr is NOT exposed to the public internet → **Socket Mode** required.

---

## 1. Architecture: Socket Mode (No Inbound Exposure)

### Why Socket Mode?

| Requirement | Traditional Webhooks | Socket Mode ✅ |
|-------------|---------------------|----------------|
| Public URL needed | Yes | **No** |
| Firewall changes | Required | **Not needed** |
| Slack → App communication | HTTP POST to your URL | **App opens WebSocket to Slack** |
| Features supported | All | **All** |

### How Socket Mode Works

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Grid Dynamics Network                            │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                    Kubernetes Cluster (cmp-ci-aks)                │  │
│  │                                                                    │  │
│  │  ┌────────────────────────────────────────────────────────────┐  │  │
│  │  │              Project Fyr (project-fyr namespace)            │  │  │
│  │  │                                                              │  │  │
│  │  │  ┌─────────────────────┐     ┌─────────────────────────┐   │  │  │
│  │  │  │   Dashboard Pod     │     │   Slack Handler Pod     │   │  │  │
│  │  │  │ (existing)          │     │   (new component)       │   │  │  │
│  │  │  │                     │     │                         │   │  │  │
│  │  │  │ - Web UI            │     │ - Socket Mode Client    │   │  │  │
│  │  │  │ - REST API          │     │ - Slash Commands        │   │  │  │
│  │  │  │ - Investigation     │     │ - Interactive Buttons   │   │  │  │
│  │  │  └─────────────────────┘     │ - App Home Tab          │   │  │  │
│  │  │            │                 │                         │   │  │  │
│  │  │            └────────────────►└──────────┬──────────────┘   │  │  │
│  │  │            Internal API calls            │                  │  │  │
│  │  │                                          │                  │  │  │
│  │  └──────────────────────────────────────────┼──────────────────┘  │  │
│  │                                              │                     │  │
│  └──────────────────────────────────────────────┼─────────────────────┘  │
│                                                 │                        │
│                        OUTBOUND WebSocket       │                        │
│                        Connection Only          │                        │
└─────────────────────────────────────────────────┼────────────────────────┘
                                                  │
                                                  ▼
                          ┌───────────────────────────────────────┐
                          │              Slack Platform           │
                          │                                       │
                          │  ┌─────────────────────────────────┐ │
                          │  │     Fyr App (Org-deployed)      │ │
                          │  │                                 │ │
                          │  │  - Bot Token                    │ │
                          │  │  - App Token (Socket Mode)      │ │
                          │  │  - Org Distribution             │ │
                          │  └─────────────────────────────────┘ │
                          │                                       │
                          │  Grid Dynamics Slack Workspace       │
                          │  ┌─────────────────────────────────┐ │
                          │  │  #platform-alerts               │ │
                          │  │  #team-payments-alerts          │ │
                          │  │  #sre-oncall                    │ │
                          │  └─────────────────────────────────┘ │
                          └───────────────────────────────────────┘
```

### Required Tokens (Socket Mode)

| Token | Purpose | Where to Get |
|-------|---------|--------------|
| `SLACK_BOT_TOKEN` | Post messages, update home tab | OAuth & Permissions → Bot Token |
| `SLACK_APP_TOKEN` | Socket Mode connection | Basic Information → App-Level Tokens (scope: `connections:write`) |

### Python Implementation

```python
# Using slack_bolt library (official Slack SDK)
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

app = App(token=os.environ["SLACK_BOT_TOKEN"])

# Socket Mode handler - connects OUTBOUND to Slack
handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
handler.start()  # Blocks and maintains WebSocket connection
```

---

## 2. Deployment: Grid Dynamics Organization App

### Distribution Model

**Org-deployed app** (recommended for Grid):
- App created in Slack admin portal
- Available to all workspaces in Grid Dynamics org
- Admins control installation
- No public distribution/review needed
- Socket Mode fully supported

### Installation Flow

1. Admin creates app in Grid Slack admin
2. Configure Socket Mode (toggle ON)
3. Set bot scopes (see below)
4. Install to workspace(s)
5. Share bot token + app token with Fyr deployment

### Required Bot Scopes

```
# Core messaging
chat:write              # Post messages
chat:write.public       # Post to public channels without joining

# Slash commands
commands                # Handle /fyr commands

# Interactive components
im:write                # DM users
im:history              # Read DM history (for App Home)

# App Home
app_mentions:read       # Respond when @mentioned
users:read              # Resolve user info

# Rich messages
files:write             # Share analysis reports
```

---

## 3. Use Cases

### UC-1: Failure Notification with Investigation Link ⭐ (Priority 1)

**Trigger:** Rollout failure detected and analyzed

**Flow:**
```
Watcher detects failure
        ↓
Analyzer runs AI investigation  
        ↓
Analysis stored in DB
        ↓
Slack Notifier posts to channel
```

**Slack Message (Enhanced):**

```
🔥 Deployment Failure: payments-api (cmp-ci-aks)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *Summary*
Payment service failing with OOM kills after latest rollout

🎯 *Likely Cause*  
Memory limit too low for new features. Container requests 256Mi but uses 400Mi+ under load.

🔧 *Recommended Actions*
1. Increase memory limit to 512Mi
2. Check for memory leaks in v2.3.1 release

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

┌──────────────────┐  ┌──────────────────────────────────┐
│ 🔍 View in Fyr   │  │ 💬 Investigate Further with AI  │
└──────────────────┘  └──────────────────────────────────┘

Namespace: payments | Team: Payments SRE | Severity: High
Rollout ID: 12345 | Time: 14:32 UTC
```

**Button Actions:**
- "View in Fyr" → Opens `https://fyr.griddynamics.net/rollout/12345`
- "Investigate Further" → Opens thread with AI chat

---

### UC-2: Alert Notification with Context

**Trigger:** Alertmanager/Grafana alert webhook received

**Flow:**
```
Alert webhook received
        ↓
Alert batched with related alerts
        ↓
AI investigation runs
        ↓
Slack Notifier posts correlated alert summary
```

**Slack Message:**

```
⚠️ Alert Batch: payments namespace (3 related alerts)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🚨 *Alerts in this batch:*
• PodRestartingTooOften: payments-api (12 restarts in 5m)
• HighMemoryUsage: payments-api (95% of limit)  
• PodOOMKilled: payments-api-6d7f8 (3 times)

🎯 *Analysis*
All alerts stem from memory pressure on payments-api deployment.
Root cause appears to be memory leak introduced in v2.3.1.

🔧 *Recommended Actions*
1. Rollback to v2.3.0
2. Increase memory limit as temporary measure
3. Investigate heap usage in v2.3.1

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

┌──────────────────┐  ┌─────────────────────┐
│ 🔍 View in Fyr   │  │ 📊 View in Grafana  │
└──────────────────┘  └─────────────────────┘
```

---

### UC-3: Slash Command `/fyr` ⭐ (Priority 2)

**Commands:**

| Command | Description | Example |
|---------|-------------|---------|
| `/fyr status` | Overall cluster health summary | `/fyr status` |
| `/fyr namespace <ns>` | Quick namespace health check | `/fyr namespace payments` |
| `/fyr investigate <ns> <deploy>` | Trigger on-demand investigation | `/fyr investigate payments payments-api` |
| `/fyr recent` | List recent failures (last 24h) | `/fyr recent` |
| `/fyr help` | Show available commands | `/fyr help` |

**Example: `/fyr status`**

```
📊 Fyr Cluster Status (cmp-ci-aks)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Last 24 hours:
✅ Successful rollouts: 45
⚠️ Failed rollouts: 3
🔄 In progress: 2

Recent failures:
• payments-api (2h ago) - OOM Kill
• order-service (6h ago) - ImagePullBackOff  
• auth-proxy (18h ago) - CrashLoopBackOff

┌─────────────────────┐
│ 🔍 Open Fyr Dashboard │
└─────────────────────┘
```

**Example: `/fyr namespace payments`**

```
🔍 Namespace: payments
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Status: Active
Deployments: 5 (4 healthy, 1 degraded)

⚠️ Issues detected:
• payments-api: 3/4 replicas ready (OOM restarts)
• High restart rate: 12 restarts in last hour

Resource Quotas:
• CPU: 4.2/8 cores (52%)
• Memory: 12Gi/16Gi (75%)

┌──────────────────────────┐  ┌──────────────────┐
│ 🤖 Run AI Investigation  │  │ 🔍 View in Fyr   │
└──────────────────────────┘  └──────────────────┘
```

---

### UC-4: Interactive AI Chat in Thread

**Trigger:** User clicks "Investigate Further" on failure notification

**Flow:**
1. Opens new thread under the failure notification
2. User can ask follow-up questions
3. Fyr AI responds with investigation results
4. Supports multi-turn conversation

**Example Thread:**

```
🤖 Fyr AI Investigation
I'll help you investigate the payments-api failure. What would you like to know?

👤 User: What was the exact error message?

🤖 Fyr AI: Looking at the pod logs...

The failing container shows:
```
fatal error: runtime: out of memory
SIGABRT: abort
goroutine 1 [running]:
runtime.throw(0x1a2b3c, 0x16)
```

This is a Go runtime OOM - the container was killed mid-execution 
when it exceeded its memory limit.

👤 User: Show me the memory usage trend

🤖 Fyr AI: Querying Prometheus for memory metrics...

📈 Memory trend for payments-api (last 2h):
• 12:00 - 180Mi (45% of 400Mi limit)
• 12:30 - 250Mi (62%)
• 13:00 - 320Mi (80%)  
• 13:15 - 398Mi (99%) ⚠️
• 13:16 - OOMKilled

Memory grew linearly suggesting a leak. The increase started 
after the v2.3.1 deployment at 11:45.
```

---

### UC-5: App Home Tab (Dashboard in Slack)

**Content:** Quick-access dashboard without leaving Slack

```
┌─────────────────────────────────────────────────────────────┐
│  🔥 Fyr - Kubernetes Intelligence                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  📊 Cluster: cmp-ci-aks                                    │
│                                                             │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  Last 24 Hours                                              │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                                             │
│  ✅ 45 Successful    ⚠️ 3 Failed    🔄 2 In Progress       │
│                                                             │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  Recent Failures                                            │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                                             │
│  🔴 payments-api (payments)           2h ago               │
│     OOM Kill - Memory limit exceeded                       │
│     [View Details] [Investigate]                           │
│                                                             │
│  🟡 order-service (orders)            6h ago               │
│     ImagePullBackOff - Registry auth failed                │
│     [View Details] [Investigate]                           │
│                                                             │
│  🟢 auth-proxy (platform)             18h ago              │
│     Resolved - CrashLoopBackOff                           │
│     [View Details]                                         │
│                                                             │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  Quick Actions                                              │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                                             │
│  [🔍 Open Dashboard]  [📊 View Insights]  [⚙️ Settings]    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

### UC-6: Daily/Weekly Digest

**Trigger:** Scheduled (e.g., 9am daily, Monday 9am weekly)

**Flow:**
1. Slack handler triggers on schedule
2. Queries Fyr DB for stats
3. Calls AI aggregator for insights
4. Posts digest to configured channel

**Daily Digest Example:**

```
📊 Fyr Daily Digest - January 14, 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Cluster: cmp-ci-aks

📈 *Rollout Stats (24h)*
• Total deployments: 47
• Success rate: 93.6%
• Failures: 3
• Mean time to detect: 2.3 min

🏆 *Most Stable Namespaces*
1. platform (12 rollouts, 100% success)
2. logging (8 rollouts, 100% success)
3. monitoring (5 rollouts, 100% success)

⚠️ *Namespaces Needing Attention*
1. payments (5 rollouts, 60% success)
   - Recurring OOM issues
2. orders (3 rollouts, 66% success)
   - Registry authentication failures

🔍 *Top Issues (AI Summary)*
Memory pressure remains the primary cause of failures.
Consider cluster-wide resource review for payment services.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[📊 Full Report] [🔍 Open Dashboard]
```

---

### UC-7: Team-Specific Routing

**Feature:** Route notifications to team-specific channels based on namespace annotations

**Configuration:**
```bash
kubectl annotate namespace payments \
  project-fyr/slack-channel="#payments-oncall" \
  project-fyr/team="Payments SRE"
```

**Routing Logic:**
1. Check namespace annotation `project-fyr/slack-channel`
2. Fallback to alert label `slack_channel`
3. Fallback to `SLACK_DEFAULT_CHANNEL`

---

### UC-8: Mention @oncall on Critical Issues

**Feature:** Automatically mention on-call rotation for critical/high severity issues

**Configuration:**
```yaml
# Helm values
slack:
  mentionOnCritical: true
  oncallUserGroupId: "S0123456789"  # Slack User Group ID
```

**Message with Mention:**
```
🚨 CRITICAL: Database Connection Pool Exhausted

cc: <!subteam^S0123456789> (oncall rotation)

...rest of alert...
```

---

### UC-9: Acknowledge/Resolve from Slack

**Feature:** Quick actions to acknowledge alerts without leaving Slack

**Buttons on Alert:**
- "👀 Acknowledge" - Marks alert as acknowledged, stops repeat notifications
- "✅ Resolve" - Marks issue as resolved manually
- "🔇 Snooze 1h" - Temporarily suppress notifications

**Flow:**
```
User clicks "Acknowledge"
        ↓
Fyr updates alert state in DB
        ↓
Message updated to show:
  "👀 Acknowledged by @jakub.filipczak at 14:35"
        ↓
No repeat notifications for this fingerprint (24h)
```

---

### UC-10: Investigation Report Export

**Feature:** Export full investigation report as PDF/Markdown to Slack

**Trigger:** Button on failure notification or slash command

**Flow:**
```
User clicks "📄 Export Report"
        ↓
Fyr generates comprehensive report:
  - Timeline of events
  - Full log excerpts
  - Metrics charts
  - AI analysis
  - Recommended actions
        ↓
Upload as file to Slack thread
```

---

## 4. Implementation Phases

### Phase 1: Foundation (Week 1-2)

**Deliverables:**
- [ ] Socket Mode handler component
- [ ] Basic slash command `/fyr help`
- [ ] Enhanced notification format with buttons
- [ ] "View in Fyr" deep links

**New Files:**
- `project_fyr/slack_handler.py` - Socket Mode event handler
- `project_fyr/slack_commands.py` - Slash command implementations
- `project_fyr/slack_blocks.py` - Block Kit message builders

**Configuration:**
```python
# config.py additions
SLACK_APP_TOKEN: str = ""  # xapp-... for Socket Mode
SLACK_SIGNING_SECRET: str = ""  # For request verification (future webhook support)
SLACK_SOCKET_MODE_ENABLED: bool = True
```

### Phase 2: Interactive Features (Week 3-4)

**Deliverables:**
- [ ] `/fyr status` command
- [ ] `/fyr namespace <ns>` command  
- [ ] `/fyr investigate` command
- [ ] Interactive AI chat in threads
- [ ] Acknowledge/Resolve buttons

### Phase 3: App Home & Scheduling (Week 5-6)

**Deliverables:**
- [ ] App Home tab with dashboard
- [ ] Daily digest scheduling
- [ ] Team routing based on annotations
- [ ] @oncall mentions for critical issues

### Phase 4: Polish & Advanced (Week 7+)

**Deliverables:**
- [ ] Weekly digest with trends
- [ ] Report export (PDF/Markdown)
- [ ] Custom notification preferences
- [ ] Slash command autocomplete

---

## 5. Technical Implementation Notes

### Socket Mode Handler Architecture

```python
# slack_handler.py

import asyncio
import logging
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler

from .config import settings
from .slack_commands import register_commands
from .slack_events import register_events
from .slack_actions import register_actions

logger = logging.getLogger(__name__)

class FyrSlackHandler:
    def __init__(self):
        self.app = AsyncApp(token=settings.slack_bot_token)
        
        # Register handlers
        register_commands(self.app)
        register_events(self.app)
        register_actions(self.app)
        
        self.handler = AsyncSocketModeHandler(
            self.app, 
            settings.slack_app_token
        )
    
    async def start(self):
        """Start Socket Mode connection (runs forever)."""
        logger.info("Starting Fyr Slack handler (Socket Mode)")
        await self.handler.start_async()
    
    async def stop(self):
        """Gracefully stop the handler."""
        await self.handler.close_async()
```

### Deployment Options

**Option A: Sidecar in Dashboard Pod**
```yaml
# Add to dashboard deployment
containers:
  - name: dashboard
    # existing dashboard container
  - name: slack-handler
    image: cpdevregistry.azurecr.io/project-fyr:latest
    command: ["python", "-m", "project_fyr.slack_handler"]
    env:
      - name: SLACK_BOT_TOKEN
        valueFrom:
          secretKeyRef:
            name: fyr-secrets
            key: slack-bot-token
      - name: SLACK_APP_TOKEN
        valueFrom:
          secretKeyRef:
            name: fyr-secrets
            key: slack-app-token
```

**Option B: Separate Deployment** (recommended for scaling)
```yaml
# New slack-handler-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: project-fyr-slack-handler
spec:
  replicas: 1  # Socket Mode handles reconnection
  # ...
```

---

## 6. Fyr Capabilities Summary (from codebase analysis)

Based on the codebase, Fyr can provide these features via Slack:

| Capability | Source | Slack Use Case |
|------------|--------|----------------|
| Deployment rollout monitoring | `watcher_service.py` | Failure notifications |
| AI-powered investigation | `agent.py`, `tools.py` | `/fyr investigate`, chat |
| Namespace health analysis | `namespace_analyzer.py` | `/fyr namespace` |
| 14+ K8s query tools | `tools.py` | AI chat context |
| Alert batching & correlation | `webhook.py` | Correlated alert notifications |
| Triage to teams | `triage.py` | Team routing |
| Prometheus metrics | `tools.py` | Memory/CPU analysis |
| ArgoCD integration | `tools.py` | GitOps status |
| Helm release status | `tools.py` | Release info |
| Issue aggregation | `aggregator.py` | Daily digest insights |

---

## 7. Security Considerations

### Socket Mode Security

- **No inbound exposure** - No HTTP endpoints exposed
- **TLS encryption** - WebSocket uses WSS (TLS)
- **Token rotation** - App tokens can be rotated without downtime
- **Audit logging** - All commands logged with user context

### Data Privacy

- **No secrets exposed** - `k8s_get_secret_structure` only shows keys, not values
- **Channel permissions** - Bot respects Slack channel permissions
- **User attribution** - All actions logged with Slack user ID

### Kubernetes RBAC

- **Read-only** - Fyr has no write permissions to cluster
- **Namespace scoping** - Tools respect namespace boundaries
- **Audit trail** - All K8s queries logged

---

## 8. Next Steps

1. **Create Slack App in Grid Admin**
   - Enable Socket Mode
   - Configure bot scopes
   - Generate app token

2. **Implement Phase 1**
   - Socket Mode handler
   - Basic slash commands
   - Enhanced notifications

3. **Deploy & Test**
   - Add to Helm chart
   - Test in #platform-test channel
   - Iterate on UX

---

## Appendix A: Slack App Manifest

```yaml
display_information:
  name: Fyr
  description: Kubernetes Rollout Intelligence
  background_color: "#FF6B35"

features:
  app_home:
    home_tab_enabled: true
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: Fyr
    always_online: true
  slash_commands:
    - command: /fyr
      description: Kubernetes cluster intelligence
      usage_hint: "[status|namespace|investigate|recent|help]"
      should_escape: false

oauth_config:
  scopes:
    bot:
      - chat:write
      - chat:write.public
      - commands
      - im:write
      - im:history
      - app_mentions:read
      - users:read
      - files:write

settings:
  event_subscriptions:
    bot_events:
      - app_home_opened
      - app_mention
      - message.im
  interactivity:
    is_enabled: true
  org_deploy_enabled: true
  socket_mode_enabled: true
  token_rotation_enabled: false
```

---

## Appendix B: Environment Variables

```bash
# Required for Socket Mode
SLACK_BOT_TOKEN=xoxb-...           # Bot User OAuth Token
SLACK_APP_TOKEN=xapp-...           # App-Level Token (connections:write)

# Optional
SLACK_DEFAULT_CHANNEL=#platform-alerts
SLACK_ONCALL_USER_GROUP=S0123456789
SLACK_DAILY_DIGEST_CHANNEL=#platform-daily
SLACK_DAILY_DIGEST_CRON="0 9 * * *"
```
