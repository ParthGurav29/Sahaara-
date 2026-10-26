"""
database.py

SQLite database for Sahaara — replaces flat JSON files.
Handles profiles, events, session summaries, and suggestions.

Migration: loads existing asha_profile.json on first run.
"""

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# SAHAARA_DB_PATH lets tests point at a throwaway database instead of the
# live dev DB (auth tests truncate the users table).
DB_PATH = Path(os.environ.get("SAHAARA_DB_PATH") or (Path(__file__).parent / "data" / "sahaara.db"))


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create tables and migrate existing JSON data."""
    conn = get_connection()
    
    conn.executescript("""
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'caregiver',
            profile_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
        );
        
        CREATE TABLE IF NOT EXISTS auth_tokens (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        
        CREATE INDEX IF NOT EXISTS idx_events_profile ON events(profile_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(profile_id, type);
        CREATE INDEX IF NOT EXISTS idx_suggestions_profile ON suggestions(profile_id, status);
        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
        CREATE INDEX IF NOT EXISTS idx_users_profile ON users(profile_id);
        CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id);
        CREATE INDEX IF NOT EXISTS idx_auth_tokens_expires ON auth_tokens(expires_at);
    """)
    
    # M3: Scheduler tables
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scheduler_settings (
            profile_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1,
            max_checkins_per_day INTEGER NOT NULL DEFAULT 4,
            min_gap_after_conversation_minutes INTEGER NOT NULL DEFAULT 20,
            checkin_window_minutes INTEGER NOT NULL DEFAULT 15,
            response_window_minutes INTEGER NOT NULL DEFAULT 10,
            intro_throttle_hours INTEGER NOT NULL DEFAULT 24,
            checkin_times TEXT NOT NULL DEFAULT '["08:00","14:00","19:00"]',
            quiet_hours_start TEXT NOT NULL DEFAULT '22:00',
            quiet_hours_end TEXT NOT NULL DEFAULT '07:00',
            intro_enabled INTEGER NOT NULL DEFAULT 1,
            intro_overrides TEXT NOT NULL DEFAULT '{}',
            wording_recent_limit INTEGER NOT NULL DEFAULT 4,
            retry_once INTEGER NOT NULL DEFAULT 1,
            proactive_v2_enabled INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
        );
        
        CREATE TABLE IF NOT EXISTS checkin_fired_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id TEXT NOT NULL,
            slot_id TEXT NOT NULL,
            checkin_type TEXT NOT NULL,
            fired_at REAL NOT NULL,
            wording_id TEXT NOT NULL,
            response_received INTEGER NOT NULL DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0,
            skip_reason TEXT NOT NULL DEFAULT '',
            delivered_audio INTEGER NOT NULL DEFAULT 0,
            audio_blocked INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
        );
        
        CREATE INDEX IF NOT EXISTS idx_checkin_fired_profile ON checkin_fired_history(profile_id, slot_id);
        CREATE INDEX IF NOT EXISTS idx_checkin_fired_date ON checkin_fired_history(profile_id, fired_at);
    """)
    
    conn.commit()
    
    # Migrate existing JSON profile if DB is empty
    cursor = conn.execute("SELECT COUNT(*) FROM profiles")
    if cursor.fetchone()[0] == 0:
        _migrate_profile(conn)
    
    conn.close()


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


# ---- Scheduler Settings (M3) ----

def decode_json_field(value, default):
    """Decode a JSON text column, tolerating legacy double-encoded values."""
    if value is None:
        return default
    if isinstance(value, type(default)):
        return value
    try:
        decoded = json.loads(value)
        if isinstance(decoded, str):  # legacy double-encoding
            decoded = json.loads(decoded)
        return decoded if isinstance(decoded, type(default)) else default
    except (ValueError, TypeError):
        return default


def _normalize_json_field(value, default):
    """Accept a list/dict OR a JSON string and return a single-encoded JSON string.

    The Step 7 caregiver endpoints pass DB rows back through
    save_scheduler_settings(); without this guard those JSON strings were
    encoded a second time, corrupting checkin_times / intro_overrides.
    """
    return json.dumps(decode_json_field(value, default))


def get_scheduler_settings(profile_id: str) -> Optional[dict]:
    """Get scheduler settings for a profile."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM scheduler_settings WHERE profile_id = ?", (profile_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def save_scheduler_settings(profile_id: str, settings: dict):
    """Save scheduler settings for a profile."""
    conn = get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO scheduler_settings 
        (profile_id, enabled, max_checkins_per_day, min_gap_after_conversation_minutes,
         checkin_window_minutes, response_window_minutes, intro_throttle_hours,
         checkin_times, quiet_hours_start, quiet_hours_end, intro_enabled,
         intro_overrides, wording_recent_limit, retry_once,
         proactive_v2_enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            profile_id,
            settings.get("enabled", 1),
            settings.get("max_checkins_per_day", 4),
            settings.get("min_gap_after_conversation_minutes", 20),
            settings.get("checkin_window_minutes", 15),
            settings.get("response_window_minutes", 10),
            settings.get("intro_throttle_hours", 24),
            _normalize_json_field(settings.get("checkin_times", ["08:00", "14:00", "19:00"]), []),
            settings.get("quiet_hours_start", "22:00"),
            settings.get("quiet_hours_end", "07:00"),
            settings.get("intro_enabled", 1),
            _normalize_json_field(settings.get("intro_overrides", {}), {}),
            settings.get("wording_recent_limit", 4),
            settings.get("retry_once", 1),
            settings.get("proactive_v2_enabled", 1),
        )
    )
    conn.commit()
    conn.close()


def get_default_scheduler_settings() -> dict:
    """Return default scheduler settings."""
    return {
        "enabled": True,
        "max_checkins_per_day": 4,
        "min_gap_after_conversation_minutes": 20,
        "checkin_window_minutes": 15,
        "response_window_minutes": 10,
        "retry_once": True,
        "intro_throttle_hours": 24,
        "checkin_times": ["08:00", "14:00", "19:00"],
        "quiet_hours_start": "22:00",
        "quiet_hours_end": "07:00",
        "intro_enabled": True,
        "intro_throttle_hours": 24,
        "intro_overrides": {},
        "wording_recent_limit": 4,
        "retry_once": True,
        "proactive_v2_enabled": True,
    }


# ---- Check-in Fired History (M3) ----

def save_checkin_fired(record: dict):
    """Save a fired check-in record for deduplication and history."""
    # fired_at is stored as epoch seconds (REAL). Accept datetime or legacy ISO strings.
    fired_at = record["fired_at"]
    if isinstance(fired_at, datetime):
        fired_at = fired_at.timestamp()
    elif isinstance(fired_at, str):
        fired_at = datetime.fromisoformat(fired_at).timestamp()
    conn = get_connection()
    conn.execute(
        """INSERT INTO checkin_fired_history 
        (profile_id, slot_id, checkin_type, fired_at, wording_id, 
         response_received, retry_count, skip_reason, delivered_audio, audio_blocked)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            record["profile_id"],
            record["slot_id"],
            record["checkin_type"],
            fired_at,
            record["wording_id"],
            record.get("response_received", 0),
            record.get("retry_count", 0),
            record.get("skip_reason", ""),
            record.get("delivered_audio", 0),
            record.get("audio_blocked", 0),
        )
    )
    conn.commit()
    conn.close()


def get_checkin_fired_history(profile_id: str, slot_id: str = None, since: float = None, limit: int = 100) -> list[dict]:
    """Get fired check-in history for a profile."""
    conn = get_connection()
    query = "SELECT * FROM checkin_fired_history WHERE profile_id = ?"
    params = [profile_id]
    
    if slot_id:
        query += " AND slot_id = ?"
        params.append(slot_id)
    if since:
        query += " AND fired_at >= ?"
        params.append(since)
    
    query += " ORDER BY fired_at DESC LIMIT ?"
    params.append(limit)
    
    rows = conn.execute(query, params).fetchall()
    conn.close()

    # Normalize fired_at to epoch seconds. Older rows stored ISO strings; mixed
    # storage classes also break SQLite's ORDER BY, so re-sort in Python.
    result = []
    for row in rows:
        item = dict(row)
        if isinstance(item.get("fired_at"), str):
            try:
                item["fired_at"] = datetime.fromisoformat(item["fired_at"]).timestamp()
            except ValueError:
                item["fired_at"] = 0.0
        result.append(item)
    result.sort(key=lambda r: r.get("fired_at") or 0, reverse=True)
    return result


def mark_checkin_response_received(profile_id: str, slot_id: str):
    """Mark a check-in as having received a user response."""
    conn = get_connection()
    conn.execute(
        "UPDATE checkin_fired_history SET response_received = 1 WHERE profile_id = ? AND slot_id = ?",
        (profile_id, slot_id)
    )
    conn.commit()
    conn.close()


def update_checkin_audio_delivery(profile_id: str, slot_id: str, delivered_audio: int = 0, audio_blocked: int = 0):
    """Update audio delivery status for a check-in."""
    conn = get_connection()
    conn.execute(
        "UPDATE checkin_fired_history SET delivered_audio = ?, audio_blocked = ? WHERE profile_id = ? AND slot_id = ?",
        (delivered_audio, audio_blocked, profile_id, slot_id)
    )
    conn.commit()
    conn.close()
