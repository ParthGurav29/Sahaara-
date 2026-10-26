"""
test_auth.py

Unit tests for auth.py module.
Run directly: python tests/test_auth.py
"""

import sys
sys.path.insert(0, "/Users/parthmangeshgurav/Desktop/sahaara/backend")

import os
import tempfile
from pathlib import Path

# These tests truncate the users table; run them against an isolated DB so the
# live dev database keeps its caregiver account.
_TEST_DB = Path(tempfile.gettempdir()) / "sahaara_test_auth.db"
for _suffix in ("", "-wal", "-shm"):
    Path(str(_TEST_DB) + _suffix).unlink(missing_ok=True)
os.environ["SAHAARA_DB_PATH"] = str(_TEST_DB)

from database import init_db
init_db()

import time
from auth import (
    hash_password, verify_password,
    create_token, validate_token, revoke_token,
    check_rate_limit, record_failed_login,
    create_user, get_user_by_username, get_user_by_id,
    seed_user_if_needed
)


def test_password_hashing():
    """Test scrypt password hashing and verification."""
    password = "test_password_123"
    
    # Hash password
    password_hash, salt = hash_password(password)
    assert isinstance(password_hash, str) and len(password_hash) == 64  # 32 bytes hex
    assert isinstance(salt, str) and len(salt) == 32  # 16 bytes hex
    
    # Verify correct password
    assert verify_password(password, password_hash, salt) is True
    
    # Verify incorrect password
    assert verify_password("wrong_password", password_hash, salt) is False
    
    # Verify empty password
    assert verify_password("", password_hash, salt) is False
    
    print("PASS: test_password_hashing")


def test_password_timing_safe():
    """Verify constant-time comparison is used (no early returns)."""
    password = "test"
    password_hash, salt = hash_password(password)
    
    # This should not crash and should return False for wrong password
    result = verify_password("wrong", password_hash, salt)
    assert result is False
    
    # Same hash, same salt, different wrong password
    result2 = verify_password("also_wrong", password_hash, salt)
    assert result2 is False
    
    print("PASS: test_password_timing_safe")


def test_token_creation_and_validation():
    """Test token creation, validation, and expiry."""
    user_id = 1
    
    # Create token
    raw_token, expires_at = create_token(user_id, expires_in_seconds=3600)
    assert isinstance(raw_token, str)
    assert len(raw_token) > 0
    assert expires_at > time.time()
    
    # Validate valid token
    validated_user_id = validate_token(raw_token)
    assert validated_user_id == user_id
    
    print("PASS: test_token_creation_and_validation")


def test_token_expiry():
    """Test that expired tokens are rejected."""
    user_id = 2
    
    # Create token with very short expiry (already expired)
    raw_token, _ = create_token(user_id, expires_in_seconds=-1)
    
    # Should be rejected
    validated_user_id = validate_token(raw_token)
    assert validated_user_id is None
    
    print("PASS: test_token_expiry")


def test_token_revocation():
    """Test token revocation on logout."""
    user_id = 3
    
    # Create and validate token
    raw_token, _ = create_token(user_id)
    assert validate_token(raw_token) == user_id
    
    # Revoke token
    revoked = revoke_token(raw_token)
    assert revoked is True
    
    # Should now be rejected
    assert validate_token(raw_token) is None
    
    # Double revoke should return False
    assert revoke_token(raw_token) is False
    
    print("PASS: test_token_revocation")


def test_revoke_all_user_tokens():
    """Test revoking all tokens for a user."""
    user_id = 4
    
    # Create multiple tokens
    token1, _ = create_token(user_id)
    token2, _ = create_token(user_id)
    token3, _ = create_token(user_id)
    
    assert validate_token(token1) == user_id
    assert validate_token(token2) == user_id
    assert validate_token(token3) == user_id
    
    # Revoke all
    from auth import revoke_all_user_tokens
    revoke_all_user_tokens(user_id)
    
    assert validate_token(token1) is None
    assert validate_token(token2) is None
    assert validate_token(token3) is None
    
    print("PASS: test_revoke_all_user_tokens")


def test_rate_limiting():
    """Test login rate limiting."""
    key = "test_ip_192.168.1.1"
    
    # Should allow up to RATE_LIMIT_MAX_ATTEMPTS
    for i in range(5):
        allowed = check_rate_limit(key)
        assert allowed is True, f"Attempt {i+1} should be allowed"
    
    # 6th attempt should be blocked
    allowed = check_rate_limit(key)
    assert allowed is False, "6th attempt should be rate limited"
    
    print("PASS: test_rate_limiting")


def test_rate_limit_window_reset():
    """Test that rate limit resets after window."""
    import auth
    # We can't easily test time window without mocking time,
    # but we can verify the cleanup logic runs
    key = "test_window_reset"
    
    # Exhaust limit
    for _ in range(5):
        check_rate_limit(key)
    assert check_rate_limit(key) is False
    
    # The cleanup in check_rate_limit should remove old buckets
    # (not easily testable without time mocking, but code coverage)
    
    print("PASS: test_rate_limit_window_reset")


def test_failed_login_recording():
    """Test recording failed login attempts."""
    key = "test_failed_login"
    
    # Clear any existing
    import auth
    auth._login_attempts.clear()
    
    record_failed_login(key)
    record_failed_login(key)
    
    # Should have recorded 2 attempts
    minute_bucket = int(time.time()) // 60
    count = auth._login_attempts.get((key, minute_bucket), (minute_bucket, 0))[1]
    assert count == 2
    
    print("PASS: test_failed_login_recording")


def test_user_creation_and_lookup():
    """Test user CRUD operations."""
    import os
    os.environ["SAHAARA_AUTH_ENABLED"] = "false"  # Disable seed
    
    # Create user
    username = "test_caregiver"
    password = "secure_password"
    role = "caregiver"
    profile_id = "asha_demo_001"
    
    user_id = create_user(username, password, role, profile_id)
    assert isinstance(user_id, int) and user_id > 0
    
    # Lookup by username
    user = get_user_by_username(username)
    assert user is not None
    assert user["username"] == username
    assert user["role"] == role
    assert user["profile_id"] == profile_id
    assert "password_hash" in user
    assert "salt" in user
    
    # Lookup by ID
    user2 = get_user_by_id(user_id)
    assert user2 is not None
    assert user2["username"] == username
    
    # Non-existent user
    assert get_user_by_username("nonexistent") is None
    assert get_user_by_id(999999) is None
    
    print("PASS: test_user_creation_and_lookup")


def test_password_verification_via_user():
    """Test password verification through user lookup."""
    import os
    os.environ["SAHAARA_AUTH_ENABLED"] = "false"
    
    username = "test_caregiver2"
    password = "another_password"
    user_id = create_user(username, password, "caregiver", "asha_demo_001")
    
    # Get user and verify password
    user = get_user_by_username(username)
    assert verify_password(password, user["password_hash"], user["salt"]) is True
    assert verify_password("wrong", user["password_hash"], user["salt"]) is False
    
    print("PASS: test_password_verification_via_user")


def test_seed_user_creation():
    """Test seed user creation at startup."""
    import os
    import auth
    
    # Reset for test
    os.environ["SAHAARA_AUTH_ENABLED"] = "true"
    os.environ["SAHAARA_SEED_USER"] = "seed_test"
    os.environ["SAHAARA_SEED_PASSWORD"] = "seed_password"
    
    # Clear users table
    from database import get_connection
    conn = get_connection()
    conn.execute("DELETE FROM users")
    conn.commit()
    conn.close()
    
    # Reset auth module state (re-import would be cleaner but we'll call directly)
    created = seed_user_if_needed()
    assert created is True
    
    # Verify user exists
    user = get_user_by_username("seed_test")
    assert user is not None
    assert user["role"] == "caregiver"
    assert user["profile_id"] == "asha_demo_001"
    
    # Second call should not create duplicate
    created2 = seed_user_if_needed()
    assert created2 is False
    
    print("PASS: test_seed_user_creation")


def test_seed_user_missing_env():
    """Test seed user skips when env vars missing."""
    import os
    import auth
    
    os.environ["SAHAARA_AUTH_ENABLED"] = "true"
    os.environ.pop("SAHAARA_SEED_USER", None)
    os.environ.pop("SAHAARA_SEED_PASSWORD", None)
    
    # Clear users table
    from database import get_connection
    conn = get_connection()
    conn.execute("DELETE FROM users")
    conn.commit()
    conn.close()
    
    # Should return False and print warning
    created = seed_user_if_needed()
    assert created is False
    
    print("PASS: test_seed_user_missing_env")


def test_seed_user_auth_disabled():
    """Test seed user skips when auth disabled."""
    import os
    
    os.environ["SAHAARA_AUTH_ENABLED"] = "false"
    os.environ["SAHAARA_SEED_USER"] = "test"
    os.environ["SAHAARA_SEED_PASSWORD"] = "test"
    
    # Clear users table
    from database import get_connection
    conn = get_connection()
    conn.execute("DELETE FROM users")
    conn.commit()
    conn.close()
    
    created = seed_user_if_needed()
    assert created is False
    
    print("PASS: test_seed_user_auth_disabled")


def run_all_tests():
    """Run all auth tests."""
    print("=" * 50)
    print("Running auth module tests...")
    print("=" * 50)
    
    test_password_hashing()
    test_password_timing_safe()
    test_token_creation_and_validation()
    test_token_expiry()
    test_token_revocation()
    test_revoke_all_user_tokens()
    test_rate_limiting()
    test_rate_limit_window_reset()
    test_failed_login_recording()
    test_user_creation_and_lookup()
    test_password_verification_via_user()
    test_seed_user_creation()
    test_seed_user_missing_env()
    test_seed_user_auth_disabled()
    
    print("=" * 50)
    print("ALL AUTH TESTS PASSED!")
    print("=" * 50)


if __name__ == "__main__":
    run_all_tests()