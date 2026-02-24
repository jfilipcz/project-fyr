# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Agent implementation for Project Fyr."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from prometheus_client import Histogram, Counter

from .models import Analysis
from .tools import (
    k8s_check_rbac,
    k8s_describe,
    k8s_events,
    k8s_get_argocd_application,
    k8s_get_configmap,
    k8s_get_deployment_history,
    k8s_get_endpoints,
    k8s_get_init_containers,
    k8s_get_network,
    k8s_get_network_policies,
    k8s_get_nodes,
    k8s_get_replicasets,
    k8s_get_resources,
    k8s_get_secret_structure,
    k8s_get_storage,
    k8s_list_helm_releases,
    k8s_logs,
    k8s_query_prometheus,
    get_namespace_details,
    get_namespace_resource_quotas,
    get_namespace_pods_summary,
    get_namespace_events,
    # Phase 2 - Scaling & Resource Issues
    k8s_get_hpa,
    k8s_get_pod_disruption_budget,
    k8s_get_limit_ranges,
    # Phase 3 - Advanced Diagnostics
    k8s_get_jobs,
    k8s_get_priority_classes,
    k8s_get_cronjobs,
    k8s_check_image_pull_status,
    k8s_get_service_mesh_status,
    k8s_get_resource_quotas_usage,
    k8s_check_service_connectivity,
)

logger = logging.getLogger(__name__)

# Prometheus metrics
AGENT_ITERATIONS = Histogram(
    'project_fyr_agent_iterations',
    'Number of LLM iterations per investigation',
    buckets=[1, 2, 3, 5, 10, 20, 50, 100, 200, 500, 1000]
)

AGENT_INVESTIGATIONS = Counter(
    'project_fyr_agent_investigations_total',
    'Total number of investigations performed',
    ['status']  # success, error, mock, disabled
)

AGENT_TOOL_CALLS = Counter(
    'project_fyr_agent_tool_calls_total',
    'Total number of tool calls made by the agent',
    ['tool_name']  # k8s_get_resources, k8s_logs, k8s_events, etc.
)

AGENT_SYSTEM_PROMPT = """You are an expert Kubernetes SRE. Your task is to diagnose why a deployment is failing.
You have access to READ-ONLY tools to inspect the cluster. You CANNOT and MUST NOT make any changes to the cluster.

IMPORTANT NOTES:
- Pods in 'Succeeded' state from Jobs/CronJobs are NOT failures - they completed successfully
- Only investigate actual problems, not completed workloads

INVESTIGATION PROCESS:
1. Start by listing the pods for the deployment to see their status.
2. Check events in the namespace for any errors related to the deployment or pods.
3. If pods are crashing (CrashLoopBackOff), inspect their logs. Use `previous=True` if they have restarted recently.
4. If pods are pending:
   - Describe them to check for scheduling issues (resources, affinity, taints).
   - Use `k8s_get_nodes` to check node status and capacity.
   - Use `k8s_get_storage` if there are PVC/Volume mounting issues.
5. Check for missing dependencies or configuration:
   - Use `k8s_get_network` to verify Services and Ingresses.
   - Use `k8s_get_endpoints` to verify Service targeting.
   - Use `k8s_get_configmap` or `k8s_get_secret_structure` if logs indicate configuration or credential errors.
6. If connectivity is an issue, use `k8s_get_network_policies` to check for traffic blocking.
7. If permission errors are found, use `k8s_check_rbac` to verify ServiceAccount permissions.
8. If the deployment is managed by ArgoCD or Helm, check the application status or release status for sync errors or failed hooks.
9. Use Prometheus metrics (if available) to check for:
   - Frequent pod restarts that might indicate instability
   - OOMKills suggesting memory limits are too low
   - CPU throttling indicating resource constraints
   - High memory usage approaching limits
   - Network errors that might cause connectivity issues

COMMON FAILURE PATTERNS TO INVESTIGATE:
- **ImagePullBackOff**: Check image name typos, registry authentication (pull secrets), image tag existence
- **CrashLoopBackOff**: Check container logs (current AND previous), liveness probe configuration, startup time
- **OOMKilled**: Compare memory limits vs actual usage, check for memory leaks in application
- **Pending (Unschedulable)**: Check node capacity, resource requests, taints/tolerations, affinity rules
- **Init containers failing**: Use `k8s_get_init_containers` - these often block pod startup silently
- **CreateContainerConfigError**: Check ConfigMap/Secret existence and keys, volume mount configurations
- **Readiness probe failures**: Examine probe configuration, check if service dependencies are available

QUALITY CHECKLIST - Verify BEFORE providing your final analysis:
☐ Pod status examined (current state, restarts, age)
☐ Recent events reviewed (errors, warnings, scheduling issues)
☐ Container logs inspected (both current and previous if restarted)
☐ Init containers checked (if pods are stuck in Init state)
☐ Resource constraints verified (limits, quotas, node capacity)
☐ Dependencies validated (ConfigMaps, Secrets, Services, PVCs)
☐ Deployment/ReplicaSet history reviewed (if rollout-related failure)
☐ Evidence cited from tool outputs (don't speculate without data)

EVIDENCE-BASED ANALYSIS REQUIREMENTS:
- Base conclusions ONLY on tool output data you've gathered
- Quote specific error messages from logs/events when identifying root cause
- If you see an error pattern, cite the exact error text
- If you cannot find evidence for something, say "Unable to determine" rather than guessing
- When recommending remediation, explain WHY based on what you observed

Your final answer must be a structured analysis containing:
- **Summary**: Brief description of what's failing
- **Root Cause**: The underlying issue causing the failure (cite evidence)
- **Supporting Evidence**: Key observations from your investigation (log excerpts, event messages)
- **Remediation Steps**: Concrete actions to resolve the issue
- **Severity**: low, medium, high, or critical

FORMAT YOUR RESPONSE IN CLEAN MARKDOWN:
- Use headings (##, ###) to structure sections
- Use bullet points for lists
- Use **bold** for emphasis (ensure asterisks are directly adjacent to text, no spaces: **word** not ** word **)
- Use `code` formatting for resource names, commands, and technical terms
- Use code blocks (```) for multi-line logs or YAML excerpts
- Make your response easy to read and visually organized
- IMPORTANT: Do not mix single and double asterisks (use **bold** consistently, never *bold *)
- When writing numbered lists with bold items, format as: **1. Item** not *1. *Item**

Do not give up easily. Dig deep into logs and events. Be thorough and systematic.
"""

class ToolMetricsCallback(BaseCallbackHandler):
    """Callback handler to track tool usage metrics."""

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        """Track when a tool is called."""
        tool_name = serialized.get("name", "unknown")
        AGENT_TOOL_CALLS.labels(tool_name=tool_name).inc()
        logger.debug(f"Tool called: {tool_name}")

class InvestigatorAgent:
    def __init__(self, model_name: str = "gpt-4-turbo-preview", api_key: str | None = None,
                 api_base: str | None = None, api_version: str | None = None,
                 azure_deployment: str | None = None):
        self._model_name = model_name
        self._enabled = api_key is not None or model_name == "mock"

        if self._enabled and model_name != "mock":
            from .llm import create_llm
            from .config import Settings
            # Build a minimal Settings-like object for the factory
            _settings = Settings(
                langchain_model_name=model_name,
                openai_api_key=api_key,
                openai_api_base=api_base,
                openai_api_version=api_version,
                azure_deployment=azure_deployment,
            )
            llm = create_llm(_settings, temperature=1.0)

            tools = [
                k8s_get_resources,
                k8s_describe,
                k8s_logs,
                k8s_events,
                k8s_get_argocd_application,
                k8s_list_helm_releases,
                k8s_get_configmap,
                k8s_get_secret_structure,
                k8s_get_storage,
                k8s_get_network,
                k8s_get_nodes,
                k8s_check_rbac,
                k8s_get_network_policies,
                k8s_get_endpoints,
                k8s_query_prometheus,
                # Phase 1 tools - Core troubleshooting
                k8s_get_init_containers,
                k8s_get_replicasets,
                k8s_get_deployment_history,
                # Phase 2 tools - Scaling & Resource Issues
                k8s_get_hpa,
                k8s_get_pod_disruption_budget,
                k8s_get_limit_ranges,
                # Phase 3 tools - Advanced Diagnostics
                k8s_get_jobs,
                k8s_get_priority_classes,
                k8s_get_cronjobs,
                k8s_check_image_pull_status,
                k8s_get_service_mesh_status,
                k8s_get_resource_quotas_usage,
                k8s_check_service_connectivity,
                # Namespace investigation tools
                get_namespace_details,
                get_namespace_resource_quotas,
                get_namespace_pods_summary,
                get_namespace_events,
            ]

            # Use the new create_agent API with recursion limit
            self._agent = create_agent(
                model=llm,
                tools=tools,
                system_prompt=AGENT_SYSTEM_PROMPT,
                debug=True
            ).with_config({"recursion_limit": 1000})
        else:
            self._agent = None

    def investigate(
        self,
        deployment: str,
        namespace: str,
        alert_context: dict[str, Any] | None = None,
        question: str | None = None,
        is_initial_slack_investigation: bool = False,
        trigger_context: dict[str, Any] | None = None,
    ) -> Analysis:
        if self._model_name == "mock":
            AGENT_INVESTIGATIONS.labels(status='mock').inc()
            return Analysis(
                summary=f"[MOCK AGENT] Investigated {deployment}",
                likely_cause="Mock agent active.",
                recommended_steps=["Enable OpenAI API key for real investigation."],
                severity="low",
            )

        if not self._enabled or self._agent is None:
            AGENT_INVESTIGATIONS.labels(status='disabled').inc()
            return Analysis(
                summary="Agent disabled",
                likely_cause="Missing API key",
                recommended_steps=["Provide OPENAI_API_KEY"],
                severity="low",
            )

        try:
            # Use the new agent API - it expects messages format
            user_message = f"Investigate the deployment '{deployment}' in namespace '{namespace}'."

            if question:
                # Conversational mode for follow-up questions in Slack threads
                user_message = (
                    f"Context: You are helping investigate the deployment '{deployment}' in namespace '{namespace}'.\n\n"
                    f"The user asked: {question}\n\n"
                    "IMPORTANT INSTRUCTIONS FOR YOUR RESPONSE:\n"
                    "1. Answer their question directly and conversationally - like a helpful colleague in a chat\n"
                    "2. DO NOT use formal headers like '## Summary' or '## Likely Cause'\n"
                    "3. DO NOT provide a full structured analysis unless they ask for one\n"
                    "4. Keep your response focused on their specific question\n"
                    "5. Use Slack-compatible formatting: *bold* for emphasis, `code` for technical terms, bullet points (•) for lists\n"
                    "6. Be concise but helpful - this is a conversation, not a report\n"
                    "7. If you need to investigate using tools, do so, but present findings naturally\n"
                    "8. End with a brief offer to help with anything else if appropriate"
                )
            elif is_initial_slack_investigation:
                user_message += (
                    "\n\nIMPORTANT: This investigation is triggered from Slack. "
                    "At the end of your analysis, invite the user to ask follow-up questions by replying in the thread. "
                    "Don't say 'Just tell me' or 'Continue investigation' - instead, say something like "
                    "'Feel free to ask me any questions by replying in this thread!' or "
                    "'If you have more questions, just ask here in the thread!'"
                )

            if alert_context:
                user_message += f"\n\nCONTEXT: The investigation was triggered by the following alerts:\n{alert_context.get('summary', '')}\n"
                alerts = alert_context.get("alerts", [])
                if alerts:
                    user_message += "Active Alerts:\n"
                    for a in alerts:
                        user_message += f"- {a.get('name')} ({a.get('severity')}): {a.get('description')}\n"
                user_message += "\nPlease prioritize investigating the root cause of these alerts."

            if trigger_context:
                user_message += "\n\nTRIGGER CONTEXT (what the watcher observed):\n"
                if trigger_context.get("trigger_reason"):
                    user_message += f"- Trigger reason: {trigger_context['trigger_reason']}\n"
                if trigger_context.get("observed_failures"):
                    user_message += "- Initial failure observations:\n"
                    for obs in trigger_context["observed_failures"]:
                        user_message += f"  • {obs}\n"
                if trigger_context.get("transient_failures_detected"):
                    user_message += "- Transient failures were detected before this investigation.\n"
                    user_message += "  These issues may have self-healed. Please verify current state and note any recovery.\n"
                if trigger_context.get("time_to_failure_seconds"):
                    user_message += f"- Time from rollout start to failure: {trigger_context['time_to_failure_seconds']} seconds\n"
                if trigger_context.get("failure_type"):
                    user_message += f"- Failure type: {trigger_context['failure_type']}\n"

            # Invoke the agent with the new format
            result = self._agent.invoke(
                {"messages": [{"role": "user", "content": user_message}]},
                config={"callbacks": [ToolMetricsCallback()]}
            )

            # Extract the final message content and count iterations
            messages = result.get("messages", [])

            # Count iterations: number of AI messages (excluding the initial user message)
            iteration_count = sum(1 for msg in messages if hasattr(msg, 'type') and msg.type == 'ai')
            AGENT_ITERATIONS.observe(iteration_count)
            logger.info(f"Investigation completed in {iteration_count} iterations")
            if messages:
                # Get the last AI message
                last_message = messages[-1]
                output_text = messages[-1].content if hasattr(last_message, 'content') else str(last_message)
            else:
                output_text = "No response from agent"

            AGENT_INVESTIGATIONS.labels(status='success').inc()
            summary = f"Follow-up for {deployment}" if question else f"Agent Investigation for {deployment}"
            return Analysis(
                summary=summary,
                likely_cause=output_text,
                recommended_steps=["See detailed analysis above."],
                severity="medium"
            )

        except Exception as e:
            logger.error(f"Agent investigation failed: {e}", exc_info=True)
            AGENT_INVESTIGATIONS.labels(status='error').inc()
            return Analysis(
                summary=f"Agent failed to investigate {deployment}",
                likely_cause=f"Internal error: {str(e)}",
                recommended_steps=["Check logs"],
                severity="high"
            )
