"""Tests for project_fyr.auth.middleware."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from project_fyr.auth.middleware import AuthenticationMiddleware, _request_path


class _DummyProvider:
    def __init__(self, name: str, *, result: dict | None = None, exc: Exception | None = None) -> None:
        self._name = name
        self._result = result
        self._exc = exc

    def get_provider_name(self) -> str:
        return self._name

    async def validate_token(self, token: str) -> dict:
        if self._exc is not None:
            raise self._exc
        return self._result or {"token": token, "provider": self._name}


def _request(*, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/protected",
            "headers": headers or [],
        }
    )


def test_extract_token_prefers_bearer_header_over_cookie() -> None:
    middleware = AuthenticationMiddleware(FastAPI())
    request = _request(
        headers=[
            (b"authorization", b"Bearer header-token"),
            (b"cookie", b"fyr_session=cookie-token"),
        ]
    )

    assert middleware._extract_token(request) == "header-token"


def test_validate_with_providers_falls_back_to_local() -> None:
    middleware = AuthenticationMiddleware(
        FastAPI(),
        sso_provider=_DummyProvider("sso", exc=HTTPException(status_code=401, detail="bad token")),
        local_provider=_DummyProvider("local", result={"email": "user@example.com", "provider": "local"}),
    )

    result = asyncio.run(middleware._validate_with_providers("candidate-token"))

    assert result == {"email": "user@example.com", "provider": "local"}


def test_validate_with_providers_returns_none_when_all_providers_fail() -> None:
    middleware = AuthenticationMiddleware(
        FastAPI(),
        sso_provider=_DummyProvider("sso", exc=HTTPException(status_code=401, detail="bad token")),
        local_provider=_DummyProvider("local", exc=HTTPException(status_code=401, detail="still bad")),
    )

    result = asyncio.run(middleware._validate_with_providers("candidate-token"))

    assert result is None


def test_request_path_uses_asgi_scope_path_instead_of_url_path() -> None:
    request = SimpleNamespace(
        scope={"path": "/protected"},
        url=SimpleNamespace(path="/login"),
    )

    assert _request_path(request) == "/protected"
