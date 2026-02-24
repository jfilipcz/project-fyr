"""Tests for project_fyr.auth.providers.oidc — Generic OIDC provider."""

import asyncio
import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

from project_fyr.auth.providers.oidc import GenericOIDCProvider


def test_init_requires_issuer_or_jwks():
    with pytest.raises(ValueError, match="requires at least"):
        GenericOIDCProvider()


@patch("project_fyr.auth.providers.oidc.requests.get")
def test_init_discovers_jwks_from_issuer(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"jwks_uri": "https://idp.example.com/jwks"}
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    provider = GenericOIDCProvider(issuer="https://idp.example.com")
    assert provider._jwks_uri == "https://idp.example.com/jwks"
    assert provider.get_provider_name() == "generic_oidc"


def test_init_uses_explicit_jwks_uri():
    provider = GenericOIDCProvider(jwks_uri="https://idp.example.com/jwks")
    assert provider._jwks_uri == "https://idp.example.com/jwks"


@patch("project_fyr.auth.providers.oidc.requests.get")
def test_discovery_failure_raises(mock_get):
    mock_get.side_effect = Exception("Network error")
    with pytest.raises(ValueError, match="Failed to discover"):
        GenericOIDCProvider(issuer="https://bad.example.com")


def test_validate_rejects_hs256():
    """HS256 tokens should be rejected — only RSA tokens are valid for OIDC."""
    import jwt as pyjwt

    provider = GenericOIDCProvider(jwks_uri="https://idp.example.com/jwks")
    token = pyjwt.encode({"sub": "x"}, "secret-key-long-enough-for-hs256!!", algorithm="HS256")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(provider.validate_token(token))
    assert exc_info.value.status_code == 401
    assert "Not a valid SSO token" in exc_info.value.detail


def test_validate_no_matching_kid():
    """Token with unknown kid should raise."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    import jwt as pyjwt

    # Generate RSA key pair for signing
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())

    token = pyjwt.encode(
        {"sub": "user1", "email": "u@e.com"},
        private_key,
        algorithm="RS256",
        headers={"kid": "unknown-kid"},
    )

    provider = GenericOIDCProvider(jwks_uri="https://idp.example.com/jwks")

    # Mock JWKS with non-matching kid
    with patch.object(provider, "_get_jwks", return_value={"keys": [{"kid": "other-kid"}]}):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(provider.validate_token(token))
        assert exc_info.value.status_code == 401
        assert "No matching signing key" in exc_info.value.detail
