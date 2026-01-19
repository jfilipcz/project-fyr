"""Slack Block Kit message builders for Project Fyr."""

from __future__ import annotations

from typing import Optional, List, Dict, Any
from .models import Analysis
from .config import settings


def _get_severity_emoji(severity: str) -> str:
    """Get emoji for severity level."""
    severity_emojis = {
        "critical": "🚨",
        "high": "🔴",
        "medium": "🟡",
        "low": "🟢",
    }
    return severity_emojis.get(severity.lower(), "⚠️")


def _get_dashboard_url(path: str) -> str:
    """Build full dashboard URL."""
    base = settings.dashboard_base_url or "http://localhost:8000"
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def build_failure_notification(
    *,
    rollout_ref: str,
    analysis: Analysis,
    rollout_id: Optional[int] = None,
    namespace: Optional[str] = None,
    deployment: Optional[str] = None,
    cluster: Optional[str] = None,
    team: Optional[str] = None,
    pipeline_url: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Build enhanced Slack Block Kit message for deployment failure.
    
    Includes:
    - Header with severity indicator
    - Summary section
    - Likely cause
    - Recommended actions
    - Action buttons (View in Fyr, Investigate Further)
    - Context footer
    """
    severity_emoji = _get_severity_emoji(analysis.severity)
    cluster_name = cluster or settings.k8s_cluster_name
    
    blocks = []
    
    # Header
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"{severity_emoji} Deployment Failure: {rollout_ref}",
            "emoji": True
        }
    })
    
    # Divider
    blocks.append({"type": "divider"})
    
    # Summary section
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*📊 Summary*\n{analysis.summary}"
        }
    })
    
    # Likely Cause section
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*🎯 Likely Cause*\n{analysis.likely_cause}"
        }
    })
    
    # Recommended Actions
    if analysis.recommended_steps:
        steps_text = "\n".join([f"• {step}" for step in analysis.recommended_steps[:5]])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🔧 Recommended Actions*\n{steps_text}"
            }
        })
    
    # Triage info if available
    triage_team = getattr(analysis, "triage_team", None)
    triage_reason = getattr(analysis, "triage_reason", None)
    if triage_team:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*👥 Triage*\nAssigned to: *{triage_team}*\n_{triage_reason}_"
            }
        })
    
    # Divider before actions
    blocks.append({"type": "divider"})
    
    # Action buttons
    action_elements = []
    
    # View in Fyr button
    if rollout_id:
        view_url = _get_dashboard_url(f"/rollout/{rollout_id}")
        action_elements.append({
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": "🔍 View in Fyr",
                "emoji": True
            },
            "url": view_url,
            "action_id": "view_in_fyr"
        })
    
    # Investigate Further button (triggers AI chat in thread)
    if namespace and deployment:
        action_elements.append({
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": "💬 Investigate Further",
                "emoji": True
            },
            "action_id": "investigate_further",
            "value": f"{namespace}/{deployment}"
        })
    
    # Pipeline link if available
    if pipeline_url:
        action_elements.append({
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": "📋 View Pipeline",
                "emoji": True
            },
            "url": pipeline_url,
            "action_id": "view_pipeline"
        })
    
    if action_elements:
        blocks.append({
            "type": "actions",
            "elements": action_elements
        })
    
    # Context footer
    context_elements = []
    
    if namespace:
        context_elements.append({
            "type": "mrkdwn",
            "text": f"*Namespace:* {namespace}"
        })
    
    if team:
        context_elements.append({
            "type": "mrkdwn",
            "text": f"*Team:* {team}"
        })
    
    context_elements.append({
        "type": "mrkdwn",
        "text": f"*Severity:* {analysis.severity.capitalize()}"
    })
    
    context_elements.append({
        "type": "mrkdwn",
        "text": f"*Cluster:* {cluster_name}"
    })
    
    if rollout_id:
        context_elements.append({
            "type": "mrkdwn",
            "text": f"*Rollout ID:* {rollout_id}"
        })
    
    blocks.append({
        "type": "context",
        "elements": context_elements
    })
    
    return blocks


def build_alert_batch_notification(
    *,
    batch_id: int,
    namespace: str,
    alerts: List[Dict[str, Any]],
    analysis: Optional[Analysis] = None,
    primary_alert_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Build Slack Block Kit message for alert batch notification.
    """
    alert_count = len(alerts)
    severity = analysis.severity if analysis else "medium"
    severity_emoji = _get_severity_emoji(severity)
    
    blocks = []
    
    # Header
    header_text = f"{severity_emoji} Alert Batch: {namespace}"
    if alert_count > 1:
        header_text += f" ({alert_count} related alerts)"
    
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": header_text,
            "emoji": True
        }
    })
    
    blocks.append({"type": "divider"})
    
    # Alert list
    alert_list = []
    for alert in alerts[:5]:  # Limit to 5 alerts
        labels = alert.get("labels", {})
        alert_name = labels.get("alertname", "Unknown")
        pod = labels.get("pod", labels.get("instance", ""))
        alert_list.append(f"• *{alert_name}*" + (f": {pod}" if pod else ""))
    
    if alert_count > 5:
        alert_list.append(f"_...and {alert_count - 5} more_")
    
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*🚨 Alerts in this batch:*\n" + "\n".join(alert_list)
        }
    })
    
    # Analysis if available
    if analysis:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🎯 Analysis*\n{analysis.summary}"
            }
        })
        
        if analysis.recommended_steps:
            steps_text = "\n".join([f"• {step}" for step in analysis.recommended_steps[:3]])
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🔧 Recommended Actions*\n{steps_text}"
                }
            })
    
    blocks.append({"type": "divider"})
    
    # Action buttons
    action_elements = [
        {
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": "🔍 View in Fyr",
                "emoji": True
            },
            "url": _get_dashboard_url(f"/alerts/{batch_id}"),
            "action_id": "view_alert_batch"
        },
        {
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": "👀 Acknowledge",
                "emoji": True
            },
            "action_id": "acknowledge_alert",
            "value": str(batch_id),
            "style": "primary"
        }
    ]
    
    blocks.append({
        "type": "actions",
        "elements": action_elements
    })
    
    # Context
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"*Namespace:* {namespace}"},
            {"type": "mrkdwn", "text": f"*Severity:* {severity.capitalize()}"},
            {"type": "mrkdwn", "text": f"*Batch ID:* {batch_id}"},
        ]
    })
    
    return blocks


def build_status_response(
    *,
    cluster: str,
    successful: int,
    failed: int,
    in_progress: int,
    recent_failures: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build response for /fyr status command."""
    blocks = []
    
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"📊 Fyr Cluster Status ({cluster})",
            "emoji": True
        }
    })
    
    blocks.append({"type": "divider"})
    
    # Stats
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Last 24 hours:*\n✅ Successful rollouts: {successful}\n⚠️ Failed rollouts: {failed}\n🔄 In progress: {in_progress}"
        }
    })
    
    # Recent failures
    if recent_failures:
        failure_list = []
        for f in recent_failures[:5]:
            name = f.get("deployment", "unknown")
            ns = f.get("namespace", "")
            time_ago = f.get("time_ago", "")
            cause = f.get("cause", "")
            failure_list.append(f"• *{name}* ({ns}) - {time_ago}\n  _{cause}_")
        
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Recent failures:*\n" + "\n".join(failure_list)
            }
        })
    else:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "✨ *No recent failures!*"
            }
        })
    
    blocks.append({"type": "divider"})
    
    # Dashboard button
    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "🔍 Open Fyr Dashboard",
                    "emoji": True
                },
                "url": _get_dashboard_url("/"),
                "action_id": "open_dashboard"
            }
        ]
    })
    
    return blocks


def build_namespace_response(
    *,
    namespace: str,
    status: str,
    deployments_total: int,
    deployments_healthy: int,
    deployments_degraded: int,
    issues: List[str],
    quotas: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Build response for /fyr namespace <ns> command."""
    blocks = []
    
    status_emoji = "🟢" if deployments_degraded == 0 else "🟡" if deployments_degraded < deployments_total / 2 else "🔴"
    
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"🔍 Namespace: {namespace}",
            "emoji": True
        }
    })
    
    blocks.append({"type": "divider"})
    
    # Status overview
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Status:* {status_emoji} {status}\n*Deployments:* {deployments_total} ({deployments_healthy} healthy, {deployments_degraded} degraded)"
        }
    })
    
    # Issues
    if issues:
        issues_text = "\n".join([f"• {issue}" for issue in issues[:5]])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*⚠️ Issues detected:*\n{issues_text}"
            }
        })
    
    # Quotas
    if quotas:
        quota_lines = []
        for resource, info in quotas.items():
            used = info.get("used", 0)
            limit = info.get("limit", 0)
            pct = (used / limit * 100) if limit else 0
            emoji = "🔴" if pct > 90 else "🟡" if pct > 70 else "🟢"
            quota_lines.append(f"{emoji} {resource}: {used}/{limit} ({pct:.0f}%)")
        
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Resource Quotas:*\n" + "\n".join(quota_lines)
            }
        })
    
    blocks.append({"type": "divider"})
    
    # Action buttons
    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "🤖 Run AI Investigation",
                    "emoji": True
                },
                "action_id": "investigate_namespace",
                "value": namespace,
                "style": "primary"
            },
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "🔍 View in Fyr",
                    "emoji": True
                },
                "url": _get_dashboard_url(f"/investigate?namespace={namespace}"),
                "action_id": "view_namespace"
            }
        ]
    })
    
    return blocks


def build_help_response() -> List[Dict[str, Any]]:
    """Build response for /fyr help command."""
    blocks = []
    
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": "🔥 Fyr - Kubernetes Rollout Intelligence",
            "emoji": True
        }
    })
    
    blocks.append({"type": "divider"})
    
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "*Available Commands:*"
        }
    })
    
    commands = [
        ("`/fyr status`", "Show cluster health summary"),
        ("`/fyr namespace <ns>`", "Check namespace health"),
        ("`/fyr investigate <ns> <deploy>`", "Trigger AI investigation"),
        ("`/fyr recent`", "List recent failures (24h)"),
        ("`/fyr help`", "Show this help message"),
    ]
    
    for cmd, desc in commands:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{cmd}\n_{desc}_"
            }
        })
    
    blocks.append({"type": "divider"})
    
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": "💡 Tip: Click *Investigate Further* on any failure notification to ask follow-up questions"
            }
        ]
    })
    
    # Dashboard button
    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "🔍 Open Fyr Dashboard",
                    "emoji": True
                },
                "url": _get_dashboard_url("/"),
                "action_id": "open_dashboard"
            }
        ]
    })
    
    return blocks


def build_investigation_started_response(
    namespace: str,
    deployment: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Build response when investigation is starting."""
    target = f"{namespace}/{deployment}" if deployment else namespace
    
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"🔍 *Starting AI investigation for {target}...*\n\nThis may take 30-60 seconds. I'll post the results when ready."
            }
        }
    ]


def build_investigation_result_response(
    *,
    namespace: str,
    deployment: Optional[str],
    analysis: Analysis,
) -> List[Dict[str, Any]]:
    """Build response with investigation results."""
    target = f"{namespace}/{deployment}" if deployment else namespace
    severity_emoji = _get_severity_emoji(analysis.severity)
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{severity_emoji} Investigation Results: {target}",
                "emoji": True
            }
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*📊 Summary*\n{analysis.summary}"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🎯 Likely Cause*\n{analysis.likely_cause}"
            }
        },
    ]
    
    if analysis.recommended_steps:
        steps_text = "\n".join([f"• {step}" for step in analysis.recommended_steps])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🔧 Recommended Actions*\n{steps_text}"
            }
        })
    
    blocks.append({"type": "divider"})
    
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"*Severity:* {analysis.severity.capitalize()}"},
            {"type": "mrkdwn", "text": f"*Namespace:* {namespace}"},
        ]
    })
    
    return blocks


def build_error_response(message: str) -> List[Dict[str, Any]]:
    """Build error response."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"❌ *Error:* {message}"
            }
        }
    ]
