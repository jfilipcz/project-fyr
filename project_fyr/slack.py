"""Slack notification helper."""

from __future__ import annotations

from dataclasses import dataclass
import time
import threading
from typing import Literal

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .models import Analysis

SlackTargetKind = Literal["dm", "channel"]
_CACHE_MISS = object()


@dataclass(frozen=True)
class SlackTarget:
    kind: SlackTargetKind
    value: str


@dataclass(frozen=True)
class SlackRoutingPlan:
    primary: list[SlackTarget]
    fallback: list[SlackTarget]


def build_routing_plan(
    *,
    requestor_email: str | None,
    owner_channel: str | None,
    namespace_channel: str | None,
    default_channel: str | None,
    enable_requestor_dm: bool,
    enable_owner_channel: bool,
) -> SlackRoutingPlan:
    """Build routing targets for Slack notifications.

    Rules:
    - If requestor DM is enabled and email is present, DM is primary.
    - Requestor overrides owner channel, but does not disable namespace channel.
    - Namespace channel is always additive when present.
    - If DM fails and namespace channel is absent, fallback to owner (if enabled), then default.
    - If no primary targets are available, use default channel.
    """
    requestor_email = _clean_value(requestor_email)
    owner_channel = _clean_value(owner_channel)
    namespace_channel = _clean_value(namespace_channel)
    default_channel = _clean_value(default_channel)

    primary: list[SlackTarget] = []
    fallback: list[SlackTarget] = []

    use_requestor_dm = bool(requestor_email and enable_requestor_dm)
    use_owner_channel = bool(owner_channel and enable_owner_channel)

    if use_requestor_dm:
        primary.append(SlackTarget("dm", requestor_email))
        if namespace_channel:
            primary.append(SlackTarget("channel", namespace_channel))
        if not namespace_channel and use_owner_channel:
            fallback.append(SlackTarget("channel", owner_channel))
    else:
        if use_owner_channel:
            primary.append(SlackTarget("channel", owner_channel))
        if namespace_channel:
            primary.append(SlackTarget("channel", namespace_channel))

    primary = _dedupe_targets(primary)

    if not primary and default_channel:
        primary = [SlackTarget("channel", default_channel)]

    if default_channel and not _has_channel(primary, default_channel):
        fallback.append(SlackTarget("channel", default_channel))

    fallback = _dedupe_targets(fallback)

    return SlackRoutingPlan(primary=primary, fallback=fallback)


def _clean_value(value: str | None) -> str | None:
    if not value:
        return None
    trimmed = value.strip()
    return trimmed or None


def _dedupe_targets(targets: list[SlackTarget]) -> list[SlackTarget]:
    seen: set[tuple[str, str]] = set()
    deduped: list[SlackTarget] = []
    for target in targets:
        key = (target.kind, target.value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    return deduped


def _has_channel(targets: list[SlackTarget], channel: str) -> bool:
    return any(t.kind == "channel" and t.value == channel for t in targets)


def _extract_requestor_email(metadata: dict | None) -> str | None:
    if not metadata:
        return None
    if email := metadata.get("requestor_email"):
        return email
    annotations = metadata.get("namespace_annotations") or metadata.get("metadata_json") or {}
    return annotations.get("example.com/requestor-email")


def _extract_owner_channel(metadata: dict | None) -> str | None:
    if not metadata:
        return None
    if owner := metadata.get("owner_channel"):
        return owner
    labels = metadata.get("deployment_labels") or metadata.get("labels") or {}
    return labels.get("example.com/owner-channel") or metadata.get("example.com/owner-channel")


def _extract_namespace_channel(metadata: dict | None) -> str | None:
    if not metadata:
        return None
    if channel := metadata.get("namespace_channel"):
        return channel
    annotations = metadata.get("namespace_annotations") or metadata.get("metadata_json") or {}
    return annotations.get("project-fyr/slack-channel")


class SlackNotifier:
    def __init__(
        self,
        *,
        token: str | None,
        default_channel: str | None = None,
        mock_log_file: str | None = None,
        base_url: str | None = None,
        enable_requestor_dm: bool = False,
        enable_owner_channel: bool = False,
        cache_ttl_seconds: int = 3600,
    ):
        self._mock_mode = mock_log_file is not None
        self._mock_log_file = mock_log_file
        self._enabled = bool(token) or self._mock_mode
        self._default_channel = default_channel
        self._enable_requestor_dm = enable_requestor_dm
        self._enable_owner_channel = enable_owner_channel
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache_lock = threading.Lock()
        self._user_id_cache: dict[str, tuple[float, str | None]] = {}
        self._dm_channel_cache: dict[str, tuple[float, str]] = {}
        
        # Create WebClient with custom base_url if provided (for mock service)
        if token and not self._mock_mode:
            if base_url:
                self._client = WebClient(token=token, base_url=base_url)
            else:
                self._client = WebClient(token=token)
        else:
            self._client = None

    def send_analysis(
        self,
        *,
        channel: str | None,
        rollout_ref: str,
        analysis: Analysis,
        metadata: dict | None = None,
        max_attempts: int = 2,
        rollout_id: int | None = None,
    ) -> bool:
        if not self._enabled:
            return False

        # Use enhanced blocks with buttons if rollout_id provided
        if rollout_id is not None:
            from .slack_blocks import build_failure_notification
            
            # Extract metadata fields
            meta = metadata or {}
            namespace = meta.get("namespace")
            deployment = meta.get("deployment")
            
            # Parse namespace/deployment from rollout_ref if not in metadata
            if not namespace and "/" in rollout_ref:
                parts = rollout_ref.split("/", 1)
                if len(parts) >= 2:
                    namespace = parts[0]
                    deployment = parts[1] if len(parts) > 1 else None
            if deployment and "#" in deployment:
                deployment = deployment.split("#", 1)[0]
            
            payload = build_failure_notification(
                rollout_ref=rollout_ref,
                analysis=analysis,
                rollout_id=rollout_id,
                namespace=namespace,
                deployment=deployment,
                cluster=meta.get("cluster"),
                team=meta.get("team"),
                pipeline_url=meta.get("pipeline_url"),
            )
        else:
            payload = self._build_blocks(rollout_ref, analysis, metadata or {})

        plan = self._build_plan(channel=channel, metadata=metadata)
        return self._send_with_routing(
            plan=plan,
            payload=payload,
            max_attempts=max_attempts,
            kind="analysis",
            context={
                "rollout_ref": rollout_ref,
                "analysis": analysis,
                "metadata": metadata,
            },
        )

    def send_alert_batch(
        self,
        *,
        channel: str | None,
        batch_id: int,
        namespace: str,
        alerts: list[dict],
        analysis: Analysis | None = None,
        primary_alert_name: str | None = None,
        metadata: dict | None = None,
        max_attempts: int = 2,
    ) -> bool:
        if not self._enabled:
            return False

        from .slack_blocks import build_alert_batch_notification

        payload = build_alert_batch_notification(
            batch_id=batch_id,
            namespace=namespace,
            alerts=alerts,
            analysis=analysis,
            primary_alert_name=primary_alert_name,
        )

        plan = self._build_plan(channel=channel, metadata=metadata or {"namespace": namespace})
        return self._send_with_routing(
            plan=plan,
            payload=payload,
            max_attempts=max_attempts,
            kind="alert_batch",
            context={
                "batch_id": batch_id,
                "namespace": namespace,
                "alerts": alerts,
                "analysis": analysis,
                "primary_alert_name": primary_alert_name,
            },
        )

    def _build_plan(self, *, channel: str | None, metadata: dict | None) -> SlackRoutingPlan:
        namespace_channel = channel or _extract_namespace_channel(metadata)
        return build_routing_plan(
            requestor_email=_extract_requestor_email(metadata),
            owner_channel=_extract_owner_channel(metadata),
            namespace_channel=namespace_channel,
            default_channel=self._default_channel,
            enable_requestor_dm=self._enable_requestor_dm,
            enable_owner_channel=self._enable_owner_channel,
        )

    def _send_with_routing(
        self,
        *,
        plan: SlackRoutingPlan,
        payload: list[dict],
        max_attempts: int,
        kind: str,
        context: dict,
    ) -> bool:
        if not plan.primary:
            return False

        primary_success = False
        for target in plan.primary:
            if self._send_to_target(target, payload, max_attempts, kind, context):
                primary_success = True

        if primary_success:
            return True

        for target in plan.fallback:
            if self._send_to_target(target, payload, max_attempts, kind, context):
                return True

        return False

    def _send_to_target(
        self,
        target: SlackTarget,
        payload: list[dict],
        max_attempts: int,
        kind: str,
        context: dict,
    ) -> bool:
        channel = self._resolve_target_channel(target)
        if not channel:
            return False
        return self._deliver_payload(
            channel=channel,
            payload=payload,
            max_attempts=max_attempts,
            kind=kind,
            context=context,
            target=target,
        )

    def _resolve_target_channel(self, target: SlackTarget) -> str | None:
        if target.kind == "channel":
            return target.value
        if self._mock_mode:
            return f"dm:{target.value}"
        if not self._client:
            return None
        user_id = self._resolve_user_id(target.value)
        if not user_id:
            return None
        return self._open_dm_channel(user_id)

    def _deliver_payload(
        self,
        *,
        channel: str,
        payload: list[dict],
        max_attempts: int,
        kind: str,
        context: dict,
        target: SlackTarget,
    ) -> bool:
        if self._mock_mode:
            self._log_mock_payload(
                channel=channel,
                payload=payload,
                kind=kind,
                context=context,
                target=target,
            )
            return True
        return self._post_blocks(channel, payload, max_attempts=max_attempts)

    def _post_blocks(self, channel: str, payload: list[dict], max_attempts: int = 2) -> bool:
        # Real mode: post to Slack
        if not self._client:
            return False

        attempts = 0
        while attempts < max_attempts:
            attempts += 1
            try:
                self._client.chat_postMessage(channel=channel, blocks=payload)
                return True
            except SlackApiError as exc:
                print(f"failed to post slack message (attempt {attempts}/{max_attempts}): {exc}")
                if attempts >= max_attempts:
                    break
                time.sleep(1)
        return False

    def _log_mock_payload(
        self,
        *,
        channel: str,
        payload: list[dict],
        kind: str,
        context: dict,
        target: SlackTarget,
    ) -> None:
        import json
        from datetime import datetime

        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "channel": channel,
            "type": kind,
            "target": {"kind": target.kind, "value": target.value},
            "slack_blocks": payload,
        }

        if kind == "analysis":
            analysis: Analysis = context["analysis"]
            log_entry.update(
                {
                    "rollout_ref": context.get("rollout_ref"),
                    "analysis": {
                        "summary": analysis.summary,
                        "likely_cause": analysis.likely_cause,
                        "recommended_steps": analysis.recommended_steps,
                        "severity": analysis.severity,
                        "triage_team": getattr(analysis, "triage_team", None),
                        "triage_reason": getattr(analysis, "triage_reason", None),
                    },
                    "metadata": context.get("metadata"),
                }
            )
        elif kind == "alert_batch":
            analysis = context.get("analysis")
            log_entry.update(
                {
                    "batch_id": context.get("batch_id"),
                    "namespace": context.get("namespace"),
                    "alerts": context.get("alerts"),
                    "analysis": {
                        "summary": analysis.summary if analysis else None,
                        "likely_cause": analysis.likely_cause if analysis else None,
                        "recommended_steps": analysis.recommended_steps if analysis else None,
                        "severity": analysis.severity if analysis else None,
                    },
                }
            )

        with open(self._mock_log_file, "a") as f:
            f.write(json.dumps(log_entry, indent=2))
            f.write("\n" + "=" * 80 + "\n")

    def _resolve_user_id(self, email: str) -> str | None:
        import logging
        logger = logging.getLogger(__name__)
        cached = self._cache_get(self._user_id_cache, email)
        if cached is not _CACHE_MISS:
            return cached
        try:
            response = self._client.users_lookupByEmail(email=email)  # type: ignore[union-attr]
            user_id = response.get("user", {}).get("id")
            logger.info(f"Resolved email {email} to user_id {user_id}")
        except SlackApiError as exc:
            error = exc.response.get("error") if exc.response else None
            logger.error(f"Slack API error looking up email {email}: {error} (full response: {exc.response})")
            if error in {"users_not_found", "user_not_found"}:
                user_id = None
            else:
                return None
        self._cache_set(self._user_id_cache, email, user_id)
        return user_id

    def _open_dm_channel(self, user_id: str) -> str | None:
        cached = self._cache_get(self._dm_channel_cache, user_id)
        if cached is not _CACHE_MISS:
            return cached
        try:
            response = self._client.conversations_open(users=[user_id])  # type: ignore[union-attr]
            channel_id = response.get("channel", {}).get("id")
        except SlackApiError:
            return None
        if channel_id:
            self._cache_set(self._dm_channel_cache, user_id, channel_id)
        return channel_id

    def _cache_get(self, cache: dict, key: str):
        now = time.time()
        with self._cache_lock:
            entry = cache.get(key)
            if not entry:
                return _CACHE_MISS
            timestamp, value = entry
            if now - timestamp > self._cache_ttl_seconds:
                cache.pop(key, None)
                return _CACHE_MISS
            return value

    def _cache_set(self, cache: dict, key: str, value):
        now = time.time()
        with self._cache_lock:
            cache[key] = (now, value)

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
