# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Local username/password authentication provider."""

import logging
from typing import Optional, Dict
from datetime import timedelta
from project_fyr import utcnow

import jwt
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .base import AuthProvider
from .models import User
from .utils import verify_password

logger = logging.getLogger(__name__)


class LocalAuthProvider(AuthProvider):
    """Local authentication provider using username/password and JWT tokens."""

    def __init__(self, jwt_secret: str, jwt_expiry_hours: int = 24):
        """
        Initialize local auth provider.

        Args:
            jwt_secret: Secret key for JWT token signing
            jwt_expiry_hours: Token expiry time in hours
        """
        if not jwt_secret or jwt_secret == "change-me-in-production":
            raise ValueError(
                "JWT secret is not configured. Set PROJECT_FYR_LOCAL_AUTH_JWT_SECRET "
                "to a strong random value (e.g. `python -c 'import secrets; print(secrets.token_urlsafe(32))'`)"
            )

        self.jwt_secret = jwt_secret
        self.jwt_expiry_hours = jwt_expiry_hours
        logger.info("Initialized local authentication provider")

    def create_access_token(self, user_id: int, email: str, name: str, is_admin: bool = False) -> str:
        """
        Create a JWT access token for a user.

        Args:
            user_id: User's database ID
            email: User's email
            name: User's full name
            is_admin: Whether user has admin privileges

        Returns:
            JWT token string
        """
        expires = utcnow() + timedelta(hours=self.jwt_expiry_hours)
        payload = {
            "user_id": user_id,
            "email": email,
            "name": name,
            "is_admin": is_admin,
            "exp": expires,
            "iat": utcnow(),
            "provider": "local"
        }

        token = jwt.encode(payload, self.jwt_secret, algorithm="HS256")
        logger.debug(f"Created access token for user {email}")
        return token

    async def validate_token(self, token: str) -> Dict:
        """
        Validate a local JWT token.

        Args:
            token: JWT token to validate

        Returns:
            Dict with user information

        Raises:
            HTTPException: If token validation fails
        """
        try:
            payload = jwt.decode(
                token,
                self.jwt_secret,
                algorithms=["HS256"],
                options={"verify_signature": True, "verify_exp": True}
            )

            user_info = {
                "user_id": payload.get("user_id"),
                "email": payload.get("email"),
                "name": payload.get("name"),
                "is_admin": payload.get("is_admin", False),
                "roles": ["admin"] if payload.get("is_admin") else ["user"],
                "provider": "local"
            }

            logger.debug(f"Successfully validated token for user {user_info.get('email')}")
            return user_info

        except jwt.ExpiredSignatureError:
            logger.warning("Token has expired")
            raise HTTPException(status_code=401, detail="Token has expired")
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {str(e)}")
            raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
        except Exception as e:
            logger.error(f"Token validation failed: {str(e)}", exc_info=True)
            raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")

    def authenticate_user(self, session: Session, username: str, password: str) -> Optional[User]:
        """
        Authenticate a user with username and password.

        Args:
            session: Database session
            username: Username
            password: Plain text password

        Returns:
            User object if authentication succeeds, None otherwise
        """
        try:
            stmt = select(User).where(User.username == username, User.is_active.is_(True))
            user = session.scalars(stmt).first()

            if not user:
                logger.warning(f"User not found: {username}")
                return None

            if not verify_password(password, user.hashed_password):
                logger.warning(f"Invalid password for user: {username}")
                return None

            logger.info(f"Successfully authenticated user: {username}")
            return user

        except Exception as e:
            logger.error(f"Authentication error: {str(e)}", exc_info=True)
            return None

    def get_provider_name(self) -> str:
        """Return the provider name."""
        return "local"
