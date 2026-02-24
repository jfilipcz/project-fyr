# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Microsoft Entra ID (Azure AD) authentication provider."""

import json
import logging
from typing import Dict
from functools import lru_cache

import jwt
import requests
from fastapi import HTTPException
from cryptography.hazmat.primitives import serialization

from ..base import AuthProvider

logger = logging.getLogger(__name__)


class EntraIDProvider(AuthProvider):
    """Microsoft Entra ID (Azure AD) authentication provider.

    This provider validates JWT tokens issued by Microsoft Entra ID (formerly Azure AD).
    It fetches and caches the public keys from Microsoft's JWKS endpoint and verifies
    tokens using RS256 signature algorithm.
    """

    def __init__(self, tenant_id: str, client_id: str):
        """
        Initialize Entra ID provider.

        Args:
            tenant_id: Azure AD tenant ID
            client_id: Application (client) ID from Azure AD app registration
        """
        if not tenant_id or not client_id:
            raise ValueError("tenant_id and client_id are required for Entra ID authentication")

        self.tenant_id = tenant_id
        self.client_id = client_id
        self.jwks_uri_v1 = f"https://login.microsoftonline.com/{tenant_id}/discovery/keys"
        self.jwks_uri_v2 = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"

        logger.info(f"Initialized Entra ID provider for tenant {tenant_id}")

    @lru_cache(maxsize=3)
    def get_jwks(self, token_version: str) -> Dict:
        """
        Fetch and cache JWKS (JSON Web Key Set) from Microsoft.

        Args:
            token_version: Token version ('1.0' or '2.0')

        Returns:
            JWKS data containing public keys

        Raises:
            HTTPException: If fetching JWKS fails
        """
        try:
            jwks_uri = self.jwks_uri_v1 if token_version == '1.0' else self.jwks_uri_v2
            logger.debug(f"Fetching JWKS from {jwks_uri}")

            response = requests.get(jwks_uri, timeout=10)
            response.raise_for_status()

            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch JWKS keys: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to fetch JWKS keys: {str(e)}"
            )

    async def validate_token(self, token: str) -> Dict:
        """
        Validate an Entra ID JWT token.

        Args:
            token: JWT token from Entra ID

        Returns:
            Dict with user information

        Raises:
            HTTPException: If token validation fails
        """
        try:
            # Decode without verification to get header and basic payload info
            unverified_header = jwt.get_unverified_header(token)
            unverified_payload = jwt.decode(token, options={"verify_signature": False})

            issuer = unverified_payload.get('iss')
            kid = unverified_header.get('kid')
            token_version = unverified_payload.get('ver', '2.0')
            token_alg = unverified_header.get('alg')

            # Quick check: If this is an HS256 token without an issuer, it's likely a local session token
            # Reject it immediately so the middleware can try the local provider
            if token_alg == 'HS256' or (not issuer and token_alg != 'RS256'):
                logger.debug(f"Token appears to be local (alg={token_alg}, issuer={issuer}), rejecting for SSO provider")
                raise HTTPException(status_code=401, detail="Not a valid SSO token")

            logger.debug(f"Token header: {unverified_header}")
            logger.debug(f"Token algorithm: {token_alg}")
            logger.debug(f"Token kid: {kid}")
            logger.debug(f"Token issuer: {issuer}")
            logger.debug(f"Token audience: {unverified_payload.get('aud')}")
            logger.debug(f"Token version: {token_version}")

            logger.debug(f"Validating token with kid={kid}, version={token_version}, issuer={issuer}")
            logger.debug(f"Token payload keys: {list(unverified_payload.keys())}")

            # Get public keys from Microsoft's JWKS endpoint
            jwks_data = self.get_jwks(token_version)
            logger.debug(f"Retrieved {len(jwks_data.get('keys', []))} keys from JWKS")

            # Find the matching key by kid (key ID)
            public_key = None
            public_key_bytes = None

            if kid:
                # If kid is present, find the matching key
                for key_data in jwks_data.get('keys', []):
                    if key_data.get('kid') == kid:
                        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
                        public_key_bytes = public_key.public_bytes(
                            encoding=serialization.Encoding.PEM,
                            format=serialization.PublicFormat.SubjectPublicKeyInfo
                        )
                        break
            else:
                # If no kid, try all keys until one works
                logger.warning("No kid in token header, will try all available keys")
                for key_data in jwks_data.get('keys', []):
                    try:
                        test_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
                        public_key_bytes = test_key.public_bytes(
                            encoding=serialization.Encoding.PEM,
                            format=serialization.PublicFormat.SubjectPublicKeyInfo
                        )
                        # Try to decode with this key to see if it works
                        jwt.decode(
                            jwt=token,
                            key=public_key_bytes,
                            algorithms=['RS256'],
                            options={"verify_signature": True, "verify_aud": False, "verify_iss": False}
                        )
                        # If we get here, this key worked
                        logger.info(f"Found working key: {key_data.get('kid')}")
                        break
                    except Exception:
                        continue

            if not public_key_bytes:
                logger.error(f"Token key ID not found: {kid}, available keys: {[k.get('kid') for k in jwks_data.get('keys', [])]}")
                raise HTTPException(status_code=401, detail="Token key ID not found")

            # Verify the token with the public key
            # For ID tokens in implicit flow, audience is the client_id (not api://client_id)
            # Try multiple common algorithms that Azure AD might use
            decoded_payload = jwt.decode(
                jwt=token,
                key=public_key_bytes,
                verify=True,
                algorithms=['RS256', 'RS384', 'RS512', 'ES256', 'ES384', 'ES512'],
                audience=self.client_id,  # ID tokens use client_id as audience
                issuer=issuer,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_aud": True,
                    "verify_iss": True
                }
            )

            # Extract user information
            user_info = {
                "user_id": decoded_payload.get("oid"),  # Object ID
                "email": decoded_payload.get("preferred_username") or decoded_payload.get("upn"),
                "name": decoded_payload.get("name"),
                "roles": decoded_payload.get("roles", []),
                "provider": "entra"
            }

            logger.info(f"Successfully validated token for user {user_info.get('email')}")
            return user_info

        except HTTPException:
            # Re-raise HTTPException without catching it (for early rejection of local tokens)
            raise
        except jwt.ExpiredSignatureError:
            logger.warning("Token has expired")
            raise HTTPException(status_code=401, detail="Token has expired")
        except jwt.InvalidAudienceError:
            logger.warning(f"Invalid token audience, expected {self.client_id}")
            raise HTTPException(status_code=401, detail="Invalid token audience")
        except jwt.InvalidIssuerError:
            logger.warning("Invalid token issuer")
            raise HTTPException(status_code=401, detail="Invalid token issuer")
        except jwt.InvalidTokenError as e:
            logger.warning(f"Token validation failed: {str(e)}")
            raise HTTPException(status_code=401, detail=f"Token validation failed: {str(e)}")
        except Exception as e:
            logger.error(f"Authentication failed: {str(e)}", exc_info=True)
            raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")

    def get_provider_name(self) -> str:
        """Return the provider name."""
        return "entra"
