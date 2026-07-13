import asyncio
import hashlib
import time
from collections import OrderedDict
from pathlib import Path

from aiohttp import ClientSession as AiohttpClient
from aiohttp import ClientTimeout, web

from auth import extract_init_data, is_member, verify_init_data
from config import VIDEO_CACHE_DIR, VIDEO_CACHE_MAX_BYTES
from database import add_pin, get_chat_ids_for_file, get_open_trip_for_member, get_pins, get_pins_meta, pop_pending_pin, set_pins_trip
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
    chat_id = request.rel_url.query.get("chat_id")
    if not chat_id:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    _, err = await _authorize(request, int(chat_id))
    if err:
        return err
    try:
        limit = int(request.rel_url.query.get("limit", 500))
        offset = int(request.rel_url.query.get("offset", 0))
        limit = max(limit, 1)
        offset = max(offset, 0)
        user_ids_raw = request.rel_url.query.get("user_ids")
        user_ids = user_ids_raw.split(",") if user_ids_raw else None
        date_from = request.rel_url.query.get("date_from")
        date_to = request.rel_url.query.get("date_to")
        q = request.rel_url.query.get("q")
        trip_id_raw = request.rel_url.query.get("trip_id")
        trip_id = int(trip_id_raw) if trip_id_raw and trip_id_raw.isdigit() else None
        result = get_pins(int(chat_id), limit=limit, offset=offset, user_ids=user_ids, date_from=date_from, date_to=date_to, q=q, trip_id=trip_id)
        return web.json_response(result)
    except Exception:
        return web.json_response({"error": "internal error"}, status=400)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_pins_meta(request: web.Request) -> web.Response:
    chat_id = request.rel_url.query.get("chat_id")
    if not chat_id:
        return web.json_response({"error": "chat_id required"}, status=400)
    if not chat_id.lstrip("-").isdigit():
        return web.json_response({"error": "invalid chat_id"}, status=400)
    _, err = await _authorize(request, int(chat_id))
    if err:
        return err
    try:
        result = get_pins_meta(int(chat_id))
        return web.json_response(result)
    except Exception:
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
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
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
