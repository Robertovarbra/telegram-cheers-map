import asyncio

from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ChatMemberHandler,
)

from config import TOKEN, WEB_HOST, WEB_PORT, WEB_DIR, logger
from database import init_db, migrate_db
from telegram_handlers import (
    start,
    map_command,
    handle_video,
    handle_location,
    handle_my_chat_member,
    cleanup_inactive_chats,
)
from web_handlers import (
    handle_pins,
    handle_chat,
    handle_video_proxy,
    handle_bot_info,
)


async def main() -> None:
    init_db()
    migrate_db()

    web_app = web.Application()
    web_app.router.add_get("/api/pins", handle_pins)
    web_app.router.add_get("/api/chat", handle_chat)
    web_app.router.add_get("/api/video", handle_video_proxy)
    web_app.router.add_get("/api/bot-info", handle_bot_info)
    web_app.router.add_get("/", lambda r: web.FileResponse(WEB_DIR / "index.html"))

    runner = web.AppRunner(web_app)
    await runner.setup()

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("map", map_command))
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

            await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
