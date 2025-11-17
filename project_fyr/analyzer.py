"""LangChain driver that turns ReducedContext into an Analysis."""

from __future__ import annotations

from typing import Any

from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .models import Analysis, ReducedContext


SYSTEM_PROMPT = """You are a seasoned Kubernetes SRE who inspects deployment rollouts.
Summarize the likely root cause and remediation succinctly.
Format responses exactly following the provided schema."""

HUMAN_PROMPT = """Deployment: {deployment}
Namespace: {namespace}
Phase: {phase}
Summary: {summary}
Failing Pods: {failing_pods}
Events: {events}
Log clusters: {log_clusters}
"""


class Analyzer:
    def __init__(self, *, model_name: str, api_key: str | None = None):
        self._enabled = api_key is not None
        if self._enabled:
            self._model = ChatOpenAI(model=model_name, temperature=0, api_key=api_key)
            self._parser = PydanticOutputParser(pydantic_object=Analysis)
            prompt = ChatPromptTemplate.from_messages(
                [
                    ("system", SYSTEM_PROMPT),
                    ("human", HUMAN_PROMPT + "\nFormat: {format_instructions}"),
                ]
            )
            self._chain = prompt | self._model | self._parser
        else:
            self._chain = None

    def analyze(self, context: ReducedContext) -> Analysis:
        if not self._enabled or self._chain is None:
            fallback = Analysis(
                summary=f"Rollout {context.deployment} is {context.phase}",
                likely_cause="LLM disabled",
                recommended_steps=["Provide OPENAI API key to enable LangChain analysis."],
                severity="low" if context.phase == "STABLE" else "medium",
            )
            return fallback

        payload: dict[str, Any] = {
            "deployment": context.deployment,
            "namespace": context.namespace,
            "phase": context.phase,
            "summary": context.summary,
            "failing_pods": ", ".join(context.failing_pods) or "none",
            "events": "\n".join(
                [f"{e.reason}: {e.message_template} (x{e.count})" for e in context.events]
            )
            or "none",
            "log_clusters": "\n".join(
                [
                    f"{c.pod}/{c.container}: {c.template} (x{c.count}) -> {c.example}"
                    for c in context.log_clusters
                ]
            )
            or "none",
            "format_instructions": self._parser.get_format_instructions(),
        }
        return self._chain.invoke(payload)
