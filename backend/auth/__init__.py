"""
Auth package for Sahaara M2.
"""
from .models import User, TokenData
from .security import hash_password, verify_password, create_access_token, verify_access_token, revoke_access_token
from .deps import get_current_user, require_caregiver, require_person, require_profile_access
from .routes import router as auth_router

__all__ = [
    "User",
    "TokenData",
    "hash_password",
    "verify_password",
    "create_access_token",
    "verify_access_token",
    "revoke_access_token",
    "get_current_user",
    "require_caregiver",
    "require_person",
    "require_profile_access",
    "auth_router",
]