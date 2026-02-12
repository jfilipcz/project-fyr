"""Slack Block Kit message builders for Project Fyr."""

from __future__ import annotations

import re
from typing import Optional, List, Dict, Any
from .models import Analysis
from .config import settings

# Slack block text limits
SLACK_TEXT_MAX_LENGTH = 2900  # Leave some buffer from the 3000 limit


def _truncate_text(text: str, max_length: int = SLACK_TEXT_MAX_LENGTH) -> str:
    """Truncate text to fit Slack's block text limit."""
    if len(text) <= max_length:
        return text
    # Truncate and add ellipsis indicator
    return text[:max_length - 50] + "\n\n_...truncated (too long for Slack)_"


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


def _markdown_to_slack(text: str) -> str:
    """
    Convert standard Markdown to Slack mrkdwn format.
    
    Conversions:
    - ## Headers → *Bold Headers*
    - ### Sub-headers → *Bold Sub-headers*
    - **bold** → *bold* (Slack format)
    - Fix malformed bold syntax (spaces, mixed asterisks)
    - `code` → `code` (same)
    - ```code blocks``` → ```code blocks``` (same)
    - Remove excessive line breaks
    
    Slack mrkdwn rules:
    - Asterisks must be directly adjacent to text (no spaces)
    - Only single asterisks for bold (not double)
    """
    if not text:
        return text
    
    # First, protect code blocks from modification
    # Extract code blocks temporarily
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"
    
    text = re.sub(r'```[\s\S]*?```', save_code_block, text)
    
    # Protect inline code
    inline_codes = []
    def save_inline_code(match):
        inline_codes.append(match.group(0))
        return f"__INLINE_CODE_{len(inline_codes)-1}__"
    
    text = re.sub(r'`[^`]+`', save_inline_code, text)
    
    # Convert ## headers to bold with newline
    text = re.sub(r'^## (.+)$', r'*\1*', text, flags=re.MULTILINE)
    
    # Convert ### sub-headers to bold
    text = re.sub(r'^### (.+)$', r'*\1*', text, flags=re.MULTILINE)
    
    # Convert **bold** to *bold* (Slack format) - but be careful with multiple on same line
    text = re.sub(r'\*\*([^*]+?)\*\*', r'*\1*', text)
    
    # Fix malformed patterns from LLM:
    # Pattern: "*1. *text**" → "*1. text*" (number with mixed asterisks)
    text = re.sub(r'\*(\d+\.)\s*\*([^*]+?)\*\*', r'*\1 \2*', text)
    
    # Fix: "**text *" or "* text**" → "*text*"
    text = re.sub(r'\*\*\s*([^*]+?)\s*\*(?!\*)', r'*\1*', text)
    text = re.sub(r'\*(?!\*)\s*([^*]+?)\s*\*\*', r'*\1*', text)
    
    # Fix standalone issues: "*text *" or "* text*" → "*text*"
    text = re.sub(r'\*\s+([^*]+?)\*', r'*\1*', text)
    text = re.sub(r'\*([^*]+?)\s+\*', r'*\1*', text)
    
    # Remove triple or more asterisks
    text = re.sub(r'\*{3,}', r'*', text)

    # Clean up any remaining double asterisks that weren't converted
    text = re.sub(r'\*\*', r'*', text)

    # Ensure a space after bolded tokens when immediately followed by text
    text = re.sub(r'(\*[^*\n]+?\*)([A-Za-z0-9_])', r'\1 \2', text)

    # Restore inline code
    for i, code in enumerate(inline_codes):
        text = text.replace(f"__INLINE_CODE_{i}__", code)
    
    # Restore code blocks
    for i, block in enumerate(code_blocks):
        text = text.replace(f"__CODE_BLOCK_{i}__", block)
    
    # Limit consecutive newlines to max 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()


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
    summary_text = _markdown_to_slack(analysis.summary)
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": _truncate_text(f"*📊 Summary*\n{summary_text}")
        }
    })
    
    # Likely Cause section - convert markdown for Slack
    cause_text = _markdown_to_slack(analysis.likely_cause)
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": _truncate_text(f"*🎯 Likely Cause*\n{cause_text}")
        }
    })
    
    # Recommended Actions
    if analysis.recommended_steps:
        steps_text = "\n".join([f"• {step}" for step in analysis.recommended_steps[:5]])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate_text(f"*🔧 Recommended Actions*\n{steps_text}")
            }
        })
    
    # Triage info if available and enabled
    triage_team = getattr(analysis, "triage_team", None)
    triage_reason = getattr(analysis, "triage_reason", None)
    if settings.show_triage_in_slack and triage_team:
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
        summary_text = _markdown_to_slack(analysis.summary)
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🎯 Analysis*\n{summary_text}"
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
                "text": f"{severity_emoji} Investigation Results: {target}"[:150],  # Header limit
                "emoji": True
            }
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate_text(f"*📊 Summary*\n{analysis.summary}")
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate_text(f"*🎯 Likely Cause*\n{analysis.likely_cause}")
            }
        },
    ]
    
    if analysis.recommended_steps:
        steps_text = "\n".join([f"• {step}" for step in analysis.recommended_steps])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate_text(f"*🔧 Recommended Actions*\n{steps_text}")
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


def build_conversational_response(response_text: str) -> List[Dict[str, Any]]:
    """
    Build a conversational response for thread replies.
    
    This is a simpler, more chat-like format compared to the formal
    investigation result template. It converts Markdown to Slack mrkdwn
    and presents the response as a natural conversation.
    
    Args:
        response_text: The agent's response (can contain Markdown)
        
    Returns:
        Slack blocks in a conversational format
    """
    # Convert Markdown to Slack mrkdwn format
    formatted_text = _markdown_to_slack(response_text)
    
    # Truncate if needed
    formatted_text = _truncate_text(formatted_text)
    
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": formatted_text
            }
        }
    ]
    
    return blocks
