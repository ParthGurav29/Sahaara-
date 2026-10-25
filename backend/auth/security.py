"""
Auth security utilities for Sahaara M2.
"""
import secrets
import hashlib
import bcrypt
from typing import Optional, Tuple

from database import (
    create_token as db_create_token,
    verify_token as db_verify_token,
    revoke_token as db_revoke_token,
    update_user_login,
)
from auth.models import User, TokenData


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


def create_access_token(user_id: str, expires_in_seconds: int = 30 * 86400) -> Tuple[str, float]:
    """
    Create a new access token for a user.
    Returns (raw_token, expires_at).
    """
    return db_create_token(user_id, expires_in_seconds)


def verify_access_token(token: str) -> Optional[User]:
    """
    Verify an access token and return the User if valid.
    Returns None if invalid, expired, or revoked.
    """
    token_data = db_verify_token(token)
    if token_data is None:
        return None
    
    # Update last_login
    update_user_login(token_data["user_id"])
    
    return User(
        user_id=token_data["user_id"],
        email=token_data["email"],
        role=token_data["role"],
        profile_id=token_data["profile_id"],
        is_active=bool(token_data["is_active"]),
    )


def revoke_access_token(token: str) -> bool:
    """Revoke an access token (logout)."""
    return db_revoke_token(token)


def generate_user_id() -> str:
    """Generate a new user ID."""
    return secrets.token_urlsafe(16)