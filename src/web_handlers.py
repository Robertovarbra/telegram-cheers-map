import asyncio
import hashlib
import math
import time
from collections import OrderedDict
from pathlib import Path

from aiohttp import ClientSession as AiohttpClient
from aiohttp import ClientTimeout, web
from telegram import LabeledPrice, ReactionTypeEmoji

from auth import extract_init_data, is_member, verify_init_data
from config import (
    GROUP_PASS_DAYS,
    GROUP_PASS_STARS,
    REEL_MAX_CLIPS,
    REEL_PRICE_STARS,
    VIDEO_CACHE_DIR,
    VIDEO_CACHE_MAX_BYTES,
    logger,
)
from database import (
    MAX_CAPTION_LEN,
    MAX_COMMENT_LEN,
    add_pin,
    add_pin_comment,
    delete_pin,
    delete_pin_comment,
    get_atlas_meta,
    get_atlas_pins,
    get_atlas_stats,
    get_chat_entitlements,
    get_chat_ids_for_file,
    get_chat_stats,
    get_open_trip_for_member,
    get_pin_message_ref,
    get_pin_owner,
    get_pins,
    get_pins_meta,
    get_trip,
    get_trip_pins_for_reel,
    is_file_owner,
    is_trip_reel_unlocked,
    normalize_reaction_emoji,
    pop_pending_pin,
    set_chat_title,
    set_pins_trip,
    to_telegram_reaction_emoji,
    toggle_pin_reaction,
    track,
    update_pin_caption,
    update_pin_location,
)
from reel_generator import get_or_render_trip_reel
from telegram_handlers import reverse_geocode

# Cache the file_id -> Telegram CDN path so a marker re-fetched on every pan/zoom/loop doesn't hit
# the Bot API each time. Telegram guarantees a get_file path stays valid for at least 1h, so a 45m
# TTL is always served while still live. Same bounded-LRU shape as auth._member_cache.
_FILE_PATH_TTL = 2700
_FILE_PATH_CACHE_MAX = 2000
_file_path_cache: dict[str, tuple[float, str]] = {}


async def _resolve_file_path(bot, file_id: str) -> str:
    now = time.monotonic()
    cached = _file_path_cache.get(file_id)
    if cached and cached[0] > now:
        return cached[1]
    path = (await bot.get_file(file_id)).file_path
    if len(_file_path_cache) >= _FILE_PATH_CACHE_MAX:
        for stale in [k for k, v in _file_path_cache.items() if v[0] <= now]:
            _file_path_cache.pop(stale, None)
        if len(_file_path_cache) >= _FILE_PATH_CACHE_MAX:
            _file_path_cache.pop(next(iter(_file_path_cache)), None)
    _file_path_cache[file_id] = (now + _FILE_PATH_TTL, path)
    return path


# On-disk byte cache: pull each clip from Telegram's CDN once, then serve all repeats (any user, any
# reload, any Range) straight off the Pi's disk. Browsers re-issue a `Range: bytes=0-` per <video>
# on every fresh page load and ignore `immutable`, so without this the proxy re-streams the same
# bytes over and over. Bounded by a simple LRU; all bookkeeping is synchronous (single event loop),
# so only per-file_id locks are needed — to stop two concurrent first-loads double-fetching.
_CACHE_CONTROL = "private, max-age=86400, immutable"  # private: auth-gated, so no shared/CDN cache
_CACHE_LOCKS_MAX = 4096
_cache_lru: "OrderedDict[str, int]" = OrderedDict()  # filename -> size, oldest first
_cache_total = 0
_cache_locks: dict[str, asyncio.Lock] = {}
_cache_ready = False


def _cache_name(file_id: str) -> str:
    return hashlib.sha256(file_id.encode()).hexdigest() + ".mp4"  # .mp4 so FileResponse infers video/mp4


def _init_cache() -> None:
    """Rebuild LRU accounting from whatever's on disk (so the cap survives restarts). Runs once."""
    global _cache_total, _cache_ready
    if _cache_ready:
        return
    VIDEO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    found = []
    for p in VIDEO_CACHE_DIR.iterdir():
        if p.is_file() and p.name.endswith(".mp4"):
            try:
                st = p.stat()
                found.append((st.st_mtime, p.name, st.st_size))
            except OSError:
                pass
    for _, name, size in sorted(found):  # oldest mtime first == LRU order
        _cache_lru[name] = size
        _cache_total += size
    _evict()  # enforce the cap right away, so a lowered VIDEO_CACHE_MAX_MB / oversized dir is fixed on boot
    _cache_ready = True


def _evict() -> None:
    global _cache_total
    while _cache_total > VIDEO_CACHE_MAX_BYTES and len(_cache_lru) > 1:  # never drop the just-added sole entry
        name, size = _cache_lru.popitem(last=False)
        _cache_total -= size
        try:
            (VIDEO_CACHE_DIR / name).unlink()
        except OSError:
            pass


def _record(name: str, size: int) -> None:
    """Mark a freshly-written file as most-recently-used, then evict down to the cap."""
    global _cache_total
    old = _cache_lru.pop(name, None)  # drop any stale accounting (e.g. file had vanished off disk)
    if old is not None:
        _cache_total -= old
    _cache_lru[name] = size
    _cache_total += size
    _evict()


def _prune_locks(keep: str) -> None:
    """Bound _cache_locks: drop idle (unlocked) locks once the map grows large. An unlocked lock has
    no holder, so dropping it can't strand a waiter; the worst case is a rare, harmless re-fetch."""
    if len(_cache_locks) <= _CACHE_LOCKS_MAX:
        return
    for k in [k for k, v in _cache_locks.items() if k != keep and not v.locked()]:
        _cache_locks.pop(k, None)


def _cache_lookup(file_id: str) -> Path | None:
    """Hit path: return the cached file (marking it MRU), or None on a miss. No lock — the LRU
    bookkeeping is synchronous, and a concurrent miss for the same file re-checks under its lock."""
    _init_cache()
    name = _cache_name(file_id)
    path = VIDEO_CACHE_DIR / name
    if name in _cache_lru and path.exists():
        _cache_lru.move_to_end(name)
        return path
    return None


def _video_file_response(path: Path) -> web.FileResponse:
    # FileResponse natively handles Range / Content-Range / Accept-Ranges / conditional requests.
    resp = web.FileResponse(path)
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["Cache-Control"] = _CACHE_CONTROL
    return resp


async def _tee(source, stream: web.StreamResponse, tmp: Path) -> bool:
    """Forward upstream chunks to the client (so the first byte ships immediately) while teeing them
    into `tmp`. Returns True iff the whole file landed in `tmp` and is safe to promote to the cache.
    A client disconnect or a disk error degrades to a plain pass-through (no cache write this time)."""
    caching = True
    f = None
    try:
        f = open(tmp, "wb")
    except OSError:
        caching = False
    try:
        async for chunk in source.iter_chunked(65536):
            if caching:
                try:
                    f.write(chunk)
                except OSError:  # e.g. disk full — keep serving the client, just don't cache
                    caching = False
            try:
                await stream.write(chunk)
            except (ConnectionResetError, asyncio.CancelledError):  # client gone — abandon the partial cache
                return False
    finally:
        if f is not None:
            f.close()
    return caching


async def _stream_and_cache(request: web.Request, bot, file_id: str) -> web.StreamResponse:
    """Miss path: stream the clip from Telegram to the client AND warm the disk cache in one pass."""
    name = _cache_name(file_id)
    path = VIDEO_CACHE_DIR / name
    lock = _cache_locks.setdefault(file_id, asyncio.Lock())
    _prune_locks(file_id)
    async with lock:
        cached = _cache_lookup(file_id)  # another request may have cached it while we waited for the lock
        if cached is not None:
            return _video_file_response(cached)
        try:
            file_path = await _resolve_file_path(bot, file_id)
        except Exception:
            raise web.HTTPNotFound(text="file not found")

        tmp = path.with_name(name + ".tmp")
        renamed = False
        try:
            async with AiohttpClient() as session:
                async with session.get(file_path, timeout=ClientTimeout(sock_read=30)) as up:
                    if up.status != 200:
                        raise web.HTTPNotFound(text="file not found")
                    headers = {
                        "Content-Type": "video/mp4",
                        "Content-Disposition": "inline",
                        "Accept-Ranges": "bytes",
                        "Cache-Control": _CACHE_CONTROL,
                    }
                    if "Content-Length" in up.headers:
                        headers["Content-Length"] = up.headers["Content-Length"]
                    stream = web.StreamResponse(status=200, headers=headers)
                    await stream.prepare(request)
                    complete = await _tee(up.content, stream, tmp)
            if complete:
                tmp.replace(path)  # atomic: readers never see a partial file
                renamed = True
                _record(name, path.stat().st_size)
            return stream
        finally:
            if not renamed:
                tmp.unlink(missing_ok=True)


async def ensure_video_cached(bot, file_id: str) -> Path:
    """Ensure a clip is present in VIDEO_CACHE_DIR (downloading from Telegram CDN only on a miss).
    Shared by the Trip Recap Reel generator so cached map clips aren't re-fetched."""
    cached = _cache_lookup(file_id)
    if cached is not None:
        return cached

    name = _cache_name(file_id)
    path = VIDEO_CACHE_DIR / name
    lock = _cache_locks.setdefault(file_id, asyncio.Lock())
    _prune_locks(file_id)
    async with lock:
        cached = _cache_lookup(file_id)
        if cached is not None:
            return cached
        file_path = await _resolve_file_path(bot, file_id)
        tmp = path.with_name(name + ".tmp")
        renamed = False
        try:
            async with AiohttpClient() as session:
                async with session.get(file_path, timeout=ClientTimeout(sock_read=30)) as up:
                    if up.status != 200:
                        raise FileNotFoundError(f"upstream returned {up.status}")
                    with open(tmp, "wb") as f:
                        async for chunk in up.content.iter_chunked(65536):
                            f.write(chunk)
            tmp.replace(path)
            renamed = True
            _record(name, path.stat().st_size)
            return path
        finally:
            if not renamed:
                tmp.unlink(missing_ok=True)


def _authorize_user(request: web.Request):
    """Verify the caller's Mini App initData signature & freshness (user-scoped routes like My Atlas).

    Returns ``(data, user_id, None)`` on success or ``(None, None, web.Response)`` (401) on failure."""
    data = verify_init_data(extract_init_data(request))
    if not data or "user" not in data:
        return None, None, web.json_response({"error": "unauthorized"}, status=401)
    return data, int(data["user"]["id"]), None


async def _authorize(request: web.Request, chat_id: int):
    """Verify the caller's Mini App initData and chat membership.

    Returns ``(data, None)`` when authorized — where ``data`` is the parsed initData with the decoded
    ``user`` — otherwise ``(None, web.Response)`` to return immediately. Callers that only gate on
    access can ignore ``data``; the location endpoint uses it to recover the trusted ``user.id``.
    """
    data = verify_init_data(extract_init_data(request))
    if not data or "user" not in data:
        return None, web.json_response({"error": "unauthorized"}, status=401)
    bot = request.app.get("bot")
    if not bot:
        return None, web.json_response({"error": "bot not ready"}, status=503)
    if not await is_member(bot, chat_id, int(data["user"]["id"])):
        return None, web.json_response({"error": "forbidden"}, status=403)
    return data, None


async def handle_chat(request: web.Request) -> web.Response:
    chat_id = request.rel_url.query.get("chat_id")
    if not chat_id:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    _, err = await _authorize(request, int(chat_id))
    if err:
        return err
    bot = request.app.get("bot")
    if not bot:
        return web.json_response({"username": None})
    try:
        chat = await bot.get_chat(int(chat_id))
        if chat.title:
            set_chat_title(int(chat_id), chat.title)
        return web.json_response({"username": chat.username, "title": chat.title, "type": chat.type})
    except Exception:
        return web.json_response({"username": None})


async def handle_video_proxy(request: web.Request) -> web.StreamResponse:
    file_id = request.rel_url.query.get("file_id")
    if not file_id:
        raise web.HTTPBadRequest(text="file_id required")

    bot = request.app.get("bot")
    if not bot:
        raise web.HTTPServiceUnavailable(text="bot not ready")

    data = verify_init_data(extract_init_data(request))
    if not data or "user" not in data:
        raise web.HTTPUnauthorized(text="unauthorized")

    chat_ids = get_chat_ids_for_file(file_id)
    if not chat_ids:
        raise web.HTTPNotFound(text="file not found")

    user_id = int(data["user"]["id"])
    # Allow streaming if the caller recorded the clip themselves (e.g. viewing My Atlas)
    # OR currently belongs to any chat referencing the file.
    if not is_file_owner(file_id, user_id):
        for cid in chat_ids:
            if await is_member(bot, cid, user_id):
                break
        else:
            raise web.HTTPForbidden(text="forbidden")

    cached = _cache_lookup(file_id)
    if cached is not None:
        return _video_file_response(cached)
    # Miss: stream from Telegram immediately (no download-first stall) while warming the cache.
    return await _stream_and_cache(request, bot, file_id)


async def handle_bot_info(request: web.Request) -> web.Response:
    bot = request.app.get("bot")
    if not bot or not bot.username:
        return web.json_response({"username": None})
    return web.json_response({"username": bot.username})


async def handle_pins(request: web.Request) -> web.Response:
    if request.rel_url.query.get("mode") == "atlas":
        _, user_id, err = _authorize_user(request)
        if err:
            return err
        try:
            limit = min(max(int(request.rel_url.query.get("limit", 500)), 1), 500)
            offset = max(int(request.rel_url.query.get("offset", 0)), 0)
            chat_ids_raw = request.rel_url.query.get("chat_ids")
            chat_ids = None
            if chat_ids_raw:
                chat_ids = [int(x) for x in chat_ids_raw.split(",") if x.lstrip("-").isdigit()]
                if not chat_ids:
                    chat_ids = [0]
            date_from = request.rel_url.query.get("date_from")
            date_to = request.rel_url.query.get("date_to")
            q = request.rel_url.query.get("q")
            if offset == 0 and not any((chat_ids_raw, date_from, date_to, q)):
                track("atlas_opened", user_id=user_id)
            result = get_atlas_pins(user_id, limit=limit, offset=offset, chat_ids=chat_ids, date_from=date_from, date_to=date_to, q=q)
            return web.json_response(result)
        except Exception:
            logger.warning("handle_pins (atlas) failed for user %s", user_id, exc_info=True)
            return web.json_response({"error": "internal error"}, status=400)

    chat_id = request.rel_url.query.get("chat_id")
    if not chat_id:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    data, err = await _authorize(request, int(chat_id))
    if err:
        return err
    viewer_id = int(data["user"]["id"])
    try:
        limit = int(request.rel_url.query.get("limit", 500))
        offset = int(request.rel_url.query.get("offset", 0))
        limit = min(max(limit, 1), 500)
        offset = max(offset, 0)
        user_ids_raw = request.rel_url.query.get("user_ids")
        user_ids = user_ids_raw.split(",") if user_ids_raw else None
        date_from = request.rel_url.query.get("date_from")
        date_to = request.rel_url.query.get("date_to")
        q = request.rel_url.query.get("q")
        trip_id_raw = request.rel_url.query.get("trip_id")
        trip_id = int(trip_id_raw) if trip_id_raw and trip_id_raw.isdigit() else None
        # The frontend refetches on every filter change and pagination page; only the first
        # unfiltered page corresponds to actually opening the map.
        if offset == 0 and not any((user_ids_raw, date_from, date_to, q, trip_id_raw)):
            track("map_opened", chat_id=int(chat_id), user_id=viewer_id)
        result = get_pins(
            int(chat_id),
            limit=limit,
            offset=offset,
            user_ids=user_ids,
            date_from=date_from,
            date_to=date_to,
            q=q,
            trip_id=trip_id,
            viewer_user_id=viewer_id,
        )
        return web.json_response(result)
    except Exception:
        logger.warning("handle_pins failed for chat %s", chat_id, exc_info=True)
        return web.json_response({"error": "internal error"}, status=400)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_pins_meta(request: web.Request) -> web.Response:
    if request.rel_url.query.get("mode") == "atlas":
        _, user_id, err = _authorize_user(request)
        if err:
            return err
        try:
            result = get_atlas_meta(user_id)
            result["current_user_id"] = user_id
            result["mode"] = "atlas"
            return web.json_response(result)
        except Exception:
            logger.warning("handle_pins_meta (atlas) failed for user %s", user_id, exc_info=True)
            return web.json_response({"error": "internal error"}, status=400)

    chat_id = request.rel_url.query.get("chat_id")
    if not chat_id:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    data, err = await _authorize(request, int(chat_id))
    if err:
        return err
    try:
        result = get_pins_meta(int(chat_id))
        result["current_user_id"] = int(data["user"]["id"])
        result["entitlements"] = get_chat_entitlements(int(chat_id))
        result["pricing"] = {
            "reel_stars": REEL_PRICE_STARS,
            "group_pass_stars": GROUP_PASS_STARS,
            "group_pass_days": GROUP_PASS_DAYS,
        }
        return web.json_response(result)
    except Exception:
        logger.warning("handle_pins_meta failed for chat %s", chat_id, exc_info=True)
        return web.json_response({"error": "internal error"}, status=400)


async def handle_submit_location(request: web.Request) -> web.Response:
    """Create a pin from a location captured by the Mini App's LocationManager.

    Pairs the posted coordinates with the caller's pending video. Authorization mirrors the other
    chat-scoped endpoints: a valid initData signature plus membership of the requested chat. The pin
    is attributed to the *signed* user.id, and the pending row is looked up by that same id — so a
    caller can only ever complete their own cheers, never someone else's."""
    chat_id_raw = request.rel_url.query.get("chat_id")
    if not chat_id_raw:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id_raw.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    chat_id = int(chat_id_raw)

    data, err = await _authorize(request, chat_id)
    if err:
        return err
    user_id = int(data["user"]["id"])  # signed -> trustworthy; never the body/query

    try:
        body = await request.json()
        lat = float(body["lat"])
        lng = float(body["lng"])
    except (ValueError, TypeError, KeyError):  # invalid JSON raises ValueError (json.JSONDecodeError)
        return web.json_response({"error": "invalid coordinates"}, status=400)
    if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        return web.json_response({"error": "invalid coordinates"}, status=400)

    pending = pop_pending_pin(chat_id, user_id)
    if not pending:
        return web.json_response({"error": "no pending video"}, status=409)

    city, country, country_code = await reverse_geocode(lat, lng)
    trip = get_open_trip_for_member(chat_id, user_id)
    add_pin(
        chat_id=chat_id,
        message_id=pending["message_id"],
        user_id=user_id,
        user_name=pending["user_name"],
        video_file_id=pending["file_id"],
        lat=lat,
        lng=lng,
        video_link=pending.get("video_link"),
        city=city,
        country=country,
        country_code=country_code,
        trip_id=trip["id"] if trip else None,
    )
    track("pin_created", chat_id=chat_id, user_id=user_id)

    # Best-effort: clear the bot's "tap to share location" prompt from the group, matching the
    # attachment-menu flow's cleanup. The shared bot instance lives on the app (see main.py).
    bot = request.app.get("bot")
    prompt_msg_id = pending.get("prompt_msg_id")
    if bot and prompt_msg_id:
        try:
            await bot.delete_message(chat_id, prompt_msg_id)
        except Exception:
            pass

    return web.json_response({"ok": True})


async def handle_pin_trip(request: web.Request) -> web.Response:
    """Retro-tag: assign one or more pins to a trip (or clear with trip_id=null) from the Mini App.

    The fallback for cheers the auto-tagging missed — someone who forgot to join the trip, or a
    whole run of pins from a trip nobody had started yet. Any authorized member of the chat may tag
    any of that chat's pins (same group-trust model as the rest of the map); set_pins_trip enforces
    that both the pins and the trip belong to the authorized chat_id.

    Body accepts either {"pin_id": N} (single) or {"pin_ids": [...]} (bulk)."""
    chat_id_raw = request.rel_url.query.get("chat_id")
    if not chat_id_raw:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id_raw.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    chat_id = int(chat_id_raw)

    _, err = await _authorize(request, chat_id)
    if err:
        return err

    try:
        body = await request.json()
        raw_ids = body["pin_ids"] if "pin_ids" in body else [body["pin_id"]]
        if not isinstance(raw_ids, list):  # reject a bare string/number, which int()-iterating would mangle
            raise TypeError
        pin_ids = [int(x) for x in raw_ids]
        trip_id = body.get("trip_id")
        if trip_id is not None:
            trip_id = int(trip_id)
    except (ValueError, TypeError, KeyError):
        return web.json_response({"error": "invalid request"}, status=400)

    # Cap the batch well under SQLite's bound-variable limit; the in-view list is small in practice.
    if not pin_ids or len(pin_ids) > 500:
        return web.json_response({"error": "invalid request"}, status=400)

    updated = set_pins_trip(chat_id, pin_ids, trip_id)
    if not updated:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"ok": True, "updated": updated})


async def handle_delete_pin(request: web.Request) -> web.Response:
    """Delete a single pin owned by the authenticated caller.

    Strictly owner-only: compares the pin's user_id against the signed initData user.id (never a
    client-supplied id). Returns 404 if the pin doesn't exist in this chat, 403 if owned by
    someone else."""
    chat_id_raw = request.rel_url.query.get("chat_id")
    if not chat_id_raw:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id_raw.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    chat_id = int(chat_id_raw)

    data, err = await _authorize(request, chat_id)
    if err:
        return err
    user_id = int(data["user"]["id"])

    try:
        body = await request.json()
        raw_id = body["pin_id"]
        if isinstance(raw_id, bool):
            raise TypeError
        pin_id = int(raw_id)
    except (ValueError, TypeError, KeyError):
        return web.json_response({"error": "invalid request"}, status=400)

    status, info = delete_pin(chat_id, pin_id, user_id)
    if status == "not_found":
        return web.json_response({"error": "not found"}, status=404)
    if status == "forbidden":
        return web.json_response({"error": "forbidden"}, status=403)

    track("pin_deleted", chat_id=chat_id, user_id=user_id)
    return web.json_response({"ok": True, "pin_id": pin_id, "trip_id": info["trip_id"]})


async def _mirror_reaction_to_telegram(bot, chat_id: int, pin_id: int, added: bool, emoji: str, reactions: list[dict]) -> None:
    """Best-effort mirror of Mini App reactions onto the original Telegram video note message.

    Telegram Bot API lets a bot place 1 reaction (under the bot's own account) per message. We
    reflect the just-added emoji (or top remaining supported emoji), or clear it if none remain."""
    if not bot:
        return
    message_id = get_pin_message_ref(chat_id, pin_id)
    if not message_id:
        return
    tg_emoji = to_telegram_reaction_emoji(emoji) if added else None
    if tg_emoji is None:
        for item in reactions:
            cand = to_telegram_reaction_emoji(item.get("emoji"))
            if cand:
                tg_emoji = cand
                break
    try:
        payload = [ReactionTypeEmoji(emoji=tg_emoji)] if tg_emoji else []
        await bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=payload)
    except Exception:
        logger.debug("Could not mirror reaction to Telegram chat=%s msg=%s", chat_id, message_id, exc_info=True)


async def handle_pin_reaction(request: web.Request) -> web.Response:
    """Toggle an emoji reaction on a pin in an authorized chat and mirror it to Telegram."""
    chat_id_raw = request.rel_url.query.get("chat_id")
    if not chat_id_raw:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id_raw.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    chat_id = int(chat_id_raw)

    data, err = await _authorize(request, chat_id)
    if err:
        return err
    user_id = int(data["user"]["id"])

    try:
        body = await request.json()
        raw_id = body["pin_id"]
        if isinstance(raw_id, bool):
            raise TypeError
        pin_id = int(raw_id)
        emoji = normalize_reaction_emoji(body.get("emoji"))
        if not emoji:
            raise ValueError("invalid emoji")
    except (ValueError, TypeError, KeyError):
        return web.json_response({"error": "invalid request"}, status=400)

    status, added, reactions = toggle_pin_reaction(chat_id, pin_id, user_id, emoji)
    if status == "not_found":
        return web.json_response({"error": "not found"}, status=404)

    await _mirror_reaction_to_telegram(request.app.get("bot"), chat_id, pin_id, added, emoji, reactions)

    if added:
        track("pin_reacted", chat_id=chat_id, user_id=user_id)
    return web.json_response({"ok": True, "added": added, "reactions": reactions})


async def handle_pin_comment(request: web.Request) -> web.Response:
    """Add a reply/comment to a pin from the Mini App (and mirror quietly to Telegram in groups)."""
    chat_id_raw = request.rel_url.query.get("chat_id")
    if not chat_id_raw:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id_raw.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    chat_id = int(chat_id_raw)

    data, err = await _authorize(request, chat_id)
    if err:
        return err
    u = data["user"]
    user_id = int(u["id"])
    full_name = " ".join(p for p in (u.get("first_name"), u.get("last_name")) if p).strip()
    user_name = full_name or u.get("username") or str(user_id)

    try:
        body = await request.json()
        raw_id = body["pin_id"]
        if isinstance(raw_id, bool):
            raise TypeError
        pin_id = int(raw_id)
        raw_text = body.get("text")
        if not isinstance(raw_text, str):
            raise TypeError
        text = " ".join(raw_text.split())
        if not text:
            return web.json_response({"error": "empty reply"}, status=400)
        if len(text) > MAX_COMMENT_LEN:
            return web.json_response({"error": f"reply too long (max {MAX_COMMENT_LEN})"}, status=400)
    except (ValueError, TypeError, KeyError):
        return web.json_response({"error": "invalid request"}, status=400)

    comment = add_pin_comment(chat_id, pin_id, user_id, user_name, text)
    if not comment:
        return web.json_response({"error": "not found"}, status=404)

    # Best-effort silent reply in the Telegram group so conversations stay unified across both surfaces.
    bot = request.app.get("bot")
    if bot and chat_id < 0:
        orig_msg_id = get_pin_message_ref(chat_id, pin_id)
        if orig_msg_id:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"💬 {user_name}: {text}",
                    reply_to_message_id=orig_msg_id,
                    disable_notification=True,
                )
            except Exception:
                logger.debug("Could not mirror comment to Telegram chat=%s msg=%s", chat_id, orig_msg_id, exc_info=True)

    track("pin_commented", chat_id=chat_id, user_id=user_id)
    return web.json_response({"ok": True, "comment": comment})


async def handle_delete_comment(request: web.Request) -> web.Response:
    """Delete a comment owned by the authenticated caller."""
    chat_id_raw = request.rel_url.query.get("chat_id")
    if not chat_id_raw:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id_raw.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    chat_id = int(chat_id_raw)

    data, err = await _authorize(request, chat_id)
    if err:
        return err
    user_id = int(data["user"]["id"])

    try:
        body = await request.json()
        raw_id = body["comment_id"]
        if isinstance(raw_id, bool):
            raise TypeError
        comment_id = int(raw_id)
    except (ValueError, TypeError, KeyError):
        return web.json_response({"error": "invalid request"}, status=400)

    status, _ = delete_pin_comment(chat_id, comment_id, user_id)
    if status == "not_found":
        return web.json_response({"error": "not found"}, status=404)
    if status == "forbidden":
        return web.json_response({"error": "forbidden"}, status=403)
    return web.json_response({"ok": True, "comment_id": comment_id})


async def handle_pin_caption(request: web.Request) -> web.Response:
    """Set or clear a lightweight caption on a pin owned by the authenticated caller."""
    chat_id_raw = request.rel_url.query.get("chat_id")
    if not chat_id_raw:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id_raw.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    chat_id = int(chat_id_raw)

    data, err = await _authorize(request, chat_id)
    if err:
        return err
    user_id = int(data["user"]["id"])

    try:
        body = await request.json()
        raw_id = body["pin_id"]
        if isinstance(raw_id, bool):
            raise TypeError
        pin_id = int(raw_id)
        raw_caption = body.get("caption")
        if raw_caption is None:
            caption = None
        elif isinstance(raw_caption, str):
            cleaned = " ".join(raw_caption.split())
            if len(cleaned) > MAX_CAPTION_LEN:
                return web.json_response({"error": f"caption too long (max {MAX_CAPTION_LEN})"}, status=400)
            caption = cleaned or None
        else:
            raise TypeError
    except (ValueError, TypeError, KeyError):
        return web.json_response({"error": "invalid request"}, status=400)

    status = update_pin_caption(chat_id, pin_id, user_id, caption)
    if status == "not_found":
        return web.json_response({"error": "not found"}, status=404)
    if status == "forbidden":
        return web.json_response({"error": "forbidden"}, status=403)

    if caption:
        track("pin_captioned", chat_id=chat_id, user_id=user_id)
    return web.json_response({"ok": True, "pin_id": pin_id, "caption": caption})


async def handle_pin_location(request: web.Request) -> web.Response:
    """Fix indoor GPS drift by updating a pin's coordinates (strictly owner-only)."""
    chat_id_raw = request.rel_url.query.get("chat_id")
    if not chat_id_raw:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id_raw.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    chat_id = int(chat_id_raw)

    data, err = await _authorize(request, chat_id)
    if err:
        return err
    user_id = int(data["user"]["id"])

    try:
        body = await request.json()
        raw_id = body["pin_id"]
        if isinstance(raw_id, bool):
            raise TypeError
        pin_id = int(raw_id)
        lat = float(body["lat"])
        lng = float(body["lng"])
    except (ValueError, TypeError, KeyError):
        return web.json_response({"error": "invalid request"}, status=400)
    if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        return web.json_response({"error": "invalid coordinates"}, status=400)

    # Verify existence & ownership BEFORE reverse-geocoding so unauthorized callers can't consume Nominatim quota.
    owner_id = get_pin_owner(chat_id, pin_id)
    if owner_id is None:
        return web.json_response({"error": "not found"}, status=404)
    if owner_id != user_id:
        return web.json_response({"error": "forbidden"}, status=403)

    city, country, country_code = await reverse_geocode(lat, lng)
    status = update_pin_location(chat_id, pin_id, user_id, lat, lng, city, country, country_code)
    if status == "not_found":
        return web.json_response({"error": "not found"}, status=404)
    if status == "forbidden":
        return web.json_response({"error": "forbidden"}, status=403)

    track("pin_relocated", chat_id=chat_id, user_id=user_id)
    return web.json_response(
        {
            "ok": True,
            "pin_id": pin_id,
            "latitude": lat,
            "longitude": lng,
            "city": city,
            "country": country,
            "country_code": country_code,
        }
    )


async def handle_stats(request: web.Request) -> web.Response:
    """Group Passport & leaderboard stats (or personal My Atlas Passport when mode=atlas)."""
    if request.rel_url.query.get("mode") == "atlas":
        _, user_id, err = _authorize_user(request)
        if err:
            return err
        try:
            track("stats_opened", user_id=user_id)
            return web.json_response(get_atlas_stats(user_id))
        except Exception:
            logger.warning("handle_stats (atlas) failed for user %s", user_id, exc_info=True)
            return web.json_response({"error": "internal error"}, status=400)

    chat_id_raw = request.rel_url.query.get("chat_id")
    if not chat_id_raw:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id_raw.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    chat_id = int(chat_id_raw)

    data, err = await _authorize(request, chat_id)
    if err:
        return err

    try:
        track("stats_opened", chat_id=chat_id, user_id=int(data["user"]["id"]))
        return web.json_response(get_chat_stats(chat_id))
    except Exception:
        logger.warning("handle_stats failed for chat %s", chat_id, exc_info=True)
        return web.json_response({"error": "internal error"}, status=400)


async def handle_entitlements(request: web.Request) -> web.Response:
    """Return current paid entitlements + pricing for an authorized chat (used after Stars checkout)."""
    chat_id_raw = request.rel_url.query.get("chat_id")
    if not chat_id_raw:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id_raw.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    chat_id = int(chat_id_raw)

    _, err = await _authorize(request, chat_id)
    if err:
        return err

    return web.json_response(
        {
            "entitlements": get_chat_entitlements(chat_id),
            "pricing": {
                "reel_stars": REEL_PRICE_STARS,
                "group_pass_stars": GROUP_PASS_STARS,
                "group_pass_days": GROUP_PASS_DAYS,
            },
        }
    )


async def handle_reel_invoice(request: web.Request) -> web.Response:
    """Create a Telegram Stars (XTR) invoice link to unlock an HD Trip Reel or a 30-day Group Pass."""
    chat_id_raw = request.rel_url.query.get("chat_id")
    if not chat_id_raw:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id_raw.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    chat_id = int(chat_id_raw)

    data, err = await _authorize(request, chat_id)
    if err:
        return err
    user_id = int(data["user"]["id"])

    try:
        body = await request.json()
        product = body.get("product")
        if product not in ("trip_reel", "group_pass"):
            raise ValueError("invalid product")
        trip_id = None
        if product == "trip_reel":
            raw_tid = body["trip_id"]
            if isinstance(raw_tid, bool):
                raise TypeError
            trip_id = int(raw_tid)
    except (ValueError, TypeError, KeyError):
        return web.json_response({"error": "invalid request"}, status=400)

    bot = request.app.get("bot")
    if not bot:
        return web.json_response({"error": "bot not ready"}, status=503)

    if product == "trip_reel":
        trip = get_trip(trip_id)
        if not trip or trip["chat_id"] != chat_id:
            return web.json_response({"error": "trip not found"}, status=404)
        if is_trip_reel_unlocked(chat_id, trip_id):
            return web.json_response({"already_unlocked": True, "entitlements": get_chat_entitlements(chat_id)})
        title = f"HD Trip Reel — {trip['name'][:22]}"
        description = f"Unlock the 720p unwatermarked recap reel for '{trip['name']}' for everyone in this group."
        payload = f"reel:{chat_id}:{trip_id}"
        stars = REEL_PRICE_STARS
    else:
        ent = get_chat_entitlements(chat_id)
        if ent["has_group_pass"]:
            return web.json_response({"already_unlocked": True, "entitlements": ent})
        title = f"Cheers Group Pass ({GROUP_PASS_DAYS}d)"
        description = f"Unlimited HD unwatermarked Trip Recap Reels for every trip in this group for {GROUP_PASS_DAYS} days."
        payload = f"pass:{chat_id}"
        stars = GROUP_PASS_STARS

    try:
        invoice_url = await bot.create_invoice_link(
            title=title,
            description=description,
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=title, amount=stars)],
        )
    except Exception:
        logger.warning("create_invoice_link failed for chat %s product %s", chat_id, product, exc_info=True)
        return web.json_response({"error": "could not create invoice"}, status=502)

    track("invoice_created", chat_id=chat_id, user_id=user_id)
    return web.json_response({"invoice_url": invoice_url, "stars": stars})


async def _prepare_trip_reel(request: web.Request, chat_id: int, trip_id: int, quality: str):
    """Validate trip ownership, enforce the HD paywall (fail closed -> 402), and render/fetch the reel."""
    trip = get_trip(trip_id)
    if not trip or trip["chat_id"] != chat_id:
        return None, None, web.json_response({"error": "trip not found"}, status=404)

    hd = quality == "hd"
    if hd and not is_trip_reel_unlocked(chat_id, trip_id):
        return None, None, web.json_response({"error": "payment_required", "stars": REEL_PRICE_STARS}, status=402)

    pins = get_trip_pins_for_reel(chat_id, trip_id, limit=REEL_MAX_CLIPS)
    if not pins:
        return None, None, web.json_response({"error": "trip has no cheers yet"}, status=404)

    bot = request.app.get("bot")
    if not bot:
        return None, None, web.json_response({"error": "bot not ready"}, status=503)

    async def _fetch(file_id: str) -> Path:
        return await ensure_video_cached(bot, file_id)

    try:
        reel_path = await get_or_render_trip_reel(chat_id, trip_id, trip["name"], pins, hd=hd, fetch_video_fn=_fetch)
    except Exception:
        logger.warning("Trip reel render failed for chat %s trip %s hd=%s", chat_id, trip_id, hd, exc_info=True)
        return None, None, web.json_response({"error": "could not render reel"}, status=500)

    return trip, reel_path, None


async def handle_trip_reel(request: web.Request) -> web.StreamResponse:
    """Stream (with Range support) a vertical 9:16 Trip Recap Reel (free preview or Stars-unlocked HD)."""
    chat_id_raw = request.rel_url.query.get("chat_id")
    trip_id_raw = request.rel_url.query.get("trip_id")
    if not chat_id_raw or not chat_id_raw.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    if not trip_id_raw or not trip_id_raw.isdigit():
        return web.json_response({"error": "invalid trip_id"}, status=400)

    chat_id = int(chat_id_raw)
    trip_id = int(trip_id_raw)
    quality = request.rel_url.query.get("quality", "preview")
    if quality not in ("preview", "hd"):
        return web.json_response({"error": "invalid quality"}, status=400)

    data, err = await _authorize(request, chat_id)
    if err:
        return err

    _, reel_path, prep_err = await _prepare_trip_reel(request, chat_id, trip_id, quality)
    if prep_err:
        return prep_err

    track("reel_hd_generated" if quality == "hd" else "reel_preview_generated", chat_id=chat_id, user_id=int(data["user"]["id"]))
    resp = web.FileResponse(reel_path)
    resp.headers["Content-Disposition"] = f'inline; filename="cheers-trip-{trip_id}-{quality}.mp4"'
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


async def handle_send_trip_reel(request: web.Request) -> web.Response:
    """Post the Trip Recap Reel directly into the Telegram group so members can save or share it."""
    chat_id_raw = request.rel_url.query.get("chat_id")
    if not chat_id_raw or not chat_id_raw.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    chat_id = int(chat_id_raw)

    data, err = await _authorize(request, chat_id)
    if err:
        return err
    user_id = int(data["user"]["id"])

    try:
        body = await request.json()
        raw_tid = body["trip_id"]
        if isinstance(raw_tid, bool):
            raise TypeError
        trip_id = int(raw_tid)
        quality = body.get("quality", "preview")
        if quality not in ("preview", "hd"):
            raise ValueError("invalid quality")
    except (ValueError, TypeError, KeyError):
        return web.json_response({"error": "invalid request"}, status=400)

    trip, reel_path, prep_err = await _prepare_trip_reel(request, chat_id, trip_id, quality)
    if prep_err:
        return prep_err

    bot = request.app["bot"]
    hd = quality == "hd"
    w, h = (720, 1280) if hd else (480, 854)
    caption = f"🎬 {trip['name']} — HD Cheers Recap" if hd else f"🎬 {trip['name']} — Cheers Recap (preview)"
    try:
        with open(reel_path, "rb") as vf:
            await bot.send_video(
                chat_id=chat_id,
                video=vf,
                caption=caption,
                width=w,
                height=h,
                supports_streaming=True,
            )
    except Exception:
        logger.warning("Could not send trip reel to chat %s", chat_id, exc_info=True)
        return web.json_response({"error": "could not send reel to chat"}, status=502)

    track("reel_sent_to_chat", chat_id=chat_id, user_id=user_id)
    return web.json_response({"ok": True})
