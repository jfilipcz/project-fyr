"""Slack notification helper."""

from __future__ import annotations

import time

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .models import Analysis


class SlackNotifier:
    def __init__(self, *, token: str | None, default_channel: str | None = None, mock_log_file: str | None = None):
        self._mock_mode = mock_log_file is not None
        self._mock_log_file = mock_log_file
        self._enabled = bool(token) or self._mock_mode
        self._default_channel = default_channel
        self._client = WebClient(token=token) if token and not self._mock_mode else None

    def send_analysis(
        self,
        *,
        channel: str | None,
        rollout_ref: str,
        analysis: Analysis,
        metadata: dict | None = None,
        max_attempts: int = 2,
    ) -> bool:
        if not self._enabled:
            return False

        target_channel = channel or self._default_channel
        if not target_channel:
            return False

        payload = self._build_blocks(rollout_ref, analysis, metadata or {})
        
        # Mock mode: write to log file instead of posting to Slack
        if self._mock_mode:
            import json
            from datetime import datetime
            
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "channel": target_channel,
                "rollout_ref": rollout_ref,
                "analysis": {
                    "summary": analysis.summary,
                    "likely_cause": analysis.likely_cause,
                    "recommended_steps": analysis.recommended_steps,
                    "severity": analysis.severity,
                    "triage_team": getattr(analysis, "triage_team", None),
                    "triage_reason": getattr(analysis, "triage_reason", None),
                },
                "metadata": metadata,
                "slack_blocks": payload,
            }
            
            with open(self._mock_log_file, "a") as f:
                f.write(json.dumps(log_entry, indent=2))
                f.write("\n" + "="*80 + "\n")
            
            return True
        
        # Real mode: post to Slack
        if not self._client:
            return False
            
        attempts = 0
        while attempts < max_attempts:
            attempts += 1
            try:
                self._client.chat_postMessage(channel=target_channel, blocks=payload)
                return True
            except SlackApiError as exc:
                print(f"failed to post slack message (attempt {attempts}/{max_attempts}): {exc}")
                if attempts >= max_attempts:
                    break
                time.sleep(1)
        return False

    @staticmethod
    def _build_blocks(rollout_ref: str, analysis: Analysis, metadata: dict) -> list[dict]:
        metadata = metadata or {}
        pipeline_url = metadata.get("pipeline_url")
        team = metadata.get("team")
        triage_team = metadata.get("triage_team") or getattr(analysis, "triage_team", None)
        triage_reason = metadata.get("triage_reason") or getattr(analysis, "triage_reason", None)
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
        if triage_team:
            triage_text = f"*Triage:* {triage_team}"
            if triage_reason:
                triage_text += f" — {triage_reason}"
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": triage_text}})
        if annotations := metadata.get("namespace_annotations"):
            formatted = ", ".join(f"{k}={v}" for k, v in annotations.items())
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"*Namespace annotations:* {formatted}"}]})
        return blocks
