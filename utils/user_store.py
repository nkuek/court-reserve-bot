"""
Encrypted user credential storage.

Uses SQLite for storage and Fernet for encryption.
"""

import base64
import hashlib
import os
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet


def _get_encryption_key() -> bytes:
    """
    Get or generate the encryption key.

    Uses ENCRYPTION_KEY from environment, or generates one from a secret.
    """
    key = os.environ.get("ENCRYPTION_KEY")

    if key:
        # Use provided key directly (must be valid Fernet key)
        return key.encode()

    # Generate key from bot token (deterministic)
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "default-secret-key")
    # Create a 32-byte key from the token using SHA256
    key_bytes = hashlib.sha256(bot_token.encode()).digest()
    # Fernet requires base64-encoded 32-byte key
    return base64.urlsafe_b64encode(key_bytes)


def _get_fernet() -> Fernet:
    """Get the Fernet encryption instance."""
    return Fernet(_get_encryption_key())


def _get_db_path() -> Path:
    """Get the path to the SQLite database."""
    return Path(__file__).parent.parent / "data" / "users.db"


def _get_connection() -> sqlite3.Connection:
    """Get a database connection, creating the table if needed."""
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            discord_id INTEGER PRIMARY KEY,
            email_encrypted TEXT NOT NULL,
            password_encrypted TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def save_user_credentials(discord_id: int, email: str, password: str) -> None:
    """
    Save or update a user's credentials.

    Args:
        discord_id: The user's Discord ID
        email: The user's CourtReserve email
        password: The user's CourtReserve password (will be encrypted)
    """
    fernet = _get_fernet()

    email_encrypted = fernet.encrypt(email.encode()).decode()
    password_encrypted = fernet.encrypt(password.encode()).decode()

    conn = _get_connection()
    try:
        conn.execute("""
            INSERT INTO users (discord_id, email_encrypted, password_encrypted, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(discord_id) DO UPDATE SET
                email_encrypted = excluded.email_encrypted,
                password_encrypted = excluded.password_encrypted,
                updated_at = CURRENT_TIMESTAMP
        """, (discord_id, email_encrypted, password_encrypted))
        conn.commit()
    finally:
        conn.close()


def get_user_credentials(discord_id: int) -> tuple[str, str] | None:
    """
    Get a user's decrypted credentials.

    Args:
        discord_id: The user's Discord ID

    Returns:
        Tuple of (email, password) or None if user not found
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT email_encrypted, password_encrypted FROM users WHERE discord_id = ?",
            (discord_id,)
        )
        row = cursor.fetchone()

        if not row:
            return None

        fernet = _get_fernet()
        email = fernet.decrypt(row[0].encode()).decode()
        password = fernet.decrypt(row[1].encode()).decode()

        return (email, password)
    finally:
        conn.close()


def delete_user_credentials(discord_id: int) -> bool:
    """
    Delete a user's credentials.

    Args:
        discord_id: The user's Discord ID

    Returns:
        True if deleted, False if user not found
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM users WHERE discord_id = ?",
            (discord_id,)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def user_exists(discord_id: int) -> bool:
    """Check if a user has registered credentials."""
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "SELECT 1 FROM users WHERE discord_id = ?",
            (discord_id,)
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


# ============== Scheduled Tasks ==============

def _init_schedules_table(conn: sqlite3.Connection) -> None:
    """Create the schedules table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id INTEGER NOT NULL,
            task_type TEXT NOT NULL,
            day_of_week INTEGER NOT NULL,
            hour INTEGER NOT NULL,
            minute INTEGER NOT NULL,
            enabled INTEGER DEFAULT 1,
            last_run TEXT,
            params TEXT,
            skip_next INTEGER DEFAULT 0,
            run_at TEXT,
            one_time INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (discord_id) REFERENCES users(discord_id)
        )
    """)
    # Add columns if they don't exist (for existing databases)
    for col, col_type in [
        ("params", "TEXT"),
        ("skip_next", "INTEGER DEFAULT 0"),
        ("run_at", "TEXT"),
        ("one_time", "INTEGER DEFAULT 0"),
    ]:
        try:
            conn.execute(f"ALTER TABLE schedules ADD COLUMN {col} {col_type}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()


def save_schedule(
    discord_id: int,
    task_type: str,
    day_of_week: int,
    hour: int,
    minute: int,
    params: dict | None = None,
    run_at: str | None = None,
    one_time: bool = False,
) -> int:
    """
    Save a new scheduled task.

    Args:
        discord_id: The user's Discord ID
        task_type: Type of task ("openplay", "book", etc.)
        day_of_week: 0=Monday, 1=Tuesday, ..., 6=Sunday
        hour: Hour (0-23)
        minute: Minute (0-59)
        params: Optional dict of task-specific parameters

    Returns:
        The schedule ID
    """
    import json
    conn = _get_connection()
    _init_schedules_table(conn)
    params_json = json.dumps(params) if params else None
    try:
        cursor = conn.execute("""
            INSERT INTO schedules (discord_id, task_type, day_of_week, hour, minute, params, run_at, one_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (discord_id, task_type, day_of_week, hour, minute, params_json, run_at, 1 if one_time else 0))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_user_schedules(discord_id: int) -> list[dict]:
    """
    Get all schedules for a user.

    Returns:
        List of schedule dicts with id, task_type, day_of_week, hour, minute, enabled, params, skip_next
    """
    import json
    conn = _get_connection()
    _init_schedules_table(conn)
    try:
        cursor = conn.execute("""
            SELECT id, task_type, day_of_week, hour, minute, enabled, last_run, params, skip_next, run_at, one_time
            FROM schedules
            WHERE discord_id = ?
            ORDER BY day_of_week, hour, minute
        """, (discord_id,))

        schedules = []
        for row in cursor.fetchall():
            schedules.append({
                "id": row[0],
                "task_type": row[1],
                "day_of_week": row[2],
                "hour": row[3],
                "minute": row[4],
                "enabled": bool(row[5]),
                "last_run": row[6],
                "params": json.loads(row[7]) if row[7] else None,
                "skip_next": bool(row[8]) if row[8] is not None else False,
                "run_at": row[9],
                "one_time": bool(row[10]) if row[10] is not None else False,
            })
        return schedules
    finally:
        conn.close()


def get_all_schedules() -> list[dict]:
    """
    Get all enabled schedules (for the background task).

    Returns:
        List of schedule dicts including discord_id and params
    """
    import json
    conn = _get_connection()
    _init_schedules_table(conn)
    try:
        cursor = conn.execute("""
            SELECT id, discord_id, task_type, day_of_week, hour, minute, last_run, params, skip_next, run_at, one_time
            FROM schedules
            WHERE enabled = 1
        """)

        schedules = []
        for row in cursor.fetchall():
            schedules.append({
                "id": row[0],
                "discord_id": row[1],
                "task_type": row[2],
                "day_of_week": row[3],
                "hour": row[4],
                "minute": row[5],
                "last_run": row[6],
                "params": json.loads(row[7]) if row[7] else None,
                "skip_next": bool(row[8]) if row[8] is not None else False,
                "run_at": row[9],
                "one_time": bool(row[10]) if row[10] is not None else False,
            })
        return schedules
    finally:
        conn.close()


def update_schedule_last_run(schedule_id: int, last_run: str) -> None:
    """Update the last_run timestamp for a schedule."""
    conn = _get_connection()
    try:
        conn.execute("""
            UPDATE schedules SET last_run = ? WHERE id = ?
        """, (last_run, schedule_id))
        conn.commit()
    finally:
        conn.close()


def delete_schedule(discord_id: int, schedule_id: int) -> bool:
    """
    Delete a schedule (only if it belongs to the user).

    Returns:
        True if deleted, False if not found or not owned by user
    """
    conn = _get_connection()
    try:
        cursor = conn.execute("""
            DELETE FROM schedules WHERE id = ? AND discord_id = ?
        """, (schedule_id, discord_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def set_schedule_enabled(discord_id: int, schedule_id: int, enabled: bool) -> bool:
    """
    Enable or disable a schedule (only if it belongs to the user).

    Returns:
        True if updated, False if not found or not owned by user
    """
    conn = _get_connection()
    try:
        cursor = conn.execute("""
            UPDATE schedules SET enabled = ? WHERE id = ? AND discord_id = ?
        """, (1 if enabled else 0, schedule_id, discord_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def set_schedule_skip_next(discord_id: int, schedule_id: int, skip: bool) -> bool:
    """
    Set skip_next flag for a schedule (only if it belongs to the user).

    Returns:
        True if updated, False if not found or not owned by user
    """
    conn = _get_connection()
    _init_schedules_table(conn)
    try:
        cursor = conn.execute("""
            UPDATE schedules SET skip_next = ? WHERE id = ? AND discord_id = ?
        """, (1 if skip else 0, schedule_id, discord_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def clear_schedule_skip(schedule_id: int) -> None:
    """Clear the skip_next flag after a schedule has been skipped."""
    conn = _get_connection()
    try:
        conn.execute("""
            UPDATE schedules SET skip_next = 0 WHERE id = ?
        """, (schedule_id,))
        conn.commit()
    finally:
        conn.close()


# ============== Admin Functions ==============

def admin_get_all_schedules() -> list[dict]:
    """
    Get ALL schedules across all users (for admin use).

    Returns:
        List of schedule dicts including discord_id and all fields
    """
    import json
    conn = _get_connection()
    _init_schedules_table(conn)
    try:
        cursor = conn.execute("""
            SELECT id, discord_id, task_type, day_of_week, hour, minute, enabled, last_run, params, skip_next, run_at, one_time
            FROM schedules
            ORDER BY discord_id, day_of_week, hour, minute
        """)

        schedules = []
        for row in cursor.fetchall():
            schedules.append({
                "id": row[0],
                "discord_id": row[1],
                "task_type": row[2],
                "day_of_week": row[3],
                "hour": row[4],
                "minute": row[5],
                "enabled": bool(row[6]),
                "last_run": row[7],
                "params": json.loads(row[8]) if row[8] else None,
                "skip_next": bool(row[9]) if row[9] is not None else False,
                "run_at": row[10],
                "one_time": bool(row[11]) if row[11] is not None else False,
            })
        return schedules
    finally:
        conn.close()


def admin_delete_schedule(schedule_id: int) -> bool:
    """
    Delete a schedule by ID (admin only, no ownership check).

    Returns:
        True if deleted, False if not found
    """
    conn = _get_connection()
    try:
        cursor = conn.execute("""
            DELETE FROM schedules WHERE id = ?
        """, (schedule_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def admin_set_schedule_enabled(schedule_id: int, enabled: bool) -> bool:
    """
    Enable or disable a schedule by ID (admin only, no ownership check).

    Returns:
        True if updated, False if not found
    """
    conn = _get_connection()
    try:
        cursor = conn.execute("""
            UPDATE schedules SET enabled = ? WHERE id = ?
        """, (1 if enabled else 0, schedule_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def admin_get_all_users() -> list[dict]:
    """
    Get all registered users (for admin use).

    Returns:
        List of user dicts with discord_id and email (decrypted)
    """
    conn = _get_connection()
    try:
        cursor = conn.execute("""
            SELECT discord_id, email_encrypted FROM users
        """)

        fernet = _get_fernet()
        users = []
        for row in cursor.fetchall():
            try:
                email = fernet.decrypt(row[1].encode()).decode()
            except:
                email = "(decrypt error)"
            users.append({
                "discord_id": row[0],
                "email": email,
            })
        return users
    finally:
        conn.close()
