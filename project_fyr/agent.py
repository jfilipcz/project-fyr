"""Agent implementation for Project Fyr."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from prometheus_client import Histogram, Counter

from .models import Analysis
from .tools import (
    k8s_check_rbac,
    k8s_describe,
    k8s_events,
    k8s_get_argocd_application,
    k8s_get_configmap,
    k8s_get_endpoints,
    k8s_get_network,
    k8s_get_network_policies,
    k8s_get_nodes,
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

AGENT_SYSTEM_PROMPT = """You are an expert Kubernetes SRE. Your task is to diagnose why a deployment is failing.
You have access to tools to inspect the cluster.

Follow this investigation process:
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

Your final answer must be a structured analysis containing:
- A summary of the issue.
- The likely root cause.
- Recommended remediation steps.
- A severity level (low, medium, high, critical).

Do not give up easily. Dig deep into logs and events.
"""

class InvestigatorAgent:
    def __init__(self, model_name: str = "gpt-4-turbo-preview", api_key: str | None = None, 
                 api_base: str | None = None, api_version: str | None = None,
                 azure_deployment: str | None = None):
        self._model_name = model_name
        self._enabled = api_key is not None or model_name == "mock"
        
        if self._enabled and model_name != "mock":
            # Configure for Azure OpenAI if api_base is provided
            if api_base:
                # For Azure OpenAI, use AzureChatOpenAI instead
                from langchain_openai import AzureChatOpenAI
                llm = AzureChatOpenAI(
                    model=azure_deployment or model_name,
                    azure_deployment=azure_deployment or model_name,
                    temperature=1,
                    api_key=api_key,
                    azure_endpoint=api_base,
                    api_version=api_version,
                )
            else:
                llm = ChatOpenAI(model=model_name, temperature=0, api_key=api_key)
            
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

    def investigate(self, deployment: str, namespace: str, alert_context: dict[str, Any] | None = None) -> Analysis:
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
            
            if alert_context:
                user_message += f"\n\nCONTEXT: The investigation was triggered by the following alerts:\n{alert_context.get('summary', '')}\n"
                alerts = alert_context.get("alerts", [])
                if alerts:
                    user_message += "Active Alerts:\n"
                    for a in alerts:
                        user_message += f"- {a.get('name')} ({a.get('severity')}): {a.get('description')}\n"
                user_message += "\nPlease prioritize investigating the root cause of these alerts."
            
            # Invoke the agent with the new format
            result = self._agent.invoke(
                {"messages": [{"role": "user", "content": user_message}]}
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
            return Analysis(
                summary=f"Agent Investigation for {deployment}",
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
