# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Generic OpenID Connect (OIDC) authentication provider.

Works with any standards-compliant OIDC provider (Okta, Keycloak,
Auth0, Google Workspace, etc.) using the well-known JWKS endpoint.
"""

import logging
from typing import Dict
from functools import lru_cache

import jwt
import requests
from fastapi import HTTPException

from ..base import AuthProvider

logger = logging.getLogger(__name__)


class GenericOIDCProvider(AuthProvider):
    """Validates JWTs against any OIDC-compliant identity provider.

    Configuration requires either:
      * ``issuer`` — the provider's issuer URL (used to derive JWKS URI
        from ``<issuer>/.well-known/openid-configuration``), **or**
      * ``jwks_uri`` — an explicit JWKS endpoint.

    If both are given, ``jwks_uri`` takes precedence.
    """

    def __init__(
        self,
        issuer: str | None = None,
        jwks_uri: str | None = None,
        audience: str | None = None,
        client_id: str | None = None,
    ):
        if not issuer and not jwks_uri:
            raise ValueError(
                "GenericOIDCProvider requires at least 'oidc_issuer' or 'oidc_jwks_uri'"
            )

        self._issuer = issuer
        self._audience = audience or client_id
        self._jwks_uri = jwks_uri or self._discover_jwks_uri(issuer)

        logger.info(
            "Initialized GenericOIDC provider (issuer=%s, jwks_uri=%s)",
            issuer,
            self._jwks_uri,
        )

    # ------------------------------------------------------------------
    # AuthProvider interface
    # ------------------------------------------------------------------

    def get_provider_name(self) -> str:
        return "generic_oidc"

    async def validate_token(self, token: str) -> Dict:
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            alg = header.get("alg", "RS256")

            # Reject HS256 tokens — those are likely local-auth tokens
            if alg == "HS256":
                raise HTTPException(
                    status_code=401, detail="Not a valid SSO token"
                )

            jwks = self._get_jwks()
            public_key = self._find_key(jwks, kid)

            decode_opts: dict = {
                "algorithms": [alg],
            }
            if self._audience:
                decode_opts["audience"] = self._audience
            if self._issuer:
                decode_opts["issuer"] = self._issuer

            payload = jwt.decode(token, public_key, **decode_opts)

            # Standard OIDC claims → normalised user info
            email = (
                payload.get("email")
                or payload.get("preferred_username")
                or payload.get("upn")
                or payload.get("sub")
            )
            name = payload.get("name") or email

            return {
                "user_id": payload.get("sub"),
                "email": email,
                "name": name,
                "roles": payload.get("roles", []),
                "provider": "generic_oidc",
            }

        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token has expired")
        except jwt.InvalidAudienceError:
            raise HTTPException(status_code=401, detail="Invalid audience")
        except jwt.InvalidIssuerError:
            raise HTTPException(status_code=401, detail="Invalid issuer")
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("OIDC token validation failed: %s", exc)
            raise HTTPException(status_code=401, detail="Invalid token")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _discover_jwks_uri(issuer: str) -> str:
        """Fetch the JWKS URI from the OIDC discovery document."""
        url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            jwks_uri = resp.json().get("jwks_uri")
            if not jwks_uri:
                raise ValueError("Discovery document missing 'jwks_uri'")
            return jwks_uri
        except Exception as exc:
            raise ValueError(
                f"Failed to discover JWKS URI from {url}: {exc}"
            ) from exc

    @lru_cache(maxsize=1)
    def _get_jwks(self) -> dict:
        try:
            resp = requests.get(self._jwks_uri, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("Failed to fetch JWKS from %s: %s", self._jwks_uri, exc)
            raise HTTPException(
                status_code=500, detail="Failed to fetch identity provider keys"
            )

    @staticmethod
    def _find_key(jwks: dict, kid: str | None):
        """Locate the RSA public key matching *kid* in the JWKS."""

        keys = jwks.get("keys", [])
        for key_data in keys:
            if kid and key_data.get("kid") != kid:
                continue
            # Build RSA public key from JWK
            from jwt.algorithms import RSAAlgorithm  # type: ignore[attr-defined]

            return RSAAlgorithm.from_jwk(key_data)

        raise HTTPException(
            status_code=401,
            detail="No matching signing key found in identity provider",
        )
