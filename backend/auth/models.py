"""
Auth models for Sahaara M2.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class User:
    """Authenticated user."""
    user_id: str
    email: str
    role: str  # "caregiver" or "person"
    profile_id: str
    is_active: bool = True
    created_at: float = 0
    last_login: Optional[float] = None
    
    @property
    def is_caregiver(self) -> bool:
        return self.role == "caregiver"
    
    @property
    def is_person(self) -> bool:
        return self.role == "person"


@dataclass
class TokenData:
    """Decoded token data."""
    user_id: str
    email: str
    role: str
    profile_id: str
    expires_at: float