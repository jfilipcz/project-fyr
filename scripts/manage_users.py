#!/usr/bin/env python3
"""CLI tool for managing Fyr authentication users."""

import sys
import argparse
from getpass import getpass
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from project_fyr.config import settings
from project_fyr.auth.models import User
from project_fyr.auth.utils import hash_password
from project_fyr.db import Base


def init_db():
    """Initialize database connection."""
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    return engine


def create_user(engine, username: str, email: str, password: str, full_name: str = None, is_admin: bool = False):
    """Create a new user."""
    with Session(engine) as session:
        # Check if user already exists
        stmt = select(User).where(User.username == username)
        existing_user = session.scalars(stmt).first()
        
        if existing_user:
            print(f"Error: User '{username}' already exists")
            return False
        
        # Check if email already exists
        stmt = select(User).where(User.email == email)
        existing_email = session.scalars(stmt).first()
        
        if existing_email:
            print(f"Error: Email '{email}' is already registered")
            return False
        
        # Create new user
        user = User(
            username=username,
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name or username,
            is_active=True,
            is_admin=is_admin,
            created_at=datetime.utcnow()
        )
        
        session.add(user)
        session.commit()
        
        print(f"✓ User '{username}' created successfully")
        if is_admin:
            print("  Admin privileges: YES")
        return True


def list_users(engine):
    """List all users."""
    with Session(engine) as session:
        stmt = select(User).order_by(User.created_at)
        users = session.scalars(stmt).all()
        
        if not users:
            print("No users found")
            return
        
        print(f"\n{'ID':<5} {'Username':<20} {'Email':<30} {'Admin':<8} {'Active':<8} {'Created'}")
        print("-" * 100)
        
        for user in users:
            print(f"{user.id:<5} {user.username:<20} {user.email:<30} "
                  f"{'Yes' if user.is_admin else 'No':<8} "
                  f"{'Yes' if user.is_active else 'No':<8} "
                  f"{user.created_at.strftime('%Y-%m-%d %H:%M')}")


def delete_user(engine, username: str):
    """Delete a user."""
    with Session(engine) as session:
        stmt = select(User).where(User.username == username)
        user = session.scalars(stmt).first()
        
        if not user:
            print(f"Error: User '{username}' not found")
            return False
        
        session.delete(user)
        session.commit()
        
        print(f"✓ User '{username}' deleted successfully")
        return True


def change_password(engine, username: str, new_password: str):
    """Change user password."""
    with Session(engine) as session:
        stmt = select(User).where(User.username == username)
        user = session.scalars(stmt).first()
        
        if not user:
            print(f"Error: User '{username}' not found")
            return False
        
        user.hashed_password = hash_password(new_password)
        session.commit()
        
        print(f"✓ Password changed for user '{username}'")
        return True


def toggle_admin(engine, username: str):
    """Toggle admin status for a user."""
    with Session(engine) as session:
        stmt = select(User).where(User.username == username)
        user = session.scalars(stmt).first()
        
        if not user:
            print(f"Error: User '{username}' not found")
            return False
        
        user.is_admin = not user.is_admin
        session.commit()
        
        status = "granted" if user.is_admin else "revoked"
        print(f"✓ Admin privileges {status} for user '{username}'")
        return True


def main():
    parser = argparse.ArgumentParser(description="Manage Fyr authentication users")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Create user command
    create_parser = subparsers.add_parser("create", help="Create a new user")
    create_parser.add_argument("username", help="Username")
    create_parser.add_argument("email", help="Email address")
    create_parser.add_argument("--password", help="Password (will prompt if not provided)")
    create_parser.add_argument("--name", help="Full name")
    create_parser.add_argument("--admin", action="store_true", help="Grant admin privileges")
    
    # List users command
    subparsers.add_parser("list", help="List all users")
    
    # Delete user command
    delete_parser = subparsers.add_parser("delete", help="Delete a user")
    delete_parser.add_argument("username", help="Username to delete")
    
    # Change password command
    passwd_parser = subparsers.add_parser("passwd", help="Change user password")
    passwd_parser.add_argument("username", help="Username")
    passwd_parser.add_argument("--password", help="New password (will prompt if not provided)")
    
    # Toggle admin command
    admin_parser = subparsers.add_parser("toggle-admin", help="Toggle admin privileges")
    admin_parser.add_argument("username", help="Username")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Initialize database
    engine = init_db()
    
    # Execute command
    if args.command == "create":
        password = args.password
        if not password:
            password = getpass("Password: ")
            password_confirm = getpass("Confirm password: ")
            if password != password_confirm:
                print("Error: Passwords do not match")
                sys.exit(1)
        
        create_user(
            engine,
            args.username,
            args.email,
            password,
            args.name,
            args.admin
        )
    
    elif args.command == "list":
        list_users(engine)
    
    elif args.command == "delete":
        confirm = input(f"Delete user '{args.username}'? (yes/no): ")
        if confirm.lower() == "yes":
            delete_user(engine, args.username)
        else:
            print("Cancelled")
    
    elif args.command == "passwd":
        password = args.password
        if not password:
            password = getpass("New password: ")
            password_confirm = getpass("Confirm password: ")
            if password != password_confirm:
                print("Error: Passwords do not match")
                sys.exit(1)
        
        change_password(engine, args.username, password)
    
    elif args.command == "toggle-admin":
        toggle_admin(engine, args.username)


if __name__ == "__main__":
    main()
