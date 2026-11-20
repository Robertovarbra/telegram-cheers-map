import asyncio
from datetime import datetime, timezone

from aiohttp import ClientSession
from telegram import Chat, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji, Update
from telegram.error import BadRequest, ChatMigrated, Forbidden
from telegram.ext import ContextTypes

from auth import is_member
from config import ADMIN_USER_IDS, BOT_USERNAME, GROUP_PASS_DAYS, GROUP_PASS_STARS, REEL_PRICE_STARS, THROWBACK_HOUR_UTC, logger
from database import (
    MAX_COMMENT_LEN,
    add_pin,
    add_pin_comment,
    close_trip,
    create_trip,
    delete_chat_pins,
    get_all_chat_ids,
    get_analytics_summary,
    get_chat_entitlements,
    get_chat_setting,
    get_chat_throwbacks_enabled,
    get_chat_trip_users,
    get_due_throwback_chats,
    get_on_this_day_pins,
    get_open_trip_for_member,
    get_open_trips,
    get_pin,
    get_pin_by_message,
    get_pins_meta,
    get_random_throwback_pin,
    get_trip,
    get_trip_members,
    get_trip_pins_for_reel,
    is_trip_reel_unlocked,
    mark_throwback_sent,
    migrate_chat_data,
    normalize_reaction_emoji,
    pop_pending_pin,
    record_payment_and_grant,
    register_pin_message_alias,
    set_chat_setting,
    set_chat_throwbacks_enabled,
    set_chat_title,
    set_pending_pin,
    set_trip_checklist_msg,
    set_user_pref,
    sync_telegram_reactions,
    toggle_trip_member,
    track,
    update_pin_comment_by_message,
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

        # Enforce the same tenant-isolation invariant as the Mini App API: only current members of
        # the source chat can watch its pins. Checked before get_pin so non-members can't probe IDs.
        user = update.effective_user
        if not user or not await is_member(context.bot, chat_id, user.id):
            await update.message.reply_text("You don't have access to this cheers.")
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

    reply_markup = None
    if BOT_USERNAME:
        atlas_url = f"https://t.me/{BOT_USERNAME}/map?startapp=atlas"
        add_url = f"https://t.me/{BOT_USERNAME}?startgroup=true&admin=pin_messages+delete_messages"
        reply_markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🌍 Open My Atlas", url=atlas_url)],
                [InlineKeyboardButton("➕ Add to a Group", url=add_url)],
            ]
        )

    await update.message.reply_text(
        "🍻 Cheers Map\n"
        "Real, unfiltered video-note moments with your friends — pinned to the world map. No filters, no uploads, no feed.\n\n"
        "How it works:\n"
        "1. Add me to a friend group — or send a video note right here in DM for solo pins\n"
        "2. Tap 📍 Share location to pin it on the map\n"
        "3. Use /map in a group, /atlas for your personal map across all chats, & /trip for travels\n\n"
        "Tip: allow pinning and deleting messages in groups so I can keep the chat tidy.",
        reply_markup=reply_markup,
    )


async def atlas_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open the caller's personal My Atlas (all their pins across every group + solo DM pins)."""
    if not BOT_USERNAME:
        await update.message.reply_text("My Atlas is not configured yet (missing BOT_USERNAME).")
        return
    atlas_url = f"https://t.me/{BOT_USERNAME}/map?startapp=atlas"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🌍 Open My Atlas", url=atlas_url)]])
    await update.message.reply_text("🌍 Your personal Cheers Atlas — every pin you've dropped across all groups & solo DMs:", reply_markup=kb)


async def admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Internal analytics snapshot for bot admins. Strictly DM-only and gated by ADMIN_USER_IDS."""
    user = update.effective_user
    if not user or user.id not in ADMIN_USER_IDS:
        return  # fail closed: don't even reveal the command exists to non-admins

    if update.effective_chat.type != Chat.PRIVATE:
        await update.message.reply_text("Run /admin_stats in a private chat with me.")
        return

    days = 7
    if context.args and context.args[0].isdigit():
        days = max(1, min(365, int(context.args[0])))

    s = get_analytics_summary(days=days)
    ev = s["events"]
    added = ev.get("bot_added", 0)
    removed = ev.get("bot_removed", 0)
    started = ev.get("pin_started", 0)
    created = ev.get("pin_created", 0)
    deleted = ev.get("pin_deleted", 0)
    map_cmd = ev.get("map_command", 0)
    map_open = ev.get("map_opened", 0)
    atlas_open = ev.get("atlas_opened", 0)
    stats_open = ev.get("stats_opened", 0)
    reacted = ev.get("pin_reacted", 0)
    commented = ev.get("pin_commented", 0)
    captioned = ev.get("pin_captioned", 0)
    relocated = ev.get("pin_relocated", 0)
    throwbacks = ev.get("throwback_sent", 0)
    reel_prev = ev.get("reel_preview_generated", 0)
    reel_hd = ev.get("reel_hd_generated", 0)
    conv = f"{(created / started * 100):.0f}%" if started else "n/a"

    lines = [
        f"📊 Cheers Map Stats (last {days}d)",
        "",
        "All-time:",
        f"• Pins: {s['total_pins']} across {s['total_chats']} chats ({s['total_users']} users)",
        f"• Trips: {s['total_trips']} total ({s['open_trips']} open)",
        f"• Revenue: ⭐ {s['stars_total']} Stars ({s['payments_total']} payments)",
        "",
        f"Last {days}d activity:",
        f"• Active: {s['active_chats']} chats, {s['active_users']} users",
        f"• Installs: +{added} / -{removed} (net {added - removed:+d})",
        f"• Pin funnel: {started} started → {created} created ({conv})",
        f"• Pin actions: {reacted} reactions · {commented} replies · {captioned} captions · {relocated} moves · {deleted} deletes",
        f"• Maps: {map_cmd} /map cmds → {map_open} group · {atlas_open} atlas · {stats_open} passport",
        f"• Throwbacks: {throwbacks} sent",
        f"• Reels: {reel_prev} preview · {reel_hd} HD · ⭐ {s['stars_window']} Stars ({s['payments_window']} paid)",
    ]
    if s["top_chats"]:
        lines.append("")
        lines.append(f"Top chats ({days}d):")
        for row in s["top_chats"]:
            lines.append(f"• {row['chat_id']}: {row['pins']} pins")

    await update.message.reply_text("\n".join(lines))


async def map_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    chat_id = chat.id
    user_id = update.effective_user.id if update.effective_user else None
    track("map_command", chat_id=chat_id, user_id=user_id)

    if chat.type == Chat.PRIVATE:
        atlas_url = f"https://t.me/{BOT_USERNAME}/map?startapp=atlas"
        keyboard = [[InlineKeyboardButton("🌍 Open My Atlas", url=atlas_url)]]
        await update.message.reply_text("🌍 My Cheers Atlas", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if chat.title:
        set_chat_title(chat_id, chat.title)
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
    # Keep already-captured Telegram replies to pinned video notes in sync if the user edits them.
    if update.edited_message is not None:
        emsg = update.edited_message
        if emsg.text and emsg.reply_to_message and emsg.from_user and not emsg.from_user.is_bot:
            cleaned = " ".join(emsg.text.split())
            if cleaned and not cleaned.startswith("/"):
                update_pin_comment_by_message(emsg.chat_id, emsg.message_id, cleaned[:MAX_COMMENT_LEN])
        return

    msg = update.message
    if not msg or not msg.text:
        return

    # Trip-name capture (from the /trip -> Start button) takes precedence over the emoji flow.
    # user_data is per-user across ALL chats, so only consume it when the reply comes from the same
    # chat where Start was pressed — otherwise an unrelated message elsewhere (even a DM to the bot)
    # would be turned into a trip name in the original group.
    pending_trip = context.user_data.get("awaiting_trip_name")
    if pending_trip and msg.chat_id == pending_trip["chat_id"]:
        context.user_data.pop("awaiting_trip_name", None)
        name = msg.text.strip()
        if not name.startswith("/"):  # a command, not a name — cancel quietly
            await _start_trip(context, pending_trip["chat_id"], msg.from_user, name)
            if pending_trip.get("prompt_id"):  # tidy up the "send the trip name" prompt
                try:
                    await context.bot.delete_message(pending_trip["chat_id"], pending_trip["prompt_id"])
                except Exception:
                    pass
        return

    pending = context.user_data.pop("awaiting_emoji", None)
    if not pending:
        # Capture direct replies to a pinned video note in Telegram so they appear inside the Mini App!
        if msg.reply_to_message and msg.from_user and not msg.from_user.is_bot:
            cleaned = " ".join(msg.text.split())
            if cleaned and not cleaned.startswith("/"):
                pin = get_pin_by_message(msg.chat_id, msg.reply_to_message.message_id)
                if pin:
                    user = msg.from_user
                    user_name = user.full_name or user.username or str(user.id)
                    add_pin_comment(msg.chat_id, pin["id"], user.id, user_name, cleaned[:MAX_COMMENT_LEN], message_id=msg.message_id)
                    track("pin_commented", chat_id=msg.chat_id, user_id=user.id)
        return

    user_id = msg.from_user.id
    text = msg.text.strip()
    chat_id = msg.chat_id
    prompt_id = pending["prompt_id"]
    user_msg_id = msg.message_id

    if text in ("/none", "/skip", "none", "skip"):
        if text in ("/none", "none"):
            set_user_pref(user_id, "pin_emoji", None)
            reply = await msg.reply_text("✅ Custom emoji removed.")
        else:
            reply = await msg.reply_text("Skipped.")
    elif len(text) <= 10:
        set_user_pref(user_id, "pin_emoji", text)
        reply = await msg.reply_text(f"Pin emoji set to {text}!")
    else:
        reply = await msg.reply_text("❌ That's too long. Send a single emoji or /none.")

    await asyncio.sleep(3)
    for mid in (prompt_id, user_msg_id, reply.message_id):
        try:
            await context.bot.delete_message(chat_id, mid)
        except Exception:
            pass


async def handle_message_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sync native Telegram emoji reactions on pinned video notes directly into the Mini App.

    Requires the bot to be a group admin (Telegram only delivers message_reaction updates to admins).
    Bot-authored reactions are ignored so mirroring Mini App reactions back to Telegram never loops."""
    mr = update.message_reaction
    if not mr:
        return
    if mr.user:
        if mr.user.is_bot:
            return
        actor_id = mr.user.id
    elif mr.actor_chat:
        actor_id = mr.actor_chat.id
    else:
        return

    def _extract(reactions) -> set[str]:
        out: set[str] = set()
        for r in reactions or ():
            if isinstance(r, ReactionTypeEmoji):
                norm = normalize_reaction_emoji(r.emoji)
                if norm:
                    out.add(norm)
        return out

    old_set = _extract(mr.old_reaction)
    new_set = _extract(mr.new_reaction)
    added = new_set - old_set
    removed = old_set - new_set
    if not added and not removed:
        return

    pin_id = sync_telegram_reactions(mr.chat.id, mr.message_id, actor_id, added, removed)
    logger.info(
        "Telegram reaction chat=%s msg=%s actor=%s +%s -%s -> pin_id=%s",
        mr.chat.id,
        mr.message_id,
        actor_id,
        sorted(added),
        sorted(removed),
        pin_id,
    )
    if pin_id and added:
        track("pin_reacted", chat_id=mr.chat.id, user_id=actor_id)


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
    """Close a trip (if still open), retire its checklist message, and post a recap summary card
    linking straight to the trip's map & Reel player when the trip has at least one cheers."""
    if trip.get("closed_at") is None:
        close_trip(trip["id"])
    if trip.get("checklist_msg_id"):
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=trip["checklist_msg_id"], text=f"🔚 Trip '{trip['name']}' ended.")
        except Exception as e:
            logger.warning("Could not retire checklist for trip %s: %s", trip["id"], e)

    pins = get_trip_pins_for_reel(chat_id, trip["id"], limit=500)
    if pins and BOT_USERNAME:
        cities = {p["city"] for p in pins if p.get("city")}
        friends = {p["user_id"] for p in pins}
        parts = [f"{len(pins)} cheers"]
        if cities:
            parts.append(f"{len(cities)} {'city' if len(cities) == 1 else 'cities'}")
        parts.append(f"{len(friends)} {'friend' if len(friends) == 1 else 'friends'}")
        trip_url = f"https://t.me/{BOT_USERNAME}/map?startapp=c{chat_id}_t{trip['id']}"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎬 Watch Trip Recap Reel", url=trip_url)]])
        try:
            await bot.send_message(
                chat_id,
                f"🎬 Trip '{trip['name']}' wrapped!\n" + " · ".join(parts) + "\n\nOpen the map to watch the route & export your Trip Recap Reel.",
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("Could not send trip summary for trip %s: %s", trip["id"], e)


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
    if chat.type not in (Chat.GROUP, Chat.SUPERGROUP, Chat.PRIVATE):
        return

    message = update.message

    if not message or not message.video_note:
        return

    file_id = message.video_note.file_id

    user = message.from_user
    user_name = user.full_name or user.username or str(user.id)

    is_private = chat.type == Chat.PRIVATE
    if chat.type == Chat.SUPERGROUP:
        video_link = f"https://t.me/c/{str(chat.id)[4:]}/{message.message_id}"
    elif chat.type == Chat.GROUP:
        video_link = f"tg://openmessage?chat_id={chat.id}&message_id={message.message_id}"
    elif is_private:
        video_link = None
    else:
        video_link = message.link

    if not is_private and chat.title:
        set_chat_title(chat.id, chat.title)

    # A one-tap "share location" button opens the Mini App in location mode (startapp=loc_c<chat_id>),
    # which reads the device location and posts it to /api/submit-location. request_location keyboard
    # buttons don't work in groups, so we route through the Mini App instead. Requires BOT_USERNAME for
    # the deep link; without it we fall back to the attachment-menu instructions only.
    reply_markup = None
    dest = "your Atlas" if is_private else "the map"
    if BOT_USERNAME:
        share_url = f"https://t.me/{BOT_USERNAME}/map?startapp=loc_c{chat.id}"
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("📍 Share location", url=share_url)]])
        prompt = f"Cheers received! Tap 📍 Share location below to pin it on {dest} — or send your location from the attachment menu."
    else:
        prompt = f"Cheers received! Now tap the 📍 attachment button and send your location to pin it on {dest}."

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
    track("pin_started", chat_id=chat.id, user_id=user.id)


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
    if not update.message:
        return
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

    # Tag with the open trip this user is on (membership is the gate; None -> untagged; trips are group-only).
    is_private = update.effective_chat.type == Chat.PRIVATE
    trip = None if is_private else get_open_trip_for_member(chat_id, user.id)

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
    track("pin_created", chat_id=chat_id, user_id=user.id)

    location_msg_id = update.effective_message.message_id
    prompt_msg_id = pending.get("prompt_msg_id")

    if is_private:
        confirm_text = "Pinned to your Atlas! Use /map to explore your personal map."
    elif trip:
        confirm_text = f"Pinned to trip '{trip['name']}'! Use /map to view the cheers map."
    else:
        confirm_text = "Pinned! Use /map to view the cheers map."
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
        # Private DM chats (chat_id > 0) hold personal solo Atlas pins — never purge them via group-kick checks.
        if chat_id > 0:
            continue
        try:
            chat = await bot.get_chat(chat_id)
            try:
                member = await chat.get_member(bot.id)
                if member.status in (ChatMember.LEFT, ChatMember.BANNED):
                    deleted = delete_chat_pins(chat_id)
                    logger.info("Chat %s: Bot is %s. Deleted %s pin(s).", chat_id, member.status, deleted)
                    total_deleted += deleted
                    continue
            except Exception:
                pass
            # Backfill/refresh the cached group name for My Atlas; groups that predate the chat_title column
            # (or were renamed while the bot was offline) would otherwise show the "Group" placeholder.
            if chat.title:
                set_chat_title(chat_id, chat.title)
        except ChatMigrated as e:
            # Upgraded to a supergroup while we weren't listening (or before migrations were handled): its data is
            # still under the dead basic-group id, where nothing can reach it. Move it rather than purge it.
            await _migrate_chat(bot, chat_id, e.new_chat_id)
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


# Bot membership statuses that mean "the bot is actually in the chat". RESTRICTED only counts
# when is_member is True; LEFT and BANNED (kicked) are out.
_PRESENT_STATUSES = (ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER)


def _is_in_chat(member):
    if member.status in _PRESENT_STATUSES:
        return True
    if member.status == ChatMember.RESTRICTED:
        return bool(member.is_member)
    return False


async def _migrate_chat(bot, old_chat_id, new_chat_id, new_title=None):
    """Move a basic group's data to its supergroup and cache the supergroup's name for My Atlas."""
    moved = migrate_chat_data(old_chat_id, new_chat_id)
    logger.info("Chat %s migrated to supergroup %s. Moved %s pin(s).", old_chat_id, new_chat_id, moved)
    if not new_title:
        try:
            new_title = (await bot.get_chat(new_chat_id)).title
        except Exception:
            logger.debug("Could not fetch title for migrated chat %s", new_chat_id, exc_info=True)
    if new_title:
        set_chat_title(new_chat_id, new_title)


async def handle_chat_migration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A basic group was upgraded to a supergroup, which gets a new chat id. Telegram posts a service message in
    both chats (migrate_to in the old one, migrate_from in the new one); either one moves the data, the other is a no-op."""
    message = update.effective_message
    if not message:
        return
    if message.migrate_to_chat_id:
        await _migrate_chat(context.bot, message.chat_id, message.migrate_to_chat_id)
    elif message.migrate_from_chat_id:
        await _migrate_chat(context.bot, message.migrate_from_chat_id, message.chat_id, message.chat.title)


async def handle_new_chat_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Keep the cached group name in sync when a group is renamed (service messages arrive even in privacy mode)."""
    message = update.effective_message
    if message and message.new_chat_title:
        set_chat_title(message.chat_id, message.new_chat_title)


async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record when the bot is added to / removed from a group, for internal analytics only.
    Pin cleanup stays with cleanup_inactive_chats (startup-only, so accidental removals are recoverable)."""
    cm = update.my_chat_member
    if cm is None:
        return

    # Group-centric bot: a private-chat "add" is just a user opening the DM, so it's skipped.
    if cm.chat.type not in (Chat.GROUP, Chat.SUPERGROUP):
        return

    was_in = _is_in_chat(cm.old_chat_member)
    now_in = _is_in_chat(cm.new_chat_member)
    if was_in == now_in:
        return  # e.g. member -> administrator; role churn, not an add/remove

    actor_id = cm.from_user.id if cm.from_user else None
    event = "bot_added" if now_in else "bot_removed"
    track(event, chat_id=cm.chat.id, user_id=actor_id)
    logger.info("Tracked %s for chat %s (by user %s)", event, cm.chat.id, actor_id)

    if event == "bot_added":
        if cm.chat.title:
            set_chat_title(cm.chat.id, cm.chat.title)
        try:
            await context.bot.send_message(
                cm.chat.id,
                "🍻 Cheers Map is ready!\n"
                "Send a circular video note in this group, then tap 📍 Share location to drop your first pin on the map.\n\n"
                "Commands: /map · /trip · /atlas · /throwback · /color · /emoji\n"
                "(Granting pin & delete-message permissions keeps the chat tidy.)",
            )
        except Exception as e:
            logger.info("Could not send welcome message to chat %s: %s", cm.chat.id, e)


def _parse_stars_payload(payload: str):
    """Parse our Stars invoice payload (`reel:<chat_id>:<trip_id>` or `pass:<chat_id>`)."""
    parts = (payload or "").split(":")
    if len(parts) == 3 and parts[0] == "reel" and parts[1].lstrip("-").isdigit() and parts[2].isdigit():
        return "trip_reel", int(parts[1]), int(parts[2])
    if len(parts) == 2 and parts[0] == "pass" and parts[1].lstrip("-").isdigit():
        return "group_pass", int(parts[1]), None
    return None


async def handle_pre_checkout_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Validate Telegram Stars checkout before Telegram charges the user (must answer within 10s)."""
    query = update.pre_checkout_query
    if not query:
        return
    if query.currency != "XTR":
        await query.answer(ok=False, error_message="Unsupported currency.")
        return

    parsed = _parse_stars_payload(query.invoice_payload)
    if not parsed:
        await query.answer(ok=False, error_message="Invalid payment request.")
        return
    product_type, chat_id, target_id = parsed

    user = query.from_user
    if not user or not await is_member(context.bot, chat_id, user.id):
        await query.answer(ok=False, error_message="You must be a member of this group to unlock its reels.")
        return

    if product_type == "trip_reel":
        if query.total_amount != REEL_PRICE_STARS:
            await query.answer(ok=False, error_message="Price changed — please reopen checkout.")
            return
        trip = get_trip(target_id)
        if not trip or trip["chat_id"] != chat_id:
            await query.answer(ok=False, error_message="This trip no longer exists.")
            return
        if is_trip_reel_unlocked(chat_id, target_id):
            await query.answer(ok=False, error_message="This trip's HD reel is already unlocked for your group!")
            return
    else:
        if query.total_amount != GROUP_PASS_STARS:
            await query.answer(ok=False, error_message="Price changed — please reopen checkout.")
            return
        if get_chat_entitlements(chat_id)["has_group_pass"]:
            await query.answer(ok=False, error_message="Your group already has an active Group Pass!")
            return

    await query.answer(ok=True)


async def handle_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record a completed Telegram Stars payment and grant the group entitlement atomically."""
    msg = update.message
    sp = msg.successful_payment if msg else None
    if not sp:
        return

    parsed = _parse_stars_payload(sp.invoice_payload)
    if not parsed:
        logger.warning("Unknown successful_payment payload: %s", sp.invoice_payload)
        return
    product_type, chat_id, target_id = parsed
    user = msg.from_user
    user_id = user.id if user else 0

    duration = GROUP_PASS_DAYS if product_type == "group_pass" else None
    newly_recorded = record_payment_and_grant(
        telegram_charge_id=sp.telegram_payment_charge_id,
        user_id=user_id,
        chat_id=chat_id,
        product_type=product_type,
        target_id=target_id,
        amount_stars=sp.total_amount,
        currency=sp.currency,
        duration_days=duration,
    )
    if newly_recorded:
        track("payment_succeeded", chat_id=chat_id, user_id=user_id)
        logger.info("Stars payment succeeded: %s chat=%s target=%s stars=%s by user=%s", product_type, chat_id, target_id, sp.total_amount, user_id)

    reply_markup = None
    if BOT_USERNAME:
        sp_param = f"c{chat_id}_t{target_id}" if product_type == "trip_reel" else f"c{chat_id}"
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🎬 Open Trip Reel", url=f"https://t.me/{BOT_USERNAME}/map?startapp={sp_param}")]])

    if product_type == "trip_reel":
        trip = get_trip(target_id)
        tname = trip["name"] if trip else "your trip"
        await msg.reply_text(
            f"✨ HD Trip Reel unlocked for '{tname}'! Everyone in the group can now watch and export it without a watermark.",
            reply_markup=reply_markup,
        )
    else:
        await msg.reply_text(
            f"✨ Group Pass active for {GROUP_PASS_DAYS} days! Everyone in your group now has unlimited HD unwatermarked Trip Recap Reels.",
            reply_markup=reply_markup,
        )


def _country_flag(cc: str | None) -> str:
    if not cc or len(cc) != 2 or not cc.isalpha():
        return ""
    up = cc.upper()
    return chr(0x1F1E6 + ord(up[0]) - 65) + chr(0x1F1E6 + ord(up[1]) - 65)


def _format_place(pin: dict) -> str:
    parts = [p for p in (pin.get("city"), pin.get("country")) if p]
    flag = _country_flag(pin.get("country_code"))
    place = ", ".join(parts)
    if place and flag:
        return f"{place} {flag}"
    return place or flag


async def _send_throwback(bot, chat_id: int, pins: list[dict], *, is_on_this_day: bool, now: datetime | None = None) -> bool:
    """Post a nostalgic throwback card (featured video note + summary + deep link) to `chat_id`."""
    if not pins:
        return False
    now_dt = now or datetime.now(timezone.utc)
    featured = pins[0]
    try:
        pin_dt = datetime.fromisoformat(featured["created_at"])
        date_label = pin_dt.strftime("%b %d, %Y").replace(" 0", " ")
        date_compact = pin_dt.strftime("%Y%m%d")
        years_ago = max(1, now_dt.year - pin_dt.year)
    except ValueError:
        date_label = featured["created_at"][:10]
        date_compact = date_label.replace("-", "")
        years_ago = 1

    if is_on_this_day:
        yr_word = "year" if years_ago == 1 else "years"
        header = f"🕰️ On this day {years_ago} {yr_word} ago ({date_label})"
    else:
        header = f"🕰️ Cheers flashback · {date_label}"

    place = _format_place(featured)
    same_day = [p for p in pins if (p.get("created_at") or "")[:10] == (featured.get("created_at") or "")[:10]]
    names = list(dict.fromkeys(p["user_name"] for p in same_day))
    if len(same_day) == 1:
        who = featured["user_name"]
    else:
        who = f"{len(same_day)} cheers from " + ", ".join(names[:3]) + ("…" if len(names) > 3 else "")

    lines = [header]
    if place:
        lines.append(f"📍 {place} · {who}")
    else:
        lines.append(f"🍻 {who}")
    if featured.get("caption"):
        lines.append(f"“{featured['caption']}”")

    reply_markup = None
    if BOT_USERNAME:
        sp = f"c{chat_id}_d{date_compact}" if len(date_compact) == 8 and date_compact.isdigit() else f"c{chat_id}"
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🗺️ View on Map", url=f"https://t.me/{BOT_USERNAME}/map?startapp={sp}")]])

    video_msg_id = None
    try:
        vmsg = await bot.send_video_note(chat_id=chat_id, video_note=featured["video_file_id"])
        video_msg_id = vmsg.message_id
        register_pin_message_alias(chat_id, video_msg_id, featured["id"])
    except (Forbidden, BadRequest) as e:
        # If the bot was removed/blocked, abort so we don't also fail on send_message.
        if isinstance(e, Forbidden) or "chat not found" in str(e).lower():
            logger.info("Skipping throwback for inaccessible chat %s: %s", chat_id, e)
            return False
        logger.info("Could not resend video note for throwback in chat %s: %s", chat_id, e)
    except Exception as e:
        logger.info("Could not resend video note for throwback in chat %s: %s", chat_id, e)

    try:
        await bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            reply_to_message_id=video_msg_id,
            reply_markup=reply_markup,
        )
        return True
    except Exception as e:
        logger.info("Could not send throwback message to chat %s: %s", chat_id, e)
        return False


async def throwback_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual throwback trigger and toggle (`/throwback`, `/throwback on`, `/throwback off`)."""
    chat = update.effective_chat
    if chat.type not in (Chat.GROUP, Chat.SUPERGROUP):
        await update.message.reply_text("Throwbacks only work in groups.")
        return
    if chat.title:
        set_chat_title(chat.id, chat.title)

    args = [a.lower() for a in (context.args or [])]
    if args and args[0] in ("off", "disable", "stop"):
        set_chat_throwbacks_enabled(chat.id, False)
        await update.message.reply_text("🔕 Automatic 'On this day' throwbacks are now OFF for this group. Use /throwback on to re-enable.")
        return
    if args and args[0] in ("on", "enable", "start"):
        set_chat_throwbacks_enabled(chat.id, True)
        await update.message.reply_text("🕰️ Automatic 'On this day' throwbacks are now ON for this group!")
        return

    now = datetime.now(timezone.utc)
    month_day = now.strftime("%m-%d")
    otd_pins = get_on_this_day_pins(chat.id, month_day, now.year)
    if otd_pins:
        sent = await _send_throwback(context.bot, chat.id, otd_pins, is_on_this_day=True, now=now)
        if sent:
            track("throwback_sent", chat_id=chat.id, user_id=update.effective_user.id if update.effective_user else None)
        return

    flashback = get_random_throwback_pin(chat.id, min_age_days=14)
    if flashback:
        sent = await _send_throwback(context.bot, chat.id, [flashback], is_on_this_day=False, now=now)
        if sent:
            track("throwback_sent", chat_id=chat.id, user_id=update.effective_user.id if update.effective_user else None)
        return

    status = "ON" if get_chat_throwbacks_enabled(chat.id) else "OFF"
    await update.message.reply_text(
        f"🕰️ No memories old enough for a throwback yet — keep dropping cheers!\n"
        f"(Automatic 'On this day' throwbacks are {status}. Toggle with /throwback on or /throwback off.)"
    )


async def run_due_throwbacks(bot, now: datetime | None = None) -> int:
    """Check all active groups for 'On this day' memories from prior years and post once per day."""
    now_dt = now or datetime.now(timezone.utc)
    if now_dt.hour < THROWBACK_HOUR_UTC:
        return 0
    month_day = now_dt.strftime("%m-%d")
    today_str = now_dt.strftime("%Y-%m-%d")
    due_chats = get_due_throwback_chats(month_day, now_dt.year, today_str)
    sent_count = 0
    for chat_id in due_chats:
        pins = get_on_this_day_pins(chat_id, month_day, now_dt.year)
        if not pins:
            continue
        # Record today's attempt first so a flaky Telegram error never causes repeat spam in a loop.
        mark_throwback_sent(chat_id, today_str)
        if await _send_throwback(bot, chat_id, pins, is_on_this_day=True, now=now_dt):
            track("throwback_sent", chat_id=chat_id)
            sent_count += 1
    return sent_count
