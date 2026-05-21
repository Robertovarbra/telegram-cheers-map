import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from config import DB_PATH


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS pins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            video_file_id TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON pins(chat_id)")
    conn.commit()
    conn.close()


def migrate_db():
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    try:
        c.execute("ALTER TABLE pins ADD COLUMN video_type TEXT DEFAULT 'video_note'")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE pins ADD COLUMN video_link TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def add_pin(chat_id, message_id, user_id, user_name, video_file_id, lat, lng, video_type="video_note", video_link=None):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "INSERT INTO pins (chat_id, message_id, user_id, user_name, video_file_id, latitude, longitude, created_at, video_type, video_link) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (chat_id, message_id, user_id, user_name, video_file_id, lat, lng, datetime.now(timezone.utc).isoformat(), video_type, video_link),
    )
    conn.commit()
    conn.close()


def get_pin(chat_id, message_id):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT video_file_id, user_name, video_type FROM pins WHERE chat_id = ? AND message_id = ?", (chat_id, message_id))
    row = c.fetchone()
    conn.close()
    return row


def get_pins(chat_id):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "SELECT id, message_id, user_id, user_name, video_file_id, latitude, longitude, created_at, video_link FROM pins WHERE chat_id = ? ORDER BY created_at",
        (chat_id,),
    )
    rows = c.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "message_id": r[1],
            "user_id": r[2],
            "user_name": r[3],
            "video_file_id": r[4],
            "latitude": r[5],
            "longitude": r[6],
            "created_at": r[7],
            "video_link": r[8],
        }
        for r in rows
    ]


def delete_chat_pins(chat_id):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM pins WHERE chat_id = ?", (chat_id,))
    count = c.fetchone()[0]
    if count > 0:
        c.execute("DELETE FROM pins WHERE chat_id = ?", (chat_id,))
        conn.commit()
    conn.close()
    return count


def get_all_chat_ids():
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT DISTINCT chat_id FROM pins")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]
