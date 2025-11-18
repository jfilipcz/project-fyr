"""Slack notification helper."""

from __future__ import annotations

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .models import Analysis


class SlackNotifier:
    def __init__(self, *, token: str | None, default_channel: str | None = None):
        self._enabled = bool(token and default_channel)
        self._default_channel = default_channel
        self._client = WebClient(token=token) if token else None

    def send_analysis(
        self,
        *,
        channel: str | None,
        rollout_ref: str,
        analysis: Analysis,
        metadata: dict | None = None,
    ) -> None:
        if not self._enabled or not self._client:
            return
        payload = self._build_blocks(rollout_ref, analysis, metadata or {})
        try:
            self._client.chat_postMessage(channel=channel or self._default_channel, blocks=payload)
        except SlackApiError as exc:
            print(f"failed to post slack message: {exc}")

    @staticmethod
    def _build_blocks(rollout_ref: str, analysis: Analysis, metadata: dict) -> list[dict]:
        metadata = metadata or {}
        pipeline_url = metadata.get("pipeline_url")
        team = metadata.get("team")
        fields = [
            {"type": "mrkdwn", "text": f"*Rollout:* {rollout_ref}"},
            {"type": "mrkdwn", "text": f"*Severity:* {analysis.severity}"},
        ]
        if team:
            fields.append({"type": "mrkdwn", "text": f"*Team:* {team}"})
        if url := pipeline_url:
            fields.append({"type": "mrkdwn", "text": f"*Pipeline:* <{url}|view>"})
        blocks = [
            {"type": "section", "fields": fields},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Summary:* {analysis.summary}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Likely cause:* {analysis.likely_cause}"}},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join(f"• {step}" for step in analysis.recommended_steps),
                },
            },
        ]
        if analysis.details:
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": analysis.details}]})
        if annotations := metadata.get("namespace_annotations"):
            formatted = ", ".join(f"{k}={v}" for k, v in annotations.items())
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"*Namespace annotations:* {formatted}"}]})
        return blocks
