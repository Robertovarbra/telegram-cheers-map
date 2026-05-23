from aiohttp import ClientSession as AiohttpClient
from aiohttp import web

from database import get_pins


async def handle_chat(request: web.Request) -> web.Response:
    chat_id = request.rel_url.query.get("chat_id")
    if not chat_id:
        return web.json_response({"error": "chat_id required"}, status=400)
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

    try:
        file = await bot.get_file(file_id)
    except Exception:
        raise web.HTTPNotFound(text="file not found")

    range_header = request.headers.get("Range", "")

    async with AiohttpClient() as session:
        req_headers = {}
        if range_header:
            req_headers["Range"] = range_header

        async with session.get(file.file_path, headers=req_headers) as resp:
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
            async for chunk in resp.content.iter_chunked(65536):
                await stream.write(chunk)
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
    try:
        pins = get_pins(int(chat_id))
        return web.json_response(pins)
    except (ValueError, Exception) as e:
        return web.json_response({"error": str(e)}, status=400)
