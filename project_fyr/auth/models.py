"""Database models for authentication."""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

from ..db import Base


class User(Base):
    """Local user account."""
    
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    username = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255))
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login = Column(DateTime, nullable=True)


class UserSession(Base):
    """User session tracking and OAuth state storage."""
    
    __tablename__ = "user_sessions"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True, index=True)  # Nullable for OAuth state
    session_id = Column(String(255), unique=True, nullable=True, index=True)  # For OAuth state
    session_token = Column(String(255), unique=True, nullable=True, index=True)
    provider = Column(String(50), nullable=True)  # 'local', 'entra', etc.
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    user_agent = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
