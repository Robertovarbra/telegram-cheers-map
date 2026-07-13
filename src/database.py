import sqlite3
from datetime import datetime, timedelta, timezone

from config import DB_PATH


def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
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
    finally:
        conn.close()


def migrate_db():
    conn = sqlite3.connect(str(DB_PATH))
    try:
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
        try:
            c.execute("ALTER TABLE pins ADD COLUMN trip_id INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("""
                CREATE TABLE trips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    closed_at TEXT,
                    checklist_msg_id INTEGER
                )
            """)
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE trips ADD COLUMN checklist_msg_id INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("""
                CREATE TABLE trip_members (
                    trip_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    PRIMARY KEY (trip_id, user_id)
                )
            """)
        except sqlite3.OperationalError:
            pass
        try:
            # Roster of everyone who has EVER joined a trip in the chat. Never pruned (except on
            # chat cleanup) so a checklist row survives leaving a trip — otherwise a user who never
            # posted a pin would lose their toggle button the moment they left, with no way back in.
            c.execute("""
                CREATE TABLE chat_trip_users (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    PRIMARY KEY (chat_id, user_id)
                )
            """)
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("""
                CREATE TABLE pending_pins (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    file_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    video_link TEXT,
                    prompt_msg_id INTEGER,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, user_id)
                )
            """)
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


def set_user_pref(user_id, key, value):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        if key == "pin_color":
            c.execute(
                """
                INSERT INTO user_preferences (user_id, pin_color, pin_emoji, updated_at)
                VALUES (?, ?, NULL, ?)
                ON CONFLICT(user_id) DO UPDATE SET pin_color = excluded.pin_color, updated_at = excluded.updated_at
            """,
                (user_id, value, now),
            )
        elif key == "pin_emoji":
            c.execute(
                """
                INSERT INTO user_preferences (user_id, pin_emoji, pin_color, updated_at)
                VALUES (?, ?, NULL, ?)
                ON CONFLICT(user_id) DO UPDATE SET pin_emoji = excluded.pin_emoji, updated_at = excluded.updated_at
            """,
                (user_id, value, now),
            )
        conn.commit()
    finally:
        conn.close()


def get_chat_setting(chat_id, key):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        if key == "pinned_map_msg_id":
            c.execute("SELECT pinned_map_msg_id FROM chat_settings WHERE chat_id = ?", (chat_id,))
            row = c.fetchone()
            return row[0] if row else None
        return None
    finally:
        conn.close()


def set_chat_setting(chat_id, key, value):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        if key == "pinned_map_msg_id":
            c.execute(
                """
                INSERT INTO chat_settings (chat_id, pinned_map_msg_id, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    pinned_map_msg_id = excluded.pinned_map_msg_id,
                    updated_at = excluded.updated_at
            """,
                (chat_id, value, now, now),
            )
            conn.commit()
    finally:
        conn.close()


def set_pending_pin(chat_id, user_id, file_id, message_id, user_name, video_link, prompt_msg_id):
    """Store the video a user just sent, awaiting a location. Keyed by (chat_id, user_id),
    so a new video from the same user in the same chat overwrites the previous pending one."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        c.execute(
            """
            INSERT INTO pending_pins (chat_id, user_id, file_id, message_id, user_name, video_link, prompt_msg_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                file_id = excluded.file_id,
                message_id = excluded.message_id,
                user_name = excluded.user_name,
                video_link = excluded.video_link,
                prompt_msg_id = excluded.prompt_msg_id,
                created_at = excluded.created_at
        """,
            (chat_id, user_id, file_id, message_id, user_name, video_link, prompt_msg_id, now),
        )
        conn.commit()
    finally:
        conn.close()


def pop_pending_pin(chat_id, user_id, max_age_seconds=86400):
    """Atomically fetch and delete the caller's pending video for a chat.

    The SELECT + DELETE run inside a single BEGIN IMMEDIATE transaction so two concurrent
    callers (e.g. a double-tapped Mini App button) can't both claim the same row. Returns a
    dict on success, or None if there is no pending row (or it is older than max_age_seconds)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.isolation_level = None
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            "SELECT file_id, message_id, user_name, video_link, prompt_msg_id, created_at FROM pending_pins WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        row = c.fetchone()
        if row:
            c.execute("DELETE FROM pending_pins WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        c.execute("COMMIT")
        if not row:
            return None
        file_id, message_id, user_name, video_link, prompt_msg_id, created_at = row
        if max_age_seconds is not None:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(created_at)).total_seconds()
            except ValueError:
                return None  # unparseable timestamp -> treat as expired (fail closed)
            if age > max_age_seconds:
                return None
        return {"file_id": file_id, "message_id": message_id, "user_name": user_name, "video_link": video_link, "prompt_msg_id": prompt_msg_id}
    finally:
        conn.close()


def delete_stale_pending_pins(max_age_seconds=86400):
    """Best-effort cleanup of pending videos that were never completed with a location."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)).isoformat()
        c.execute("DELETE FROM pending_pins WHERE created_at < ?", (cutoff,))
        count = c.rowcount
        conn.commit()
        return count
    finally:
        conn.close()


def add_pin(
    chat_id,
    message_id,
    user_id,
    user_name,
    video_file_id,
    lat,
    lng,
    video_type="video_note",
    video_link=None,
    city=None,
    country=None,
    country_code=None,
    trip_id=None,
):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO pins (chat_id, message_id, user_id, user_name, video_file_id,"
            " latitude, longitude, created_at, video_type, video_link, city, country, country_code, trip_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chat_id,
                message_id,
                user_id,
                user_name,
                video_file_id,
                lat,
                lng,
                datetime.now(timezone.utc).isoformat(),
                video_type,
                video_link,
                city,
                country,
                country_code,
                trip_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _exclusive_join(c, chat_id, trip_id, user_id, user_name):
    """Add the user to a trip, removing them from every *other open* trip in the same chat — you
    can only physically be in one place, so membership among concurrent trips is exclusive.
    Returns the trips the user was moved off (so callers can refresh those stale checklists)."""
    c.execute(
        "SELECT t.id, t.name, t.checklist_msg_id FROM trips t JOIN trip_members m ON m.trip_id = t.id"
        " WHERE t.chat_id = ? AND t.closed_at IS NULL AND t.id != ? AND m.user_id = ?",
        (chat_id, trip_id, user_id),
    )
    moved_from = [{"id": r[0], "name": r[1], "checklist_msg_id": r[2]} for r in c.fetchall()]
    for t in moved_from:
        c.execute("DELETE FROM trip_members WHERE trip_id = ? AND user_id = ?", (t["id"], user_id))
    c.execute("INSERT OR REPLACE INTO trip_members (trip_id, user_id, user_name) VALUES (?, ?, ?)", (trip_id, user_id, user_name))
    c.execute("INSERT OR REPLACE INTO chat_trip_users (chat_id, user_id, user_name) VALUES (?, ?, ?)", (chat_id, user_id, user_name))
    return moved_from


def create_trip(chat_id, name, created_by, creator_name):
    """Start a new trip. Other open trips keep running (a chat can hold several in parallel); the
    creator auto-joins, which moves them off any other open trip. Returns (trip_id, moved_from)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.isolation_level = None
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        now = datetime.now(timezone.utc).isoformat()
        c.execute("INSERT INTO trips (chat_id, name, created_by, created_at) VALUES (?, ?, ?, ?)", (chat_id, name, created_by, now))
        trip_id = c.lastrowid
        moved_from = _exclusive_join(c, chat_id, trip_id, created_by, creator_name)
        c.execute("COMMIT")
        return trip_id, moved_from
    finally:
        conn.close()


def close_trip(trip_id):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute("UPDATE trips SET closed_at = ? WHERE id = ? AND closed_at IS NULL", (datetime.now(timezone.utc).isoformat(), trip_id))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


def set_trip_checklist_msg(trip_id, message_id):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute("UPDATE trips SET checklist_msg_id = ? WHERE id = ?", (message_id, trip_id))
        conn.commit()
    finally:
        conn.close()


def get_open_trips(chat_id):
    """All still-open trips for a chat, newest first."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            "SELECT id, name, created_by, checklist_msg_id FROM trips WHERE chat_id = ? AND closed_at IS NULL ORDER BY created_at DESC",
            (chat_id,),
        )
        return [{"id": r[0], "name": r[1], "created_by": r[2], "checklist_msg_id": r[3]} for r in c.fetchall()]
    finally:
        conn.close()


def get_open_trip_for_member(chat_id, user_id):
    """The open trip this user is on, or None — the tagging rule for new pins. Membership among a
    chat's open trips is kept exclusive by _exclusive_join, so at most one row matches; ORDER BY is
    a belt-and-braces tiebreak (newest trip wins) for any legacy overlap."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            "SELECT t.id, t.name FROM trips t JOIN trip_members m ON m.trip_id = t.id"
            " WHERE t.chat_id = ? AND t.closed_at IS NULL AND m.user_id = ?"
            " ORDER BY t.created_at DESC LIMIT 1",
            (chat_id, user_id),
        )
        row = c.fetchone()
        return {"id": row[0], "name": row[1]} if row else None
    finally:
        conn.close()


def get_trip(trip_id):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute("SELECT id, chat_id, name, created_by, closed_at FROM trips WHERE id = ?", (trip_id,))
        row = c.fetchone()
        return {"id": row[0], "chat_id": row[1], "name": row[2], "created_by": row[3], "closed_at": row[4]} if row else None
    finally:
        conn.close()


def toggle_trip_member(trip_id, user_id, user_name):
    """Add the user to the trip if absent (moving them off any other open trip in the chat, see
    _exclusive_join), remove them if present. Returns (now_member, moved_from)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.isolation_level = None
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute("SELECT 1 FROM trip_members WHERE trip_id = ? AND user_id = ?", (trip_id, user_id))
        if c.fetchone():
            c.execute("DELETE FROM trip_members WHERE trip_id = ? AND user_id = ?", (trip_id, user_id))
            member, moved_from = False, []
        else:
            c.execute("SELECT chat_id FROM trips WHERE id = ?", (trip_id,))
            chat_id = c.fetchone()[0]
            moved_from = _exclusive_join(c, chat_id, trip_id, user_id, user_name)
            member = True
        c.execute("COMMIT")
        return member, moved_from
    finally:
        conn.close()


def get_chat_trip_users(chat_id):
    """Everyone who has ever joined a trip in this chat (see chat_trip_users). Keeps checklist
    rows stable: leaving a trip must not erase your toggle button."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute("SELECT user_id, user_name FROM chat_trip_users WHERE chat_id = ?", (chat_id,))
        return [{"user_id": r[0], "user_name": r[1]} for r in c.fetchall()]
    finally:
        conn.close()


def get_trip_members(trip_id):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute("SELECT user_id, user_name FROM trip_members WHERE trip_id = ? ORDER BY user_name", (trip_id,))
        return [{"user_id": r[0], "user_name": r[1]} for r in c.fetchall()]
    finally:
        conn.close()


def get_trips(chat_id):
    """All trips for a chat (newest first) with pin counts, for the Mini App filter panel."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            "SELECT t.id, t.name, t.created_at, t.closed_at, COUNT(p.id)"
            " FROM trips t LEFT JOIN pins p ON p.trip_id = t.id"
            " WHERE t.chat_id = ? GROUP BY t.id ORDER BY t.created_at DESC",
            (chat_id,),
        )
        return [{"id": r[0], "name": r[1], "created_at": r[2], "closed_at": r[3], "pin_count": r[4]} for r in c.fetchall()]
    finally:
        conn.close()


def set_pins_trip(chat_id, pin_ids, trip_id):
    """Assign one or more pins to a trip (or clear with trip_id=None). Both the pins and the trip
    must belong to chat_id — the caller's authorized chat — so a member can never touch another
    chat's data. Returns the number of pins actually updated."""
    if not pin_ids:
        return 0
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        if trip_id is not None:
            c.execute("SELECT 1 FROM trips WHERE id = ? AND chat_id = ?", (trip_id, chat_id))
            if not c.fetchone():
                return 0
        # Placeholders are a fixed count of "?" — the ids themselves are always bound params, never
        # interpolated — so there's no injection surface here.
        placeholders = ",".join("?" for _ in pin_ids)
        c.execute(f"UPDATE pins SET trip_id = ? WHERE chat_id = ? AND id IN ({placeholders})", [trip_id, chat_id, *pin_ids])
        conn.commit()
        return c.rowcount
    finally:
        conn.close()


def get_pin(chat_id, message_id):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            "SELECT video_file_id, user_name, video_type, city, country, country_code FROM pins WHERE chat_id = ? AND message_id = ?", (chat_id, message_id)
        )
        row = c.fetchone()
        return row
    finally:
        conn.close()


def get_pins(chat_id, limit=500, offset=0, user_ids=None, date_from=None, date_to=None, q=None, trip_id=None):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        where = "WHERE p.chat_id = ?"
        params = [chat_id]
        if user_ids:
            placeholders = ",".join("?" * len(user_ids))
            where += " AND p.user_id IN (" + placeholders + ")"
            params.extend(user_ids)
        if date_from:
            where += " AND p.created_at >= ?"
            params.append(date_from)
        if date_to:
            where += " AND p.created_at <= ?"
            params.append(date_to + "T23:59:59")
        if q:
            where += " AND (INSTR(LOWER(p.city), LOWER(?)) > 0 OR INSTR(LOWER(p.country), LOWER(?)) > 0 OR INSTR(LOWER(p.country_code), LOWER(?)) > 0)"
            params.extend([q, q, q])
        if trip_id is not None:
            where += " AND p.trip_id = ?"
            params.append(trip_id)
        c.execute("SELECT COUNT(*) FROM pins p " + where, params)
        total = c.fetchone()[0]
        c.execute(
            "SELECT p.id, p.message_id, p.user_id, p.user_name, p.video_file_id,"
            " p.latitude, p.longitude, p.created_at, p.video_link,"
            " COALESCE(up.pin_color, ''), COALESCE(up.pin_emoji, ''),"
            " p.city, p.country, p.country_code, p.trip_id"
            " FROM pins p LEFT JOIN user_preferences up ON p.user_id = up.user_id"
            " " + where + " ORDER BY p.created_at DESC"
            " LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = c.fetchall()
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "pins": [
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
                    "trip_id": r[14],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


def delete_chat_pins(chat_id):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute("DELETE FROM pins WHERE chat_id = ?", (chat_id,))
        count = c.rowcount
        conn.commit()
        try:
            c.execute("DELETE FROM chat_settings WHERE chat_id = ?", (chat_id,))
            c.execute("DELETE FROM pending_pins WHERE chat_id = ?", (chat_id,))
            c.execute("DELETE FROM trip_members WHERE trip_id IN (SELECT id FROM trips WHERE chat_id = ?)", (chat_id,))
            c.execute("DELETE FROM trips WHERE chat_id = ?", (chat_id,))
            c.execute("DELETE FROM chat_trip_users WHERE chat_id = ?", (chat_id,))
            conn.commit()
        except Exception:
            pass
        return count
    finally:
        conn.close()


def get_all_chat_ids():
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute("SELECT DISTINCT chat_id FROM pins")
        rows = c.fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_chat_ids_for_file(video_file_id):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute("SELECT DISTINCT chat_id FROM pins WHERE video_file_id = ?", (video_file_id,))
        rows = c.fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_pins_meta(chat_id):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            "SELECT DISTINCT p.user_id, p.user_name, COALESCE(up.pin_emoji, ''), COALESCE(up.pin_color, '')"
            " FROM pins p LEFT JOIN user_preferences up ON p.user_id = up.user_id"
            " WHERE p.chat_id = ? ORDER BY p.user_name",
            (chat_id,),
        )
        users = [{"user_id": r[0], "user_name": r[1], "pin_emoji": r[2] or None, "pin_color": r[3] or None} for r in c.fetchall()]
        c.execute("SELECT MIN(created_at), MAX(created_at) FROM pins WHERE chat_id = ?", (chat_id,))
        row = c.fetchone()
        return {"users": users, "min_date": row[0], "max_date": row[1], "trips": get_trips(chat_id)}
    finally:
        conn.close()
