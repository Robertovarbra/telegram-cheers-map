import asyncio

from aiohttp import ClientSession
from telegram import Chat, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from config import WEB_URL, logger
from database import add_pin, delete_chat_pins, get_all_chat_ids, get_chat_setting, get_pin, set_chat_setting, set_user_pref


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args and context.args[0].startswith("watch_"):
        payload = context.args[0]
        rest = payload[6:]
        last_ = rest.rfind("_")
        if last_ == -1:
            await update.message.reply_text("Invalid link.")
            return
        try:
            chat_id = int(rest[:last_])
            msg_id = int(rest[last_ + 1 :])
        except ValueError:
            await update.message.reply_text("Invalid link.")
            return

        pin = get_pin(chat_id, msg_id)
        if not pin:
            await update.message.reply_text("This cheers pin no longer exists.")
            return

        video_file_id, user_name, video_type, _, _, _ = pin
        await update.message.reply_text(f"Cheers from {user_name}:")
        try:
            if video_type == "video_note":
                await update.message.reply_video_note(video_file_id)
            elif video_type == "video":
                await update.message.reply_video(video_file_id)
            else:
                await update.message.reply_document(video_file_id)
        except Exception as e:
            await update.message.reply_text(f"Could not send video: {e}")
        return

    await update.message.reply_text(
        "Send a telegram video message in a group where I'm an admin, then share your location — "
        "I'll pin it on the map!\n"
        "Use /map to view all cheers.\n"
        "For better experience, allow the bot to pinned messages and delete messages."
    )


async def map_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    map_url = f"{WEB_URL}/?chat_id={chat_id}&v=1"
    keyboard = [[InlineKeyboardButton("Open Map", url=map_url)]]

    pinned_msg_id = get_chat_setting(chat_id, "pinned_map_msg_id")
    if pinned_msg_id:
        still_valid = False
        try:
            await context.bot.edit_message_text(text="📍 Cheers Map", chat_id=chat_id, message_id=pinned_msg_id, reply_markup=InlineKeyboardMarkup(keyboard))
            still_valid = True
        except Exception as e:
            estr = str(e).lower()
            if "not modified" in estr or "message is not modified" in estr:
                still_valid = True

        if still_valid:
            try:
                await context.bot.pin_chat_message(chat_id, pinned_msg_id, disable_notification=True)
            except Exception:
                pass
            return

    old_pinned_id = pinned_msg_id

    msg = await update.message.reply_text("📍 Cheers Map", reply_markup=InlineKeyboardMarkup(keyboard))

    try:
        await context.bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        set_chat_setting(chat_id, "pinned_map_msg_id", msg.message_id)

        if old_pinned_id and old_pinned_id != msg.message_id:
            try:
                await context.bot.unpin_chat_message(chat_id, old_pinned_id)
            except Exception:
                pass
    except Exception as e:
        logger.info("Could not pin map message in chat %s (not admin?): %s", chat_id, e)


COLORS = {
    "🔴 Red": "#e74c3c",
    "🔵 Blue": "#3498db",
    "🟢 Green": "#2ecc71",
    "🟠 Orange": "#f39c12",
    "🟣 Purple": "#9b59b6",
    "🟡 Yellow": "#f1c40f",
    "🩷 Pink": "#e91e63",
    "🩵 Cyan": "#00bcd4",
}
COLOR_EMOJI = {v: k.split()[0] for k, v in COLORS.items()}


async def color_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keys = list(COLORS.keys())
    kb = []
    for i in range(0, len(keys), 4):
        row = []
        for name in keys[i : i + 4]:
            row.append(InlineKeyboardButton(name, callback_data=f"clr_{COLORS[name]}"))
        kb.append(row)
    kb.append([InlineKeyboardButton("❌ Remove custom color", callback_data="clr_")])
    kb.append([InlineKeyboardButton("Skip", callback_data="skip")])
    await update.message.reply_text("🎨 Choose your pin color:", reply_markup=InlineKeyboardMarkup(kb))


async def emoji_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text(
        "😀 Type any emoji from your keyboard to use as your pin.\n\n"
        "Options:\n"
        "• Send any emoji to set it\n"
        "• /none  to remove your custom emoji\n"
        "• /skip  to close without changes"
    )
    context.user_data["awaiting_emoji"] = {"prompt_id": msg.message_id, "chat_id": msg.chat_id}


async def handle_emoji_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = context.user_data.pop("awaiting_emoji", None)
    if not pending:
        return

    user_id = update.message.from_user.id
    text = update.message.text.strip()
    chat_id = update.message.chat_id
    prompt_id = pending["prompt_id"]
    user_msg_id = update.message.message_id

    if text in ("/none", "/skip", "none", "skip"):
        if text in ("/none", "none"):
            set_user_pref(user_id, "pin_emoji", None)
            reply = await update.message.reply_text("✅ Custom emoji removed.")
        else:
            reply = await update.message.reply_text("Skipped.")
    elif len(text) <= 10:
        set_user_pref(user_id, "pin_emoji", text)
        reply = await update.message.reply_text(f"Pin emoji set to {text}!")
    else:
        reply = await update.message.reply_text("❌ That's too long. Send a single emoji or /none.")

    await asyncio.sleep(3)
    for mid in (prompt_id, user_msg_id, reply.message_id):
        try:
            await context.bot.delete_message(chat_id, mid)
        except Exception:
            pass


async def handle_pref_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    chat_id = query.message.chat_id
    message_id = query.message.message_id
    try:
        if data == "skip":
            await context.bot.delete_message(chat_id, message_id)
            return
        if data.startswith("clr_"):
            color = data[4:]
            if color:
                set_user_pref(user_id, "pin_color", color)
                emoji = COLOR_EMOJI.get(color, "")
                await query.edit_message_text(f"{emoji} Pin color set!")
            else:
                set_user_pref(user_id, "pin_color", None)
                await query.edit_message_text("✅ Custom color removed.")
        await asyncio.sleep(3)
        await context.bot.delete_message(chat_id, message_id)
    except Exception as e:
        logger.warning("Could not edit preference message: %s", e)


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return

    message = update.message

    if not message.video_note:
        return

    file_id = message.video_note.file_id

    user = message.from_user
    user_name = user.full_name or user.username or str(user.id)

    if chat.type == Chat.SUPERGROUP:
        video_link = f"https://t.me/c/{str(chat.id)[4:]}/{message.message_id}"
    elif chat.type == Chat.GROUP:
        video_link = f"tg://openmessage?chat_id={chat.id}&message_id={message.message_id}"
    else:
        video_link = message.link

    context.user_data["pending_video"] = {
        "file_id": file_id,
        "message_id": message.message_id,
        "chat_id": message.chat_id,
        "user_id": user.id,
        "user_name": user_name,
        "video_link": video_link,
    }

    bot_reply = await message.reply_text("Cheers received! Now tap the 📍 attachment button and send your location to pin it on the map.")
    context.user_data["bot_reply_message_id"] = bot_reply.message_id


async def reverse_geocode(lat, lng):
    try:
        async with ClientSession() as session:
            url = "https://nominatim.openstreetmap.org/reverse"
            params = {"format": "jsonv2", "lat": lat, "lon": lng}
            headers = {"User-Agent": "TelegramCheersMap/1.0", "Accept-Language": "en"}
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    return None, None, None
                data = await resp.json()
                addr = data.get("address", {})
                city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality")
                country = addr.get("country")
                country_code = addr.get("country_code")
                return city, country, country_code
    except Exception:
        return None, None, None


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = context.user_data.pop("pending_video", None)
    if not pending:
        return

    location = update.message.location
    if not location:
        return

    city, country, country_code = await reverse_geocode(location.latitude, location.longitude)

    add_pin(
        chat_id=pending["chat_id"],
        message_id=pending["message_id"],
        user_id=pending["user_id"],
        user_name=pending["user_name"],
        video_file_id=pending["file_id"],
        lat=location.latitude,
        lng=location.longitude,
        video_link=pending.get("video_link"),
        city=city,
        country=country,
        country_code=country_code,
    )

    chat_id = update.effective_chat.id
    location_msg_id = update.effective_message.message_id
    bot_reply_msg_id = context.user_data.pop("bot_reply_message_id", None)

    confirmation = await context.bot.send_message(chat_id, "Pinned! Use /map to view the cheers map.")

    await asyncio.sleep(3)

    for mid in (confirmation.message_id, bot_reply_msg_id, location_msg_id):
        if mid:
            try:
                await context.bot.delete_message(chat_id, mid)
            except Exception:
                pass


async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_member = update.my_chat_member
    if not chat_member:
        return

    chat = chat_member.chat
    new_status = chat_member.new_chat_member.status
    user = chat_member.new_chat_member.user
    bot_id = context.bot.id

    if user.id != bot_id:
        return

    if chat.type not in (Chat.GROUP, Chat.SUPERGROUP):
        return

    if new_status in (ChatMember.LEFT, ChatMember.BANNED):
        chat_name = chat.title or str(chat.id)
        deleted = delete_chat_pins(chat.id)
        logger.info("Bot removed from chat '%s' (id: %s). Deleted %s pin(s) from database.", chat_name, chat.id, deleted)


async def cleanup_inactive_chats(bot):
    chat_ids = get_all_chat_ids()
    if not chat_ids:
        return

    logger.info("Checking %s chat(s) for cleanup...", len(chat_ids))

    total_deleted = 0
    for chat_id in chat_ids:
        try:
            chat = await bot.get_chat(chat_id)
            try:
                member = await chat.get_member(bot.id)
                if member.status in (ChatMember.LEFT, ChatMember.BANNED):
                    deleted = delete_chat_pins(chat_id)
                    logger.info("Chat %s: Bot is %s. Deleted %s pin(s).", chat_id, member.status, deleted)
                    total_deleted += deleted
            except Exception:
                pass
        except Forbidden:
            deleted = delete_chat_pins(chat_id)
            logger.info("Chat %s: Forbidden (bot was kicked). Deleted %s pin(s).", chat_id, deleted)
            total_deleted += deleted
        except BadRequest as e:
            if "chat not found" in str(e).lower() or "peer_id" in str(e).lower():
                deleted = delete_chat_pins(chat_id)
                logger.info("Chat %s: Not found (deleted). Deleted %s pin(s).", chat_id, deleted)
                total_deleted += deleted
            else:
                logger.warning("Chat %s: BadRequest: %s", chat_id, e)
        except Exception as e:
            logger.warning("Chat %s: Could not check: %s", chat_id, e)

    if total_deleted > 0:
        logger.info("Startup cleanup complete. Deleted %s pin(s) total.", total_deleted)
    else:
        logger.info("Cleanup check complete. All chats are accessible.")
