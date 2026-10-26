"""
auth.py

Authentication module for Sahaara M2.
Provides password hashing, token management, rate limiting, and seed user creation.
Uses only Python stdlib: hashlib, secrets, hmac, time.
"""

import hashlib
import hmac
import secrets
import time
import threading
from typing import Optional, Tuple

from database import get_connection, init_db


# ---- Password hashing (scrypt) ----

SCRYPT_N = 16384  # CPU/memory cost parameter
SCRYPT_R = 8      # Block size
SCRYPT_P = 1      # Parallelization
SCRYPT_DKLEN = 32  # Derived key length (bytes)
SALT_LENGTH = 16   # Salt length (bytes)

def hash_password(password: str) -> Tuple[str, str]:
    """
    Hash a password using scrypt.
    Returns (password_hash, salt) as hex strings.
    """
    salt = secrets.token_bytes(SALT_LENGTH)
    dk = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN
    )
    return dk.hex(), salt.hex()


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """
    Verify a password against its hash using constant-time comparison.
    Returns True if password matches.
    """
    salt_bytes = bytes.fromhex(salt)
    expected_hash = bytes.fromhex(password_hash)
    
    dk = hashlib.scrypt(
        password.encode(),
        salt=salt_bytes,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN
    )
    
    return hmac.compare_digest(dk, expected_hash)


# ---- Token management ----

TOKEN_BYTES = 32
TOKEN_EXPIRY_SECONDS = 12 * 3600  # 12 hours

def _hash_token(token: str) -> str:
    """SHA256 hash of token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(user_id: int, expires_in_seconds: int = TOKEN_EXPIRY_SECONDS) -> Tuple[str, float]:
    """
    Create a new auth token for a user.
    Returns (raw_token, expires_at).
    Token is URL-safe random string; only its SHA256 hash is stored.
    """
    raw_token = secrets.token_urlsafe(TOKEN_BYTES)
    token_hash = _hash_token(raw_token)
    now = time.time()
    expires_at = now + expires_in_seconds
    
    conn = get_connection()
    conn.execute(
        """INSERT INTO auth_tokens (token_hash, user_id, created_at, expires_at, revoked)
           VALUES (?, ?, ?, ?, 0)""",
        (token_hash, user_id, now, expires_at)
    )
    conn.commit()
    conn.close()
    return raw_token, expires_at


def validate_token(token: str) -> Optional[int]:
    """
    Validate a token and return the user_id if valid.
    Returns None if invalid, expired, or revoked.
    """
    token_hash = _hash_token(token)
    conn = get_connection()
    row = conn.execute(
        """SELECT user_id FROM auth_tokens
           WHERE token_hash = ? AND revoked = 0 AND expires_at > ?""",
        (token_hash, time.time())
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return row["user_id"]


def validate_token_and_get_user(token: str) -> Optional[dict]:
    """
    Validate a token and return the full user info if valid.
    Returns None if invalid, expired, or revoked.
    """
    user_id = validate_token(token)
    if user_id is None:
        return None
    return get_user_by_id(user_id)


def revoke_token(token: str) -> bool:
    """Revoke a token (logout). Returns True if token was found and revoked."""
    token_hash = _hash_token(token)
    conn = get_connection()
    cursor = conn.execute(
        "UPDATE auth_tokens SET revoked = 1 WHERE token_hash = ? AND revoked = 0",
        (token_hash,)
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def revoke_all_user_tokens(user_id: int):
    """Revoke all tokens for a user (e.g., on password change)."""
    conn = get_connection()
    conn.execute("UPDATE auth_tokens SET revoked = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# ---- Rate limiting (simple in-memory) ----

# (ip_or_username, minute_bucket) -> count
_login_attempts = {}
_login_lock = threading.Lock()

RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_ATTEMPTS = 5

def check_rate_limit(key: str) -> bool:
    """
    Check if key has exceeded rate limit.
    Returns True if allowed, False if rate limited.
    """
    now = int(time.time())
    minute_bucket = now // RATE_LIMIT_WINDOW
    
    with _login_lock:
        # Clean old buckets
        keys_to_delete = [
            k for k, v in _login_attempts.items()
            if v[0] < minute_bucket - 1
        ]
        for k in keys_to_delete:
            del _login_attempts[k]
        
        bucket_key = (key, minute_bucket)
        count = _login_attempts.get(bucket_key, (minute_bucket, 0))[1]
        
        if count >= RATE_LIMIT_MAX_ATTEMPTS:
            return False
        
        _login_attempts[bucket_key] = (minute_bucket, count + 1)
        return True


def record_failed_login(key: str):
    """Record a failed login attempt for rate limiting."""
    check_rate_limit(key)  # This will increment the counter


# ---- User management ----

def create_user(username: str, password: str, role: str, profile_id: str) -> int:
    """
    Create a new user.
    Returns the user_id.
    """
    password_hash, salt = hash_password(password)
    conn = get_connection()
    now = time.time()
    cursor = conn.execute(
        """INSERT INTO users (username, password_hash, salt, role, profile_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (username, password_hash, salt, role, profile_id, now)
    )
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()
    return user_id


def get_user_by_username(username: str) -> Optional[dict]:
    """Get user by username (for login)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def get_user_by_id(user_id: int) -> Optional[dict]:
    """Get user by ID."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


# ---- Seed user ----

def seed_user_if_needed():
    """
    Create seed user at startup if SAHAARA_AUTH_ENABLED is true and no users exist.
    Uses env vars SAHAARA_SEED_USER / SAHAARA_SEED_PASSWORD.
    If vars missing, logs warning and returns False.
    Returns True if user created, False otherwise.
    """
    import os
    
    # Check if auth is enabled
    auth_enabled = os.environ.get("SAHAARA_AUTH_ENABLED", "false").lower() == "true"
    if not auth_enabled:
        return False
    
    # Check if any users exist
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    
    if count > 0:
        return False  # Users already exist
    
    # Get seed credentials from env
    seed_user = os.environ.get("SAHAARA_SEED_USER")
    seed_password = os.environ.get("SAHAARA_SEED_PASSWORD")
    
    if not seed_user or not seed_password:
        print("WARNING: SAHAARA_AUTH_ENABLED=true but SAHAARA_SEED_USER or SAHAARA_SEED_PASSWORD not set. Skipping seed user creation.")
        return False
    
    # Create seed user
    user_id = create_user(seed_user, seed_password, "caregiver", "asha_demo_001")
    print(f"Created seed user: {seed_user} (user_id={user_id})")
    return True


def auth_enabled() -> bool:
    """Check if auth feature flag is enabled."""
    import os
    return os.environ.get("SAHAARA_AUTH_ENABLED", "false").lower() == "true"


# ---- Initialize on import ----

# Ensure DB tables exist
init_db()
# Try to seed user if configured
seed_user_if_needed()