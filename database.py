import sqlite3
from datetime import datetime, timezone
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
    try:
        c.execute("ALTER TABLE pins ADD COLUMN city TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE pins ADD COLUMN country TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE pins ADD COLUMN country_code TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("""
            CREATE TABLE chat_settings (
                chat_id INTEGER PRIMARY KEY,
                pinned_map_msg_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("""
            CREATE TABLE user_preferences (
                user_id INTEGER PRIMARY KEY,
                pin_color TEXT,
                pin_emoji TEXT,
                updated_at TEXT NOT NULL
            )
        """)
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def set_user_pref(user_id, key, value):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    if key == "pin_color":
        c.execute("""
            INSERT INTO user_preferences (user_id, pin_color, pin_emoji, updated_at)
            VALUES (?, ?, NULL, ?)
            ON CONFLICT(user_id) DO UPDATE SET pin_color = excluded.pin_color, updated_at = excluded.updated_at
        """, (user_id, value, now))
    elif key == "pin_emoji":
        c.execute("""
            INSERT INTO user_preferences (user_id, pin_emoji, pin_color, updated_at)
            VALUES (?, ?, NULL, ?)
            ON CONFLICT(user_id) DO UPDATE SET pin_emoji = excluded.pin_emoji, updated_at = excluded.updated_at
        """, (user_id, value, now))
    conn.commit()
    conn.close()

def get_chat_setting(chat_id, key):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    if key == "pinned_map_msg_id":
        c.execute("SELECT pinned_map_msg_id FROM chat_settings WHERE chat_id = ?", (chat_id,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else None
    conn.close()
    return None


def set_chat_setting(chat_id, key, value):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    if key == "pinned_map_msg_id":
        c.execute("""
            INSERT INTO chat_settings (chat_id, pinned_map_msg_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                pinned_map_msg_id = excluded.pinned_map_msg_id,
                updated_at = excluded.updated_at
        """, (chat_id, value, now, now))
        conn.commit()
    conn.close()


def add_pin(chat_id, message_id, user_id, user_name, video_file_id, lat, lng, video_type="video_note", video_link=None, city=None, country=None, country_code=None):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "INSERT INTO pins (chat_id, message_id, user_id, user_name, video_file_id, latitude, longitude, created_at, video_type, video_link, city, country, country_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (chat_id, message_id, user_id, user_name, video_file_id, lat, lng, datetime.now(timezone.utc).isoformat(), video_type, video_link, city, country, country_code),
    )
    conn.commit()
    conn.close()


def get_pin(chat_id, message_id):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT video_file_id, user_name, video_type, city, country, country_code FROM pins WHERE chat_id = ? AND message_id = ?", (chat_id, message_id))
    row = c.fetchone()
    conn.close()
    return row


def get_pins(chat_id):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "SELECT p.id, p.message_id, p.user_id, p.user_name, p.video_file_id, p.latitude, p.longitude, p.created_at, p.video_link, COALESCE(up.pin_color, ''), COALESCE(up.pin_emoji, ''), p.city, p.country, p.country_code FROM pins p LEFT JOIN user_preferences up ON p.user_id = up.user_id WHERE p.chat_id = ? ORDER BY p.created_at",
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
            "pin_color": r[9] or None,
            "pin_emoji": r[10] or None,
            "city": r[11],
            "country": r[12],
            "country_code": r[13],
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
    try:
        c.execute("DELETE FROM chat_settings WHERE chat_id = ?", (chat_id,))
        conn.commit()
    except Exception:
        pass
    conn.close()
    return count


def get_all_chat_ids():
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT DISTINCT chat_id FROM pins")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]
