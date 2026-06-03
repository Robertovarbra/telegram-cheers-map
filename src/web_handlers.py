import asyncio

from aiohttp import ClientSession as AiohttpClient
from aiohttp import ClientTimeout, web

from auth import extract_init_data, is_member, verify_init_data
from database import add_pin, get_chat_ids_for_file, get_pins, get_pins_meta, pop_pending_pin
from telegram_handlers import reverse_geocode


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

    try:
        file = await bot.get_file(file_id)
    except Exception:
        raise web.HTTPNotFound(text="file not found")

    range_header = request.headers.get("Range", "")

    async with AiohttpClient() as session:
        req_headers = {}
        if range_header:
            req_headers["Range"] = range_header

        async with session.get(file.file_path, headers=req_headers, timeout=ClientTimeout(sock_read=10)) as resp:
            if resp.status == 404:
                raise web.HTTPNotFound(text="file not found on Telegram")
            if resp.status not in (200, 206):
                raise web.HTTPNotFound(text="unexpected response from Telegram")

            headers = {
                "Content-Type": "video/mp4",
                "Content-Disposition": "inline",
                "Accept-Ranges": "bytes",
            }
            if "Content-Length" in resp.headers:
                headers["Content-Length"] = resp.headers["Content-Length"]
            if "Content-Range" in resp.headers:
                headers["Content-Range"] = resp.headers["Content-Range"]

            stream = web.StreamResponse(status=resp.status, headers=headers)
            await stream.prepare(request)
            try:
                async for chunk in resp.content.iter_chunked(65536):
                    await stream.write(chunk)
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            return stream


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
        result = get_pins(int(chat_id), limit=limit, offset=offset, user_ids=user_ids, date_from=date_from, date_to=date_to, q=q)
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
