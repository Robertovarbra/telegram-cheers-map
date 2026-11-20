import logging
import math
import sqlite3
from datetime import datetime, timedelta, timezone

from telegram.constants import ReactionEmoji

from config import DB_PATH

logger = logging.getLogger(__name__)

# Default 6 quick-pick reaction pills (all valid native Telegram reactions so two-way sync works cleanly).
DEFAULT_REACTIONS = ("🍾", "🔥", "🤣", "❤️", "🤯", "👍")
_LEGACY_REACTIONS = frozenset(("🍻", "😂", "🥂"))
_TELEGRAM_REACTION_SET = frozenset(e.value for e in ReactionEmoji)
MAX_CAPTION_LEN = 140
MAX_COMMENT_LEN = 500


def normalize_reaction_emoji(emoji: str | None) -> str | None:
    """Validate and normalize a reaction emoji for consistent DB storage & UI display.

    Accepts all standard Telegram ReactionEmoji values (normalizing Telegram's bare '❤' to '❤️'
    for crisp cross-platform rendering) plus legacy in-app cheers emojis."""
    if not isinstance(emoji, str):
        return None
    raw = emoji.strip()
    if not raw or len(raw) > 16:
        return None
    if raw == "❤" or raw == "❤️":
        return "❤️"
    if raw in _TELEGRAM_REACTION_SET or raw in _LEGACY_REACTIONS:
        return raw
    stripped = raw.replace("\ufe0f", "")
    if stripped in _TELEGRAM_REACTION_SET:
        return raw
    return None


def to_telegram_reaction_emoji(emoji: str | None) -> str | None:
    """Map a stored reaction emoji to a valid Telegram ReactionTypeEmoji string, or None if unsupported."""
    if not emoji:
        return None
    if emoji == "❤️":
        return "❤"
    if emoji in _TELEGRAM_REACTION_SET:
        return emoji
    stripped = emoji.replace("\ufe0f", "")
    if stripped in _TELEGRAM_REACTION_SET:
        return stripped
    # Best-effort fallback for legacy cheers-themed emojis so they still reflect in Telegram
    fallback = {"🍻": "🍾", "🥂": "🍾", "😂": "🤣"}.get(emoji)
    return fallback if fallback in _TELEGRAM_REACTION_SET else None


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
        c.execute("""
            CREATE TABLE IF NOT EXISTS analytics_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                chat_id INTEGER,
                user_id INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_analytics_event_created ON analytics_events(event, created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_analytics_created ON analytics_events(created_at)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_charge_id TEXT UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                product_type TEXT NOT NULL,
                target_id INTEGER,
                amount_stars INTEGER NOT NULL,
                currency TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS chat_entitlements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                entitlement_type TEXT NOT NULL,
                target_id INTEGER,
                granted_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_entitlements_chat ON chat_entitlements(chat_id, entitlement_type, target_id)")
        try:
            c.execute("ALTER TABLE pins ADD COLUMN caption TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE chat_settings ADD COLUMN chat_title TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE chat_settings ADD COLUMN throwbacks_enabled INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE chat_settings ADD COLUMN last_throwback_date TEXT")
        except sqlite3.OperationalError:
            pass
        c.execute("""
            CREATE TABLE IF NOT EXISTS pin_reactions (
                pin_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (pin_id, user_id, emoji)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_pin_reactions_pin ON pin_reactions(pin_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pins_user_id ON pins(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pins_chat_msg ON pins(chat_id, message_id)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS pin_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pin_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_pin_comments_pin ON pin_comments(pin_id)")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pin_comments_chat_msg ON pin_comments(chat_id, message_id) WHERE message_id IS NOT NULL")
        c.execute("""
            CREATE TABLE IF NOT EXISTS pin_message_aliases (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                pin_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        conn.commit()
    finally:
        conn.close()


def track(event, chat_id=None, user_id=None):
    """Fire-and-forget analytics write. Never raises into the caller —
    a broken/locked DB must not take down a bot handler."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=1)
        try:
            conn.execute(
                "INSERT INTO analytics_events (event, chat_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                (event, chat_id, user_id, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("track(%s) failed", event, exc_info=True)


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
    """Start a new trip. Other open trips keep running (a chat can hold several in parallel). The
    creator auto-joins ONLY if they're not already on another open trip — creating a trip while you
    are on one (e.g. setting up a parallel/subgroup trip) must not silently pull you off it; you'd
    tap in deliberately to switch. Returns (trip_id, moved_from)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.isolation_level = None
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        now = datetime.now(timezone.utc).isoformat()
        c.execute("INSERT INTO trips (chat_id, name, created_by, created_at) VALUES (?, ?, ?, ?)", (chat_id, name, created_by, now))
        trip_id = c.lastrowid
        c.execute(
            "SELECT 1 FROM trips t JOIN trip_members m ON m.trip_id = t.id WHERE t.chat_id = ? AND t.closed_at IS NULL AND t.id != ? AND m.user_id = ?",
            (chat_id, trip_id, created_by),
        )
        already_on_open_trip = c.fetchone() is not None
        moved_from = [] if already_on_open_trip else _exclusive_join(c, chat_id, trip_id, created_by, creator_name)
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
        c.execute("SELECT id, chat_id, name, created_by, closed_at, checklist_msg_id FROM trips WHERE id = ?", (trip_id,))
        row = c.fetchone()
        return {"id": row[0], "chat_id": row[1], "name": row[2], "created_by": row[3], "closed_at": row[4], "checklist_msg_id": row[5]} if row else None
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


def _fetch_reactions_map(c, pin_ids, viewer_user_id=None):
    """Batch-fetch aggregated reactions for a list of pin IDs in one query."""
    if not pin_ids:
        return {}
    placeholders = ",".join("?" for _ in pin_ids)
    vid = viewer_user_id if viewer_user_id is not None else -1
    c.execute(
        f"SELECT pin_id, emoji, COUNT(*), MAX(CASE WHEN user_id = ? THEN 1 ELSE 0 END)"
        f" FROM pin_reactions WHERE pin_id IN ({placeholders}) GROUP BY pin_id, emoji",
        [vid, *pin_ids],
    )
    order = {e: i for i, e in enumerate(DEFAULT_REACTIONS)}
    by_pin: dict[int, list[dict]] = {}
    for pid, emoji, cnt, me in c.fetchall():
        by_pin.setdefault(pid, []).append({"emoji": emoji, "count": cnt, "me": bool(me)})
    for items in by_pin.values():
        items.sort(key=lambda x: (-x["count"], order.get(x["emoji"], 99), x["emoji"]))
    return by_pin


def _fetch_comments_map(c, pin_ids):
    """Batch-fetch replies/comments for a list of pin IDs in chronological order (oldest -> newest)."""
    if not pin_ids:
        return {}
    placeholders = ",".join("?" for _ in pin_ids)
    c.execute(
        f"SELECT id, pin_id, user_id, user_name, text, created_at FROM pin_comments WHERE pin_id IN ({placeholders}) ORDER BY created_at ASC, id ASC",
        list(pin_ids),
    )
    by_pin: dict[int, list[dict]] = {}
    for cid, pid, uid, uname, text, created_at in c.fetchall():
        by_pin.setdefault(pid, []).append(
            {
                "id": cid,
                "user_id": uid,
                "user_name": uname,
                "text": text,
                "created_at": created_at,
            }
        )
    return by_pin


def get_pins(chat_id, limit=500, offset=0, user_ids=None, date_from=None, date_to=None, q=None, trip_id=None, viewer_user_id=None):
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
            where += (
                " AND (INSTR(LOWER(COALESCE(p.city, '')), LOWER(?)) > 0"
                " OR INSTR(LOWER(COALESCE(p.country, '')), LOWER(?)) > 0"
                " OR INSTR(LOWER(COALESCE(p.country_code, '')), LOWER(?)) > 0"
                " OR INSTR(LOWER(COALESCE(p.caption, '')), LOWER(?)) > 0)"
            )
            params.extend([q, q, q, q])
        if trip_id is not None:
            where += " AND p.trip_id = ?"
            params.append(trip_id)
        c.execute("SELECT COUNT(*) FROM pins p " + where, params)
        total = c.fetchone()[0]
        c.execute(
            "SELECT p.id, p.message_id, p.user_id, p.user_name, p.video_file_id,"
            " p.latitude, p.longitude, p.created_at, p.video_link,"
            " COALESCE(up.pin_color, ''), COALESCE(up.pin_emoji, ''),"
            " p.city, p.country, p.country_code, p.trip_id, p.chat_id, p.caption"
            " FROM pins p LEFT JOIN user_preferences up ON p.user_id = up.user_id"
            " " + where + " ORDER BY p.created_at DESC"
            " LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = c.fetchall()
        pin_ids = [r[0] for r in rows]
        rx_map = _fetch_reactions_map(c, pin_ids, viewer_user_id=viewer_user_id)
        cm_map = _fetch_comments_map(c, pin_ids)
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
                    "chat_id": r[15],
                    "caption": r[16],
                    "reactions": rx_map.get(r[0], []),
                    "comments": cm_map.get(r[0], []),
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
        try:
            c.execute("DELETE FROM pin_reactions WHERE pin_id IN (SELECT id FROM pins WHERE chat_id = ?)", (chat_id,))
            c.execute("DELETE FROM pin_comments WHERE chat_id = ?", (chat_id,))
            c.execute("DELETE FROM pin_message_aliases WHERE chat_id = ?", (chat_id,))
        except Exception:
            pass
        c.execute("DELETE FROM pins WHERE chat_id = ?", (chat_id,))
        count = c.rowcount
        conn.commit()
        try:
            c.execute("DELETE FROM chat_settings WHERE chat_id = ?", (chat_id,))
            c.execute("DELETE FROM pending_pins WHERE chat_id = ?", (chat_id,))
            c.execute("DELETE FROM trip_members WHERE trip_id IN (SELECT id FROM trips WHERE chat_id = ?)", (chat_id,))
            c.execute("DELETE FROM trips WHERE chat_id = ?", (chat_id,))
            c.execute("DELETE FROM chat_trip_users WHERE chat_id = ?", (chat_id,))
            c.execute("DELETE FROM chat_entitlements WHERE chat_id = ?", (chat_id,))
            conn.commit()
        except Exception:
            pass
        return count
    finally:
        conn.close()


def migrate_chat_data(old_chat_id, new_chat_id):
    """Move a basic group's data to the supergroup it was upgraded to. Returns the number of pins moved.

    Idempotent: Telegram announces a migration in both chats, and startup can also detect it, so later calls find nothing left.
    Message ids from the old group mean nothing in the supergroup (whose ids restart at 1), so every stored reference to
    an old message is cleared rather than carried over, where it could match an unrelated new message. A pin's
    message_id is NOT NULL, so 0 stands for "no Telegram message" (real ids are >= 1; get_pin_message_ref callers
    already treat 0 as missing). Payments and analytics stay under the old id as historical records.
    """
    if old_chat_id == new_chat_id:
        return 0
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        conn.isolation_level = None
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE pins SET chat_id = ?, message_id = 0 WHERE chat_id = ?", (new_chat_id, old_chat_id))
        moved = c.rowcount
        c.execute("UPDATE pin_comments SET chat_id = ?, message_id = NULL WHERE chat_id = ?", (new_chat_id, old_chat_id))
        c.execute("DELETE FROM pin_message_aliases WHERE chat_id = ?", (old_chat_id,))
        c.execute("UPDATE trips SET chat_id = ?, checklist_msg_id = NULL WHERE chat_id = ?", (new_chat_id, old_chat_id))
        c.execute("UPDATE chat_entitlements SET chat_id = ? WHERE chat_id = ?", (new_chat_id, old_chat_id))
        # Composite-key tables: a row already present under the new id wins; whatever couldn't move is dropped.
        c.execute("UPDATE OR IGNORE chat_trip_users SET chat_id = ? WHERE chat_id = ?", (new_chat_id, old_chat_id))
        c.execute("DELETE FROM chat_trip_users WHERE chat_id = ?", (old_chat_id,))
        c.execute(
            "UPDATE OR IGNORE pending_pins SET chat_id = ?, message_id = 0, prompt_msg_id = NULL WHERE chat_id = ?",
            (new_chat_id, old_chat_id),
        )
        c.execute("DELETE FROM pending_pins WHERE chat_id = ?", (old_chat_id,))
        c.execute(
            "UPDATE OR IGNORE chat_settings SET chat_id = ?, pinned_map_msg_id = NULL, updated_at = ? WHERE chat_id = ?",
            (new_chat_id, datetime.now(timezone.utc).isoformat(), old_chat_id),
        )
        c.execute("DELETE FROM chat_settings WHERE chat_id = ?", (old_chat_id,))
        c.execute("COMMIT")
        return moved
    except Exception:
        if conn.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def get_all_chat_ids():
    """Every chat we hold data for — pins, trips, or trip rosters. Startup cleanup iterates these to
    purge chats the bot was removed from; trips can create chat-scoped data before any pin exists, so
    a trip-only chat must be discoverable here too (delete_chat_pins already purges its trip data)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute("SELECT chat_id FROM pins UNION SELECT chat_id FROM trips UNION SELECT chat_id FROM chat_trip_users")
        return [r[0] for r in c.fetchall()]
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


def delete_pin(chat_id, pin_id, user_id):
    """Delete a single pin iff it belongs to both `chat_id` and `user_id` (owner-only).

    Runs inside a BEGIN IMMEDIATE transaction so concurrent deletes can't double-succeed.
    Returns ("ok", {"id", "trip_id", "video_file_id"}), ("not_found", None), or ("forbidden", None)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.isolation_level = None
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute("SELECT user_id, trip_id, video_file_id FROM pins WHERE id = ? AND chat_id = ?", (pin_id, chat_id))
        row = c.fetchone()
        if not row:
            c.execute("COMMIT")
            return "not_found", None
        owner_id, trip_id, video_file_id = row
        if owner_id != user_id:
            c.execute("COMMIT")
            return "forbidden", None
        c.execute("DELETE FROM pin_reactions WHERE pin_id = ?", (pin_id,))
        c.execute("DELETE FROM pin_comments WHERE pin_id = ?", (pin_id,))
        c.execute("DELETE FROM pin_message_aliases WHERE pin_id = ?", (pin_id,))
        c.execute("DELETE FROM pins WHERE id = ? AND chat_id = ? AND user_id = ?", (pin_id, chat_id, user_id))
        c.execute("COMMIT")
        return "ok", {"id": pin_id, "trip_id": trip_id, "video_file_id": video_file_id}
    finally:
        conn.close()


def get_analytics_summary(days=7):
    """Aggregate high-level product health metrics over the last `days` plus all-time totals."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        c.execute("SELECT COUNT(*), COUNT(DISTINCT chat_id), COUNT(DISTINCT user_id) FROM pins")
        total_pins, total_chats, total_users = c.fetchone()

        c.execute("SELECT COUNT(*), SUM(CASE WHEN closed_at IS NULL THEN 1 ELSE 0 END) FROM trips")
        total_trips, open_trips = c.fetchone()
        open_trips = open_trips or 0

        c.execute("SELECT event, COUNT(*) FROM analytics_events WHERE created_at >= ? GROUP BY event", (since,))
        events = dict(c.fetchall())

        c.execute("SELECT COUNT(DISTINCT chat_id) FROM analytics_events WHERE created_at >= ? AND chat_id IS NOT NULL", (since,))
        active_chats = c.fetchone()[0]

        c.execute("SELECT COUNT(DISTINCT user_id) FROM analytics_events WHERE created_at >= ? AND user_id IS NOT NULL", (since,))
        active_users = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM pins WHERE created_at >= ?", (since,))
        pins_in_window = c.fetchone()[0]

        c.execute(
            "SELECT chat_id, COUNT(*) AS cnt FROM pins WHERE created_at >= ? GROUP BY chat_id ORDER BY cnt DESC LIMIT 5",
            (since,),
        )
        top_chats = [{"chat_id": r[0], "pins": r[1]} for r in c.fetchall()]

        c.execute("SELECT COUNT(*), COALESCE(SUM(amount_stars), 0) FROM payments WHERE created_at >= ?", (since,))
        payments_window, stars_window = c.fetchone()

        c.execute("SELECT COUNT(*), COALESCE(SUM(amount_stars), 0) FROM payments")
        payments_total, stars_total = c.fetchone()

        return {
            "days": days,
            "total_pins": total_pins,
            "total_chats": total_chats,
            "total_users": total_users,
            "total_trips": total_trips,
            "open_trips": open_trips,
            "active_chats": active_chats,
            "active_users": active_users,
            "pins_in_window": pins_in_window,
            "events": events,
            "top_chats": top_chats,
            "payments_window": payments_window,
            "stars_window": stars_window,
            "payments_total": payments_total,
            "stars_total": stars_total,
        }
    finally:
        conn.close()


def _haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_chat_stats(chat_id):
    """Passport & leaderboard summary for a single chat (used by GET /api/stats)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            "SELECT p.user_id, p.user_name, p.latitude, p.longitude, p.city, p.country, p.country_code, p.created_at,"
            " COALESCE(up.pin_color, ''), COALESCE(up.pin_emoji, '')"
            " FROM pins p LEFT JOIN user_preferences up ON p.user_id = up.user_id"
            " WHERE p.chat_id = ? ORDER BY p.created_at ASC",
            (chat_id,),
        )
        rows = c.fetchall()

        c.execute("SELECT COUNT(*) FROM trips WHERE chat_id = ?", (chat_id,))
        total_trips = c.fetchone()[0]

        countries: dict[str, dict] = {}
        cities: dict[tuple[str, str], dict] = {}
        users: dict[int, dict] = {}
        # Track distance per user chronologically (not interleaved across different friends), and
        # ignore <100 m GPS drift so staying at one bar doesn't inflate traveled distance.
        prev_coords_by_user: dict[int, tuple[float, float]] = {}

        for uid, uname, lat, lng, city, country, cc, _created_at, color, emoji in rows:
            cc_norm = (cc or "").strip().lower()
            if country:
                key = cc_norm or country.lower()
                entry = countries.get(key)
                if entry is None:
                    countries[key] = {"country": country, "country_code": cc_norm or None, "pin_count": 1}
                else:
                    entry["pin_count"] += 1

            if city:
                ckey = (city.lower(), cc_norm)
                centry = cities.get(ckey)
                if centry is None:
                    cities[ckey] = {"city": city, "country_code": cc_norm or None, "pin_count": 1}
                else:
                    centry["pin_count"] += 1

            u = users.get(uid)
            if u is None:
                u = {
                    "user_id": uid,
                    "user_name": uname,
                    "pin_color": color or None,
                    "pin_emoji": emoji or None,
                    "pin_count": 0,
                    "distance_km": 0.0,
                    "_countries": set(),
                    "_cities": set(),
                }
                users[uid] = u
            prev = prev_coords_by_user.get(uid)
            if prev is not None:
                step_km = _haversine_km(prev[0], prev[1], lat, lng)
                if step_km >= 0.1:
                    u["distance_km"] += step_km
            prev_coords_by_user[uid] = (lat, lng)

            u["user_name"] = uname  # chronological scan -> freshest display name wins
            u["pin_count"] += 1
            if country:
                u["_countries"].add(cc_norm or country.lower())
            if city:
                u["_cities"].add((city.lower(), cc_norm))

        total_km = sum(u["distance_km"] for u in users.values())

        leaderboard = [
            {
                "user_id": u["user_id"],
                "user_name": u["user_name"],
                "pin_color": u["pin_color"],
                "pin_emoji": u["pin_emoji"],
                "pin_count": u["pin_count"],
                "country_count": len(u["_countries"]),
                "city_count": len(u["_cities"]),
                "distance_km": round(u["distance_km"]),
            }
            for u in sorted(users.values(), key=lambda x: (-x["pin_count"], x["user_name"].lower()))
        ]

        sorted_countries = sorted(countries.values(), key=lambda x: (-x["pin_count"], x["country"].lower()))
        sorted_cities = sorted(cities.values(), key=lambda x: (-x["pin_count"], x["city"].lower()))[:20]

        return {
            "total_pins": len(rows),
            "total_users": len(users),
            "total_trips": total_trips,
            "total_countries": len(countries),
            "total_cities": len(cities),
            "total_distance_km": round(total_km),
            "first_pin_at": rows[0][7] if rows else None,
            "last_pin_at": rows[-1][7] if rows else None,
            "countries": sorted_countries,
            "cities": sorted_cities,
            "leaderboard": leaderboard,
        }
    finally:
        conn.close()


def record_payment_and_grant(telegram_charge_id, user_id, chat_id, product_type, target_id, amount_stars, currency="XTR", duration_days=None):
    """Atomically record a completed Telegram Stars payment and grant the chat entitlement.

    Idempotent on `telegram_charge_id` so Telegram webhook/polling retries never double-grant.
    Returns True if newly recorded, False if this charge_id was already processed."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.isolation_level = None
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute("SELECT 1 FROM payments WHERE telegram_charge_id = ?", (telegram_charge_id,))
        if c.fetchone():
            c.execute("COMMIT")
            return False

        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        c.execute(
            "INSERT INTO payments (telegram_charge_id, user_id, chat_id, product_type, target_id, amount_stars, currency, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (telegram_charge_id, user_id, chat_id, product_type, target_id, amount_stars, currency, now),
        )

        expires_at = None
        if product_type == "group_pass" and duration_days:
            # Extend from an existing active pass if one exists, otherwise from now.
            c.execute(
                "SELECT MAX(expires_at) FROM chat_entitlements WHERE chat_id = ? AND entitlement_type = 'group_pass' AND expires_at > ?",
                (chat_id, now),
            )
            existing_exp = c.fetchone()[0]
            base_dt = datetime.fromisoformat(existing_exp) if existing_exp else now_dt
            expires_at = (base_dt + timedelta(days=duration_days)).isoformat()

        c.execute(
            "INSERT INTO chat_entitlements (chat_id, entitlement_type, target_id, granted_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, product_type, target_id, user_id, now, expires_at),
        )
        c.execute("COMMIT")
        return True
    finally:
        conn.close()


def get_chat_entitlements(chat_id):
    """Return active paid entitlements for a chat (Group Pass + HD-unlocked trip IDs)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        c.execute(
            "SELECT MAX(expires_at) FROM chat_entitlements WHERE chat_id = ? AND entitlement_type = 'group_pass' AND (expires_at IS NULL OR expires_at > ?)",
            (chat_id, now),
        )
        pass_exp = c.fetchone()[0]
        c.execute(
            "SELECT DISTINCT target_id FROM chat_entitlements WHERE chat_id = ? AND entitlement_type = 'trip_reel' AND target_id IS NOT NULL",
            (chat_id,),
        )
        unlocked_trips = sorted(r[0] for r in c.fetchall())
        return {
            "has_group_pass": pass_exp is not None,
            "group_pass_expires_at": pass_exp,
            "unlocked_trip_ids": unlocked_trips,
        }
    finally:
        conn.close()


def is_trip_reel_unlocked(chat_id, trip_id):
    """True if this trip's HD unwatermarked reel is unlocked (either directly or via active Group Pass)."""
    ent = get_chat_entitlements(chat_id)
    return ent["has_group_pass"] or (trip_id in ent["unlocked_trip_ids"])


def get_trip_pins_for_reel(chat_id, trip_id, limit=20):
    """Chronological pins (oldest -> newest) for rendering a Trip Recap Reel."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            "SELECT p.id, p.user_id, p.user_name, p.video_file_id, p.city, p.country, p.country_code, p.created_at,"
            " COALESCE(up.pin_color, ''), COALESCE(up.pin_emoji, '')"
            " FROM pins p LEFT JOIN user_preferences up ON p.user_id = up.user_id"
            " WHERE p.chat_id = ? AND p.trip_id = ?"
            " ORDER BY p.created_at ASC LIMIT ?",
            (chat_id, trip_id, limit),
        )
        return [
            {
                "id": r[0],
                "user_id": r[1],
                "user_name": r[2],
                "video_file_id": r[3],
                "city": r[4],
                "country": r[5],
                "country_code": r[6],
                "created_at": r[7],
                "pin_color": r[8] or None,
                "pin_emoji": r[9] or None,
            }
            for r in c.fetchall()
        ]
    finally:
        conn.close()


def is_file_owner(video_file_id, user_id):
    """True iff `user_id` owns at least one pin referencing `video_file_id` (used for My Atlas video playback)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute("SELECT 1 FROM pins WHERE video_file_id = ? AND user_id = ? LIMIT 1", (video_file_id, user_id))
        return c.fetchone() is not None
    finally:
        conn.close()


def set_chat_title(chat_id, title):
    """Cache a group's human-readable title in chat_settings so My Atlas can label pins by source group."""
    clean = (title or "").strip()
    if not clean or chat_id > 0:
        return
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=1)
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO chat_settings (chat_id, chat_title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    chat_title = excluded.chat_title,
                    updated_at = excluded.updated_at
                """,
                (chat_id, clean[:120], now, now),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("set_chat_title failed for chat %s", chat_id, exc_info=True)


def _format_atlas_chat_title(chat_id, user_id, stored_title):
    if chat_id == user_id:
        return "Solo (DM)"
    return stored_title or "Group"


def get_atlas_pins(user_id, limit=500, offset=0, chat_ids=None, date_from=None, date_to=None, q=None):
    """All pins owned by `user_id` across every group plus solo DM pins (My Atlas)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        where = "WHERE p.user_id = ?"
        params = [user_id]
        if chat_ids:
            placeholders = ",".join("?" * len(chat_ids))
            where += " AND p.chat_id IN (" + placeholders + ")"
            params.extend(chat_ids)
        if date_from:
            where += " AND p.created_at >= ?"
            params.append(date_from)
        if date_to:
            where += " AND p.created_at <= ?"
            params.append(date_to + "T23:59:59")
        if q:
            where += (
                " AND (INSTR(LOWER(COALESCE(p.city, '')), LOWER(?)) > 0"
                " OR INSTR(LOWER(COALESCE(p.country, '')), LOWER(?)) > 0"
                " OR INSTR(LOWER(COALESCE(p.country_code, '')), LOWER(?)) > 0"
                " OR INSTR(LOWER(COALESCE(p.caption, '')), LOWER(?)) > 0)"
            )
            params.extend([q, q, q, q])
        c.execute("SELECT COUNT(*) FROM pins p " + where, params)
        total = c.fetchone()[0]
        c.execute(
            "SELECT p.id, p.message_id, p.user_id, p.user_name, p.video_file_id,"
            " p.latitude, p.longitude, p.created_at, p.video_link,"
            " COALESCE(up.pin_color, ''), COALESCE(up.pin_emoji, ''),"
            " p.city, p.country, p.country_code, p.trip_id, p.chat_id, p.caption, cs.chat_title"
            " FROM pins p"
            " LEFT JOIN user_preferences up ON p.user_id = up.user_id"
            " LEFT JOIN chat_settings cs ON p.chat_id = cs.chat_id"
            " " + where + " ORDER BY p.created_at DESC"
            " LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        rows = c.fetchall()
        pin_ids = [r[0] for r in rows]
        rx_map = _fetch_reactions_map(c, pin_ids, viewer_user_id=user_id)
        cm_map = _fetch_comments_map(c, pin_ids)
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
                    "chat_id": r[15],
                    "caption": r[16],
                    "chat_title": _format_atlas_chat_title(r[15], user_id, r[17]),
                    "reactions": rx_map.get(r[0], []),
                    "comments": cm_map.get(r[0], []),
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


def get_atlas_meta(user_id):
    """Filter metadata for a user's personal My Atlas view across all their chats."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            "SELECT p.chat_id, cs.chat_title, COUNT(p.id)"
            " FROM pins p LEFT JOIN chat_settings cs ON p.chat_id = cs.chat_id"
            " WHERE p.user_id = ? GROUP BY p.chat_id ORDER BY COUNT(p.id) DESC",
            (user_id,),
        )
        chats = [
            {
                "chat_id": r[0],
                "title": _format_atlas_chat_title(r[0], user_id, r[1]),
                "pin_count": r[2],
                "is_solo": r[0] == user_id,
            }
            for r in c.fetchall()
        ]
        c.execute("SELECT MIN(created_at), MAX(created_at) FROM pins WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        return {"users": [], "chats": chats, "min_date": row[0], "max_date": row[1], "trips": []}
    finally:
        conn.close()


def get_atlas_stats(user_id):
    """Personal Passport & per-group breakdown for a user's My Atlas across all chats."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            "SELECT p.chat_id, p.latitude, p.longitude, p.city, p.country, p.country_code, p.created_at, cs.chat_title"
            " FROM pins p LEFT JOIN chat_settings cs ON p.chat_id = cs.chat_id"
            " WHERE p.user_id = ? ORDER BY p.created_at ASC",
            (user_id,),
        )
        rows = c.fetchall()

        countries: dict[str, dict] = {}
        cities: dict[tuple[str, str], dict] = {}
        chats: dict[int, dict] = {}
        prev_coords: tuple[float, float] | None = None
        total_km = 0.0

        for cid, lat, lng, city, country, cc, _created_at, stored_title in rows:
            cc_norm = (cc or "").strip().lower()
            if country:
                key = cc_norm or country.lower()
                entry = countries.get(key)
                if entry is None:
                    countries[key] = {"country": country, "country_code": cc_norm or None, "pin_count": 1}
                else:
                    entry["pin_count"] += 1

            if city:
                ckey = (city.lower(), cc_norm)
                centry = cities.get(ckey)
                if centry is None:
                    cities[ckey] = {"city": city, "country_code": cc_norm or None, "pin_count": 1}
                else:
                    centry["pin_count"] += 1

            if prev_coords is not None:
                step_km = _haversine_km(prev_coords[0], prev_coords[1], lat, lng)
                if step_km >= 0.1:
                    total_km += step_km
            prev_coords = (lat, lng)

            ch = chats.get(cid)
            if ch is None:
                ch = {
                    "chat_id": cid,
                    "title": _format_atlas_chat_title(cid, user_id, stored_title),
                    "is_solo": cid == user_id,
                    "pin_count": 0,
                    "_countries": set(),
                    "_cities": set(),
                }
                chats[cid] = ch
            ch["pin_count"] += 1
            if country:
                ch["_countries"].add(cc_norm or country.lower())
            if city:
                ch["_cities"].add((city.lower(), cc_norm))

        chat_rows = [
            {
                "chat_id": ch["chat_id"],
                "title": ch["title"],
                "is_solo": ch["is_solo"],
                "pin_count": ch["pin_count"],
                "country_count": len(ch["_countries"]),
                "city_count": len(ch["_cities"]),
            }
            for ch in sorted(chats.values(), key=lambda x: (-x["pin_count"], x["title"].lower()))
        ]

        sorted_countries = sorted(countries.values(), key=lambda x: (-x["pin_count"], x["country"].lower()))
        sorted_cities = sorted(cities.values(), key=lambda x: (-x["pin_count"], x["city"].lower()))[:20]

        return {
            "mode": "atlas",
            "total_pins": len(rows),
            "total_chats": len(chats),
            "total_countries": len(countries),
            "total_cities": len(cities),
            "total_distance_km": round(total_km),
            "first_pin_at": rows[0][6] if rows else None,
            "last_pin_at": rows[-1][6] if rows else None,
            "countries": sorted_countries,
            "cities": sorted_cities,
            "chats": chat_rows,
            "leaderboard": [],
        }
    finally:
        conn.close()


def toggle_pin_reaction(chat_id, pin_id, user_id, emoji):
    """Set, switch, or clear a user's single reaction emoji on a pin (matching Telegram's 1-reaction UX).

    Tapping the same emoji removes it; tapping a different emoji replaces the user's previous
    reaction on this pin. Returns ("ok", added: bool, reactions: list) or ("not_found", False, [])."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.isolation_level = None
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute("SELECT 1 FROM pins WHERE id = ? AND chat_id = ?", (pin_id, chat_id))
        if not c.fetchone():
            c.execute("COMMIT")
            return "not_found", False, []
        c.execute("SELECT 1 FROM pin_reactions WHERE pin_id = ? AND user_id = ? AND emoji = ?", (pin_id, user_id, emoji))
        if c.fetchone():
            c.execute("DELETE FROM pin_reactions WHERE pin_id = ? AND user_id = ? AND emoji = ?", (pin_id, user_id, emoji))
            added = False
        else:
            now = datetime.now(timezone.utc).isoformat()
            c.execute("DELETE FROM pin_reactions WHERE pin_id = ? AND user_id = ?", (pin_id, user_id))
            c.execute(
                "INSERT INTO pin_reactions (pin_id, user_id, emoji, created_at) VALUES (?, ?, ?, ?)",
                (pin_id, user_id, emoji, now),
            )
            added = True
        reactions = _fetch_reactions_map(c, [pin_id], viewer_user_id=user_id).get(pin_id, [])
        c.execute("COMMIT")
        return "ok", added, reactions
    finally:
        conn.close()


def register_pin_message_alias(chat_id, message_id, pin_id):
    """Map an alternate Telegram message (e.g. a resent throwback video note) back to its original pin."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO pin_message_aliases (chat_id, message_id, pin_id) VALUES (?, ?, ?)",
            (chat_id, message_id, pin_id),
        )
        conn.commit()
    finally:
        conn.close()


def _resolve_pin_id_for_message(c, chat_id, message_id):
    c.execute("SELECT id FROM pins WHERE chat_id = ? AND message_id = ? LIMIT 1", (chat_id, message_id))
    row = c.fetchone()
    if row:
        return row[0]
    c.execute("SELECT pin_id FROM pin_message_aliases WHERE chat_id = ? AND message_id = ? LIMIT 1", (chat_id, message_id))
    alias = c.fetchone()
    return alias[0] if alias else None


def get_pin_by_message(chat_id, message_id):
    """Look up a pin by its Telegram (chat_id, message_id) — including throwback aliases — to sync reactions & replies."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        pin_id = _resolve_pin_id_for_message(c, chat_id, message_id)
        if not pin_id:
            return None
        c.execute("SELECT id, chat_id, message_id, user_id FROM pins WHERE id = ? LIMIT 1", (pin_id,))
        row = c.fetchone()
        if not row:
            return None
        return {"id": row[0], "chat_id": row[1], "message_id": row[2], "user_id": row[3]}
    finally:
        conn.close()


def get_pin_message_ref(chat_id, pin_id):
    """Return the original Telegram message_id for a pin in `chat_id`, or None if not found."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute("SELECT message_id FROM pins WHERE id = ? AND chat_id = ?", (pin_id, chat_id))
        row = c.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def sync_telegram_reactions(chat_id, message_id, user_id, added_emojis, removed_emojis):
    """Apply a user's Telegram reaction delta on a video note directly to `pin_reactions`.

    Returns the pin_id if a matching pin was updated, else None."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.isolation_level = None
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        pin_id = _resolve_pin_id_for_message(c, chat_id, message_id)
        if not pin_id:
            c.execute("COMMIT")
            return None
        now = datetime.now(timezone.utc).isoformat()
        for em in removed_emojis:
            c.execute("DELETE FROM pin_reactions WHERE pin_id = ? AND user_id = ? AND emoji = ?", (pin_id, user_id, em))
        for em in added_emojis:
            c.execute(
                "INSERT OR IGNORE INTO pin_reactions (pin_id, user_id, emoji, created_at) VALUES (?, ?, ?, ?)",
                (pin_id, user_id, em, now),
            )
        c.execute("COMMIT")
        return pin_id
    finally:
        conn.close()


def add_pin_comment(chat_id, pin_id, user_id, user_name, text, message_id=None):
    """Add a reply/comment to a pin in `chat_id`. Returns comment dict on success, or None if pin doesn't exist."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.isolation_level = None
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute("SELECT 1 FROM pins WHERE id = ? AND chat_id = ?", (pin_id, chat_id))
        if not c.fetchone():
            c.execute("COMMIT")
            return None
        now = datetime.now(timezone.utc).isoformat()
        if message_id is not None:
            c.execute(
                "SELECT id, user_id, user_name, text, created_at FROM pin_comments WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            )
            existing = c.fetchone()
            if existing:
                c.execute("COMMIT")
                return {
                    "id": existing[0],
                    "user_id": existing[1],
                    "user_name": existing[2],
                    "text": existing[3],
                    "created_at": existing[4],
                }
        c.execute(
            "INSERT INTO pin_comments (pin_id, chat_id, message_id, user_id, user_name, text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pin_id, chat_id, message_id, user_id, user_name, text, now),
        )
        cid = c.lastrowid
        c.execute("COMMIT")
        return {
            "id": cid,
            "user_id": user_id,
            "user_name": user_name,
            "text": text,
            "created_at": now,
        }
    finally:
        conn.close()


def update_pin_comment_by_message(chat_id, message_id, text):
    """Keep an already-captured Telegram reply in sync if the user edits their message."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute("UPDATE pin_comments SET text = ? WHERE chat_id = ? AND message_id = ?", (text, chat_id, message_id))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


def delete_pin_comment(chat_id, comment_id, user_id):
    """Delete a comment iff it belongs to `chat_id` and `user_id` (owner-only).

    Returns ('ok', message_id), ('not_found', None), or ('forbidden', None)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.isolation_level = None
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute("SELECT user_id, message_id FROM pin_comments WHERE id = ? AND chat_id = ?", (comment_id, chat_id))
        row = c.fetchone()
        if not row:
            c.execute("COMMIT")
            return "not_found", None
        owner_id, msg_id = row
        if owner_id != user_id:
            c.execute("COMMIT")
            return "forbidden", None
        c.execute("DELETE FROM pin_comments WHERE id = ? AND chat_id = ? AND user_id = ?", (comment_id, chat_id, user_id))
        c.execute("COMMIT")
        return "ok", msg_id
    finally:
        conn.close()


def update_pin_caption(chat_id, pin_id, user_id, caption):
    """Set or clear a pin's caption (owner-only). Returns 'ok', 'not_found', or 'forbidden'."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.isolation_level = None
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute("SELECT user_id FROM pins WHERE id = ? AND chat_id = ?", (pin_id, chat_id))
        row = c.fetchone()
        if not row:
            c.execute("COMMIT")
            return "not_found"
        if row[0] != user_id:
            c.execute("COMMIT")
            return "forbidden"
        c.execute("UPDATE pins SET caption = ? WHERE id = ? AND chat_id = ? AND user_id = ?", (caption, pin_id, chat_id, user_id))
        c.execute("COMMIT")
        return "ok"
    finally:
        conn.close()


def get_pin_owner(chat_id, pin_id):
    """Return the owner user_id of a pin in `chat_id`, or None if it doesn't exist."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute("SELECT user_id FROM pins WHERE id = ? AND chat_id = ?", (pin_id, chat_id))
        row = c.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def update_pin_location(chat_id, pin_id, user_id, lat, lng, city, country, country_code):
    """Update a pin's coordinates and reverse-geocoded place metadata (owner-only).

    Returns 'ok', 'not_found', or 'forbidden'."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.isolation_level = None
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute("SELECT user_id FROM pins WHERE id = ? AND chat_id = ?", (pin_id, chat_id))
        row = c.fetchone()
        if not row:
            c.execute("COMMIT")
            return "not_found"
        if row[0] != user_id:
            c.execute("COMMIT")
            return "forbidden"
        c.execute(
            "UPDATE pins SET latitude = ?, longitude = ?, city = ?, country = ?, country_code = ? WHERE id = ? AND chat_id = ? AND user_id = ?",
            (lat, lng, city, country, country_code, pin_id, chat_id, user_id),
        )
        c.execute("COMMIT")
        return "ok"
    finally:
        conn.close()


def get_chat_throwbacks_enabled(chat_id):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute("SELECT throwbacks_enabled FROM chat_settings WHERE chat_id = ?", (chat_id,))
        row = c.fetchone()
        if row is None or row[0] is None:
            return True
        return bool(row[0])
    finally:
        conn.close()


def set_chat_throwbacks_enabled(chat_id, enabled):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO chat_settings (chat_id, throwbacks_enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                throwbacks_enabled = excluded.throwbacks_enabled,
                updated_at = excluded.updated_at
            """,
            (chat_id, 1 if enabled else 0, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def mark_throwback_sent(chat_id, date_str):
    conn = sqlite3.connect(str(DB_PATH))
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO chat_settings (chat_id, last_throwback_date, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                last_throwback_date = excluded.last_throwback_date,
                updated_at = excluded.updated_at
            """,
            (chat_id, date_str, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_due_throwback_chats(month_day, current_year, today_str):
    """Group chats (chat_id < 0) that have pins on `month_day` ('MM-DD') from a prior year,
    have throwbacks enabled (default on), and have not already received a throwback today."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            "SELECT DISTINCT p.chat_id"
            " FROM pins p LEFT JOIN chat_settings cs ON p.chat_id = cs.chat_id"
            " WHERE p.chat_id < 0"
            " AND SUBSTR(p.created_at, 6, 5) = ?"
            " AND CAST(SUBSTR(p.created_at, 1, 4) AS INTEGER) < ?"
            " AND COALESCE(cs.throwbacks_enabled, 1) = 1"
            " AND COALESCE(cs.last_throwback_date, '') != ?",
            (month_day, current_year, today_str),
        )
        return [r[0] for r in c.fetchall()]
    finally:
        conn.close()


def get_on_this_day_pins(chat_id, month_day, current_year):
    """All pins in `chat_id` recorded on `month_day` ('MM-DD') in prior years, newest year first."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        c.execute(
            "SELECT id, user_id, user_name, video_file_id, city, country, country_code, created_at, caption"
            " FROM pins"
            " WHERE chat_id = ?"
            " AND SUBSTR(created_at, 6, 5) = ?"
            " AND CAST(SUBSTR(created_at, 1, 4) AS INTEGER) < ?"
            " ORDER BY created_at DESC",
            (chat_id, month_day, current_year),
        )
        return [
            {
                "id": r[0],
                "user_id": r[1],
                "user_name": r[2],
                "video_file_id": r[3],
                "city": r[4],
                "country": r[5],
                "country_code": r[6],
                "created_at": r[7],
                "caption": r[8],
            }
            for r in c.fetchall()
        ]
    finally:
        conn.close()


def get_random_throwback_pin(chat_id, min_age_days=30):
    """Pick a random nostalgic pin from `chat_id` at least `min_age_days` old (for manual /throwback)."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        c = conn.cursor()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=min_age_days)).isoformat()
        c.execute(
            "SELECT id, user_id, user_name, video_file_id, city, country, country_code, created_at, caption"
            " FROM pins WHERE chat_id = ? AND created_at <= ? ORDER BY RANDOM() LIMIT 1",
            (chat_id, cutoff),
        )
        r = c.fetchone()
        if not r:
            return None
        return {
            "id": r[0],
            "user_id": r[1],
            "user_name": r[2],
            "video_file_id": r[3],
            "city": r[4],
            "country": r[5],
            "country_code": r[6],
            "created_at": r[7],
            "caption": r[8],
        }
    finally:
        conn.close()
