"""Agent implementation for Project Fyr."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

from .models import Analysis
from .tools import (
    k8s_describe,
    k8s_events,
    k8s_get_argocd_application,
    k8s_get_resources,
    k8s_list_helm_releases,
    k8s_logs,
)

logger = logging.getLogger(__name__)

AGENT_SYSTEM_PROMPT = """You are an expert Kubernetes SRE. Your task is to diagnose why a deployment is failing.
You have access to tools to inspect the cluster.

Follow this investigation process:
1. Start by listing the pods for the deployment to see their status.
2. Check events in the namespace for any errors related to the deployment or pods.
3. If pods are crashing (CrashLoopBackOff), inspect their logs. Use `previous=True` if they have restarted recently.
4. If pods are pending, describe them to check for scheduling issues (resources, affinity, etc.).
5. Check for missing dependencies like Services or ConfigMaps if logs indicate connection or configuration errors.
6. If the deployment is managed by ArgoCD or Helm, check the application status or release status for sync errors or failed hooks.

Your final answer must be a structured analysis containing:
- A summary of the issue.
- The likely root cause.
- Recommended remediation steps.
- A severity level (low, medium, high, critical).

Do not give up easily. Dig deep into logs and events.
"""

class InvestigatorAgent:
    def __init__(self, model_name: str = "gpt-4-turbo-preview", api_key: str | None = None):
        self._model_name = model_name
        self._enabled = api_key is not None or model_name == "mock"
        
        if self._enabled and model_name != "mock":
            llm = ChatOpenAI(model=model_name, temperature=0, api_key=api_key)
            tools = [
                k8s_get_resources,
                k8s_describe,
                k8s_logs,
                k8s_events,
                k8s_get_argocd_application,
                k8s_list_helm_releases,
            ]
            
            prompt = ChatPromptTemplate.from_messages(
                [
                    ("system", AGENT_SYSTEM_PROMPT),
                    ("human", "Investigate the deployment '{deployment}' in namespace '{namespace}'."),
                    MessagesPlaceholder(variable_name="agent_scratchpad"),
                ]
            )
            
            agent = create_openai_tools_agent(llm, tools, prompt)
            self._agent_executor = AgentExecutor(
                agent=agent, 
                tools=tools, 
                verbose=True,
                handle_parsing_errors=True,
                max_iterations=15
            )
        else:
            self._agent_executor = None

    def investigate(self, deployment: str, namespace: str) -> Analysis:
        if self._model_name == "mock":
            return Analysis(
                summary=f"[MOCK AGENT] Investigated {deployment}",
                likely_cause="Mock agent active.",
                recommended_steps=["Enable OpenAI API key for real investigation."],
                severity="low",
            )

        if not self._enabled or self._agent_executor is None:
            return Analysis(
                summary="Agent disabled",
                likely_cause="Missing API key",
                recommended_steps=["Provide OPENAI_API_KEY"],
                severity="low",
            )

        try:
            # We need the agent to return a structured output. 
            # Since the agent returns a string "output", we might need to parse it or force the agent to use a final tool.
            # For simplicity in this iteration, we will ask the agent to output the final answer in a specific format 
            # and then parse it, or we can use a structured output parser on the final result.
            # However, `Analysis` is a Pydantic model. 
            # Let's try to wrap the invocation and parse the text, or better, use a structured output chain *after* the agent.
            # OR, we can instruct the agent to return JSON.
            
            # Let's refine the prompt to ask for JSON or use a Pydantic parser on the result.
            # For now, let's rely on the agent's text output and wrap it in Analysis, 
            # or we can try to parse it if it follows a format.
            
            # Actually, let's just take the text output and put it in 'summary' and 'likely_cause' for now,
            # as fully structured parsing from a free-form agent is tricky without a specific "FinalAnswer" tool.
            
            result = self._agent_executor.invoke(
                {"deployment": deployment, "namespace": namespace}
            )
            output_text = result["output"]
            
            # Heuristic parsing or just dumping the text.
            # Ideally we would use a structured output parser.
            # Let's assume the agent follows instructions well enough to provide a clear text we can put in summary.
            
            return Analysis(
                summary=f"Agent Investigation for {deployment}",
                likely_cause=output_text, # The agent's full explanation usually contains the cause
                recommended_steps=["See detailed analysis above."],
                severity="medium" # Default
            )
            
        except Exception as e:
            logger.error(f"Agent investigation failed: {e}")
            return Analysis(
                summary=f"Agent failed to investigate {deployment}",
                likely_cause=f"Internal error: {str(e)}",
                recommended_steps=["Check logs"],
                severity="high"
            )
