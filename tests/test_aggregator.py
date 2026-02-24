"""Tests for project_fyr.aggregator — LLM-powered failure aggregation."""

from unittest.mock import MagicMock, patch
from project_fyr.aggregator import IssueAggregator, AggregationResult, AggregatedIssue


def test_aggregate_empty_list():
    """Aggregating no failures returns an empty result without calling the LLM."""
    with patch("project_fyr.llm.create_llm") as mock_create:
        mock_llm = MagicMock()
        mock_create.return_value = mock_llm
        mock_llm.with_structured_output.return_value = mock_llm

        agg = IssueAggregator(model_name="test", api_key="k")
        result = agg.aggregate_issues([])

    assert result["top_issues"] == []
    assert "No recent failures" in result["summary"]


def test_aggregate_issues_calls_llm():
    """aggregate_issues formats failures and invokes the LLM chain."""
    with patch("project_fyr.llm.create_llm") as mock_create:
        mock_llm = MagicMock()
        mock_create.return_value = mock_llm

        expected = AggregationResult(
            top_issues=[
                AggregatedIssue(
                    cause="OOMKill",
                    count=3,
                    description="Pods killed due to memory limits",
                    severity="high",
                    affected_namespaces=["prod"],
                )
            ],
            summary="3 OOM incidents",
        )

        # The structured output chain invoke returns our expected result
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        # prompt | llm produces a chain; we mock the __or__ on the prompt
        mock_chain = MagicMock()
        mock_chain.invoke.return_value = expected

        agg = IssueAggregator(model_name="test", api_key="k")
        # Replace the prompt's pipe operator result
        agg.prompt = MagicMock()
        agg.prompt.__or__ = MagicMock(return_value=mock_chain)

        rollout = MagicMock()
        rollout.namespace = "prod"
        rollout.deployment = "worker"
        analysis = MagicMock()
        analysis.analysis = {"summary": "OOMKill", "likely_cause": "Memory limit too low"}

        result = agg.aggregate_issues([(rollout, analysis)])

    assert result["top_issues"][0]["cause"] == "OOMKill"
    assert result["summary"] == "3 OOM incidents"


def test_aggregation_result_model():
    """Pydantic model serialises correctly."""
    result = AggregationResult(
        top_issues=[
            AggregatedIssue(
                cause="DNS failure",
                count=2,
                description="Pods cannot resolve external DNS",
                severity="medium",
                affected_namespaces=["staging", "dev"],
            )
        ],
        summary="DNS issues across environments",
    )
    d = result.model_dump()
    assert len(d["top_issues"]) == 1
    assert d["top_issues"][0]["severity"] == "medium"
