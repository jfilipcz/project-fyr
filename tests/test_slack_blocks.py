"""Tests for project_fyr.slack_blocks — Slack Block Kit message builders."""

import pytest
from project_fyr.slack_blocks import (
    _truncate_text,
    _get_severity_emoji,
    _markdown_to_slack,
    build_failure_notification,
)
from project_fyr.models import Analysis


# ─── _truncate_text ──────────────────────────────────────────────────────────

def test_truncate_short_text():
    assert _truncate_text("hello") == "hello"


def test_truncate_long_text():
    text = "x" * 5000
    result = _truncate_text(text)
    assert len(result) <= 2900
    assert "truncated" in result


# ─── _get_severity_emoji ─────────────────────────────────────────────────────

@pytest.mark.parametrize("severity,expected", [
    ("critical", "🚨"),
    ("high", "🔴"),
    ("medium", "🟡"),
    ("low", "🟢"),
    ("CRITICAL", "🚨"),
    ("unknown", "⚠️"),
])
def test_severity_emoji(severity, expected):
    assert _get_severity_emoji(severity) == expected


# ─── _markdown_to_slack ──────────────────────────────────────────────────────

def test_markdown_headers():
    assert _markdown_to_slack("## Title") == "*Title*"
    assert _markdown_to_slack("### Sub") == "*Sub*"


def test_markdown_bold():
    assert "*text*" in _markdown_to_slack("**text**")


def test_markdown_code_preserved():
    result = _markdown_to_slack("`foo`")
    assert "`foo`" in result


def test_markdown_empty():
    assert _markdown_to_slack("") == ""
    assert _markdown_to_slack(None) is None


# ─── build_failure_notification ──────────────────────────────────────────────

def test_build_failure_notification_basic():
    analysis = Analysis(
        summary="OOMKill on worker pod",
        likely_cause="Memory limit too low",
        recommended_steps=["Increase memory limit", "Check for leaks"],
        severity="high",
    )
    blocks = build_failure_notification(
        rollout_ref="cluster/ns/dep/1",
        analysis=analysis,
        rollout_id=42,
        namespace="production",
        deployment="worker",
    )
    assert isinstance(blocks, list)
    assert len(blocks) > 0

    # Serialise to check the blocks are valid dicts
    text = str(blocks)
    assert "OOMKill" in text
    assert "Memory limit" in text


def test_build_failure_notification_severity_emoji():
    analysis = Analysis(
        summary="Critical failure",
        likely_cause="Database down",
        recommended_steps=["Restart DB"],
        severity="critical",
    )
    blocks = build_failure_notification(
        rollout_ref="c/n/d/1",
        analysis=analysis,
    )
    text = str(blocks)
    assert "🚨" in text
