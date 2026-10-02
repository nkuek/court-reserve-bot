"""SQLite queue of WhatsApp posts waiting for their send time.

A row is a Yes/No poll or a day's sign-up post. A sign-up post keeps only its date and is
written at send time, so it reflects who has signed up by then.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _db_path() -> Path:
    return Path(__file__).parent.parent / "data" / "polls.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS polls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL UNIQUE,
            send_at TEXT NOT NULL,
            sent_at TEXT,
            failed_at TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            target TEXT
        )
    """)
    for column in ("target TEXT", "kind TEXT NOT NULL DEFAULT 'poll'", "session_date TEXT"):
        try:
            conn.execute(f"ALTER TABLE polls ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    return conn


def _utc_iso(dt: datetime) -> str:
    # Stored as naive UTC so string comparison in SQL orders correctly.
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _from_utc_iso(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def enqueue_poll(question: str, send_at: datetime, target: str | None = None) -> bool:
    """Queue a poll. Returns False when the same question is already queued or sent.

    target is a WhatsApp JID. None means the configured group.
    """
    conn = _connect()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO polls (question, send_at, created_at, target) VALUES (?, ?, ?, ?)",
            (question, _utc_iso(send_at), _utc_iso(datetime.now(timezone.utc)), target),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def enqueue_post(session_date: str, send_at: datetime, target: str | None = None) -> bool:
    """Queue a day's sign-up post. Returns False when that day's post is already queued or sent."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO polls (question, send_at, created_at, target, kind, session_date) VALUES (?, ?, ?, ?, 'signup', ?)",
            (f"Sign-up post for {session_date}", _utc_iso(send_at), _utc_iso(datetime.now(timezone.utc)), target, session_date),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def due_polls(now: datetime) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT id, question, send_at, attempts, target, kind, session_date
            FROM polls
            WHERE sent_at IS NULL AND failed_at IS NULL AND send_at <= ?
            ORDER BY send_at
            """,
            (_utc_iso(now),),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "question": r["question"],
                "send_at": _from_utc_iso(r["send_at"]),
                "attempts": r["attempts"],
                "target": r["target"],
                "kind": r["kind"],
                "session_date": r["session_date"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def mark_sent(poll_id: int, now: datetime) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE polls SET sent_at = ?, attempts = attempts + 1 WHERE id = ?",
            (_utc_iso(now), poll_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_attempt(poll_id: int, error: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE polls SET attempts = attempts + 1, last_error = ? WHERE id = ?",
            (error[:500], poll_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_failed(poll_id: int, now: datetime) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE polls SET failed_at = ? WHERE id = ?",
            (_utc_iso(now), poll_id),
        )
        conn.commit()
    finally:
        conn.close()
