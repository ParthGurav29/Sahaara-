"""
database.py

SQLite database for Sahaara — replaces flat JSON files.
Handles profiles, events, session summaries, and suggestions.

Migration: loads existing asha_profile.json on first run.
"""

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "sahaara.db"


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
        
        CREATE INDEX IF NOT EXISTS idx_events_profile ON events(profile_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(profile_id, type);
        CREATE INDEX IF NOT EXISTS idx_suggestions_profile ON suggestions(profile_id, status);
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