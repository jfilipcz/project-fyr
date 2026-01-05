"""Issue aggregator using LangChain to cluster and summarize failures."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from .db import Rollout, AnalysisRecord

logger = logging.getLogger(__name__)


class AggregatedIssue(BaseModel):
    cause: str
    count: int
    description: str
    affected_namespaces: list[str]


class AggregationResult(BaseModel):
    top_issues: list[AggregatedIssue]
    summary: str


class IssueAggregator:
    def __init__(
        self, 
        model_name: str = "gpt-4-turbo-preview",
        api_key: str | None = None,
        api_base: str | None = None,
        api_version: str | None = None,
        azure_deployment: str | None = None,
    ):
        self.llm = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=api_base,
            api_version=api_version,
            azure_deployment=azure_deployment,
            temperature=0,
        ).with_structured_output(AggregationResult)

        self.prompt = ChatPromptTemplate.from_messages([
            ("system", """You are an expert SRE / DevOps engineer analyzing a list of recent deployment failures.
            
            Your goal is to:
            1. Group similar failures together based on their root cause.
            2. Count how many times each type of failure occurred.
            3. Provide a concise technical description of the pattern.
            4. List the unique namespaces affected by each pattern.
            5. Provide a high-level summary of the overall system health based on these failures.

            Ignore transient or one-off errors if they are not significant, unless there are very few errors in total.
            Focus on recurring patterns like "OOMKills", "Missing ConfigMaps", "Image Pull Errors", etc.
            """),
            ("user", "Here are the recent failures:\n\n{failures_text}")
        ])

    def aggregate_issues(self, failures: list[tuple[Rollout, AnalysisRecord]]) -> dict[str, Any]:
        """Aggregate a list of failures into patterns."""
        if not failures:
            return {
                "top_issues": [],
                "summary": "No recent failures detected."
            }

        # Format input for the LLM
        failures_text = ""
        for i, (rollout, analysis) in enumerate(failures, 1):
            failures_text += f"Failure #{i}:\n"
            failures_text += f"Namespace: {rollout.namespace}\n"
            failures_text += f"Deployment: {rollout.deployment}\n"
            if analysis:
                failures_text += f"Summary: {analysis.analysis.get('summary', 'N/A')}\n"
                failures_text += f"Likely Cause: {analysis.analysis.get('likely_cause', 'N/A')}\n"
            else:
                failures_text += "Analysis: Pending or failed\n"
            failures_text += "---\n"

        try:
            chain = self.prompt | self.llm
            result = chain.invoke({"failures_text": failures_text})
            return result.model_dump()
        except Exception as e:
            logger.error(f"Failed to aggregate issues: {e}")
            return {
                "top_issues": [],
                "summary": f"Failed to generate insights: {str(e)}"
            }
