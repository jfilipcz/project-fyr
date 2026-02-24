# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Authentication endpoints for login/logout and user management."""

import logging
import secrets
from project_fyr import utcnow
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Response, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .local_auth import LocalAuthProvider
from .models import User, UserSession
from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    """Login request model."""
    username: str
    password: str


class LoginResponse(BaseModel):
    """Login response model."""
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserInfo(BaseModel):
    """User information model."""
    id: Optional[int] = None
    username: Optional[str] = None
    email: str
    name: str
    is_admin: bool = False
    provider: str


def get_db_session():
    """Get database session for dependency injection."""
    from ..db import init_db
    engine = init_db(settings.database_url)
    with Session(engine) as session:
        yield session


@router.post("/login", response_model=LoginResponse)
async def login(
    credentials: LoginRequest,
    response: Response,
    db: Session = Depends(get_db_session)
):
    """
    Authenticate user with username and password.

    Returns JWT access token on successful authentication.
    Sets session cookie for browser-based access.
    """
    if not settings.local_auth_enabled:
        logger.warning("Login attempt but local authentication is disabled")
        raise HTTPException(
            status_code=403,
            detail="Local authentication is disabled"
        )

    # Initialize local auth provider
    local_auth = LocalAuthProvider(
        jwt_secret=settings.local_auth_jwt_secret,
        jwt_expiry_hours=settings.local_auth_jwt_expiry_hours
    )

    # Authenticate user
    user = local_auth.authenticate_user(db, credentials.username, credentials.password)

    if not user:
        logger.warning(f"Failed login attempt for username: {credentials.username}")
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password"
        )

    # Update last login timestamp
    user.last_login = utcnow()
    db.commit()

    logger.info(f"User logged in: {user.username}")

    # Create JWT token
    token = local_auth.create_access_token(
        user_id=user.id,
        email=user.email,
        name=user.full_name or user.username,
        is_admin=user.is_admin
    )

    # Set HTTP-only cookie for browser-based authentication
    response.set_cookie(
        key="fyr_session",
        value=token,
        httponly=True,
        secure=True,  # Set to True in production with HTTPS
        samesite="lax",
        max_age=settings.local_auth_jwt_expiry_hours * 3600
    )

    return LoginResponse(
        access_token=token,
        user={
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "name": user.full_name or user.username,
            "is_admin": user.is_admin
        }
    )


@router.post("/logout")
async def logout(response: Response):
    """
    Logout user by clearing session cookie.

    Client should also discard any stored tokens.
    """
    response.delete_cookie("fyr_session")
    logger.info("User logged out")
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserInfo)
async def get_current_user(request: Request):
    """
    Get currently authenticated user information.

    Requires valid authentication (populated by middleware).
    """
    if not hasattr(request.state, "user"):
        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )

    user_info = request.state.user

    return UserInfo(
        id=user_info.get("user_id"),
        username=user_info.get("username"),
        email=user_info.get("email", ""),
        name=user_info.get("name", ""),
        is_admin=user_info.get("is_admin", False),
        provider=user_info.get("provider", "unknown")
    )


@router.get("/status")
async def auth_status():
    """
    Get authentication configuration status.

    Returns which authentication methods are enabled.
    """
    return {
        "auth_enabled": settings.auth_enabled,
        "auth_mode": settings.auth_mode,
        "local_auth_enabled": settings.local_auth_enabled,
        "sso_enabled": settings.auth_mode in ["sso", "hybrid"] and settings.sso_provider is not None,
        "sso_provider": settings.sso_provider if settings.auth_mode in ["sso", "hybrid"] else None
    }


@router.get("/sso/login")
async def sso_login(request: Request, db: Session = Depends(get_db_session)):
    """
    Initiate SSO login flow.

    Redirects to the SSO provider's authorization endpoint.
    """
    if settings.auth_mode not in ["sso", "hybrid"]:
        raise HTTPException(
            status_code=403,
            detail="SSO authentication is not enabled"
        )

    if settings.sso_provider == "entra":
        # Microsoft Entra ID (Azure AD) OAuth flow
        if not settings.sso_tenant_id or not settings.sso_client_id:
            raise HTTPException(
                status_code=500,
                detail="Entra ID SSO is not properly configured"
            )

        # Generate and store state for CSRF protection
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)

        # Store state in session (using database)
        session = UserSession(
            session_id=state,
            user_id=None,  # No user yet
            provider="entra",
            expires_at=utcnow()
        )
        db.add(session)
        db.commit()

        # Build authorization URL
        # Use request.url to get the scheme from the original request (handles HTTPS behind proxy)
        redirect_uri = f"https://{request.url.hostname}/auth/callback"

        auth_params = {
            "client_id": settings.sso_client_id,
            "response_type": "id_token",
            "redirect_uri": redirect_uri,
            "response_mode": "form_post",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
        }

        auth_url = (
            f"https://login.microsoftonline.com/{settings.sso_tenant_id}/oauth2/v2.0/authorize?"
            + urlencode(auth_params)
        )

        logger.info(f"Redirecting to Entra ID authorization: {auth_url}")
        return RedirectResponse(url=auth_url, status_code=302)

    else:
        raise HTTPException(
            status_code=501,
            detail=(
                f"Browser-based SSO login flow for '{settings.sso_provider}' is not implemented. "
                "Use a reverse proxy (nginx, Traefik) to handle the OIDC authorization flow "
                "and forward the JWT token in the Authorization header."
            ),
        )


@router.post("/callback")
@router.get("/callback")
async def sso_callback(
    request: Request,
    response: Response,
    db: Session = Depends(get_db_session),
    id_token: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None
):
    """
    Handle SSO callback from identity provider.

    Validates the token and creates/updates user session.
    """
    # Check for OAuth errors
    if error:
        logger.error(f"SSO error: {error} - {error_description}")
        return RedirectResponse(url=f"/login?error={error}", status_code=302)

    # Get form data for POST callback
    if request.method == "POST":
        form_data = await request.form()
        id_token = form_data.get("id_token")
        state = form_data.get("state")
        logger.debug(f"POST callback - form keys: {list(form_data.keys())}")
    else:
        logger.debug(f"GET callback - query params: {dict(request.query_params)}")

    if not id_token or not state:
        logger.error(f"Missing id_token or state in SSO callback. id_token present: {bool(id_token)}, state present: {bool(state)}")
        return RedirectResponse(url="/login?error=invalid_response", status_code=302)

    # Verify state (CSRF protection)
    session_record = db.query(UserSession).filter_by(session_id=state).first()
    if not session_record:
        logger.error(f"Invalid state in SSO callback: {state}")
        return RedirectResponse(url="/login?error=invalid_state", status_code=302)

    # Validate the ID token
    try:
        from .providers.entra import EntraIDProvider

        entra_provider = EntraIDProvider(
            tenant_id=settings.sso_tenant_id,
            client_id=settings.sso_client_id
        )

        token_data = await entra_provider.validate_token(id_token)

        if not token_data:
            logger.error("Invalid token from Entra ID")
            return RedirectResponse(url="/login?error=invalid_token", status_code=302)

        # Extract user information
        email = token_data.get("email") or token_data.get("preferred_username")
        name = token_data.get("name", email.split("@")[0] if email else "Unknown")

        if not email:
            logger.error("No email in token from Entra ID")
            return RedirectResponse(url="/login?error=no_email", status_code=302)

        # Find or create user
        user = db.query(User).filter_by(email=email).first()

        if not user:
            # Auto-create user on first SSO login (in hybrid mode)
            user = User(
                username=email.split("@")[0],
                email=email,
                full_name=name,
                is_active=True,
                is_admin=False,  # SSO users are not admins by default
                hashed_password=""  # No password for SSO users
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"Created new user from SSO: {email}")

        # Update last login
        user.last_login = utcnow()
        db.commit()

        logger.info(f"SSO login successful for: {email}")

        # Create JWT token for this session
        local_auth = LocalAuthProvider(
            jwt_secret=settings.local_auth_jwt_secret,
            jwt_expiry_hours=settings.local_auth_jwt_expiry_hours
        )

        token = local_auth.create_access_token(
            user_id=user.id,
            email=user.email,
            name=user.full_name or user.username,
            is_admin=user.is_admin
        )

        # Set HTTP-only cookie
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            key="fyr_session",
            value=token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=settings.local_auth_jwt_expiry_hours * 3600
        )

        # Clean up state session
        db.delete(session_record)
        db.commit()

        return response

    except Exception as e:
        logger.error(f"Error processing SSO callback: {e}", exc_info=True)
        return RedirectResponse(url="/login?error=processing_error", status_code=302)
