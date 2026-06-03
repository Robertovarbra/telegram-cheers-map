from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from aiohttp import web
from telegram import ChatMember

from config import INITDATA_MAX_AGE, TOKEN, logger

_MEMBER_TTL = 300  # seconds to cache a membership result (~one get_chat_member call per session)
_MEMBER_CACHE_MAX = 1000
_member_cache: dict[tuple[int, int], tuple[float, bool]] = {}

_MEMBER_STATUSES = (ChatMember.OWNER, ChatMember.ADMINISTRATOR, ChatMember.MEMBER)

# Derived solely from the (static) bot token, so compute it once at import.
_SECRET_KEY = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()


def verify_init_data(init_data: str | None) -> dict | None:
    """Validate a Telegram Mini App initData string.

    Checks the HMAC-SHA256 signature against the bot token and the auth_date
    freshness. Returns the parsed fields (with ``user`` decoded to a dict) on
    success, or ``None`` if anything fails to validate.
    """
    if not init_data:
        return None

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    auth_date = pairs.get("auth_date")
    if not auth_date or not auth_date.isdigit():
        return None
    if INITDATA_MAX_AGE and (time.time() - int(auth_date)) > INITDATA_MAX_AGE:
        return None

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    expected_hash = hmac.new(_SECRET_KEY, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        return None

    if "user" in pairs:
        try:
            pairs["user"] = json.loads(pairs["user"])
        except ValueError:
            return None
    return pairs


def extract_init_data(request: web.Request) -> str | None:
    """Pull initData from the ``Authorization: tma <data>`` header, falling back
    to the ``?auth=`` query param (used by ``<video>`` tags, which can't set headers)."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("tma "):
        return auth[4:]
    return request.rel_url.query.get("auth")


async def is_member(bot, chat_id: int, user_id: int) -> bool:
    """Return True if the user currently belongs to the chat.

    Results are cached for ~_MEMBER_TTL seconds so we make roughly one
    get_chat_member API call per session. Any error is treated as "not a member".
    """
    now = time.time()
    key = (chat_id, user_id)
    cached = _member_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]

    try:
        member = await bot.get_chat_member(chat_id, user_id)
        ok = member.status in _MEMBER_STATUSES or (member.status == ChatMember.RESTRICTED and getattr(member, "is_member", False))
    except Exception as e:
        logger.info("Membership check failed for chat %s user %s: %s", chat_id, user_id, e)
        ok = False

    if len(_member_cache) >= _MEMBER_CACHE_MAX:
        for stale in [k for k, v in _member_cache.items() if v[0] <= now]:
            _member_cache.pop(stale, None)
    _member_cache[key] = (now + _MEMBER_TTL, ok)
    return ok
