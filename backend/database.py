"""
database.py

SQLite database for Sahaara — replaces flat JSON files.
Handles profiles, events, session summaries, suggestions, users, auth tokens,
retention policies, and memory box clips.

Migration: loads existing asha_profile.json on first run.
"""

import hashlib
import json
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "sahaara.db"

# Schema version for migrations
SCHEMA_VERSION = 2


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create tables and migrate existing JSON data. Handles schema migrations."""
    conn = get_connection()
    
    # Check current schema version
    cursor = conn.execute("PRAGMA user_version")
    current_version = cursor.fetchone()[0]
    
    if current_version < 1:
        # Fresh install - create all tables
        _create_all_tables(conn)
        conn.execute("PRAGMA user_version = 1")
    elif current_version == 1:
        # Migration from v1 to v2 - add auth/retention/clip tables
        _migrate_v1_to_v2(conn)
        conn.execute("PRAGMA user_version = 2")
    
    conn.commit()
    
    # Migrate existing JSON profile if DB is empty
    cursor = conn.execute("SELECT COUNT(*) FROM profiles")
    if cursor.fetchone()[0] == 0:
        _migrate_profile(conn)
    
    # Ensure default retention policy for existing profiles
    _ensure_default_retention_policies(conn)
    
    conn.commit()
    conn.close()


def _create_all_tables(conn: sqlite3.Connection):
    """Create all tables for fresh install (schema v2)."""
    conn.executescript("""
        -- Core tables
        CREATE TABLE IF NOT EXISTS profiles (
            profile_id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            data JSON NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            type TEXT NOT NULL,
            detail JSON NOT NULL DEFAULT '{}',
            FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
        );
        
        CREATE TABLE IF NOT EXISTS session_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            date TEXT NOT NULL,
            summary JSON NOT NULL DEFAULT '{}',
            FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
        );
        
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            data JSON NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL,
            FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
        );
        
        -- Auth tables (M2)
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('caregiver', 'person')),
            profile_id TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at REAL NOT NULL,
            last_login REAL,
            FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
        );
        
        CREATE TABLE IF NOT EXISTS auth_tokens (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            expires_at REAL NOT NULL,
            revoked INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        
        -- Retention policies (M2)
        CREATE TABLE IF NOT EXISTS retention_policies (
            profile_id TEXT PRIMARY KEY,
            retention_days INTEGER NOT NULL DEFAULT 90,
            auto_cleanup INTEGER DEFAULT 1,
            last_cleanup_at REAL,
            FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
        );
        
        -- Memory box clips (M2)
        CREATE TABLE IF NOT EXISTS memory_box_clips (
            clip_id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            family_member_name TEXT NOT NULL,
            relationship TEXT NOT NULL,
            filename TEXT NOT NULL,
            mime_type TEXT DEFAULT 'audio/mpeg',
            size_bytes INTEGER,
            duration_seconds REAL,
            created_at REAL NOT NULL,
            uploaded_by TEXT,
            FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
        );
        
        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_events_profile ON events(profile_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(profile_id, type);
        CREATE INDEX IF NOT EXISTS idx_suggestions_profile ON suggestions(profile_id, status);
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
        CREATE INDEX IF NOT EXISTS idx_users_profile ON users(profile_id);
        CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id);
        CREATE INDEX IF NOT EXISTS idx_auth_tokens_expires ON auth_tokens(expires_at);
        CREATE INDEX IF NOT EXISTS idx_clips_profile ON memory_box_clips(profile_id);
    """)


def _migrate_v1_to_v2(conn: sqlite3.Connection):
    """Migrate from schema v1 to v2 - add auth, retention, clip tables."""
    conn.executescript("""
        -- Auth tables
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('caregiver', 'person')),
            profile_id TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at REAL NOT NULL,
            last_login REAL,
            FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
        );
        
        CREATE TABLE IF NOT EXISTS auth_tokens (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            expires_at REAL NOT NULL,
            revoked INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        
        -- Retention policies
        CREATE TABLE IF NOT EXISTS retention_policies (
            profile_id TEXT PRIMARY KEY,
            retention_days INTEGER NOT NULL DEFAULT 90,
            auto_cleanup INTEGER DEFAULT 1,
            last_cleanup_at REAL,
            FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
        );
        
        -- Memory box clips
        CREATE TABLE IF NOT EXISTS memory_box_clips (
            clip_id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            family_member_name TEXT NOT NULL,
            relationship TEXT NOT NULL,
            filename TEXT NOT NULL,
            mime_type TEXT DEFAULT 'audio/mpeg',
            size_bytes INTEGER,
            duration_seconds REAL,
            created_at REAL NOT NULL,
            uploaded_by TEXT,
            FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
        );
        
        -- New indexes
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
        CREATE INDEX IF NOT EXISTS idx_users_profile ON users(profile_id);
        CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id);
        CREATE INDEX IF NOT EXISTS idx_auth_tokens_expires ON auth_tokens(expires_at);
        CREATE INDEX IF NOT EXISTS idx_clips_profile ON memory_box_clips(profile_id);
    """)


def _ensure_default_retention_policies(conn: sqlite3.Connection):
    """Ensure all profiles have a retention policy (default 90 days)."""
    profiles = conn.execute("SELECT profile_id FROM profiles").fetchall()
    now = time.time()
    for row in profiles:
        profile_id = row["profile_id"]
        exists = conn.execute(
            "SELECT 1 FROM retention_policies WHERE profile_id = ?", (profile_id,)
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO retention_policies (profile_id, retention_days, auto_cleanup, last_cleanup_at) VALUES (?, 90, 1, ?)",
                (profile_id, now)
            )


def _migrate_profile(conn: sqlite3.Connection):
    """Load asha_profile.json into the database."""
    profile_path = Path(__file__).parent / "memory" / "data" / "asha_profile.json"
    if not profile_path.exists():
        return
    
    with open(profile_path) as f:
        data = json.load(f)
    
    profile_id = data.get("profile_id", "asha_demo_001")
    now = time.time()
    
    conn.execute(
        "INSERT INTO profiles (profile_id, name, data, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (profile_id, data.get("name", {}).get("preferred_address", "Asha"), json.dumps(data), now, now)
    )
    conn.commit()


# ---- Profile CRUD ----

def get_profile(profile_id: str) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM profiles WHERE profile_id = ?", (profile_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return json.loads(row["data"])


def list_profiles() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT profile_id, name, updated_at FROM profiles ORDER BY name").fetchall()
    conn.close()
    return [{"profile_id": r["profile_id"], "name": r["name"], "updated_at": r["updated_at"]} for r in rows]


def save_profile(profile_id: str, data: dict):
    conn = get_connection()
    name = data.get("name", {}).get("preferred_address", profile_id)
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO profiles (profile_id, name, data, created_at, updated_at) VALUES (?, ?, ?, COALESCE((SELECT created_at FROM profiles WHERE profile_id = ?), ?), ?)",
        (profile_id, name, json.dumps(data), profile_id, now, now)
    )
    conn.commit()
    conn.close()


# ---- Events ----

def append_event(profile_id: str, event_type: str, detail: dict, timestamp: float = None):
    conn = get_connection()
    conn.execute(
        "INSERT INTO events (profile_id, timestamp, type, detail) VALUES (?, ?, ?, ?)",
        (profile_id, timestamp or time.time(), event_type, json.dumps(detail))
    )
    conn.commit()
    conn.close()


def get_events(profile_id: str, event_type: str = None, since: float = None, limit: int = 200) -> list[dict]:
    conn = get_connection()
    query = "SELECT * FROM events WHERE profile_id = ?"
    params = [profile_id]
    
    if event_type:
        query += " AND type = ?"
        params.append(event_type)
    if since:
        query += " AND timestamp >= ?"
        params.append(since)
    
    query += " ORDER BY timestamp ASC LIMIT ?"
    params.append(limit)
    
    rows = conn.execute(query, params).fetchall()
    conn.close()
    
    return [
        {
            "timestamp": r["timestamp"],
            "timestamp_iso": datetime.fromtimestamp(r["timestamp"]).isoformat(),
            "type": r["type"],
            "profile_id": r["profile_id"],
            "detail": json.loads(r["detail"]),
        }
        for r in rows
    ]


def get_all_events(profile_id: str) -> list[dict]:
    return get_events(profile_id, limit=5000)


def clear_events(profile_id: str):
    conn = get_connection()
    conn.execute("DELETE FROM events WHERE profile_id = ?", (profile_id,))
    conn.commit()
    conn.close()


# ---- Session summaries ----

def save_session_summary(profile_id: str, summary: dict):
    conn = get_connection()
    conn.execute(
        "INSERT INTO session_summaries (profile_id, date, summary) VALUES (?, ?, ?)",
        (profile_id, summary.get("date", datetime.now().isoformat()), json.dumps(summary))
    )
    conn.commit()
    conn.close()


def get_recent_summaries(profile_id: str, limit: int = 3) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT summary FROM session_summaries WHERE profile_id = ? ORDER BY date DESC LIMIT ?",
        (profile_id, limit)
    ).fetchall()
    conn.close()
    return [json.loads(r["summary"]) for r in reversed(rows)]


# ---- Suggestions ----

def save_suggestion(profile_id: str, data: dict):
    conn = get_connection()
    conn.execute(
        "INSERT INTO suggestions (profile_id, data, status, created_at) VALUES (?, ?, 'pending', ?)",
        (profile_id, json.dumps(data), time.time())
    )
    conn.commit()
    conn.close()


def get_pending_suggestions(profile_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT data FROM suggestions WHERE profile_id = ? AND status = 'pending' ORDER BY created_at ASC",
        (profile_id,)
    ).fetchall()
    conn.close()
    return [json.loads(r["data"]) for r in rows]


def remove_suggestion(profile_id: str, suggestion_id: str):
    conn = get_connection()
    conn.execute(
        "DELETE FROM suggestions WHERE profile_id = ? AND json_extract(data, '$.suggestion_id') = ?",
        (profile_id, suggestion_id)
    )
    conn.commit()
    conn.close()


def clear_suggestions(profile_id: str):
    conn = get_connection()
    conn.execute("DELETE FROM suggestions WHERE profile_id = ? AND status = 'pending'", (profile_id,))
    conn.commit()
    conn.close()


# ---- Users (M2) ----

def create_user(user_id: str, email: str, password_hash: str, role: str, profile_id: str) -> dict:
    """Create a new user. Returns the user dict."""
    conn = get_connection()
    now = time.time()
    conn.execute(
        """INSERT INTO users (user_id, email, password_hash, role, profile_id, is_active, created_at)
           VALUES (?, ?, ?, ?, ?, 1, ?)""",
        (user_id, email.lower(), password_hash, role, profile_id, now)
    )
    conn.commit()
    conn.close()
    return {
        "user_id": user_id,
        "email": email.lower(),
        "role": role,
        "profile_id": profile_id,
        "is_active": True,
        "created_at": now,
        "last_login": None,
    }


def get_user_by_email(email: str) -> Optional[dict]:
    """Get user by email (case-insensitive)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email.lower(),)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def get_user_by_id(user_id: str) -> Optional[dict]:
    """Get user by user_id."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def get_users_by_profile(profile_id: str) -> list[dict]:
    """Get all users for a profile."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM users WHERE profile_id = ?", (profile_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_user_login(user_id: str):
    """Update last_login timestamp."""
    conn = get_connection()
    conn.execute(
        "UPDATE users SET last_login = ? WHERE user_id = ?", (time.time(), user_id)
    )
    conn.commit()
    conn.close()


def update_user_password(user_id: str, password_hash: str):
    """Update user's password hash."""
    conn = get_connection()
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE user_id = ?", (password_hash, user_id)
    )
    conn.commit()
    conn.close()


def deactivate_user(user_id: str):
    """Deactivate a user (soft delete)."""
    conn = get_connection()
    conn.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# ---- Auth Tokens (M2) ----

def _hash_token(token: str) -> str:
    """SHA256 hash of token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(user_id: str, expires_in_seconds: int = 30 * 86400) -> tuple[str, float]:
    """
    Create a new auth token for a user.
    Returns (raw_token, expires_at).
    Token is 32-byte URL-safe random string.
    """
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    now = time.time()
    expires_at = now + expires_in_seconds
    
    conn = get_connection()
    conn.execute(
        """INSERT INTO auth_tokens (token_hash, user_id, expires_at, revoked, created_at)
           VALUES (?, ?, ?, 0, ?)""",
        (token_hash, user_id, expires_at, now)
    )
    conn.commit()
    conn.close()
    return raw_token, expires_at


def verify_token(token: str) -> Optional[dict]:
    """
    Verify a token and return the user if valid.
    Returns None if invalid, expired, or revoked.
    """
    token_hash = _hash_token(token)
    conn = get_connection()
    row = conn.execute(
        """SELECT at.*, u.user_id, u.email, u.role, u.profile_id, u.is_active
           FROM auth_tokens at
           JOIN users u ON at.user_id = u.user_id
           WHERE at.token_hash = ? AND at.revoked = 0 AND at.expires_at > ? AND u.is_active = 1""",
        (token_hash, time.time())
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


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


def revoke_all_user_tokens(user_id: str):
    """Revoke all tokens for a user (e.g., on password change)."""
    conn = get_connection()
    conn.execute("UPDATE auth_tokens SET revoked = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def cleanup_expired_tokens():
    """Remove expired/revoked tokens older than 30 days. Call periodically."""
    conn = get_connection()
    cutoff = time.time() - (30 * 86400)
    conn.execute(
        "DELETE FROM auth_tokens WHERE (revoked = 1 OR expires_at < ?) AND created_at < ?",
        (time.time(), cutoff)
    )
    conn.commit()
    conn.close()


# ---- Retention Policies (M2) ----

def get_retention_policy(profile_id: str) -> Optional[dict]:
    """Get retention policy for a profile."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM retention_policies WHERE profile_id = ?", (profile_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def set_retention_policy(profile_id: str, retention_days: int, auto_cleanup: bool = True):
    """Set or update retention policy for a profile."""
    conn = get_connection()
    now = time.time()
    conn.execute(
        """INSERT OR REPLACE INTO retention_policies (profile_id, retention_days, auto_cleanup, last_cleanup_at)
           VALUES (?, ?, ?, COALESCE((SELECT last_cleanup_at FROM retention_policies WHERE profile_id = ?), ?))""",
        (profile_id, retention_days, 1 if auto_cleanup else 0, profile_id, now)
    )
    conn.commit()
    conn.close()


def update_retention_cleanup_time(profile_id: str):
    """Update last_cleanup_at to now."""
    conn = get_connection()
    conn.execute(
        "UPDATE retention_policies SET last_cleanup_at = ? WHERE profile_id = ?",
        (time.time(), profile_id)
    )
    conn.commit()
    conn.close()


def get_all_retention_policies() -> list[tuple[str, int]]:
    """Get all profiles with auto_cleanup enabled. Returns [(profile_id, retention_days), ...]."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT profile_id, retention_days FROM retention_policies WHERE auto_cleanup = 1 AND retention_days > 0"
    ).fetchall()
    conn.close()
    return [(r["profile_id"], r["retention_days"]) for r in rows]


# ---- Memory Box Clips (M2) ----

def create_memory_box_clip(
    clip_id: str,
    profile_id: str,
    family_member_name: str,
    relationship: str,
    filename: str,
    mime_type: str = "audio/mpeg",
    size_bytes: int = None,
    duration_seconds: float = None,
    uploaded_by: str = None,
) -> dict:
    """Create a new memory box clip record."""
    conn = get_connection()
    now = time.time()
    conn.execute(
        """INSERT INTO memory_box_clips
           (clip_id, profile_id, family_member_name, relationship, filename, mime_type, size_bytes, duration_seconds, created_at, uploaded_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (clip_id, profile_id, family_member_name, relationship, filename, mime_type, size_bytes, duration_seconds, now, uploaded_by)
    )
    conn.commit()
    conn.close()
    return {
        "clip_id": clip_id,
        "profile_id": profile_id,
        "family_member_name": family_member_name,
        "relationship": relationship,
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "duration_seconds": duration_seconds,
        "created_at": now,
        "uploaded_by": uploaded_by,
    }


def get_memory_box_clip(clip_id: str) -> Optional[dict]:
    """Get clip by clip_id."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM memory_box_clips WHERE clip_id = ?", (clip_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def get_memory_box_clips_by_profile(profile_id: str) -> list[dict]:
    """Get all clips for a profile."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM memory_box_clips WHERE profile_id = ? ORDER BY created_at DESC", (profile_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_memory_box_clip(clip_id: str) -> bool:
    """Delete a clip record. Returns True if deleted."""
    conn = get_connection()
    cursor = conn.execute("DELETE FROM memory_box_clips WHERE clip_id = ?", (clip_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


# ---- Data Deletion (M2: Delete-my-data) ----

def delete_all_profile_data(profile_id: str) -> dict:
    """
    Delete ALL data for a profile (GDPR-style).
    Returns count of deleted rows per table.
    """
    conn = get_connection()
    counts = {}
    
    # Delete in order to respect FK constraints
    tables = [
        ("auth_tokens", "DELETE FROM auth_tokens WHERE user_id IN (SELECT user_id FROM users WHERE profile_id = ?)"),
        ("users", "DELETE FROM users WHERE profile_id = ?"),
        ("memory_box_clips", "DELETE FROM memory_box_clips WHERE profile_id = ?"),
        ("suggestions", "DELETE FROM suggestions WHERE profile_id = ?"),
        ("session_summaries", "DELETE FROM session_summaries WHERE profile_id = ?"),
        ("events", "DELETE FROM events WHERE profile_id = ?"),
        ("retention_policies", "DELETE FROM retention_policies WHERE profile_id = ?"),
        ("profiles", "DELETE FROM profiles WHERE profile_id = ?"),
    ]
    
    for table_name, query in tables:
        cursor = conn.execute(query, (profile_id,))
        counts[table_name] = cursor.rowcount
    
    conn.commit()
    conn.close()
    return counts