# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""On-demand namespace analysis tool."""

from __future__ import annotations

import logging
from datetime import datetime
from project_fyr import utcnow
from typing import Optional

from kubernetes import client

from .agent import InvestigatorAgent

logger = logging.getLogger(__name__)


class NamespaceAnalyzer:
    """
    Analyzes a namespace on-demand to identify issues with deployments and pods.

    This class provides comprehensive namespace analysis including:
    - Deployment health status
    - Pod issues (CrashLoopBackOff, ImagePullBackOff, etc.)
    - Recent events
    - Resource constraints
    - AI-powered root cause analysis
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        api_version: Optional[str] = None,
        azure_deployment: Optional[str] = None,
    ):
        """
        Initialize the NamespaceAnalyzer.

        Args:
            model_name: LangChain model name (e.g., "gpt-4o-mini")
            api_key: OpenAI API key
            api_base: OpenAI API base URL (for Azure OpenAI)
            api_version: OpenAI API version (for Azure OpenAI)
            azure_deployment: Azure OpenAI deployment name
        """
        self.investigator = InvestigatorAgent(
            model_name=model_name,
            api_key=api_key,
            api_base=api_base,
            api_version=api_version,
            azure_deployment=azure_deployment,
        )

    def analyze_namespace(
        self,
        namespace: str,
        core_v1: client.CoreV1Api,
        apps_v1: client.AppsV1Api,
    ) -> dict:
        """
        Perform comprehensive analysis of a namespace.

        Args:
            namespace: Kubernetes namespace name
            core_v1: Kubernetes CoreV1Api client
            apps_v1: Kubernetes AppsV1Api client

        Returns:
            Dictionary containing:
            - summary: High-level summary of issues
            - deployments: List of deployments with health status
            - pods: List of pods with issues
            - events: Recent events in the namespace
            - analysis: AI-generated analysis and recommendations
            - timestamp: Analysis timestamp
        """
        logger.info(f"Starting namespace analysis for: {namespace}")

        try:
            # Gather deployment information
            deployments = self._get_deployment_status(namespace, apps_v1, core_v1)

            # Gather pod information
            pods = self._get_pod_status(namespace, core_v1)

            # Get recent events
            events = self._get_recent_events(namespace, core_v1)

            # Count issues
            failing_deployments = sum(1 for d in deployments if not d["healthy"])
            unhealthy_pods = sum(1 for p in pods if not p["healthy"])

            # Prepare summary
            summary = {
                "namespace": namespace,
                "total_deployments": len(deployments),
                "failing_deployments": failing_deployments,
                "total_pods": len(pods),
                "unhealthy_pods": unhealthy_pods,
                "has_issues": failing_deployments > 0 or unhealthy_pods > 0,
            }

            # Generate AI analysis if there are issues
            ai_analysis = None
            if summary["has_issues"]:
                ai_analysis = self._generate_ai_analysis(
                    namespace=namespace,
                    deployments=deployments,
                    pods=pods,
                    events=events,
                )

            return {
                "summary": summary,
                "deployments": deployments,
                "pods": pods,
                "events": events[:20],  # Limit to 20 most recent events
                "analysis": ai_analysis,
                "timestamp": utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error analyzing namespace {namespace}: {e}", exc_info=True)
            return {
                "summary": {
                    "namespace": namespace,
                    "error": str(e),
                    "has_issues": True,
                },
                "deployments": [],
                "pods": [],
                "events": [],
                "analysis": f"Error during analysis: {str(e)}",
                "timestamp": utcnow().isoformat(),
            }

    def _get_deployment_status(
        self,
        namespace: str,
        apps_v1: client.AppsV1Api,
        core_v1: client.CoreV1Api,
    ) -> list[dict]:
        """Get status of all deployments in namespace."""
        deployments = []

        try:
            deployment_list = apps_v1.list_namespaced_deployment(namespace)

            for dep in deployment_list.items:
                ready_replicas = dep.status.ready_replicas or 0
                desired_replicas = dep.spec.replicas or 0

                is_healthy = ready_replicas == desired_replicas and desired_replicas > 0

                # Get conditions
                conditions = []
                if dep.status.conditions:
                    conditions = [
                        {
                            "type": c.type,
                            "status": c.status,
                            "reason": c.reason,
                            "message": c.message,
                        }
                        for c in dep.status.conditions
                    ]

                deployments.append({
                    "name": dep.metadata.name,
                    "ready_replicas": ready_replicas,
                    "desired_replicas": desired_replicas,
                    "healthy": is_healthy,
                    "conditions": conditions,
                })

        except Exception as e:
            logger.error(f"Error getting deployments for {namespace}: {e}")

        return deployments

    def _get_pod_status(self, namespace: str, core_v1: client.CoreV1Api) -> list[dict]:
        """Get status of all pods in namespace."""
        pods = []

        try:
            pod_list = core_v1.list_namespaced_pod(namespace)

            for pod in pod_list.items:
                phase = pod.status.phase

                # Check if pod is from a Job (completed Jobs should not be treated as unhealthy)
                owner_kind = None
                if pod.metadata.owner_references:
                    owner_kind = pod.metadata.owner_references[0].kind

                # Check for common issues
                # Succeeded pods from Jobs/CronJobs are healthy (completed successfully)
                is_completed_job = phase == "Succeeded" and owner_kind in ["Job", "CronJob"]
                is_healthy = phase == "Running" or is_completed_job
                issues = []

                if phase in ["Pending", "Failed", "Unknown"]:
                    is_healthy = False
                    issues.append(f"Pod in {phase} state")

                # Check container statuses (but skip if this is a completed job)
                if pod.status.container_statuses and not is_completed_job:
                    for cs in pod.status.container_statuses:
                        if not cs.ready:
                            is_healthy = False

                            if cs.state.waiting:
                                reason = cs.state.waiting.reason
                                issues.append(f"Container waiting: {reason}")

                            if cs.state.terminated:
                                reason = cs.state.terminated.reason
                                exit_code = cs.state.terminated.exit_code
                                issues.append(f"Container terminated: {reason} (exit {exit_code})")

                        # Check restart count
                        if cs.restart_count > 5:
                            issues.append(f"High restart count: {cs.restart_count}")

                pods.append({
                    "name": pod.metadata.name,
                    "phase": phase,
                    "healthy": is_healthy,
                    "issues": issues,
                    "restart_count": sum(
                        cs.restart_count for cs in (pod.status.container_statuses or [])
                    ),
                })

        except Exception as e:
            logger.error(f"Error getting pods for {namespace}: {e}")

        return pods

    def _get_recent_events(self, namespace: str, core_v1: client.CoreV1Api) -> list[dict]:
        """Get recent events from namespace."""
        events = []

        try:
            event_list = core_v1.list_namespaced_event(namespace)

            # Sort by timestamp (most recent first)
            sorted_events = sorted(
                event_list.items,
                key=lambda e: e.last_timestamp or e.event_time or datetime.min,
                reverse=True,
            )

            for event in sorted_events[:50]:  # Get last 50 events
                events.append({
                    "type": event.type,
                    "reason": event.reason,
                    "message": event.message,
                    "object": f"{event.involved_object.kind}/{event.involved_object.name}",
                    "count": event.count or 1,
                    "timestamp": (
                        event.last_timestamp.isoformat() if event.last_timestamp
                        else event.event_time.isoformat() if event.event_time
                        else None
                    ),
                })

        except Exception as e:
            logger.error(f"Error getting events for {namespace}: {e}")

        return events

    def _generate_ai_analysis(
        self,
        namespace: str,
        deployments: list[dict],
        pods: list[dict],
        events: list[dict],
    ) -> str:
        """Generate AI-powered analysis using the investigator agent with tool access."""
        try:
            # Build context summary highlighting key issues
            context_parts = [
                f"NAMESPACE ANALYSIS REQUEST for: {namespace}",
                "",
                "OVERVIEW:",
            ]

            failing_deployments = [d for d in deployments if not d["healthy"]]
            unhealthy_pods = [p for p in pods if not p["healthy"]]
            warning_events = [e for e in events[:20] if e["type"] in ["Warning", "Error"]]

            context_parts.append(f"- Total deployments: {len(deployments)} ({len(failing_deployments)} unhealthy)")
            context_parts.append(f"- Total pods: {len(pods)} ({len(unhealthy_pods)} unhealthy)")
            context_parts.append(f"- Recent warning/error events: {len(warning_events)}")
            context_parts.append("")

            if failing_deployments:
                context_parts.append("FAILING DEPLOYMENTS:")
                for dep in failing_deployments[:5]:  # Top 5
                    context_parts.append(
                        f"  • {dep['name']}: {dep['ready_replicas']}/{dep['desired_replicas']} replicas ready"
                    )
                context_parts.append("")

            if unhealthy_pods:
                context_parts.append("UNHEALTHY PODS (sample):")
                for pod in unhealthy_pods[:5]:  # Top 5
                    context_parts.append(f"  • {pod['name']} ({pod['phase']})")
                    if pod["issues"]:
                        context_parts.append(f"    Issues: {', '.join(pod['issues'][:2])}")
                context_parts.append("")

            if warning_events:
                context_parts.append("RECENT WARNINGS/ERRORS (sample):")
                for event in warning_events[:5]:  # Top 5
                    context_parts.append(
                        f"  • {event['reason']}: {event['message'][:80]}"
                    )
                context_parts.append("")

            context = "\n".join(context_parts)

            # Build the investigation prompt - this will trigger the agent's tool-calling capabilities
            investigation_request = (
                f"Perform a comprehensive investigation of namespace '{namespace}'.\n\n"
                f"{context}\n\n"
                f"INSTRUCTIONS:\n"
                f"1. Use available READ-ONLY k8s tools to investigate the issues\n"
                f"2. Check logs, events, and configurations for failing resources\n"
                f"3. Identify root causes and patterns across the namespace\n"
                f"4. Provide specific, actionable recommendations\n"
                f"5. Prioritize issues by severity and impact\n\n"
                f"IMPORTANT NOTES:\n"
                f"- Pods in 'Succeeded' state from Jobs/CronJobs are NOT unhealthy - they completed successfully\n"
                f"- Only report on actual problems, not completed workloads\n\n"
                f"IMPORTANT - PRESENTATION GUIDELINES:\n"
                f"- Write for non-SRE users (developers, product managers)\n"
                f"- Start with a CLEAR STATUS: 'HEALTHY', 'DEGRADED', or 'UNHEALTHY'\n"
                f"- If status is HEALTHY but there were past warnings/restarts, explain:\n"
                f"  * Current state: All pods running and ready NOW\n"
                f"  * Past issues: What warnings/restarts occurred (brief)\n"
                f"  * Assessment: Whether past issues are resolved or need attention\n"
                f"- Use simple language, avoid jargon where possible\n"
                f"- Use bullet points and clear sections\n"
                f"- Provide action items only if needed (don't suggest actions for resolved issues)\n\n"
                f"OUTPUT FORMAT - USE MARKDOWN:\n"
                f"- Use headings (##, ###) to organize your analysis\n"
                f"- Use bullet points (-) for lists\n"
                f"- Use **bold** for emphasis on important points\n"
                f"- Use `code formatting` for resource names, commands, and technical terms\n"
                f"- Use code blocks (```) for multi-line logs or YAML snippets\n"
                f"- Make your response visually organized and easy to scan\n\n"
                f"Focus on the most critical issues first. Use k8s_logs, k8s_events, "
                f"k8s_describe and other tools as needed to gather evidence."
            )

            # Use the agent's invoke method with proper message format for full tool access
            if hasattr(self.investigator, '_agent') and self.investigator._agent:
                from .agent import ToolMetricsCallback
                logger.info(f"Starting agentic investigation of namespace {namespace}")
                result = self.investigator._agent.invoke(
                    {"messages": [{"role": "user", "content": investigation_request}]},
                    config={"callbacks": [ToolMetricsCallback()]}
                )

                # Extract the final analysis from messages
                messages = result.get("messages", [])
                if messages:
                    last_message = messages[-1]
                    analysis_text = last_message.content if hasattr(last_message, 'content') else str(last_message)
                    iteration_count = sum(1 for msg in messages if hasattr(msg, 'type') and msg.type == 'ai')
                    logger.info(f"Namespace investigation completed in {iteration_count} iterations")
                    return analysis_text
                else:
                    logger.warning("No messages returned from agent")
                    return "No analysis generated - agent returned no messages"
            else:
                # Agent not available
                error_msg = f"Agent not available for namespace {namespace} - investigation cannot proceed"
                logger.error(error_msg)
                return "AI agent not initialized. Please check OpenAI API configuration."

        except Exception as e:
            logger.error(f"Error generating AI analysis for {namespace}: {e}", exc_info=True)
            return f"AI analysis failed: {str(e)}"
