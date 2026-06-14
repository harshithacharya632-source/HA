import re
import asyncio
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

# ── All guard commands list ───────────────────────────────────────────────────
GUARD_COMMANDS = [
    "guard", "guardsettings", "ghelp", "guardhelp",
    "mute", "unmute", "ban", "unban",
    "warns", "resetwarns", "bannedusers",
    "start", "help", "settings", "filter",
    "connect", "disconnect", "id", "request",
    "shortlink", "fsub", "nofsub", "logs",
    "restart", "broadcast", "send", "stats"
]

# ── Ban PM message ────────────────────────────────────────────────────────────
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
            can_add_web_page_previews=True,
        )
    )

# ── Settings panel text & keyboard ───────────────────────────────────────────

def settings_keyboard(s: dict, chat_id: int) -> InlineKeyboardMarkup:
    def toggle_btn(label, key):
        state = "✅" if s.get(key, True) else "❌"
        return InlineKeyboardButton(f"{state} {label}", callback_data=f"gs_toggle_{key}_{chat_id}")

    enabled = s.get("enabled", False)
    main_btn = InlineKeyboardButton(
        f"🛡 Guard: {'ON ✅' if enabled else 'OFF ❌'}",
        callback_data=f"gs_toggle_enabled_{chat_id}"
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
            InlineKeyboardButton(f"⏱ Warn1: {s.get('warn1_mute', 30)}m",  callback_data=f"gs_set_warn1_{chat_id}"),
            InlineKeyboardButton(f"⏱ Warn2: {s.get('warn2_mute', 180)}m", callback_data=f"gs_set_warn2_{chat_id}"),
        ],
        [
            InlineKeyboardButton(f"📝 Word Limit: {s.get('word_limit', 100)}", callback_data=f"gs_set_words_{chat_id}"),
        ],
        [
            InlineKeyboardButton("📋 Banned Users", callback_data=f"gs_banned_0_{chat_id}"),
            InlineKeyboardButton("🔄 Refresh",      callback_data=f"gs_refresh_{chat_id}"),
        ]
    ])


def settings_text(s: dict, chat_title: str = "Group") -> str:
    enabled   = "✅ ON"  if s.get("enabled", False)       else "❌ OFF"
    link_g    = "✅" if s.get("link_guard", True)          else "❌"
    forward_g = "✅" if s.get("forward_guard", True)       else "❌"
    longmsg_g = "✅" if s.get("longmsg_guard", True)       else "❌"
    return (
        f"🛡 **Goflix Guard Settings**\n"
        f"📌 **Group:** {chat_title}\n\n"
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
        f"_Use buttons below to toggle or adjust._"
    )

# ═══════════════════════════════════════════════════════════════════════════════
#   ADMIN COMMANDS — all redirect to PM
# ═══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.command(["guard", "guardsettings"]) & filters.group)
async def guard_cmd(client, message):
    if not await is_admin(client, message.chat.id, message.from_user.id):
        return await message.reply("❌ Admins only!")

    chat_id    = message.chat.id
    admin_id   = message.from_user.id
    args       = message.command
    chat_title = message.chat.title or "Group"

    # Quick on/off in group — confirm then redirect
    if len(args) > 1:
        arg = args[1].lower()
        if arg in ("on", "off"):
            enabled = arg == "on"
            await update_settings(chat_id, {"enabled": enabled})
            status  = "ON ✅" if enabled else "OFF ❌"
            # Reply in group briefly
            m = await message.reply(
                f"🛡 **Guard is now {status}**\n"
                f"_For more settings check your PM._",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⚙️ Open Settings", url=f"https://t.me/{(await client.get_me()).username}")
                ]])
            )
            # Send full panel to PM
            s = await get_settings(chat_id)
            try:
                pm_msg = await client.send_message(
                    admin_id,
                    settings_text(s, chat_title),
                    reply_markup=settings_keyboard(s, chat_id)
                )
                async def _del_on_off(gm=m, pm=pm_msg):
                    await asyncio.sleep(180)
                    try: await gm.delete()
                    except: pass
                    try: await pm.delete()
                    except: pass
                asyncio.ensure_future(_del_on_off())
            except:
                await m.edit(
                    f"🛡 **Guard is now {status}**\n\n"
                    f"⚠️ Start me in PM first: @{(await client.get_me()).username}"
                )
            return

    # /guardsettings or /guard alone — redirect to PM
    try:
        await message.delete()
    except:
        pass

    s = await get_settings(chat_id)
    try:
        pm_msg = await client.send_message(
            admin_id,
            settings_text(s, chat_title),
            reply_markup=settings_keyboard(s, chat_id)
        )
        grp_msg = await message.reply(
            f"⚙️ **Guard settings sent to your PM!**\n"
            f"👆 Check your messages from bot.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📲 Open PM", url=f"https://t.me/{(await client.get_me()).username}")
            ]])
        )
        async def _del_settings(pm=pm_msg, gm=grp_msg):
            await asyncio.sleep(180)
            try: await pm.delete()
            except: pass
            try: await gm.delete()
            except: pass
        asyncio.ensure_future(_del_settings())
    except Exception as e:
        await message.reply(
            f"❌ Couldn't send PM.\n"
            f"Please start me in PM first:\n"
            f"👉 @{(await client.get_me()).username}"
        )


@Client.on_message(filters.command(["warns"]) & filters.group)
async def guard_warns_group(client, message):
    if not await is_admin(client, message.chat.id, message.from_user.id):
        return await message.reply("❌ Admins only!")

    chat_id  = message.chat.id
    admin_id = message.from_user.id

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

    s     = await get_settings(chat_id)
    warns = await get_warns(chat_id, target.id)
    level = {
        0: "🟢 Clean",
        1: "🟡 Warned once",
        2: "🟠 Warned twice",
        3: "🔴 Banned"
    }.get(warns, "🔴 Banned")

    next_action = (
        f"→ Next: Mute {s.get('warn1_mute', 30)} min" if warns == 0 else
        f"→ Next: Mute {s.get('warn2_mute', 180)} min" if warns == 1 else
        f"→ Next: **Ban**"                              if warns == 2 else
        f"→ Already banned"
    )

    buttons = []
    if warns > 0:
        buttons = [[
            InlineKeyboardButton("🔄 Reset Warns", callback_data=f"guard_resetwarns_{target.id}_{chat_id}")
        ]]

    text = (
        f"📋 **Warn Record**\n\n"
        f"👤 **User:** {target.mention}\n"
        f"🆔 **ID:** `{target.id}`\n"
        f"⚠️ **Warns:** `{warns}/3`\n"
        f"📊 **Status:** {level}\n"
        f"{next_action}"
    )

    # Send to PM
    try:
        await client.send_message(
            admin_id, text,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None
        )
        await message.reply("📲 Warn details sent to your PM!")
    except:
        await message.reply(text, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)


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
        f"👮 **By:** {message.from_user.mention}"
    )


@Client.on_message(filters.command("bannedusers") & filters.group)
async def banned_users_cmd(client, message):
    if not await is_admin(client, message.chat.id, message.from_user.id):
        return await message.reply("❌ Admins only!")

    chat_id  = message.chat.id
    admin_id = message.from_user.id
    banned   = await get_all_banned(chat_id)

    if not banned:
        return await message.reply("✅ No Guard-banned users in this group.")

    try:
        await _send_banned_page(client, admin_id, chat_id, banned, page=0)
        await message.reply("📲 Banned users list sent to your PM!")
    except:
        await message.reply("❌ Start me in PM first to view banned users.")


@Client.on_message(filters.command(["guardhelp", "ghelp"]) & filters.group)
async def guard_help_cmd(client, message):
    if not await is_admin(client, message.chat.id, message.from_user.id):
        return await message.reply("❌ Admins only!")

    text = (
        "🛡 **Goflix Guard — Commands**\n\n"

        "**⚙️ Guard Control:**\n"
        "`/guard on` — Enable guard\n"
        "`/guard off` — Disable guard\n"
        "`/guardsettings` — Full settings in PM\n\n"

        "**🔇 Mute:**\n"
        "`/mute <min>` — Mute (reply to user)\n"
        "`/mute @user <min>` — Mute by username\n"
        "`/unmute` — Unmute (reply)\n"
        "`/unmute @user` — Unmute by username\n\n"

        "**🚫 Ban:**\n"
        "`/ban <reason>` — Ban (reply to user)\n"
        "`/ban @user <reason>` — Ban by username\n"
        "`/unban` — Unban (reply)\n"
        "`/unban @user` — Unban by username\n\n"

        "**⚠️ Warns:**\n"
        "`/warns @user` — Check warns → sent to PM\n"
        "`/resetwarns @user` — Clear warns\n\n"

        "**📋 Banned List:**\n"
        "`/bannedusers` — View banned list in PM\n\n"

        "**🔁 Violation Flow:**\n"
        "Warn 1 → 🔇 Mute 30 min\n"
        "Warn 2 → 🔇 Mute 3 hr\n"
        "Warn 3 → 🚫 Ban + PM to user\n\n"

        "_All settings & lists open in your PM._"
    )

    try:
        await client.send_message(message.from_user.id, text)
        await message.reply("📲 Guard help sent to your PM!")
    except:
        await message.reply(text)

# ═══════════════════════════════════════════════════════════════════════════════
#   PM SETTINGS CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════

@Client.on_callback_query(filters.regex(r"^gs_toggle_(\w+)_(-\d+)$"))
async def gs_toggle(client, callback):
    key     = callback.matches[0].group(1)
    chat_id = int(callback.matches[0].group(2))

    # Verify the user is admin of that group
    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ You are not admin of that group!", show_alert=True)

    s       = await get_settings(chat_id)
    new_val = not s.get(key, True)
    await update_settings(chat_id, {key: new_val})

    s = await get_settings(chat_id)
    try:
        chat       = await client.get_chat(chat_id)
        chat_title = chat.title
    except:
        chat_title = "Group"

    try:
        await callback.message.edit_text(
            settings_text(s, chat_title),
            reply_markup=settings_keyboard(s, chat_id)
        )
    except:
        pass
    await callback.answer(f"{'Enabled ✅' if new_val else 'Disabled ❌'}")


@Client.on_callback_query(filters.regex(r"^gs_set_(warn1|warn2|words)_(-\d+)$"))
async def gs_set_value(client, callback):
    sub     = callback.matches[0].group(1)
    chat_id = int(callback.matches[0].group(2))

    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Not admin!", show_alert=True)

    key_map = {
        "warn1": ("warn1_mute", "Warn 1 mute duration in **minutes**"),
        "warn2": ("warn2_mute", "Warn 2 mute duration in **minutes**"),
        "words": ("word_limit", "word limit (e.g. 100)"),
    }
    field, label = key_map[sub]

    # Store pending in DB for this admin in PM
    await update_settings(chat_id, {
        "_pending_field": field,
        "_pending_admin": callback.from_user.id
    })
    await callback.message.reply(
        f"📝 Send new value for **{label}**:\n"
        f"_(Reply with a number)_"
    )
    await callback.answer()


@Client.on_callback_query(filters.regex(r"^gs_refresh_(-\d+)$"))
async def gs_refresh(client, callback):
    chat_id = int(callback.matches[0].group(1))

    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Not admin!", show_alert=True)

    s = await get_settings(chat_id)
    try:
        chat       = await client.get_chat(chat_id)
        chat_title = chat.title
    except:
        chat_title = "Group"

    try:
        await callback.message.edit_text(
            settings_text(s, chat_title),
            reply_markup=settings_keyboard(s, chat_id)
        )
    except:
        pass
    await callback.answer("Refreshed! 🔄")


@Client.on_callback_query(filters.regex(r"^gs_banned_(\d+)_(-\d+)$"))
async def gs_banned_page(client, callback):
    page    = int(callback.matches[0].group(1))
    chat_id = int(callback.matches[0].group(2))

    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Not admin!", show_alert=True)

    banned = await get_all_banned(chat_id)
    if not banned:
        return await callback.answer("✅ No banned users!", show_alert=True)

    await _edit_banned_page(client, callback.message, chat_id, banned, page)
    await callback.answer()


# ── PM value listener (for setting warn times / word limit) ───────────────────

@Client.on_message(filters.private & filters.text & filters.incoming, group=2)
async def pm_value_listener(client, message):
    if not message.from_user:
        return
    if message.text and message.text.startswith("/"):
        return
    admin_id = message.from_user.id
    from database.guard_db import get_pending_chats
    pending = await get_pending_chats(admin_id)
    if not pending:
        return

    for doc in pending:
        chat_id = doc["chat_id"]
        field   = doc.get("_pending_field")
        if not field:
            continue
        try:
            value = int(message.text.strip())
            assert value > 0
        except:
            return await message.reply("⚠️ Please send a **positive number** only.")
        await update_settings(chat_id, {
            field: value,
            "_pending_field": None,
            "_pending_admin": None
        })
        s = await get_settings(chat_id)
        try:
            chat       = await client.get_chat(chat_id)
            chat_title = chat.title
        except:
            chat_title = "Group"
        await message.reply(f"✅ Updated `{field}` → `{value}`")
        await message.reply(
            settings_text(s, chat_title),
            reply_markup=settings_keyboard(s, chat_id)
        )


# ── Callback: reset warns from PM ─────────────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^guard_resetwarns_(\d+)_(-\d+)$"))
async def cb_resetwarns(client, callback):
    user_id = int(callback.matches[0].group(1))
    chat_id = int(callback.matches[0].group(2))

    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Admins only!", show_alert=True)

    await reset_warns(chat_id, user_id)
    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ **Warns reset by Admin**"
    )
    await callback.answer("Warns cleared!")


# ── Banned users page helpers ─────────────────────────────────────────────────

async def _send_banned_page(client, send_to, chat_id, banned_list, page=0):
    text, markup = await _build_banned_page(client, chat_id, banned_list, page)
    await client.send_message(send_to, text, reply_markup=markup, disable_web_page_preview=True)


async def _edit_banned_page(client, message, chat_id, banned_list, page=0):
    text, markup = await _build_banned_page(client, chat_id, banned_list, page)
    await message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)


async def _build_banned_page(client, chat_id, banned_list, page=0):
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
            user  = await client.get_users(uid)
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
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"gs_banned_{page-1}_{chat_id}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"gs_banned_{page+1}_{chat_id}"))

    unban_btns = [
        [InlineKeyboardButton(f"🔓 Unban #{start+i+1}", callback_data=f"cmd_unban_{entry['user_id']}_{chat_id}")]
        for i, entry in enumerate(chunk)
    ]

    keyboard = []
    if nav:
        keyboard.append(nav)
    keyboard.extend(unban_btns)
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh", callback_data=f"gs_banned_{page}_{chat_id}"),
        InlineKeyboardButton("⬅️ Settings", callback_data=f"gs_refresh_{chat_id}"),
    ])

    return text, InlineKeyboardMarkup(keyboard)

# ═══════════════════════════════════════════════════════════════════════════════
#   MAIN GUARD HANDLER — works silently in group
# ═══════════════════════════════════════════════════════════════════════════════

@Client.on_message(
    filters.group
    & filters.incoming
    & ~filters.command(GUARD_COMMANDS),
    group=-1
)
async def guard_handler(client: Client, message: Message):
    if not message.from_user:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    s       = await get_settings(chat_id)

    if not s.get("enabled", False):
        return

    if await is_admin(client, chat_id, user_id):
        return

    text = message.text or message.caption or ""

    reason = None

    # 1. Forward
    if s.get("forward_guard", True) and message.forward_date:
        reason = "📨 Forwarded message not allowed"

    # 2. Link
    if not reason and s.get("link_guard", True):
        has_link = bool(URL_REGEX.search(text))
        if not has_link and message.entities:
            has_link = any(e.type.name in ("URL", "TEXT_LINK") for e in message.entities)
        if has_link:
            reason = "🔗 Links not allowed"

    # 3. Long message
    if not reason and s.get("longmsg_guard", True):
        if len(text.split()) >= s.get("word_limit", 100):
            reason = f"📝 Message too long ({len(text.split())} words)"

    if not reason:
        return

    try:
        await message.delete()
    except:
        pass

    warns = await add_warn(chat_id, user_id)
    w1    = s.get("warn1_mute", 30)
    w2    = s.get("warn2_mute", 180)
    name  = message.from_user.mention

    if warns == 1:
        until    = await do_mute(client, chat_id, user_id, w1)
        text_out = (
            f"⚠️ **Warning 1/3**\n\n"
            f"👤 {name}\n"
            f"📌 **Reason:** {reason}\n"
            f"🔇 **Muted:** {w1} min\n"
            f"🕐 **Until:** `{until.strftime('%d.%m.%y %H:%M')} UTC`\n\n"
            f"_Next violation → {w2} min mute_"
        )
        buttons = [[InlineKeyboardButton("🔓 Unmute", callback_data=f"cmd_unmute_{user_id}_{chat_id}")]]

    elif warns == 2:
        until    = await do_mute(client, chat_id, user_id, w2)
        text_out = (
            f"⚠️ **Warning 2/3**\n\n"
            f"👤 {name}\n"
            f"📌 **Reason:** {reason}\n"
            f"🔇 **Muted:** {w2} min\n"
            f"🕐 **Until:** `{until.strftime('%d.%m.%y %H:%M')} UTC`\n\n"
            f"_Next violation → **Permanent Ban**_ ⚠️"
        )
        buttons = [[InlineKeyboardButton("🔓 Unmute", callback_data=f"cmd_unmute_{user_id}_{chat_id}")]]

    else:
        try:
            await client.restrict_chat_member(
                chat_id, user_id,
                ChatPermissions()
            )
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
        buttons = [[InlineKeyboardButton("🔓 Unban", callback_data=f"cmd_unban_{user_id}_{chat_id}")]]

        try:
            await client.send_message(user_id, BAN_PM_TEXT)
        except:
            pass

    await message.reply(
        text_out,
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    message.stop_propagation()
