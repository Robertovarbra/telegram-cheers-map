import asyncio
import os
import socket

from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import TOKEN, WEB_DIR, WEB_HOST, WEB_PORT, logger
from database import init_db, migrate_db
from telegram_handlers import (
    cleanup_inactive_chats,
    color_command,
    emoji_command,
    handle_emoji_text,
    handle_location,
    handle_my_chat_member,
    handle_pref_callback,
    handle_video,
    map_command,
    start,
)
from web_handlers import (
    handle_bot_info,
    handle_chat,
    handle_health,
    handle_pins,
    handle_pins_meta,
    handle_video_proxy,
)


async def _watchdog_loop() -> None:
    sock_path = os.environ.get("NOTIFY_SOCKET")
    if not sock_path:
        return
    logger.info("systemd watchdog enabled")
    if sock_path.startswith("@"):
        sock_path = "\0" + sock_path[1:]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.connect(sock_path)
    try:
        while True:
            sock.sendall(b"WATCHDOG=1")
            await asyncio.sleep(15)
    finally:
        sock.close()


async def main() -> None:
    init_db()
    migrate_db()

    web_app = web.Application()
    web_app.router.add_get("/api/health", handle_health)
    web_app.router.add_get("/api/pins", handle_pins)
    web_app.router.add_get("/api/pins-meta", handle_pins_meta)
    web_app.router.add_get("/api/chat", handle_chat)
    web_app.router.add_get("/api/video", handle_video_proxy)
    web_app.router.add_get("/api/bot-info", handle_bot_info)

    async def index_handler(_request: web.Request) -> web.FileResponse:
        resp = web.FileResponse(WEB_DIR / "index.html")
        resp.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
        return resp

    web_app.router.add_get("/", index_handler)

    runner = web.AppRunner(web_app)
    await runner.setup()

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("map", map_command))
    application.add_handler(CommandHandler("color", color_command))
    application.add_handler(CommandHandler("emoji", emoji_command))
    application.add_handler(CallbackQueryHandler(handle_pref_callback))
    application.add_handler(MessageHandler(filters.TEXT, handle_emoji_text))
    application.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video))
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))
    application.add_handler(ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    try:
        async with application:
            await application.start()
            web_app["bot"] = application.bot
            await cleanup_inactive_chats(application.bot)
            await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

            site = web.TCPSite(runner, WEB_HOST, WEB_PORT)
            await site.start()
            logger.info("Bot + web server ready at http://%s:%s", WEB_HOST, WEB_PORT)

            asyncio.create_task(_watchdog_loop())

            await asyncio.Event().wait()
    finally:
        await application.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
