"""Authentication module for Project Fyr."""

from .base import AuthProvider
from .local_auth import LocalAuthProvider
from .middleware import AuthenticationMiddleware
from .models import User, UserSession
from .endpoints import router as auth_router
from .utils import hash_password, verify_password, generate_secret

__all__ = [
    "AuthProvider",
    "LocalAuthProvider",
    "AuthenticationMiddleware",
    "User",
    "UserSession",
    "auth_router",
    "hash_password",
    "verify_password",
    "generate_secret",
]
