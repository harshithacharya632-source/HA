import re
from datetime import datetime, timedelta
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
)
from pyrogram.enums import ChatMemberStatus
from database.guard_db import (
    get_settings, update_settings,
    get_warns, add_warn, reset_warns,
    log_ban, remove_ban_log, get_all_banned
)

URL_REGEX = re.compile(r"(https?://|www\.|t\.me/)", re.IGNORECASE)

# ── Ban message sent to user via PM ──────────────────────────────────────────

BAN_PM_TEXT = (
    "👋 **Hello from Goflix!**\n\n"
    "🚫 You have been **banned** from the group.\n\n"
    "This is an automated action due to repeated violations "
    "of group rules (links / forwarded messages / long messages).\n\n"
    "⚠️ **Please do not spam or message the bot** — "
    "this is not our issue to resolve.\n\n"
    "📩 To request removal of your ban, please **contact the group admin** directly.\n\n"
    "— **Goflix Team** 🎬"
)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def is_admin(client, chat_id, user_id):
    try:
        m = await client.get_chat_member(chat_id, user_id)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except:
        return False


async def do_mute(client, chat_id, user_id, minutes):
    until = datetime.utcnow() + timedelta(minutes=minutes)
    await client.restrict_chat_member(
        chat_id, user_id,
        ChatPermissions(),
        until_date=until
    )
    return until


async def do_unmute(client, chat_id, user_id):
    await client.restrict_chat_member(
        chat_id, user_id,
        ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
        )
    )


def settings_keyboard(s: dict) -> InlineKeyboardMarkup:
    """Main /guard settings panel keyboard."""

    def toggle_btn(label, key):
        state = "✅" if s.get(key, True) else "❌"
        return InlineKeyboardButton(f"{state} {label}", callback_data=f"gs_toggle_{key}")

    enabled = s.get("enabled", False)
    main_btn = InlineKeyboardButton(
        f"🛡 Guard: {'ON ✅' if enabled else 'OFF ❌'}",
        callback_data="gs_toggle_enabled"
    )

    return InlineKeyboardMarkup([
        [main_btn],
        [
            toggle_btn("🔗 Links", "link_guard"),
            toggle_btn("📨 Forwards", "forward_guard"),
        ],
        [
            toggle_btn("📝 Long Msg", "longmsg_guard"),
        ],
        [
            InlineKeyboardButton(f"⏱ Warn1: {s.get('warn1_mute', 30)}m", callback_data="gs_set_warn1"),
            InlineKeyboardButton(f"⏱ Warn2: {s.get('warn2_mute', 180)}m", callback_data="gs_set_warn2"),
        ],
        [
            InlineKeyboardButton(f"📝 Word Limit: {s.get('word_limit', 100)}", callback_data="gs_set_words"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="gs_refresh"),
        ]
    ])


def settings_text(s: dict) -> str:
    enabled      = "✅ ON" if s.get("enabled", False) else "❌ OFF"
    link_g       = "✅" if s.get("link_guard", True) else "❌"
    forward_g    = "✅" if s.get("forward_guard", True) else "❌"
    longmsg_g    = "✅" if s.get("longmsg_guard", True) else "❌"
    return (
        f"🛡 **Goflix Guard Settings**\n\n"
        f"**Status:** {enabled}\n\n"
        f"**Triggers:**\n"
        f"  {link_g} Block Links\n"
        f"  {forward_g} Block Forwards\n"
        f"  {longmsg_g} Block Long Messages\n\n"
        f"**Mute Durations:**\n"
        f"  ⚠️ Warn 1 → Mute `{s.get('warn1_mute', 30)}` min\n"
        f"  ⚠️ Warn 2 → Mute `{s.get('warn2_mute', 180)}` min\n"
        f"  🚫 Warn 3 → **Ban**\n\n"
        f"**Long Msg Word Limit:** `{s.get('word_limit', 100)}` words\n\n"
        f"_Use buttons to toggle features or adjust timings._"
    )


# ── /guard on | /guard off | /guardsettings ───────────────────────────────────

@Client.on_message(filters.command(["guard", "guardsettings"]) & filters.group)
async def guard_cmd(client, message):
    if not await is_admin(client, message.chat.id, message.from_user.id):
        return await message.reply("❌ Admins only!")

    args = message.command
    chat_id = message.chat.id

    # /guard on  /guard off
    if len(args) > 1:
        arg = args[1].lower()
        if arg == "on":
            await update_settings(chat_id, {"enabled": True})
            return await message.reply(
                "🛡 **Guard is now ON!**\n\n"
                "All violations will be monitored.\n"
                "Use `/guardsettings` to configure."
            )
        elif arg == "off":
            await update_settings(chat_id, {"enabled": False})
            return await message.reply(
                "🛡 **Guard is now OFF.**\n\n"
                "No violations will be monitored."
            )

    # /guardsettings — show panel
    s = await get_settings(chat_id)
    await message.reply(
        settings_text(s),
        reply_markup=settings_keyboard(s)
    )


# ── Settings callbacks ────────────────────────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^gs_toggle_(\w+)$"))
async def gs_toggle(client, callback):
    chat_id = callback.message.chat.id
    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Admins only!", show_alert=True)

    key = callback.matches[0].group(1)
    s   = await get_settings(chat_id)
    new_val = not s.get(key, True)
    await update_settings(chat_id, {key: new_val})

    s = await get_settings(chat_id)
    await callback.message.edit_text(
        settings_text(s),
        reply_markup=settings_keyboard(s)
    )
    await callback.answer(f"{'Enabled' if new_val else 'Disabled'} ✅")


@Client.on_callback_query(filters.regex(r"^gs_set_(warn1|warn2|words)$"))
async def gs_set_value(client, callback):
    chat_id = callback.message.chat.id
    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Admins only!", show_alert=True)

    key_map = {
        "warn1": ("warn1_mute", "Warn 1 mute duration in **minutes**"),
        "warn2": ("warn2_mute", "Warn 2 mute duration in **minutes**"),
        "words": ("word_limit", "word limit (e.g. 100)"),
    }
    sub     = callback.matches[0].group(1)
    field, label = key_map[sub]

    await update_settings(chat_id, {"_pending_field": field, "_pending_admin": callback.from_user.id})
    await callback.message.reply(f"📝 Send new value for **{label}**:")
    await callback.answer()


@Client.on_callback_query(filters.regex(r"^gs_refresh$"))
async def gs_refresh(client, callback):
    chat_id = callback.message.chat.id
    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Admins only!", show_alert=True)

    s = await get_settings(chat_id)
    await callback.message.edit_text(settings_text(s), reply_markup=settings_keyboard(s))
    await callback.answer("Refreshed!")


@Client.on_message(filters.group & filters.text & filters.incoming)
async def gs_value_listener(client, message):
    if not message.from_user:
        return
    chat_id = message.chat.id
    s       = await get_settings(chat_id)

    if s.get("_pending_admin") != message.from_user.id:
        return
    field = s.get("_pending_field")
    if not field:
        return

    try:
        value = int(message.text.strip())
        assert value > 0
    except:
        return await message.reply("⚠️ Please send a positive number only.")

    await update_settings(chat_id, {
        field: value,
        "_pending_field": None,
        "_pending_admin": None
    })
    await message.reply(f"✅ Updated `{field}` → `{value}`")


# ── Main Guard Handler ────────────────────────────────────────────────────────

@Client.on_message(filters.group & filters.incoming)
async def guard_handler(client: Client, message: Message):
    if not message.from_user:
        return

    chat_id  = message.chat.id
    user_id  = message.from_user.id
    s        = await get_settings(chat_id)

    # Guard OFF — skip
    if not s.get("enabled", False):
        return

    # Skip admins
    if await is_admin(client, chat_id, user_id):
        return

    text = message.text or message.caption or ""

    # ── Check triggers ────────────────────────────────────────────────────────

    reason = None

    # 1. Forwarded message
    if s.get("forward_guard", True) and message.forward_date:
        reason = "📨 Forwarded message not allowed"

    # 2. Link in text or entities
    elif s.get("link_guard", True):
        has_link = bool(URL_REGEX.search(text))
        if not has_link and message.entities:
            has_link = any(
                e.type.name in ("URL", "TEXT_LINK") for e in message.entities
            )
        if has_link:
            reason = "🔗 Links not allowed"

    # 3. Long message
    elif s.get("longmsg_guard", True):
        if len(text.split()) >= s.get("word_limit", 100):
            reason = f"📝 Message too long ({len(text.split())} words)"

    if not reason:
        return

    # ── Delete message ────────────────────────────────────────────────────────
    try:
        await message.delete()
    except:
        pass

    # ── Add warn & take action ────────────────────────────────────────────────
    warns     = await add_warn(chat_id, user_id)
    w1        = s.get("warn1_mute", 30)
    w2        = s.get("warn2_mute", 180)
    name      = message.from_user.mention

    if warns == 1:
        until = await do_mute(client, chat_id, user_id, w1)
        text_out = (
            f"⚠️ **Warning 1/3**\n\n"
            f"👤 {name}\n"
            f"📌 **Reason:** {reason}\n"
            f"🔇 **Muted:** {w1} min\n"
            f"🕐 **Until:** `{until.strftime('%d.%m.%y %H:%M')} UTC`\n\n"
            f"_Next violation → {w2} min mute_"
        )
        buttons = [[InlineKeyboardButton("🔓 Unmute", callback_data=f"cmd_unmute_{user_id}")]]

    elif warns == 2:
        until = await do_mute(client, chat_id, user_id, w2)
        text_out = (
            f"⚠️ **Warning 2/3**\n\n"
            f"👤 {name}\n"
            f"📌 **Reason:** {reason}\n"
            f"🔇 **Muted:** {w2} min\n"
            f"🕐 **Until:** `{until.strftime('%d.%m.%y %H:%M')} UTC`\n\n"
            f"_Next violation → **Permanent Ban**_ ⚠️"
        )
        buttons = [[InlineKeyboardButton("🔓 Unmute", callback_data=f"cmd_unmute_{user_id}")]]

    else:
        # Ban
        try:
            await client.ban_chat_member(chat_id, user_id)
            await log_ban(chat_id, user_id)
        except:
            pass
        await reset_warns(chat_id, user_id)

        text_out = (
            f"🚫 **Banned**\n\n"
            f"👤 {name}\n"
            f"📌 **Reason:** 3rd violation — {reason}\n\n"
            f"_Contact group admin to appeal._"
        )
        buttons = [[InlineKeyboardButton("🔓 Unban", callback_data=f"cmd_unban_{user_id}")]]

        # Send PM to banned user — one clean message only
        try:
            await client.send_message(user_id, BAN_PM_TEXT)
        except:
            pass  # User may have blocked bot

    await message.reply(
        text_out,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ── /warns ────────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("warns") & filters.group)
async def check_warns_cmd(client, message):
    if not await is_admin(client, message.chat.id, message.from_user.id):
        return await message.reply("❌ Admins only!")

    target = None
    if message.reply_to_message:
        target = message.reply_to_message.from_user
    elif len(message.command) > 1:
        try:
            target = await client.get_users(message.command[1].lstrip("@"))
        except:
            return await message.reply("❌ User not found.")
    else:
        return await message.reply("❌ Reply to user or `/warns @username`")

    s     = await get_settings(message.chat.id)
    warns = await get_warns(message.chat.id, target.id)
    level = {0: "🟢 Clean", 1: "🟡 Warned once", 2: "🟠 Warned twice", 3: "🔴 Banned"}.get(warns, "🔴 Banned")

    next_action = (
        f"→ Next: Mute {s.get('warn1_mute', 30)} min" if warns == 0 else
        f"→ Next: Mute {s.get('warn2_mute', 180)} min" if warns == 1 else
        f"→ Next: **Ban**" if warns == 2 else
        f"→ Already banned"
    )

    buttons = []
    if warns > 0:
        buttons = [[InlineKeyboardButton("🔄 Reset Warns", callback_data=f"guard_resetwarns_{target.id}")]]

    await message.reply(
        f"📋 **Warn Record**\n\n"
        f"👤 **User:** {target.mention}\n"
        f"🆔 **ID:** `{target.id}`\n"
        f"⚠️ **Warns:** `{warns}/3`\n"
        f"📊 **Status:** {level}\n"
        f"{next_action}",
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None
    )


# ── /resetwarns ───────────────────────────────────────────────────────────────

@Client.on_message(filters.command("resetwarns") & filters.group)
async def reset_warns_cmd(client, message):
    if not await is_admin(client, message.chat.id, message.from_user.id):
        return await message.reply("❌ Admins only!")

    target = None
    if message.reply_to_message:
        target = message.reply_to_message.from_user
    elif len(message.command) > 1:
        try:
            target = await client.get_users(message.command[1].lstrip("@"))
        except:
            return await message.reply("❌ User not found.")
    else:
        return await message.reply("❌ Reply to user or `/resetwarns @username`")

    w = await get_warns(message.chat.id, target.id)
    if w == 0:
        return await message.reply(f"ℹ️ {target.mention} has no warns.")

    await reset_warns(message.chat.id, target.id)
    await message.reply(
        f"✅ **Warns Cleared**\n\n"
        f"👤 {target.mention} — `{w}` warn(s) removed\n"
        f"**By:** {message.from_user.mention}"
    )


@Client.on_callback_query(filters.regex(r"^guard_resetwarns_(\d+)$"))
async def cb_resetwarns(client, callback):
    chat_id = callback.message.chat.id
    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Admins only!", show_alert=True)

    user_id = int(callback.matches[0].group(1))
    await reset_warns(chat_id, user_id)
    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ **Warns reset by** {callback.from_user.mention}"
    )
    await callback.answer("Warns cleared!")


# ── /bannedusers ──────────────────────────────────────────────────────────────

@Client.on_message(filters.command("bannedusers") & filters.group)
async def banned_users_cmd(client, message):
    if not await is_admin(client, message.chat.id, message.from_user.id):
        return await message.reply("❌ Admins only!")

    banned = await get_all_banned(message.chat.id)
    if not banned:
        return await message.reply("✅ No Guard-banned users in this group.")

    await show_banned_page(client, message, message.chat.id, banned, page=0)


async def show_banned_page(client, msg_or_cb, chat_id, banned_list, page=0):
    PAGE_SIZE   = 5
    total       = len(banned_list)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    start       = page * PAGE_SIZE
    chunk       = banned_list[start:start + PAGE_SIZE]

    lines = []
    for i, entry in enumerate(chunk, start=start + 1):
        uid      = entry["user_id"]
        ban_time = entry.get("banned_at")
        try:
            user = await client.get_users(uid)
            uname = f"{user.first_name}" + (f" (@{user.username})" if user.username else "")
        except:
            uname = "Unknown"

        ban_str = ban_time.strftime("%d.%m.%y %H:%M") if isinstance(ban_time, datetime) else "—"
        lines.append(f"{i}. 👤 [{uname}](tg://user?id={uid})\n   🆔 `{uid}` | 🕐 `{ban_str}`")

    text = (
        f"🚫 **Guard Banned Users**\n"
        f"**Total:** {total} | Page {page+1}/{total_pages}\n\n"
        + "\n\n".join(lines)
    )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"banned_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"banned_page_{page+1}"))

    unban_btns = [
        [InlineKeyboardButton(f"🔓 Unban #{start+i+1}", callback_data=f"cmd_unban_{entry['user_id']}")]
        for i, entry in enumerate(chunk)
    ]

    keyboard = []
    if nav:
        keyboard.append(nav)
    keyboard.extend(unban_btns)
    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="banned_refresh")])

    markup = InlineKeyboardMarkup(keyboard)

    if isinstance(msg_or_cb, Message):
        await msg_or_cb.reply(text, reply_markup=markup, disable_web_page_preview=True)
    else:
        await msg_or_cb.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


@Client.on_callback_query(filters.regex(r"^banned_page_(\d+)$"))
async def banned_page_cb(client, callback):
    chat_id = callback.message.chat.id
    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Admins only!", show_alert=True)

    banned = await get_all_banned(chat_id)
    if not banned:
        return await callback.message.edit_text("✅ No banned users.")

    await show_banned_page(client, callback, chat_id, banned, int(callback.matches[0].group(1)))
    await callback.answer()


@Client.on_callback_query(filters.regex(r"^banned_refresh$"))
async def banned_refresh_cb(client, callback):
    chat_id = callback.message.chat.id
    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Admins only!", show_alert=True)

    banned = await get_all_banned(chat_id)
    await show_banned_page(client, callback, chat_id, banned, 0)
    await callback.answer("Refreshed!")
