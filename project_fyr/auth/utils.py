"""Authentication utility functions."""

import secrets
import bcrypt


def hash_password(password: str) -> str:
    """Hash a password using bcrypt. Truncate if longer than 72 bytes."""
    # BCrypt has a 72-byte password limit
    password_bytes = password.encode('utf-8')[:72]
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed.decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash."""
    password_bytes = plain_password.encode('utf-8')[:72]
    hashed_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_bytes, hashed_bytes)


def generate_secret(length: int = 32) -> str:
    """Generate a secure random secret."""
    return secrets.token_urlsafe(length)
