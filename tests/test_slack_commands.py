# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Tests for Slack slash command registration and routing."""

from __future__ import annotations

from project_fyr.slack_commands import register_commands


class _DummyApp:
    def __init__(self) -> None:
        self.handlers = {}

    def command(self, command_name: str):
        def _decorator(fn):
            self.handlers[command_name] = fn
            return fn

        return _decorator


def test_register_commands_supports_default_and_env_aliases() -> None:
    app = _DummyApp()

    register_commands(app)

    assert set(app.handlers) == {"/fyr-ci", "/fyr-dev"}


def test_help_uses_invoked_command_alias() -> None:
    app = _DummyApp()
    register_commands(app)

    ack_calls = []
    responses = []

    def _ack():
        ack_calls.append(True)

    def _respond(*, blocks=None, text=None):
        responses.append({"blocks": blocks, "text": text})

    app.handlers["/fyr-dev"](
        ack=_ack,
        command={"command": "/fyr-dev", "text": "help", "user_id": "U1", "channel_id": "C1"},
        respond=_respond,
        client=None,
    )

    assert ack_calls == [True]
    assert responses
    response_text = str(responses[-1]["blocks"])
    assert "/fyr-dev status" in response_text
    assert "/fyr-dev help" in response_text
