# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Chat safety guardrails for interactive investigations."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable


@dataclass(frozen=True)
class PolicyDecision:
    """Decision produced by chat input policy evaluation."""

    allowed: bool
    reason: str = ""
    rule_name: str | None = None


_PROMPT_OVERRIDE_RULE = re.compile(
    r"\b(ignore|bypass|override)\b.{0,40}\b(previous|system|safety|guardrails?|instructions?)\b",
    re.IGNORECASE | re.DOTALL,
)

_ALLOWLIST_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "missing_secret_troubleshooting",
        re.compile(r"\b(secret|configmap)\b.{0,40}\b(missing|not found)\b", re.IGNORECASE | re.DOTALL),
    ),
    (
        "rbac_debugging",
        re.compile(
            r"\b(rbac|forbidden|permission|access denied)\b.{0,60}\b(secret|configmap|pods?|pvc|serviceaccount)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "secret_structure_debugging",
        re.compile(r"\b(secret)\b.{0,40}\b(keys?|key names?|structure|metadata|exist)\b", re.IGNORECASE | re.DOTALL),
    ),
]

_BLOCKLIST_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "credential_dump_request",
        re.compile(
            r"\b(show|dump|print|reveal|expose|extract|leak|return|display|share)\b.{0,50}"
            r"\b(secret|secrets|token|tokens|password|passwords|credentials?|api[-_ ]?key|private key|kubeconfig)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "secret_decode_request",
        re.compile(
            r"\b(base64|decode|decrypt)\b.{0,40}\b(secret|token|password|credential|key)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "environment_dump_request",
        re.compile(
            r"\b(show|dump|print|list)\b.{0,30}\b(env|environment)\b.{0,20}\b(vars?|variables?)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "serviceaccount_token_extraction",
        re.compile(
            r"\b(serviceaccount|service account)\b.{0,40}\b(token|jwt)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
]

_HIGH_RISK_EXTRACTION_HINTS = re.compile(r"\b(all|every|entire|full|complete|raw)\b", re.IGNORECASE)

_SLACK_TOKEN_PATTERN = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")
_SLACK_APP_TOKEN_PATTERN = re.compile(r"\bxapp-[A-Za-z0-9-]{10,}\b")
_OPENAI_TOKEN_PATTERN = re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9._-]{8,}\.[A-Za-z0-9._-]{8,}\b")
_AWS_KEY_PATTERN = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
    re.MULTILINE,
)
_KEY_VALUE_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|client[_-]?secret)\b"
    r"(\s*[:=]\s*)(?!\[[A-Z_]+\])([^\s,;`\"']+)"
)
_BASIC_AUTH_URL_PATTERN = re.compile(r"([a-z]+://[^/\s:@]+:)([^@\s/]+)(@)", re.IGNORECASE)


def evaluate_chat_input_policy(message: str) -> PolicyDecision:
    """Apply allowlist/blocklist checks to a user message."""

    normalized = " ".join((message or "").strip().split())
    if not normalized:
        return PolicyDecision(allowed=True, reason="empty_message")

    if _PROMPT_OVERRIDE_RULE.search(normalized):
        return PolicyDecision(
            allowed=False,
            reason="Instruction override attempts are blocked",
            rule_name="prompt_override_attempt",
        )

    allow_match = _first_match(normalized, _ALLOWLIST_RULES)
    for rule_name, pattern in _BLOCKLIST_RULES:
        if not pattern.search(normalized):
            continue
        if allow_match and not _HIGH_RISK_EXTRACTION_HINTS.search(normalized):
            return PolicyDecision(
                allowed=True,
                reason=f"allowlisted:{allow_match}",
                rule_name=allow_match,
            )
        return PolicyDecision(
            allowed=False,
            reason="Credential or secret extraction requests are blocked",
            rule_name=rule_name,
        )

    return PolicyDecision(allowed=True, reason="no_block_match")


def redact_sensitive_output(text: str) -> tuple[str, int]:
    """Redact credential-like patterns from agent output."""

    redacted = text or ""
    redaction_count = 0

    redacted, count = _replace_regex(redacted, _PRIVATE_KEY_PATTERN, "[REDACTED_PRIVATE_KEY]")
    redaction_count += count
    redacted, count = _replace_regex(redacted, _SLACK_TOKEN_PATTERN, "[REDACTED_SLACK_TOKEN]")
    redaction_count += count
    redacted, count = _replace_regex(redacted, _SLACK_APP_TOKEN_PATTERN, "[REDACTED_SLACK_APP_TOKEN]")
    redaction_count += count
    redacted, count = _replace_regex(redacted, _OPENAI_TOKEN_PATTERN, "[REDACTED_API_KEY]")
    redaction_count += count
    redacted, count = _replace_regex(redacted, _JWT_PATTERN, "[REDACTED_JWT]")
    redaction_count += count
    redacted, count = _replace_regex(redacted, _AWS_KEY_PATTERN, "[REDACTED_AWS_KEY]")
    redaction_count += count

    redacted, count = _replace_regex(
        redacted,
        _KEY_VALUE_SECRET_PATTERN,
        lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]",
    )
    redaction_count += count

    redacted, count = _replace_regex(
        redacted,
        _BASIC_AUTH_URL_PATTERN,
        lambda m: f"{m.group(1)}[REDACTED]{m.group(3)}",
    )
    redaction_count += count

    return redacted, redaction_count


def _first_match(message: str, rules: list[tuple[str, re.Pattern[str]]]) -> str | None:
    for rule_name, pattern in rules:
        if pattern.search(message):
            return rule_name
    return None


def _replace_regex(
    text: str,
    pattern: re.Pattern[str],
    replacement: str | Callable[[re.Match[str]], str],
) -> tuple[str, int]:
    count = 0

    def _wrapped(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        if callable(replacement):
            return replacement(match)
        return replacement

    return pattern.sub(_wrapped, text), count
