import re
import asyncio
from datetime import datetime, timedelta
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
)
from pyrogram.enums import ChatMemberStatus, ChatMembersFilter
from database.guard_db import (
    get_settings, update_settings,
    get_warns, add_warn, reset_warns,
    log_ban, remove_ban_log, get_all_banned
)

URL_REGEX = re.compile(r"(https?://|www\.|t\.me/|@\w+)", re.IGNORECASE)

# Matches "@admin" / "@admins" / "#admin" / "#admins" (word-bounded, case-insensitive).
# NOTE: this is intentionally separate from URL_REGEX. URL_REGEX's @\w+ pattern
# was also matching "@admin" and causing it to be deleted/warned as a "link"
# violation — that's likely why admin-call messages were being removed before
# anyone saw them. ADMIN_CALL_REGEX is checked first and is exempted from the
# link guard so users can call for an admin without being punished for it.
ADMIN_CALL_REGEX = re.compile(r"[@#]admins?\b", re.IGNORECASE)


def _banned_word_hit(text: str, banned_words: list) -> bool:
    """True if any of the group's admin-configured banned words appears
    as a whole word in the message (case-insensitive). Whole-word only
    (via \\b) so a banned word like "spam" doesn't also flag "spammer"
    or "spamalot" — same word-boundary discipline as the other guard
    checks. Rebuilt fresh on every call rather than cached: group
    banned-word lists are short (tens of words at most for a moderation
    list like this), so this costs nothing worth optimizing for, and it
    means a word added a second ago is already enforced with zero
    invalidation logic to get wrong."""
    if not banned_words or not text:
        return False
    pattern = r"\b(" + "|".join(re.escape(w) for w in banned_words) + r")\b"
    return bool(re.search(pattern, text, re.IGNORECASE))

# ── All guard commands list ───────────────────────────────────────────────────
GUARD_COMMANDS = [
    "guard", "guardsettings", "ghelp", "guardhelp",
    "mute", "unmute", "ban", "unban",
    "warns", "resetwarns", "bannedusers",
    "addword", "removeword", "bannedwords",
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
    # Anonymous admin sends as group itself — Pyrogram id 1087968824
    if user_id in (1087968824, 136817688):
        return True
    try:
        m = await client.get_chat_member(chat_id, user_id)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except:
        return False


async def get_group_admins(client, chat_id):
    """Returns a list of admin/owner User objects for the group (bots excluded)."""
    admins = []
    try:
        async for m in client.get_chat_members(
            chat_id, filter=ChatMembersFilter.ADMINISTRATORS
        ):
            if m.user and not m.user.is_bot:
                admins.append(m.user)
    except Exception as e:
        print(f"[guard] get_group_admins failed for chat {chat_id}: {e}")
    return admins


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
    word_count = len(s.get("banned_words", []))

    return InlineKeyboardMarkup([
        [main_btn],
        [
            toggle_btn("🔗 Links", "link_guard"),
            toggle_btn("📨 Forwards", "forward_guard"),
        ],
        [
            toggle_btn("📝 Long Msg", "longmsg_guard"),
            toggle_btn("🚫 Bad Words", "word_guard"),
        ],
        [
            toggle_btn("🔘 Join Buttons", "button_guard"),
        ],
        [
            InlineKeyboardButton(f"⏱ Warn1: {s.get('warn1_mute', 30)}m",  callback_data=f"gs_set_warn1_{chat_id}"),
            InlineKeyboardButton(f"⏱ Warn2: {s.get('warn2_mute', 180)}m", callback_data=f"gs_set_warn2_{chat_id}"),
        ],
        [
            InlineKeyboardButton(f"📝 Word Limit: {s.get('word_limit', 100)}", callback_data=f"gs_set_words_{chat_id}"),
        ],
        [
            InlineKeyboardButton(f"✏️ Manage Bad Words ({word_count})", callback_data=f"gs_words_0_{chat_id}"),
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
    word_g    = "✅" if s.get("word_guard", True)          else "❌"
    button_g  = "✅" if s.get("button_guard", True)        else "❌"
    word_count = len(s.get("banned_words", []))
    return (
        f"🛡 **Goflix Guard Settings**\n"
        f"📌 **Group:** {chat_title}\n\n"
        f"**Status:** {enabled}\n\n"
        f"**Triggers:**\n"
        f"  {link_g} Block Links\n"
        f"  {forward_g} Block Forwards\n"
        f"  {longmsg_g} Block Long Messages\n"
        f"  {word_g} Block Bad Words (`{word_count}` word(s) in list)\n"
        f"  {button_g} Block Join/Promo Buttons\n\n"
        f"**Mute Durations:**\n"
        f"  ⚠️ Warn 1 → Mute `{s.get('warn1_mute', 30)}` min\n"
        f"  ⚠️ Warn 2 → Mute `{s.get('warn2_mute', 180)}` min\n"
        f"  🚫 Warn 3 → **Ban**\n\n"
        f"**Long Msg Word Limit:** `{s.get('word_limit', 100)}` words\n\n"
        f"_Use buttons below to toggle or adjust. Manage the bad-words list with_\n"
        f"_`/addword`, `/removeword` and `/bannedwords` in the group._"
    )

# ═══════════════════════════════════════════════════════════════════════════════
#   ADMIN COMMANDS — all redirect to PM
# ═══════════════════════════════════════════════════════════════════════════════

@Client.on_message(filters.command(["guard", "guardsettings"]) & filters.group)
async def guard_cmd(client, message):
    sender_id = message.from_user.id if message.from_user else 1087968824
    if not await is_admin(client, message.chat.id, sender_id):
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
    sender_id = message.from_user.id if message.from_user else 1087968824
    if not await is_admin(client, message.chat.id, sender_id):
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
    sender_id = message.from_user.id if message.from_user else 1087968824
    if not await is_admin(client, message.chat.id, sender_id):
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
    sender_id = message.from_user.id if message.from_user else 1087968824
    if not await is_admin(client, message.chat.id, sender_id):
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


# ── Admin-configurable banned WORD list (distinct from banned USERS
# above) — lets group admins maintain their own custom list of blocked
# words/phrases without needing to touch code or bot config. Stored as
# a plain list under the existing per-chat settings document (same
# get_settings/update_settings used for every other guard setting), so
# no new database collection is needed.

MAX_BANNED_WORD_LENGTH = 32  # keeps callback_data comfortably under
                             # Telegram's 64-byte limit for the
                             # per-word "Remove" buttons below


@Client.on_message(filters.command("addword") & filters.group)
async def add_word_cmd(client, message):
    sender_id = message.from_user.id if message.from_user else 1087968824
    if not await is_admin(client, message.chat.id, sender_id):
        return await message.reply("❌ Admins only!")

    # Parse from the raw text (not message.command) so a multi-word phrase
    # like "fucking admin" stays as ONE entry instead of being split into
    # "fucking" and "admin" on every space. Multiple entries are separated
    # by commas instead.
    raw  = message.text or message.caption or ""
    rest = re.sub(r"^/addword(?:@\w+)?\s*", "", raw, flags=re.IGNORECASE).strip()

    if not rest:
        return await message.reply(
            "❌ **Usage:** `/addword word1, word2, a whole phrase`\n"
            "Separate multiple words/phrases with **commas** — a phrase with "
            "spaces stays together as one entry.\n"
            "Example: `/addword spamword, scamlink, fucking admin`\n\n"
            "💡 You can also add words **privately** without posting them in "
            "the group — DM me, open `/guard` → **✏️ Manage Bad Words** → **➕ Add Word**."
        )

    new_words = [w.strip().lower() for w in rest.split(",") if w.strip()]
    if not new_words:
        return await message.reply("❌ No valid word(s) found.")

    too_long = [w for w in new_words if len(w) > MAX_BANNED_WORD_LENGTH]
    if too_long:
        return await message.reply(
            f"❌ Keep each word/phrase under {MAX_BANNED_WORD_LENGTH} characters: `{', '.join(too_long)}`"
        )

    chat_id   = message.chat.id
    s         = await get_settings(chat_id)
    current   = set(s.get("banned_words", []))
    added     = sorted(set(new_words) - current)
    current.update(new_words)
    await update_settings(chat_id, {"banned_words": sorted(current)})

    if added:
        await message.reply(f"✅ Added {len(added)} word(s)/phrase(s) to the banned list:\n`{', '.join(added)}`")
    else:
        await message.reply("ℹ️ Those word(s) were already in the banned list.")


@Client.on_message(filters.command("removeword") & filters.group)
async def remove_word_cmd(client, message):
    sender_id = message.from_user.id if message.from_user else 1087968824
    if not await is_admin(client, message.chat.id, sender_id):
        return await message.reply("❌ Admins only!")

    if len(message.command) < 2:
        return await message.reply(
            "❌ **Usage:** `/removeword <word1> [word2] ...`\n"
            "See the current list with `/bannedwords`."
        )

    chat_id       = message.chat.id
    to_remove     = {w.lower() for w in message.command[1:]}
    s             = await get_settings(chat_id)
    current       = s.get("banned_words", [])
    remaining     = [w for w in current if w not in to_remove]
    removed_count = len(current) - len(remaining)
    await update_settings(chat_id, {"banned_words": remaining})

    if removed_count:
        await message.reply(f"✅ Removed {removed_count} word(s) from the banned list.")
    else:
        await message.reply("ℹ️ None of those word(s) were in the banned list.")


@Client.on_message(filters.command(["bannedwords", "badwords"]) & filters.group)
async def banned_words_cmd(client, message):
    sender_id = message.from_user.id if message.from_user else 1087968824
    if not await is_admin(client, message.chat.id, sender_id):
        return await message.reply("❌ Admins only!")

    chat_id = message.chat.id
    s       = await get_settings(chat_id)
    words   = sorted(s.get("banned_words", []))

    if not words:
        return await message.reply("✅ No banned words configured yet. Add some with `/addword <word>`.")

    if not message.from_user:
        # Anonymous admin — there's no PM to send this to, so just show
        # it inline instead of silently failing.
        return await message.reply(
            f"🚫 **Banned Words** ({len(words)}):\n`{', '.join(words)}`\n\n"
            f"_Remove with `/removeword <word>`._"
        )

    try:
        await _send_words_page(client, message.from_user.id, chat_id, words, page=0)
        await message.reply("📲 Banned words list sent to your PM!")
    except:
        await message.reply(
            "❌ Start me in PM first to view the list, or use `/removeword <word>` directly here."
        )


@Client.on_message(filters.command(["guardhelp", "ghelp"]) & filters.group)
async def guard_help_cmd(client, message):
    sender_id = message.from_user.id if message.from_user else 1087968824
    if not await is_admin(client, message.chat.id, sender_id):
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
#   MANUAL MUTE / UNMUTE / BAN / UNBAN COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

async def _resolve_target(client, message):
    """Returns (target_user, minutes, reason) from reply or @username arg."""
    args = message.command[1:]  # strip the command itself
    target = None
    minutes = None
    reason_parts = []

    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        # remaining args = [minutes_or_reason ...]
        for a in args:
            if a.isdigit() and minutes is None:
                minutes = int(a)
            else:
                reason_parts.append(a)
    elif args:
        first = args[0].lstrip("@")
        try:
            target = await client.get_users(first)
            args = args[1:]
        except:
            return None, None, "User not found"
        for a in args:
            if a.isdigit() and minutes is None:
                minutes = int(a)
            else:
                reason_parts.append(a)

    reason = " ".join(reason_parts) if reason_parts else None
    return target, minutes, reason


@Client.on_message(filters.command(["mute"]) & filters.group)
async def mute_cmd(client, message):
    # Allow real admins AND anonymous admins (from_user may be None for anon)
    sender_id = message.from_user.id if message.from_user else 1087968824
    if not await is_admin(client, message.chat.id, sender_id):
        return await message.reply("❌ Admins only!")

    chat_id = message.chat.id
    s = await get_settings(chat_id)
    target, minutes, reason = await _resolve_target(client, message)

    if target is None:
        return await message.reply(
            "❌ Reply to a user or use /mute @username minutes\n"
            "Example: /mute @user 30"
        )

    if await is_admin(client, chat_id, target.id):
        return await message.reply("❌ Cannot mute an admin!")

    mute_min = minutes if minutes and minutes > 0 else s.get("warn1_mute", 30)
    reason_text = reason or "Manual mute by admin"

    until = await do_mute(client, chat_id, target.id, mute_min)

    mute_msg = await message.reply(
        f"🔇 **Muted**\n\n"
        f"👤 {target.mention}\n"
        f"⏱ **Duration:** {mute_min} min\n"
        f"🕐 **Until:** `{until.strftime('%d.%m.%y %H:%M')} UTC`\n"
        f"📌 **Reason:** {reason_text}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔓 Unmute", callback_data=f"cmd_unmute_{target.id}_{chat_id}")
        ]])
    )

    # Auto-delete the mute notice AND the offending message after mute expires
    offending = message.reply_to_message

    async def _auto_delete_mute(mm=mute_msg, om=offending, delay=mute_min * 60):
        await asyncio.sleep(delay)
        try: await mm.delete()
        except: pass
        if om:
            try: await om.delete()
            except: pass
    asyncio.ensure_future(_auto_delete_mute())

    try: await message.delete()
    except: pass


@Client.on_message(filters.command(["unmute"]) & filters.group)
async def unmute_cmd(client, message):
    sender_id = message.from_user.id if message.from_user else 1087968824
    if not await is_admin(client, message.chat.id, sender_id):
        return await message.reply("❌ Admins only!")

    chat_id = message.chat.id
    target, _, _ = await _resolve_target(client, message)

    if target is None:
        return await message.reply("❌ Reply to a user or use `/unmute @username`")

    try:
        await do_unmute(client, chat_id, target.id)
        await reset_warns(chat_id, target.id)
        msg = await message.reply(f"✅ **Unmuted**\n\n👤 {target.mention}")
        await asyncio.sleep(30)
        try: await msg.delete()
        except: pass
    except Exception as e:
        await message.reply(f"❌ Failed: {e}")

    try: await message.delete()
    except: pass


@Client.on_message(filters.command(["ban"]) & filters.group)
async def ban_cmd(client, message):
    sender_id = message.from_user.id if message.from_user else 1087968824
    if not await is_admin(client, message.chat.id, sender_id):
        return await message.reply("❌ Admins only!")

    chat_id = message.chat.id
    target, _, reason = await _resolve_target(client, message)

    if target is None:
        return await message.reply("❌ Reply to a user or use `/ban @username <reason>`")

    if await is_admin(client, chat_id, target.id):
        return await message.reply("❌ Cannot ban an admin!")

    reason_text = reason or "Manual ban by admin"

    try:
        await client.restrict_chat_member(chat_id, target.id, ChatPermissions())
        await log_ban(chat_id, target.id)
        await reset_warns(chat_id, target.id)
    except Exception as e:
        return await message.reply(f"❌ Failed to ban: {e}")

    ban_msg = await message.reply(
        f"🚫 **Banned**\n\n"
        f"👤 {target.mention}\n"
        f"📌 **Reason:** {reason_text}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔓 Unban", callback_data=f"cmd_unban_{target.id}_{chat_id}")
        ]])
    )

    # Delete banned user's message if replied to
    if message.reply_to_message:
        try: await message.reply_to_message.delete()
        except: pass

    try: await message.delete()
    except: pass

    try:
        await client.send_message(target.id, BAN_PM_TEXT)
    except:
        pass


@Client.on_message(filters.command(["unban"]) & filters.group)
async def unban_cmd(client, message):
    sender_id = message.from_user.id if message.from_user else 1087968824
    if not await is_admin(client, message.chat.id, sender_id):
        return await message.reply("❌ Admins only!")

    chat_id = message.chat.id
    target, _, _ = await _resolve_target(client, message)

    if target is None:
        return await message.reply("❌ Reply to a user or use `/unban @username`")

    try:
        await client.unban_chat_member(chat_id, target.id)
        await remove_ban_log(chat_id, target.id)
        await reset_warns(chat_id, target.id)
        msg = await message.reply(f"✅ **Unbanned**\n\n👤 {target.mention}")
        await asyncio.sleep(30)
        try: await msg.delete()
        except: pass
    except Exception as e:
        await message.reply(f"❌ Failed: {e}")

    try: await message.delete()
    except: pass


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


@Client.on_callback_query(filters.regex(r"^gs_words_(\d+)_(-\d+)$"))
async def gs_words_page(client, callback):
    page    = int(callback.matches[0].group(1))
    chat_id = int(callback.matches[0].group(2))

    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Not admin!", show_alert=True)

    s     = await get_settings(chat_id)
    words = sorted(s.get("banned_words", []))
    if not words:
        return await callback.answer("✅ No banned words configured yet!", show_alert=True)

    await _edit_words_page(client, callback.message, chat_id, words, page)
    await callback.answer()


@Client.on_callback_query(filters.regex(r"^gs_addword_(-\d+)$"))
async def gs_addword_prompt(client, callback):
    chat_id = int(callback.matches[0].group(1))

    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Not admin!", show_alert=True)

    # Reuses the same _pending_field mechanism as warn1/warn2/word_limit —
    # pm_value_listener below checks the field name to decide how to parse
    # the next PM message from this admin.
    await update_settings(chat_id, {
        "_pending_field": "banned_words_add",
        "_pending_admin": callback.from_user.id
    })
    await callback.message.reply(
        "📝 Send the word(s)/phrase(s) to ban.\n"
        "Separate multiple with **commas** — e.g. `spamword, scamlink, fucking admin`.\n"
        "_This stays private — nothing is posted in the group._"
    )
    await callback.answer()


@Client.on_callback_query(filters.regex(r"^gs_rmword_(.+)_(-\d+)$"))
async def gs_remove_word(client, callback):
    word    = callback.matches[0].group(1)
    chat_id = int(callback.matches[0].group(2))

    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Not admin!", show_alert=True)

    s         = await get_settings(chat_id)
    current   = s.get("banned_words", [])
    remaining = [w for w in current if w != word]
    await update_settings(chat_id, {"banned_words": remaining})

    await _edit_words_page(client, callback.message, chat_id, sorted(remaining), page=0)
    await callback.answer(f"✅ Removed '{word}'")


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

        # ── Adding banned word(s)/phrase(s) privately from PM ────────────
        if field == "banned_words_add":
            new_words = [w.strip().lower() for w in message.text.split(",") if w.strip()]
            if not new_words:
                return await message.reply("⚠️ Please send at least one word or phrase.")
            too_long = [w for w in new_words if len(w) > MAX_BANNED_WORD_LENGTH]
            if too_long:
                return await message.reply(
                    f"❌ Keep each word/phrase under {MAX_BANNED_WORD_LENGTH} characters: "
                    f"`{', '.join(too_long)}`"
                )
            s       = await get_settings(chat_id)
            current = set(s.get("banned_words", []))
            added   = sorted(set(new_words) - current)
            current.update(new_words)
            await update_settings(chat_id, {
                "banned_words": sorted(current),
                "_pending_field": None,
                "_pending_admin": None
            })
            if added:
                await message.reply(
                    f"✅ Added {len(added)} word(s)/phrase(s) privately:\n`{', '.join(added)}`"
                )
            else:
                await message.reply("ℹ️ Those were already in the banned list.")
            await _send_words_page(client, message.from_user.id, chat_id, sorted(current), page=0)
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
        confirm_msg = await message.reply(f"✅ Updated `{field}` → `{value}`")
        settings_msg = await message.reply(
            settings_text(s, chat_title),
            reply_markup=settings_keyboard(s, chat_id)
        )
        async def _del(c=confirm_msg, sm=settings_msg):
            await asyncio.sleep(180)
            try: await c.delete()
            except: pass
            try: await sm.delete()
            except: pass
        asyncio.ensure_future(_del())


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


# ── Callbacks: unmute / unban from the warning/ban notice buttons ────────────
# These buttons are attached to the warn/ban notices in _check_and_act.
# Clicking Unmute/Unban performs the action, swaps the button for a disabled
# "✅ Unmuted/Unbanned by Admin" label (no admin name, not clickable — just
# the word "Admin"), and the original notice is auto-deleted from the group
# 2 minutes later.

@Client.on_callback_query(filters.regex(r"^noop$"))
async def cb_noop(client, callback):
    await callback.answer()


@Client.on_callback_query(filters.regex(r"^cmd_unmute_(\d+)_(-\d+)$"))
async def cb_cmd_unmute(client, callback):
    user_id = int(callback.matches[0].group(1))
    chat_id = int(callback.matches[0].group(2))

    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Admins only!", show_alert=True)

    try:
        await do_unmute(client, chat_id, user_id)
    except Exception as e:
        return await callback.answer(f"❌ Failed to unmute: {e}", show_alert=True)

    await reset_warns(chat_id, user_id)

    # Keep the original warning message as-is (don't replace its text) —
    # just disable the Unmute button so it can't be clicked again, and
    # let the user know via a toast popup instead of editing the message.
    try:
        await callback.message.edit_reply_markup(
            InlineKeyboardMarkup([[InlineKeyboardButton("✅ Unmuted by Admin", callback_data="noop")]])
        )
    except:
        pass
    await callback.answer("User unmuted! 🔓 This message will be removed in 2 minutes.", show_alert=True)

    # Delete the original warning message itself 2 minutes after unmute,
    # rather than replacing it with a new confirmation message first.
    target_msg = callback.message

    async def _del_unmute_confirm(m=target_msg):
        await asyncio.sleep(120)  # 2 minutes
        try:
            await m.delete()
        except Exception as e:
            print(f"[guard] failed to auto-delete unmute confirmation: {e}")
    asyncio.ensure_future(_del_unmute_confirm())


@Client.on_callback_query(filters.regex(r"^cmd_unban_(\d+)_(-\d+)$"))
async def cb_cmd_unban(client, callback):
    user_id = int(callback.matches[0].group(1))
    chat_id = int(callback.matches[0].group(2))

    if not await is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Admins only!", show_alert=True)

    try:
        await client.unban_chat_member(chat_id, user_id)
    except Exception as e:
        return await callback.answer(f"❌ Failed to unban: {e}", show_alert=True)

    await remove_ban_log(chat_id, user_id)
    await reset_warns(chat_id, user_id)

    # Keep the original ban notice as-is — just disable the Unban button
    # so it can't be clicked again, and confirm via toast popup.
    try:
        await callback.message.edit_reply_markup(
            InlineKeyboardMarkup([[InlineKeyboardButton("✅ Unbanned by Admin", callback_data="noop")]])
        )
    except:
        pass
    await callback.answer("User unbanned! 🔓 This message will be removed in 2 minutes.", show_alert=True)

    # Delete the original ban notice itself 2 minutes after unban.
    target_msg = callback.message

    async def _del_unban_confirm(m=target_msg):
        await asyncio.sleep(120)  # 2 minutes
        try:
            await m.delete()
        except Exception as e:
            print(f"[guard] failed to auto-delete unban confirmation: {e}")
    asyncio.ensure_future(_del_unban_confirm())


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


# ── Banned WORDS page (parallel to the banned USERS page above, but no
# async user lookups needed — it's just plain strings from settings) ──

async def _send_words_page(client, send_to, chat_id, words, page=0):
    text, markup = _build_words_page(chat_id, words, page)
    await client.send_message(send_to, text, reply_markup=markup)


async def _edit_words_page(client, message, chat_id, words, page=0):
    text, markup = _build_words_page(chat_id, words, page)
    await message.edit_text(text, reply_markup=markup)


def _build_words_page(chat_id, words, page=0):
    PAGE_SIZE   = 10
    total       = len(words)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    start       = page * PAGE_SIZE
    chunk       = words[start:start + PAGE_SIZE]

    lines = [f"{i}. `{w}`" for i, w in enumerate(chunk, start=start + 1)]
    text = (
        f"🚫 **Guard Banned Words**\n"
        f"**Total:** {total} | Page {page+1}/{total_pages}\n\n"
        + ("\n".join(lines) if lines else "_(none)_")
        + "\n\n_Tap **➕ Add Word** below to add one privately — nothing is "
        + "posted in the group._"
    )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"gs_words_{page-1}_{chat_id}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"gs_words_{page+1}_{chat_id}"))

    # One remove button per word on this page — the word itself sits in
    # callback_data (kept short by MAX_BANNED_WORD_LENGTH at /addword
    # time, so this always stays comfortably under Telegram's 64-byte
    # callback_data limit).
    remove_btns = [
        [InlineKeyboardButton(f"❌ Remove '{w}'", callback_data=f"gs_rmword_{w}_{chat_id}")]
        for w in chunk
    ]

    keyboard = []
    if nav:
        keyboard.append(nav)
    keyboard.extend(remove_btns)
    keyboard.append([InlineKeyboardButton("➕ Add Word", callback_data=f"gs_addword_{chat_id}")])
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh", callback_data=f"gs_words_{page}_{chat_id}"),
        InlineKeyboardButton("⬅️ Settings", callback_data=f"gs_refresh_{chat_id}"),
    ])

    return text, InlineKeyboardMarkup(keyboard)

# ═══════════════════════════════════════════════════════════════════════════════
#   ADMIN CALL — user types @admin / #admin to flag something for admins
# ═══════════════════════════════════════════════════════════════════════════════

@Client.on_message(
    filters.group
    & filters.incoming
    & filters.text
    & filters.regex(ADMIN_CALL_REGEX),
    group=-2  # runs before guard_handler (group=-1) so it isn't deleted as a "link"
)
async def admin_call_handler(client: Client, message: Message):
    if not message.from_user:
        message.stop_propagation()

    chat_id = message.chat.id
    s       = await get_settings(chat_id)

    # @admin / #admin should NEVER trigger a movie search, regardless of
    # whether guard is on/off — so we stop propagation unconditionally
    # before any early-return below. Only the alert-sending logic past
    # this point is conditional on guard being enabled / sender not admin.
    if not s.get("enabled", False):
        message.stop_propagation()

    # Don't trigger when an admin themselves writes @admin (e.g. replying to someone)
    if await is_admin(client, chat_id, message.from_user.id):
        message.stop_propagation()

    # ── Check if this @admin call also violates guard rules ──────────────────
    # If the message contains a link, is forwarded, or exceeds the word limit,
    # treat it as a guard violation AND notify admins — same warn/mute/ban flow.
    guard_violation_reason = None
    if s.get("enabled", False):
        text_body = message.text or ""

        # Check forwarded
        if s.get("forward_guard", True) and message.forward_date:
            guard_violation_reason = "📨 Forwarded message not allowed"

        # Check link (strip @admin/#admin tokens before checking)
        if not guard_violation_reason and s.get("link_guard", True):
            text_for_link_check = ADMIN_CALL_REGEX.sub("", text_body)
            has_link = bool(URL_REGEX.search(text_for_link_check))
            if not has_link and message.entities:
                has_link = any(e.type.name in ("URL", "TEXT_LINK") for e in message.entities)
            if has_link:
                guard_violation_reason = "🔗 Links not allowed"

        # Check inline "Join"/promo button
        if not guard_violation_reason and s.get("button_guard", True):
            if message.reply_markup and getattr(message.reply_markup, "inline_keyboard", None):
                guard_violation_reason = "🔘 Join/promo buttons not allowed"

        # Check long message
        if not guard_violation_reason and s.get("longmsg_guard", True):
            word_count = len(text_body.split())
            if word_count >= s.get("word_limit", 100):
                guard_violation_reason = f"📝 Message too long ({word_count} words)"

        # Check banned word
        if not guard_violation_reason and s.get("word_guard", True):
            if _banned_word_hit(text_body, s.get("banned_words", [])):
                guard_violation_reason = "🚫 Banned word detected"

    if guard_violation_reason:
        # The @admin call also broke a guard rule — apply warn/mute/ban.
        # _check_and_act_with_reason handles delete + warn and stops propagation.
        await _check_and_act_with_reason(client, message, guard_violation_reason)
        # Note: _check_and_act_with_reason calls message.stop_propagation() at the end.
        return

    # ── Normal @admin call (no guard violation) — notify admins ─────────────
    admins   = await get_group_admins(client, chat_id)
    reporter = message.from_user.mention
    preview  = (message.text or "")[:300]

    try:
        chat_title = message.chat.title or "Group"
    except:
        chat_title = "Group"

    # Build a clickable "jump to message" link.
    # Public groups: https://t.me/<username>/<msg_id>
    # Private supergroups: https://t.me/c/<internal_id>/<msg_id>
    #   (internal_id = chat_id with the leading -100 stripped)
    msg_link = None
    if message.chat.username:
        msg_link = f"https://t.me/{message.chat.username}/{message.id}"
    elif str(chat_id).startswith("-100"):
        internal_id = str(chat_id)[4:]
        msg_link = f"https://t.me/c/{internal_id}/{message.id}"

    # 1. Reply in group, tagging admins as PLAIN TEXT (not clickable).
    # Previously used tg://user?id= markdown links, which render clickable
    # when the named user is resolvable. Plain names are used here instead
    # so the tag is never a clickable link.
    if admins:
        tags = ", ".join(a.first_name for a in admins[:8])
    else:
        tags = "Admins"
    await message.reply(
        f"🔔 {tags}\n👤 {reporter} needs admin attention here.",
        disable_web_page_preview=True
    )

    # 2. PM each admin with the message details + a jump-to-message link
    pm_text = (
        f"🔔 **Admin Call**\n\n"
        f"📌 **Group:** {chat_title}\n"
        f"👤 **From:** {reporter}\n"
        f"💬 **Message:** {preview}\n"
    )
    buttons = None
    if msg_link:
        pm_text += f"\n🔗 [Jump to message]({msg_link})"
        buttons = InlineKeyboardMarkup([[InlineKeyboardButton("👉 Open in Group", url=msg_link)]])
    else:
        pm_text += "\n👉 Check the group to see the full context."

    for a in admins:
        try:
            await client.send_message(a.id, pm_text, reply_markup=buttons, disable_web_page_preview=True)
        except:
            pass

    # Stop here so this message never reaches the movie-search plugin
    # (which runs at a later group). @admin / #admin should only ever
    # trigger this admin-call alert — never a movie search.
    message.stop_propagation()


# ═══════════════════════════════════════════════════════════════════════════════
#   REPLY GUARD — any reply message should never trigger movie search.
#   Guard checks (link/forward/long-msg) still run normally for replies via
#   _check_and_act below; this only blocks the movie-search plugin, which is
#   why this runs at group=-2 (same priority as admin_call_handler), runs the
#   guard checks itself, then stops propagation so the message never reaches
#   the movie-search plugin (which lives at a later group in another file).
# ═══════════════════════════════════════════════════════════════════════════════

@Client.on_message(
    filters.group
    & filters.incoming
    & filters.reply
    & ~filters.regex(ADMIN_CALL_REGEX)  # admin_call_handler already handles & stops these
    & ~filters.command(GUARD_COMMANDS), # let /mute, /ban, /unmute, /unban etc. (sent as a
                                         # reply to the target's message/photo) fall through
                                         # to their own handlers below instead of being
                                         # swallowed here — this was the exact cause of
                                         # "/mute or /ban does nothing when replying to a photo"
    group=-2
)
async def reply_no_search_handler(client: Client, message: Message):
    if not message.from_user:
        message.stop_propagation()

    chat_id = message.chat.id
    s       = await get_settings(chat_id)

    # A reply should never be treated as a movie-search query, regardless
    # of whether guard is enabled — so stop_propagation always happens by
    # the end of this handler. Guard checks (link/forward/long-msg) only
    # run when guard is enabled, matching normal guard behavior elsewhere.
    if s.get("enabled", False):
        await _check_and_act(client, message)

    message.stop_propagation()


# ═══════════════════════════════════════════════════════════════════════════════
#   MAIN GUARD HANDLER — works silently in group
# ═══════════════════════════════════════════════════════════════════════════════

async def _apply_warn_action(client: Client, message: Message, reason: str):
    """
    Core warn/mute/ban logic shared by _check_and_act and _check_and_act_with_reason.
    Assumes the caller has already deleted the offending message and verified the user
    is not an admin. Returns after posting the warning/ban notice and scheduling any
    auto-delete.
    """
    chat_id = message.chat.id
    user_id = message.from_user.id
    s       = await get_settings(chat_id)

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

    warn_msg = await message.reply(
        text_out,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

    # Auto-delete the warning message AND original offending message once mute ends.
    # (Ban notices are left in the group since the ban is permanent.)
    if warns in (1, 2):
        delay = (w1 if warns == 1 else w2) * 60
        orig_msg = message  # the offending message (already deleted above, but keep ref)

        async def _del_warning(wm=warn_msg, delay=delay):
            await asyncio.sleep(delay)
            try:
                await wm.delete()
            except:
                pass
        asyncio.ensure_future(_del_warning())


async def _check_and_act_with_reason(client: Client, message: Message, reason: str):
    """
    Apply guard action when the calling code has already determined the violation
    reason (e.g. admin_call_handler detects a link inside an @admin message).
    Deletes the offending message, runs the warn/mute/ban flow, then stops propagation.
    """
    if not message.from_user:
        message.stop_propagation()
        return

    try:
        await message.delete()
    except:
        pass

    await _apply_warn_action(client, message, reason)
    message.stop_propagation()


async def _check_and_act(client: Client, message: Message):
    """Runs the guard checks (link/forward/long-msg) against a message and
    takes action (delete + warn/mute/ban) if it violates a rule.
    Shared by both the new-message handler and the edited-message handler,
    so that a link added via editing an otherwise-clean message is still caught."""
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
        # Strip @admin/#admin call-outs first so they're never mistaken for a link
        text_for_link_check = ADMIN_CALL_REGEX.sub("", text)
        has_link = bool(URL_REGEX.search(text_for_link_check))
        if not has_link and message.entities:
            has_link = any(e.type.name in ("URL", "TEXT_LINK") for e in message.entities)
        if has_link:
            reason = "🔗 Links not allowed"

    # 3. Inline "Join"/promo button — a common way to advertise another
    # channel without any URL appearing in the visible text at all (e.g.
    # forwarding a bot's post, or sharing via inline mode). Any incoming
    # message carrying its own inline keyboard is treated as a link-guard
    # bypass attempt, since regular users don't otherwise get one attached.
    if not reason and s.get("button_guard", True):
        if message.reply_markup and getattr(message.reply_markup, "inline_keyboard", None):
            reason = "🔘 Join/promo buttons not allowed"

    # 4. Long message
    if not reason and s.get("longmsg_guard", True):
        if len(text.split()) >= s.get("word_limit", 100):
            reason = f"📝 Message too long ({len(text.split())} words)"

    # 5. Banned word (admin-configured list — see /addword, /removeword)
    if not reason and s.get("word_guard", True):
        if _banned_word_hit(text, s.get("banned_words", [])):
            reason = "🚫 Banned word detected"

    if not reason:
        return

    try:
        await message.delete()
    except:
        pass

    await _apply_warn_action(client, message, reason)
    message.stop_propagation()


@Client.on_message(
    filters.group
    & filters.incoming
    & ~filters.command(GUARD_COMMANDS),
    group=-1
)
async def guard_handler(client: Client, message: Message):
    await _check_and_act(client, message)


# Catches the case where a user sends a clean message, then EDITS it to add
# a link (or other violating content). Without this handler, edited messages
# were never re-checked, so a link slipped past the guard.
@Client.on_edited_message(
    filters.group
    & filters.incoming
    & ~filters.command(GUARD_COMMANDS),
    group=-1
)
async def guard_edit_handler(client: Client, message: Message):
    await _check_and_act(client, message)
