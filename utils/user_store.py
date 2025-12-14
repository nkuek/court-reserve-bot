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
