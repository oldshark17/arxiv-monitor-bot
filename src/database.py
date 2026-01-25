"""
Database module for ArXiv Monitor Bot.
Uses SQLite for storing users, subscriptions, and seen papers.
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

# Database file path (same directory as this module)
DB_PATH = Path(__file__).parent / "arxiv_bot.db"


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database tables."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Subscriptions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            topic TEXT NOT NULL,
            frequency TEXT NOT NULL,
            last_checked TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)
    
    # Seen papers table (to avoid sending duplicates)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS seen_papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subscription_id INTEGER NOT NULL,
            arxiv_id TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE,
            UNIQUE(user_id, arxiv_id)
        )
    """)
    
    conn.commit()
    conn.close()


def add_user(user_id: int) -> None:
    """Register a user (idempotent)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
        (user_id,)
    )
    conn.commit()
    conn.close()


def add_subscription(user_id: int, topic: str, frequency: str) -> int:
    """Create a new subscription. Returns subscription ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO subscriptions (user_id, topic, frequency) VALUES (?, ?, ?)",
        (user_id, topic, frequency)
    )
    subscription_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return subscription_id


def get_subscriptions(user_id: int) -> list[dict]:
    """Get all subscriptions for a user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, topic, frequency, last_checked, created_at FROM subscriptions WHERE user_id = ?",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_all_subscriptions() -> list[dict]:
    """Get all subscriptions (for scheduler)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, topic, frequency, last_checked FROM subscriptions"
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_subscription(subscription_id: int) -> bool:
    """Delete a subscription. Returns True if deleted."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM subscriptions WHERE id = ?",
        (subscription_id,)
    )
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def update_last_checked(subscription_id: int) -> None:
    """Update the last_checked timestamp for a subscription."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE subscriptions SET last_checked = ? WHERE id = ?",
        (datetime.now(), subscription_id)
    )
    conn.commit()
    conn.close()


def mark_paper_seen(user_id: int, subscription_id: int, arxiv_id: str) -> None:
    """Mark a paper as sent to user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO seen_papers (user_id, subscription_id, arxiv_id) VALUES (?, ?, ?)",
        (user_id, subscription_id, arxiv_id)
    )
    conn.commit()
    conn.close()


def is_paper_seen(user_id: int, arxiv_id: str) -> bool:
    """Check if a paper was already sent to user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM seen_papers WHERE user_id = ? AND arxiv_id = ?",
        (user_id, arxiv_id)
    )
    result = cursor.fetchone() is not None
    conn.close()
    return result


def get_unseen_papers(user_id: int, arxiv_ids: list[str]) -> list[str]:
    """Filter a list of arxiv_ids to only those not yet seen by user."""
    if not arxiv_ids:
        return []
    
    conn = get_connection()
    cursor = conn.cursor()
    placeholders = ",".join("?" * len(arxiv_ids))
    cursor.execute(
        f"SELECT arxiv_id FROM seen_papers WHERE user_id = ? AND arxiv_id IN ({placeholders})",
        [user_id] + arxiv_ids
    )
    seen = {row["arxiv_id"] for row in cursor.fetchall()}
    conn.close()
    
    return [aid for aid in arxiv_ids if aid not in seen]
