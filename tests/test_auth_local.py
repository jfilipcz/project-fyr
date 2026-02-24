"""Tests for project_fyr.auth.local_auth — JWT-based local authentication."""

import asyncio
import pytest
import jwt as pyjwt
from datetime import timedelta
from fastapi import HTTPException

from project_fyr import utcnow
from project_fyr.auth.local_auth import LocalAuthProvider

SECRET = "test-secret-key-for-tests-long-enough"


def test_init_rejects_default_secret():
    with pytest.raises(ValueError, match="JWT secret is not configured"):
        LocalAuthProvider(jwt_secret="change-me-in-production")


def test_init_rejects_empty_secret():
    with pytest.raises(ValueError, match="JWT secret is not configured"):
        LocalAuthProvider(jwt_secret="")


def test_init_accepts_valid_secret():
    provider = LocalAuthProvider(jwt_secret=SECRET)
    assert provider.get_provider_name() == "local"


def test_create_and_validate_token():
    provider = LocalAuthProvider(jwt_secret=SECRET)
    token = provider.create_access_token(
        user_id=1, email="user@example.com", name="Test User", is_admin=False
    )
    assert isinstance(token, str)

    # Decode manually to check payload
    payload = pyjwt.decode(token, SECRET, algorithms=["HS256"])
    assert payload["email"] == "user@example.com"
    assert payload["user_id"] == 1
    assert payload["is_admin"] is False
    assert payload["provider"] == "local"


def test_validate_token_returns_user_info():
    provider = LocalAuthProvider(jwt_secret=SECRET)
    token = provider.create_access_token(
        user_id=42, email="admin@example.com", name="Admin", is_admin=True
    )

    info = asyncio.run(provider.validate_token(token))
    assert info["email"] == "admin@example.com"
    assert info["is_admin"] is True
    assert "admin" in info["roles"]
    assert info["provider"] == "local"


def test_validate_expired_token():
    provider = LocalAuthProvider(jwt_secret=SECRET, jwt_expiry_hours=0)
    # Manually create an already-expired token
    payload = {
        "user_id": 1,
        "email": "old@example.com",
        "name": "Old",
        "is_admin": False,
        "exp": utcnow() - timedelta(hours=1),
        "iat": utcnow() - timedelta(hours=2),
        "provider": "local",
    }
    token = pyjwt.encode(payload, SECRET, algorithm="HS256")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(provider.validate_token(token))
    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()


def test_validate_wrong_secret():
    provider = LocalAuthProvider(jwt_secret=SECRET)
    token = pyjwt.encode({"sub": "x"}, "wrong-secret-that-is-long-enough-32b!", algorithm="HS256")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(provider.validate_token(token))
    assert exc_info.value.status_code == 401
