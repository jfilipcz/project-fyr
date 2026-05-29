# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Authentication middleware for FastAPI."""

import logging
from typing import List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .base import AuthProvider

logger = logging.getLogger(__name__)


def _request_path(request: Request) -> str:
    return request.scope.get("path", "")


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """
    Middleware to authenticate requests using configured auth providers.

    Supports both SSO and local authentication providers, checking them in order.
    Tokens can be provided via Authorization header (Bearer token) or session cookie.
    """

    def __init__(
        self,
        app,
        sso_provider: Optional[AuthProvider] = None,
        local_provider: Optional[AuthProvider] = None,
        exclude_paths: Optional[List[str]] = None,
        redirect_to_login: bool = True
    ):
        """
        Initialize authentication middleware.

        Args:
            app: FastAPI application
            sso_provider: SSO authentication provider (e.g., Entra ID)
            local_provider: Local authentication provider
            exclude_paths: List of path prefixes to exclude from authentication
            redirect_to_login: If True, redirect browser requests to /login page
        """
        super().__init__(app)
        self.sso_provider = sso_provider
        self.local_provider = local_provider
        self.exclude_paths = exclude_paths or []
        self.redirect_to_login = redirect_to_login

        provider_names = []
        if sso_provider:
            provider_names.append(sso_provider.get_provider_name())
        if local_provider:
            provider_names.append(local_provider.get_provider_name())

        logger.info(f"Initialized authentication middleware with providers: {', '.join(provider_names)}")
        logger.info(f"Excluded paths: {', '.join(self.exclude_paths)}")

    async def dispatch(self, request: Request, call_next):
        """Process request and perform authentication check."""

        request_path = _request_path(request)

        # Skip authentication for excluded paths
        for excluded_path in self.exclude_paths:
            if request_path.startswith(excluded_path):
                logger.debug(f"Skipping auth for excluded path: {request_path}")
                return await call_next(request)

        # Skip authentication for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Try to extract token from Authorization header or cookie
        token = self._extract_token(request)

        if not token:
            logger.debug(f"No token found for {request_path}")
            return self._unauthorized_response(request)

        # Try to validate token with available providers
        user_info = await self._validate_with_providers(token)

        if not user_info:
            logger.warning(f"Authentication failed for {request_path}")
            return self._unauthorized_response(request)

        # Add authenticated user info to request state
        request.state.user = user_info
        logger.debug(f"Authenticated {user_info.get('email')} via {user_info.get('provider')}")

        response = await call_next(request)
        return response

    def _extract_token(self, request: Request) -> Optional[str]:
        """
        Extract authentication token from request.

        Checks Authorization header first, then session cookie.
        """
        # Try Authorization header (Bearer token)
        authorization = request.headers.get("Authorization")
        if authorization and authorization.startswith("Bearer "):
            return authorization.split(" ")[1]

        # Try session cookie
        return request.cookies.get("fyr_session")

    async def _validate_with_providers(self, token: str) -> Optional[dict]:
        """
        Try to validate token with configured providers.

        Tries SSO provider first, then falls back to local provider.
        """
        logger.debug("[AUTH] Attempting token validation (length=%d)", len(token))

        # Try SSO provider first
        if self.sso_provider:
            try:
                logger.debug("[AUTH] Trying SSO provider")
                user_info = await self.sso_provider.validate_token(token)
                logger.info("[AUTH] SSO provider successfully validated token")
                return user_info
            except Exception as exc:
                logger.info("[AUTH] SSO validation failed, trying local provider")
                logger.debug("[AUTH] SSO provider rejected token with %s", type(exc).__name__)

        # Fall back to local provider
        if self.local_provider:
            try:
                logger.debug("[AUTH] Trying local provider")
                user_info = await self.local_provider.validate_token(token)
                logger.info("[AUTH] Local provider successfully validated token")
                return user_info
            except Exception as exc:
                logger.info("[AUTH] Local validation failed")
                logger.debug("[AUTH] Local provider rejected token with %s", type(exc).__name__)

        logger.warning("[AUTH] All providers failed to validate token")
        return None

    def _unauthorized_response(self, request: Request):
        """
        Return appropriate unauthorized response based on request type.

        For API requests, return JSON response.
        For browser requests, optionally redirect to login page.
        """
        request_path = _request_path(request)

        # For API requests, return JSON
        if request_path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"}
            )

        # For browser requests, redirect to login page
        if self.redirect_to_login:
            # Don't redirect if already on login page to avoid loop
            if not request_path.startswith("/login") and not request_path.startswith("/auth/"):
                return RedirectResponse(url="/login", status_code=302)

        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"}
        )
