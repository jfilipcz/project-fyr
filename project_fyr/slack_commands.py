"""Slash command handlers for Fyr Slack integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from slack_bolt import App

from .config import settings
from .db import init_db, RolloutRepo
from .slack_blocks import (
    build_help_response,
    build_status_response,
    build_namespace_response,
    build_investigation_started_response,
    build_investigation_result_response,
    build_error_response,
)

logger = logging.getLogger(__name__)


def register_commands(app: App) -> None:
    """Register all slash command handlers."""
    
    @app.command("/fyr")
    def handle_fyr_command(ack, command, respond, client):
        """Main /fyr command router."""
        ack()  # Acknowledge immediately (3s timeout)
        
        text = command.get("text", "").strip()
        user_id = command.get("user_id")
        channel_id = command.get("channel_id")
        
        logger.info(f"Received /fyr command: '{text}' from user {user_id} in channel {channel_id}")
        
        # Parse subcommand
        parts = text.split()
        subcommand = parts[0].lower() if parts else "help"
        args = parts[1:] if len(parts) > 1 else []
        
        try:
            if subcommand == "help" or subcommand == "":
                handle_help(respond)
            elif subcommand == "status":
                handle_status(respond)
            elif subcommand == "namespace" or subcommand == "ns":
                handle_namespace(respond, args)
            elif subcommand == "investigate":
                handle_investigate(respond, client, channel_id, args)
            elif subcommand == "recent":
                handle_recent(respond)
            else:
                respond(
                    blocks=build_error_response(
                        f"Unknown command: `{subcommand}`. Use `/fyr help` to see available commands."
                    )
                )
        except Exception as e:
            logger.exception(f"Error handling /fyr {subcommand}: {e}")
            respond(blocks=build_error_response(f"An error occurred: {str(e)}"))


def handle_help(respond) -> None:
    """Handle /fyr help command."""
    respond(blocks=build_help_response())


def handle_status(respond) -> None:
    """Handle /fyr status command."""
    try:
        engine = init_db(settings.database_url)
        repo = RolloutRepo(engine)
        
        stats = repo.get_stats(hours=24, exclude_system=True)
        
        # Get recent failures with details
        recent_failures = []
        failures = repo.list_by_status("failed", limit=5, exclude_system=True)
        
        for f in failures:
            time_ago = _format_time_ago(f.started_at) if f.started_at else "unknown"
            cause = "Unknown"
            
            # Try to get cause from analysis
            if f.analysis_id:
                analysis_record = repo.get_analysis(f.analysis_id)
                if analysis_record and analysis_record.analysis:
                    cause = analysis_record.analysis.get("likely_cause", "Unknown")[:50]
                    if len(analysis_record.analysis.get("likely_cause", "")) > 50:
                        cause += "..."
            
            recent_failures.append({
                "deployment": f.deployment,
                "namespace": f.namespace,
                "time_ago": time_ago,
                "cause": cause,
            })
        
        respond(
            blocks=build_status_response(
                cluster=settings.k8s_cluster_name,
                successful=stats.get("completed", 0),
                failed=stats.get("failed", 0),
                in_progress=stats.get("in_progress", 0),
                recent_failures=recent_failures,
            )
        )
    except Exception as e:
        logger.exception(f"Error in handle_status: {e}")
        respond(blocks=build_error_response(f"Failed to get cluster status: {str(e)}"))


def handle_namespace(respond, args: list) -> None:
    """Handle /fyr namespace <ns> command."""
    if not args:
        respond(blocks=build_error_response("Please specify a namespace: `/fyr namespace <namespace-name>`"))
        return
    
    namespace = args[0]
    
    try:
        from kubernetes import client, config as k8s_config
        
        # Load k8s config
        try:
            k8s_config.load_incluster_config()
        except:
            k8s_config.load_kube_config()
        
        core_v1 = client.CoreV1Api()
        apps_v1 = client.AppsV1Api()
        
        # Check namespace exists
        try:
            ns = core_v1.read_namespace(namespace)
            status = ns.status.phase if ns.status else "Unknown"
        except client.ApiException as e:
            if e.status == 404:
                respond(blocks=build_error_response(f"Namespace `{namespace}` not found"))
                return
            raise
        
        # Get deployments
        deployments = apps_v1.list_namespaced_deployment(namespace).items
        deployments_total = len(deployments)
        deployments_healthy = 0
        deployments_degraded = 0
        issues = []
        
        for d in deployments:
            ready = d.status.ready_replicas or 0
            desired = d.spec.replicas or 0
            
            if ready >= desired and desired > 0:
                deployments_healthy += 1
            else:
                deployments_degraded += 1
                issues.append(f"{d.metadata.name}: {ready}/{desired} replicas ready")
        
        # Get pods for restart info
        pods = core_v1.list_namespaced_pod(namespace).items
        total_restarts = 0
        for pod in pods:
            for cs in (pod.status.container_statuses or []):
                total_restarts += cs.restart_count
        
        if total_restarts > 10:
            issues.append(f"High restart rate: {total_restarts} restarts across pods")
        
        # Get resource quotas
        quotas = {}
        quota_list = core_v1.list_namespaced_resource_quota(namespace).items
        for q in quota_list:
            if q.status.hard:
                for resource, limit in q.status.hard.items():
                    used = q.status.used.get(resource, "0") if q.status.used else "0"
                    # Parse values (simplified)
                    try:
                        used_val = _parse_resource_value(used)
                        limit_val = _parse_resource_value(limit)
                        quotas[resource] = {"used": used_val, "limit": limit_val}
                    except:
                        pass
        
        respond(
            blocks=build_namespace_response(
                namespace=namespace,
                status=status,
                deployments_total=deployments_total,
                deployments_healthy=deployments_healthy,
                deployments_degraded=deployments_degraded,
                issues=issues[:5],
                quotas=quotas if quotas else None,
            )
        )
    except Exception as e:
        logger.exception(f"Error in handle_namespace: {e}")
        respond(blocks=build_error_response(f"Failed to get namespace info: {str(e)}"))


def handle_investigate(respond, client, channel_id: str, args: list) -> None:
    """Handle /fyr investigate <ns> <deployment> command."""
    if len(args) < 1:
        respond(blocks=build_error_response(
            "Please specify namespace and optionally deployment:\n"
            "`/fyr investigate <namespace> [deployment]`"
        ))
        return
    
    namespace = args[0]
    deployment = args[1] if len(args) > 1 else None
    
    # Acknowledge with "investigating" message
    respond(blocks=build_investigation_started_response(namespace, deployment))
    
    try:
        if deployment:
            # Deployment-specific investigation
            from .agent import InvestigatorAgent
            
            agent = InvestigatorAgent(
                model_name=settings.langchain_model_name,
                api_key=settings.openai_api_key,
                api_base=settings.openai_api_base,
                api_version=settings.openai_api_version,
                azure_deployment=settings.azure_deployment
            )
            
            analysis = agent.investigate(deployment, namespace)
        else:
            # Namespace-level investigation
            from kubernetes import client as k8s_client, config as k8s_config
            from .namespace_analyzer import NamespaceAnalyzer
            
            try:
                k8s_config.load_incluster_config()
            except:
                k8s_config.load_kube_config()
            
            core_v1 = k8s_client.CoreV1Api()
            apps_v1 = k8s_client.AppsV1Api()
            
            analyzer = NamespaceAnalyzer(
                model_name=settings.langchain_model_name,
                api_key=settings.openai_api_key,
                api_base=settings.openai_api_base,
                api_version=settings.openai_api_version,
                azure_deployment=settings.azure_deployment,
            )
            
            result = analyzer.analyze_namespace(
                namespace=namespace,
                core_v1=core_v1,
                apps_v1=apps_v1,
            )
            
            # Convert dict result to Analysis-like object
            from .models import Analysis
            analysis = Analysis(
                summary=result.get("summary", ""),
                likely_cause=result.get("likely_cause", ""),
                recommended_steps=result.get("recommended_steps", []),
                severity=result.get("severity", "medium"),
            )
        
        # Post results
        client.chat_postMessage(
            channel=channel_id,
            blocks=build_investigation_result_response(
                namespace=namespace,
                deployment=deployment,
                analysis=analysis,
            )
        )
        
    except Exception as e:
        logger.exception(f"Error in handle_investigate: {e}")
        client.chat_postMessage(
            channel=channel_id,
            blocks=build_error_response(f"Investigation failed: {str(e)}")
        )


def handle_recent(respond) -> None:
    """Handle /fyr recent command - show recent failures."""
    try:
        engine = init_db(settings.database_url)
        repo = RolloutRepo(engine)
        
        failures = repo.list_by_status("failed", limit=10, exclude_system=True)
        
        if not failures:
            respond(text="✨ No recent failures in the last 24 hours!")
            return
        
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "📋 Recent Failures (Last 24h)",
                    "emoji": True
                }
            },
            {"type": "divider"}
        ]
        
        for f in failures:
            time_ago = _format_time_ago(f.started_at) if f.started_at else "unknown"
            
            cause = "Unknown"
            if f.analysis_id:
                analysis_record = repo.get_analysis(f.analysis_id)
                if analysis_record and analysis_record.analysis:
                    cause = analysis_record.analysis.get("likely_cause", "Unknown")[:80]
            
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{f.deployment}* ({f.namespace})\n{time_ago} • _{cause}_"
                },
                "accessory": {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "View",
                        "emoji": True
                    },
                    "url": f"{settings.dashboard_base_url or 'http://localhost:8000'}/rollout/{f.id}",
                    "action_id": f"view_rollout_{f.id}"
                }
            })
        
        respond(blocks=blocks)
        
    except Exception as e:
        logger.exception(f"Error in handle_recent: {e}")
        respond(blocks=build_error_response(f"Failed to get recent failures: {str(e)}"))


def _format_time_ago(dt: datetime) -> str:
    """Format datetime as relative time string."""
    if not dt:
        return "unknown"
    
    now = datetime.utcnow()
    if dt.tzinfo:
        dt = dt.replace(tzinfo=None)
    
    delta = now - dt
    
    if delta.days > 0:
        return f"{delta.days}d ago"
    elif delta.seconds >= 3600:
        hours = delta.seconds // 3600
        return f"{hours}h ago"
    elif delta.seconds >= 60:
        minutes = delta.seconds // 60
        return f"{minutes}m ago"
    else:
        return "just now"


def _parse_resource_value(value: str) -> float:
    """Parse Kubernetes resource value to number."""
    value = str(value).strip()
    
    multipliers = {
        'Ki': 1024,
        'Mi': 1024**2,
        'Gi': 1024**3,
        'Ti': 1024**4,
        'k': 1000,
        'm': 1000**2,
        'G': 1000**3,
        'T': 1000**4,
    }
    
    for suffix, mult in multipliers.items():
        if value.endswith(suffix):
            return float(value[:-len(suffix)]) * mult
    
    # Handle millicores (e.g., "500m" for CPU)
    if value.endswith('m'):
        return float(value[:-1]) / 1000
    
    return float(value)
