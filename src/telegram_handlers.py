import asyncio

from aiohttp import ClientSession
from telegram import Chat, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from config import BOT_USERNAME, logger
from database import (
    add_pin,
    close_trip,
    create_trip,
    delete_chat_pins,
    get_all_chat_ids,
    get_chat_setting,
    get_chat_trip_users,
    get_open_trip_for_member,
    get_open_trips,
    get_pin,
    get_pins_meta,
    get_trip,
    get_trip_members,
    pop_pending_pin,
    set_chat_setting,
    set_pending_pin,
    set_trip_checklist_msg,
    set_user_pref,
    toggle_trip_member,
)


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
    map_url = f"https://t.me/{BOT_USERNAME}/map?startapp=c{chat_id}"
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
    # Trip-name capture (from the /trip -> Start button) takes precedence over the emoji flow.
    # user_data is per-user across ALL chats, so only consume it when the reply comes from the same
    # chat where Start was pressed — otherwise an unrelated message elsewhere (even a DM to the bot)
    # would be turned into a trip name in the original group.
    pending_trip = context.user_data.get("awaiting_trip_name")
    if pending_trip and update.message.chat_id == pending_trip["chat_id"]:
        context.user_data.pop("awaiting_trip_name", None)
        name = update.message.text.strip()
        if not name.startswith("/"):  # a command, not a name — cancel quietly
            await _start_trip(context, pending_trip["chat_id"], update.message.from_user, name)
            if pending_trip.get("prompt_id"):  # tidy up the "send the trip name" prompt
                try:
                    await context.bot.delete_message(pending_trip["chat_id"], pending_trip["prompt_id"])
                except Exception:
                    pass
        return

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
    data = query.data
    if data.startswith(("trip_", "tripend_", "tripnew", "tripendmenu", "tripmembers", "tripclose_")):
        await _handle_trip_callback(query, context)  # answers the query itself (custom toasts)
        return
    await query.answer()
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


def _known_users(chat_id, trip_id):
    """Union of everyone who could appear on a trip's checklist, keyed by user_id -> display name:
    pin-posters, anyone who ever joined a trip in this chat (kept so a row survives leaving), and
    the trip's current members. Members' names win (freshest). This is the single source of truth
    for name resolution — the callback must use it too, or a toggle can write back a raw user_id as
    the name and permanently corrupt it."""
    known = {u["user_id"]: u["user_name"] for u in get_pins_meta(chat_id)["users"]}
    known.update({u["user_id"]: u["user_name"] for u in get_chat_trip_users(chat_id)})
    known.update({m["user_id"]: m["user_name"] for m in get_trip_members(trip_id)})
    return known


def _trip_keyboard(trip_id, chat_id):
    """Checklist keyboard for the trip message: one toggle button per known user — pin-posters,
    anyone who ever joined a trip in this chat, and current members — checked for current members,
    plus an End-trip row for the creator."""
    member_ids = {m["user_id"] for m in get_trip_members(trip_id)}
    known = _known_users(chat_id, trip_id)
    kb = []
    for user_id, user_name in sorted(known.items(), key=lambda kv: kv[1].lower()):
        mark = "✅" if user_id in member_ids else "⬜"
        kb.append([InlineKeyboardButton(f"{mark} {user_name}", callback_data=f"trip_{trip_id}_{user_id}")])
    kb.append([InlineKeyboardButton("🔚 End trip", callback_data=f"tripend_{trip_id}")])
    return InlineKeyboardMarkup(kb)


def _trip_text(trip):
    return f"🍻 Trip: {trip['name']}\nWho's here? Tap your name to toggle — members' cheers get tagged automatically."


async def _refresh_checklists(bot, chat_id, trips) -> None:
    """Best-effort: re-render the checklist keyboards of trips someone was just moved off, so a
    stale ✅ doesn't keep claiming them."""
    for t in trips:
        if not t.get("checklist_msg_id"):
            continue
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=t["checklist_msg_id"], reply_markup=_trip_keyboard(t["id"], chat_id))
        except Exception as e:
            logger.warning("Could not refresh checklist for trip %s: %s", t["id"], e)


async def _start_trip(context, chat_id, user, name) -> None:
    """Create a trip and post its checklist. Shared by the /trip <name> command, the Start button's
    text reply, and validated the same way from both. Posts into chat_id via the bot so it lands in
    the right chat regardless of where the trigger came from."""
    name = (name or "").strip()
    if not name:
        return
    if len(name) > 60:
        await context.bot.send_message(chat_id, "That trip name is too long (max 60 characters).")
        return
    if any(t["name"].casefold() == name.casefold() for t in get_open_trips(chat_id)):
        await context.bot.send_message(chat_id, f"A trip named '{name}' is already open. End it first, or pick another name.")
        return
    user_name = user.full_name or user.username or str(user.id)
    trip_id, moved_from = create_trip(chat_id, name, user.id, user_name)
    trip = {"id": trip_id, "name": name}
    msg = await context.bot.send_message(chat_id, _trip_text(trip), reply_markup=_trip_keyboard(trip_id, chat_id))
    set_trip_checklist_msg(trip_id, msg.message_id)
    await _refresh_checklists(context.bot, chat_id, moved_from)


async def _end_trip(bot, chat_id, trip) -> None:
    """Close a trip (if still open) and retire its checklist message so the toggle buttons stop
    implying it's live. `trip` must carry checklist_msg_id (get_trip / get_open_trips both do)."""
    if trip.get("closed_at") is None:
        close_trip(trip["id"])
    if trip.get("checklist_msg_id"):
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=trip["checklist_msg_id"], text=f"🔚 Trip '{trip['name']}' ended.")
        except Exception as e:
            logger.warning("Could not retire checklist for trip %s: %s", trip["id"], e)


def _open_trips_summary(chat_id):
    """One line per open trip with its members, or None if the chat has no open trips. Lets /trip
    (and /trip members) show who's on each trip without scrolling back to the checklist."""
    open_trips = get_open_trips(chat_id)
    if not open_trips:
        return None
    lines = []
    for t in open_trips:
        names = ", ".join(m["user_name"] for m in get_trip_members(t["id"])) or "nobody yet"
        lines.append(f"🍻 {t['name']} — {names}")
    return "\n".join(lines)


async def _resummon_checklists(context, chat_id) -> bool:
    """Re-post every open trip's checklist at the bottom of the chat so anyone can toggle their own
    membership without scrolling back to the original message. Keeps exactly one *live* checklist per
    trip: the fresh message becomes checklist_msg_id and the previous one is retired — deleted if we
    can, otherwise its keyboard is stripped so it can't cause a split-brain toggle (only the tapped
    message refreshes). Returns False when the chat has no open trips."""
    open_trips = get_open_trips(chat_id)
    if not open_trips:
        return False
    for t in open_trips:
        old_msg_id = t.get("checklist_msg_id")
        msg = await context.bot.send_message(chat_id, _trip_text(t), reply_markup=_trip_keyboard(t["id"], chat_id))
        set_trip_checklist_msg(t["id"], msg.message_id)
        if old_msg_id and old_msg_id != msg.message_id:
            try:
                await context.bot.delete_message(chat_id, old_msg_id)
            except Exception:
                try:
                    await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=old_msg_id, reply_markup=None)
                except Exception:
                    pass
    return True


async def _send_trip_menu(message, chat_id) -> None:
    """The /trip landing menu: shows each open trip with its members and buttons. Start is always
    offered; Members (re-summon the checklists) and End are offered only when a trip is open. Start
    asks for a name; End lists the caller's own open trips to close."""
    open_trips = get_open_trips(chat_id)
    row = [InlineKeyboardButton("🍻 Start trip", callback_data="tripnew")]
    if open_trips:
        row.append(InlineKeyboardButton("👥 Members", callback_data="tripmembers"))
        row.append(InlineKeyboardButton("🔚 End trip", callback_data="tripendmenu"))
    header = _open_trips_summary(chat_id) or "No open trips yet."
    await message.reply_text(header, reply_markup=InlineKeyboardMarkup([row]))


async def trip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Trips only work in groups.")
        return

    user = update.message.from_user
    args = context.args or []

    if not args:
        await _send_trip_menu(update.message, chat.id)
        return

    # /trip members (aliases) — re-summon each open trip's checklist to the bottom of the chat, so
    # people can see who's on and toggle their own membership without scrolling back to the original
    # message. Intercepted before the create path so it isn't taken as a trip named "members".
    if args[0].lower() in ("members", "member", "who", "list", "roster"):
        if not await _resummon_checklists(context, chat.id):
            await update.message.reply_text("No open trips.")
        return

    if args[0].lower() == "end":
        open_trips = get_open_trips(chat.id)
        end_name = " ".join(args[1:]).strip()
        if not open_trips:
            await update.message.reply_text("No open trip to end.")
            return
        if end_name:
            target = next((t for t in open_trips if t["name"].casefold() == end_name.casefold()), None)
            if not target:
                await update.message.reply_text(f"No open trip named '{end_name}'. Open trips: " + ", ".join(t["name"] for t in open_trips))
                return
        elif len(open_trips) == 1:
            target = open_trips[0]
        else:
            await update.message.reply_text("Several trips are open: " + ", ".join(t["name"] for t in open_trips) + "\nUse /trip end <name>.")
            return
        # Only the creator may end a trip — same rule the 🔚 End-trip button enforces.
        if user.id != target["created_by"]:
            await update.message.reply_text(f"Only the creator of '{target['name']}' can end it.")
            return
        await _end_trip(context.bot, chat.id, target)
        await update.message.reply_text(f"🔚 Trip '{target['name']}' ended. New cheers won't be tagged to it.")
        return

    await _start_trip(context, chat.id, user, " ".join(args))


def _end_menu_keyboard(chat_id, user_id):
    """Buttons for the trips this user may end (creator-only), one per row. None if they have none."""
    mine = [t for t in get_open_trips(chat_id) if t["created_by"] == user_id]
    if not mine:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"🔚 {t['name']}", callback_data=f"tripclose_{t['id']}")] for t in mine])


async def _handle_trip_callback(query, context) -> None:
    data = query.data
    user = query.from_user
    chat_id = query.message.chat_id

    # /trip menu: Start -> ask for a name via a stateful text prompt (same capture as /emoji).
    if data == "tripnew":
        await query.answer()
        context.user_data["awaiting_trip_name"] = {"chat_id": chat_id, "prompt_id": query.message.message_id}
        try:
            await query.edit_message_text("🍻 Send the trip name as a message (e.g. Lisboa 🇵🇹).")
        except Exception as e:
            logger.warning("Could not edit trip menu: %s", e)
        return

    # /trip menu: Members -> re-summon the open trips' checklists so anyone can toggle membership.
    if data == "tripmembers":
        await query.answer()
        await _resummon_checklists(context, chat_id)
        return

    # /trip menu: End -> list the caller's own open trips to close (ending is creator-only).
    if data == "tripendmenu":
        kb = _end_menu_keyboard(chat_id, user.id)
        if not kb:
            await query.answer("You can only end trips you started.", show_alert=True)
            return
        await query.answer()
        try:
            await query.edit_message_text("Which trip do you want to end?", reply_markup=kb)
        except Exception as e:
            logger.warning("Could not edit trip menu: %s", e)
        return

    if data.startswith("tripclose_"):
        trip = get_trip(int(data[len("tripclose_") :]))
        if not trip or trip["chat_id"] != chat_id:
            await query.answer()
            return
        if user.id != trip["created_by"]:
            await query.answer("Only the creator can end it.", show_alert=True)
            return
        await query.answer("Trip ended.")
        await _end_trip(context.bot, chat_id, trip)
        kb = _end_menu_keyboard(chat_id, user.id)  # refresh the menu with whatever's still open
        try:
            if kb:
                await query.edit_message_text("Trip ended. Any others to end?", reply_markup=kb)
            else:
                await query.edit_message_text("🔚 Trip ended.")
        except Exception as e:
            logger.warning("Could not edit trip menu: %s", e)
        return

    if data.startswith("tripend_"):
        trip = get_trip(int(data[8:]))
        if not trip or trip["chat_id"] != chat_id:
            return
        if user.id != trip["created_by"]:
            await query.answer("Only the trip creator can end it.", show_alert=True)
            return
        await query.answer("Trip ended.")
        await _end_trip(context.bot, chat_id, trip)  # edits this very (checklist) message to "ended"
        return

    trip_id_raw, _, target_raw = data[5:].partition("_")
    trip = get_trip(int(trip_id_raw))
    target_id = int(target_raw)
    if not trip or trip["chat_id"] != chat_id:
        return
    if trip["closed_at"] is not None:
        await query.answer("This trip has ended.", show_alert=True)
        return
    # You toggle yourself; only the trip creator can toggle anyone.
    if user.id != target_id and user.id != trip["created_by"]:
        await query.answer("You can only toggle yourself.", show_alert=True)
        return

    if user.id == target_id:
        target_name = user.full_name or user.username or str(user.id)
    else:
        # Resolve from the same union the keyboard uses — never fall through to the raw id, which
        # would get written back as the name and stick.
        target_name = _known_users(chat_id, trip["id"]).get(target_id, str(target_id))

    member, moved_from = toggle_trip_member(trip["id"], target_id, target_name)
    if member and moved_from:
        await query.answer(f"{target_name} is on '{trip['name']}' (moved off '{moved_from[0]['name']}').")
    else:
        await query.answer(f"{target_name} is {'on' if member else 'off'} the trip.")
    try:
        await query.edit_message_reply_markup(reply_markup=_trip_keyboard(trip["id"], chat_id))
    except Exception as e:
        logger.warning("Could not update trip keyboard: %s", e)
    await _refresh_checklists(context.bot, chat_id, moved_from)


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

    # A one-tap "share location" button opens the Mini App in location mode (startapp=loc_c<chat_id>),
    # which reads the device location and posts it to /api/submit-location. request_location keyboard
    # buttons don't work in groups, so we route through the Mini App instead. Requires BOT_USERNAME for
    # the deep link; without it we fall back to the attachment-menu instructions only.
    reply_markup = None
    if BOT_USERNAME:
        share_url = f"https://t.me/{BOT_USERNAME}/map?startapp=loc_c{chat.id}"
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("📍 Share location", url=share_url)]])
        prompt = "Cheers received! Tap 📍 Share location below to pin it on the map — or send your location from the attachment menu."
    else:
        prompt = "Cheers received! Now tap the 📍 attachment button and send your location to pin it on the map."

    bot_reply = await message.reply_text(prompt, reply_markup=reply_markup)

    # Persist to the DB (not context.user_data) so the Mini App's web handler and the attachment-menu
    # fallback both consume the same pending row — whichever completes first wins, preventing double pins.
    set_pending_pin(
        chat_id=chat.id,
        user_id=user.id,
        file_id=file_id,
        message_id=message.message_id,
        user_name=user_name,
        video_link=video_link,
        prompt_msg_id=bot_reply.message_id,
    )


_geocode_cache: dict[tuple[float, float], tuple[str | None, str | None, str | None]] = {}
_MAX_CACHE_SIZE = 500
_nominatim_lock = asyncio.Lock()


async def reverse_geocode(lat, lng):
    key = (round(lat, 3), round(lng, 3))

    if key in _geocode_cache:
        result = _geocode_cache.pop(key)
        _geocode_cache[key] = result
        return result

    async with _nominatim_lock:
        if key in _geocode_cache:
            result = _geocode_cache.pop(key)
            _geocode_cache[key] = result
            return result

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
                    result = (city, country, country_code)
                    if len(_geocode_cache) >= _MAX_CACHE_SIZE:
                        _geocode_cache.pop(next(iter(_geocode_cache)))
                    _geocode_cache[key] = result
                    return result
        except Exception:
            return None, None, None
        finally:
            await asyncio.sleep(1)


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    location = update.message.location
    if not location:
        return

    chat_id = update.effective_chat.id
    user = update.message.from_user

    # Consume this user's pending video for this chat. Only the user who sent the video has a row,
    # so the bot only ever pairs a location with that same user's pending cheers.
    pending = pop_pending_pin(chat_id, user.id)
    if not pending:
        return

    city, country, country_code = await reverse_geocode(location.latitude, location.longitude)

    # Tag with the open trip this user is on (membership is the gate; None -> untagged).
    trip = get_open_trip_for_member(chat_id, user.id)

    add_pin(
        chat_id=chat_id,
        message_id=pending["message_id"],
        user_id=user.id,
        user_name=pending["user_name"],
        video_file_id=pending["file_id"],
        lat=location.latitude,
        lng=location.longitude,
        video_link=pending.get("video_link"),
        city=city,
        country=country,
        country_code=country_code,
        trip_id=trip["id"] if trip else None,
    )

    location_msg_id = update.effective_message.message_id
    prompt_msg_id = pending.get("prompt_msg_id")

    confirm_text = f"Pinned to trip '{trip['name']}'! Use /map to view the cheers map." if trip else "Pinned! Use /map to view the cheers map."
    confirmation = await context.bot.send_message(chat_id, confirm_text)

    await asyncio.sleep(3)

    for mid in (confirmation.message_id, prompt_msg_id, location_msg_id):
        if mid:
            try:
                await context.bot.delete_message(chat_id, mid)
            except Exception:
                pass


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
