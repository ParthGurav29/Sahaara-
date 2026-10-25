"""
Auth dependencies for Sahaara M2.
"""
from typing import Optional
from fastapi import Depends, HTTPException, Header, Cookie, WebSocketException, Query, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from auth.security import verify_access_token
from auth.models import User


# HTTP Bearer scheme for REST endpoints
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    token_cookie: Optional[str] = Cookie(None, alias="auth_token"),
) -> User:
    """
    Get the current authenticated user from either Authorization header or cookie.
    Raises 401 if not authenticated.
    """
    token = None
    if credentials:
        token = credentials.credentials
    elif token_cookie:
        token = token_cookie
    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user = verify_access_token(token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return user


async def get_current_user_ws(
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
) -> User:
    """
    Get the current authenticated user for WebSocket connections.
    Token can be passed as query parameter or Authorization header.
    """
    ws_token = token
    if not ws_token and authorization and authorization.startswith("Bearer "):
        ws_token = authorization[7:]
    
    if not ws_token:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Not authenticated")
    
    user = verify_access_token(ws_token)
    if user is None:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired token")
    
    return user


def require_caregiver(user: User = Depends(get_current_user)) -> User:
    """Require caregiver role."""
    if user.role != "caregiver":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Caregiver role required",
        )
    return user


def require_person(user: User = Depends(get_current_user)) -> User:
    """Require person (Asha) role."""
    if user.role != "person":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Person role required",
        )
    return user


def require_caregiver_ws(user: User = Depends(get_current_user_ws)) -> User:
    """Require caregiver role for WebSocket."""
    if user.role != "caregiver":
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Caregiver role required")
    return user


def require_person_ws(user: User = Depends(get_current_user_ws)) -> User:
    """Require person role for WebSocket."""
    if user.role != "person":
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Person role required")
    return user


def require_profile_access(profile_id: str, user: User = Depends(get_current_user)) -> User:
    """Require that the user has access to the given profile."""
    if user.profile_id != profile_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this profile",
        )
    return user


def require_profile_access_ws(profile_id: str, user: User = Depends(get_current_user_ws)) -> User:
    """Require profile access for WebSocket."""
    if user.profile_id != profile_id:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Access denied to this profile")
    return user


# Dependency that extracts profile_id from path and checks access
def profile_access_dep(user: User = Depends(get_current_user)) -> User:
    """Dependency that checks profile access based on the path parameter.
    This is used as a second dependency after the path parameter is extracted."""
    # The actual check happens in the route handler using the profile_id parameter
    return user


def profile_access_ws_dep(user: User = Depends(get_current_user_ws)) -> User:
    """WebSocket version of profile_access_dep."""
    return user