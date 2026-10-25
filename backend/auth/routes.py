"""
Auth routes for Sahaara M2.
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status, Cookie
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional

from database import get_user_by_email, create_user, get_users_by_profile
from auth.security import verify_password, create_access_token, revoke_access_token, hash_password, generate_user_id
from auth.models import User
from auth.deps import get_current_user, require_caregiver, require_profile_access

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: dict


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    role: str  # "caregiver" or "person"
    profile_id: str


class RegisterResponse(BaseModel):
    user_id: str
    email: str
    role: str
    profile_id: str


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, response: Response):
    """
    Login with email and password.
    Sets HttpOnly cookie and returns token in body.
    """
    user_data = get_user_by_email(request.email)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    
    if not verify_password(request.password, user_data["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    
    if not user_data["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account deactivated",
        )
    
    # Create token (30 days)
    expires_in = 30 * 86400
    token, expires_at = create_access_token(user_data["user_id"], expires_in)
    
    # Set HttpOnly cookie
    response.set_cookie(
        key="auth_token",
        value=token,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
        max_age=expires_in,
        path="/",
    )
    
    return LoginResponse(
        access_token=token,
        expires_in=expires_in,
        user={
            "user_id": user_data["user_id"],
            "email": user_data["email"],
            "role": user_data["role"],
            "profile_id": user_data["profile_id"],
        },
    )


@router.post("/logout")
async def logout(
    response: Response,
    token: Optional[str] = Cookie(None, alias="auth_token"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
):
    """Logout - revoke token and clear cookie.
    Accepts token from either cookie or Authorization header.
    """
    # Try to get token from cookie or header
    revoked_token = token
    if not revoked_token and credentials:
        revoked_token = credentials.credentials
    
    if revoked_token:
        revoke_access_token(revoked_token)
    
    response.delete_cookie(key="auth_token", path="/")
    return {"message": "Logged out successfully"}


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(request: RegisterRequest):
    """
    Register a new user.
    In production, this should be invite-only or admin-only.
    """
    # Validate role
    if request.role not in ("caregiver", "person"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be 'caregiver' or 'person'",
        )
    
    # Check if email already exists
    existing = get_user_by_email(request.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    
    # Create user
    user_id = generate_user_id()
    password_hash = hash_password(request.password)
    
    user = create_user(
        user_id=user_id,
        email=request.email,
        password_hash=password_hash,
        role=request.role,
        profile_id=request.profile_id,
    )
    
    return RegisterResponse(**user)


@router.get("/me")
async def get_me(user: User = Depends(get_current_user)):
    """Get current user info."""
    return {
        "user_id": user.user_id,
        "email": user.email,
        "role": user.role,
        "profile_id": user.profile_id,
        "is_active": user.is_active,
    }


@router.get("/users/{profile_id}")
async def list_users_for_profile(
    profile_id: str,
    user: User = Depends(require_caregiver),
    _: User = Depends(require_profile_access),  # profile_id comes from path param
):
    """List all users for a profile (caregiver only)."""
    users = get_users_by_profile(profile_id)
    # Don't return password hashes
    return [
        {
            "user_id": u["user_id"],
            "email": u["email"],
            "role": u["role"],
            "profile_id": u["profile_id"],
            "is_active": bool(u["is_active"]),
            "created_at": u["created_at"],
            "last_login": u["last_login"],
        }
        for u in users
    ]