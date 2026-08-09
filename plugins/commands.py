import os, string, logging, random, asyncio, time, datetime, re, sys, json, base64
from Script import script
from pyrogram.errors import MediaEmpty
from pyrogram import Client, filters, enums
from pyrogram.errors import ChatAdminRequired, FloodWait
from pyrogram.types import *
from database.ia_filterdb import col, sec_col, get_file_details, unpack_new_file_id, get_bad_files
from database.users_chats_db import db, delete_all_referal_users, get_referal_users_count, get_referal_all_users, referal_add_user
from database.join_reqs import JoinReqs
from info import CLONE_MODE, OWNER_LNK, REACTIONS, CHANNELS, REQUEST_TO_JOIN_MODE, TRY_AGAIN_BTN, ADMINS, SHORTLINK_MODE, PREMIUM_AND_REFERAL_MODE, STREAM_MODE, AUTH_CHANNEL, REFERAL_PREMEIUM_TIME, REFERAL_COUNT, PAYMENT_TEXT, PAYMENT_QR, LOG_CHANNEL, PICS, BATCH_FILE_CAPTION, CUSTOM_FILE_CAPTION, PROTECT_CONTENT, CHNL_LNK, GRP_LNK, REQST_CHANNEL, SUPPORT_CHAT, MAX_B_TN, VERIFY, SHORTLINK_API, SHORTLINK_URL, TUTORIAL, VERIFY_TUTORIAL, IS_TUTORIAL, URL
from utils import get_settings, pub_is_subscribed, get_size, is_subscribed, save_group_settings, temp, verify_user, check_token, check_verification, get_token, get_shortlink, get_tutorial, get_seconds, is_premium_user, MIN_VERIFY_SECONDS
from database.connections_mdb import active_connection
from urllib.parse import quote_plus
from database.users_chats_db import db
from database.guard_db import reset_warns, remove_ban_log, get_settings
from TechVJ.util.file_properties import get_name, get_hash, get_media_file_size
from database.ia_filterdb import col, sec_col, get_file_details, unpack_new_file_id, get_bad_files
from database.ia_filterdb import col, sec_col, get_file_details, unpack_new_file_id, get_bad_files, get_search_results
from plugins.pm_filter import auto_filter
logger = logging.getLogger(__name__)

# ── Language detection for the "🗣️ ʟᴀɴɢ :" caption line ────────────────
# Mirrors the detector in plugins/pm_filter.py so both files stay in sync
# without a cross-plugin import. Scans filename + original stored caption
# for language tags (e.g. "Hin", "Eng", "Kannada") and expands them to
# full names; falls back to DEFAULT_CAPTION_LANG when nothing is found.
_LANG_TOKEN_SPLIT_RE = re.compile(r'[^A-Za-z0-9]+')
_LANGUAGE_DEFS = [
    ("hin", "Hindi",     {"hindi", "hin"}),
    ("eng", "English",   {"english", "eng"}),
    ("tam", "Tamil",     {"tamil", "tam"}),
    ("tel", "Telugu",    {"telugu", "tel"}),
    ("kan", "Kannada",   {"kannada", "kan"}),
    ("mal", "Malayalam", {"malayalam", "mal"}),
    ("chi", "Chinese",   {"chinese", "chi", "mandarin", "cantonese"}),
    ("jap", "Japanese",  {"japanese", "jap", "jpn"}),
    ("guj", "Gujarati",  {"gujarati", "guj"}),
    ("mar", "Marathi",   {"marathi", "mar"}),
    ("ben", "Bengali",   {"bengali", "ben", "bangla"}),
    ("urd", "Urdu",      {"urdu", "urd"}),
    ("tul", "Tulu",      {"tulu", "tul"}),
    ("multi", "Multi Audio", {"multi", "dual"}),
]
_LANGUAGE_LABELS = {k: v for k, v, _ in _LANGUAGE_DEFS}
_LANGUAGE_TOKENS = {k: t for k, _, t in _LANGUAGE_DEFS}
_LANGUAGE_ORDER  = [k for k, _, _ in _LANGUAGE_DEFS]
# Short 3-letter codes (hin, tam, tel, mar, ben, chi, jap, jpn, guj, urd,
# tul, kan) are only reliable in FILENAMES, where a namer deliberately
# placed them as a standalone token. In free-text captions those same 3
# letters collide with unrelated words far too often ("Mar" = the month
# March, "Tel" = "Tel:" phone prefix, etc.), so captions are only matched
# against the FULL language name (e.g. "marathi", not "mar").
_LANGUAGE_TOKENS_CAPTION_SAFE = {
    k: {t for t in toks if len(t) >= 4} for k, toks in _LANGUAGE_TOKENS.items()
}
DEFAULT_CAPTION_LANG = "Original Audio"

# Captions commonly list subtitle languages separately from audio languages,
# often as a multi-line block (e.g. "Subtitle Tracks (5):\n- English\n-
# Chinese (Simplified)\n..."). This strips the WHOLE block - from a
# "subtitle(s)"/"sub(s)" keyword up to the next blank line (or end of
# caption) - so subtitle-only languages never get mistaken for audio
# languages, even when listed across multiple lines.
_SUBTITLE_STRIP_RE = re.compile(r'(?is)\b(?:subtitles?|subs?)\b.*?(?=\n\s*\n|\Z)')


def _strip_subtitle_info(text: str) -> str:
    return _SUBTITLE_STRIP_RE.sub('', text or '')


def format_caption_language(filename: str, original_caption: str = None) -> str:
    """Combines languages found in the FILENAME (checked against the full
    tag set, including short deliberate codes like 'Kan', 'Eng', 'Tam')
    with languages found in the CAPTION (checked against the caption-safe
    set only - full words like 'kannada', not bare 'kan'). Both sources
    are always combined so a file whose filename only carries ONE tag but
    whose caption genuinely lists several audio languages still shows all
    of them. Any "Subtitle: ..." block in the caption is stripped out
    first so subtitle languages aren't mistaken for audio languages.
    Falls back to DEFAULT_CAPTION_LANG only when nothing at all is found."""
    fname_tokens = set(_LANG_TOKEN_SPLIT_RE.split((filename or "").lower()))
    cap_tokens = set(_LANG_TOKEN_SPLIT_RE.split(_strip_subtitle_info(original_caption).lower()))

    detected = [
        k for k in _LANGUAGE_ORDER
        if (fname_tokens & _LANGUAGE_TOKENS[k]) or (cap_tokens & _LANGUAGE_TOKENS_CAPTION_SAFE[k])
    ]

    if not detected:
        return DEFAULT_CAPTION_LANG
    return " ".join(_LANGUAGE_LABELS[k] for k in detected)


# Regex for pulling a duration like "52m21s", "1h05m30s", or "01:02:03"
# out of the file's original stored caption (e.g. "File Duration 52m21s").
_DURATION_RE = re.compile(
    r'(?:duration|runtime|length)\s*[:\-]?\s*'
    r'(\d{1,2}:\d{2}(?::\d{2})?|(?:\d{1,2}\s*h)?(?:\d{1,2}\s*m)?(?:\d{1,2}\s*s)?)',
    re.IGNORECASE
)
DEFAULT_DURATION = "Not Available"


def extract_duration(original_caption: str = None) -> str:
    """Pulls a duration value out of the file's original stored caption,
    e.g. 'File Duration 52m21s' -> '52m21s'. Falls back to
    DEFAULT_DURATION when no duration is mentioned in the caption."""
    if not original_caption:
        return DEFAULT_DURATION
    m = _DURATION_RE.search(original_caption)
    if not m:
        return DEFAULT_DURATION
    val = re.sub(r'\s+', '', m.group(1) or '')
    return val if val else DEFAULT_DURATION
# ─────────────────────────────────────────────────────────────────────


# ── Editable /plan pricing (used by /plan and /plan_rate) ─────────────
# Stored in MongoDB via the existing db.get_bot_setting/update_bot_setting
# helpers (keyed by the bot's own Telegram id), so the owner can change
# prices from PM (/plan_rate) and it survives restarts/redeploys.
PLAN_RATES_SETTING_KEY = "plan_rates"
_DEFAULT_PLAN_RATES = {"week": "15", "month": "40", "3months": "110", "6months": "200"}


async def load_plan_rates(bot_id) -> dict:
    stored = await db.get_bot_setting(bot_id, PLAN_RATES_SETTING_KEY, None)
    if not stored:
        return dict(_DEFAULT_PLAN_RATES)
    return {**_DEFAULT_PLAN_RATES, **stored}


async def save_plan_rates(bot_id, rates: dict) -> None:
    await db.update_bot_setting(bot_id, PLAN_RATES_SETTING_KEY, rates)


def format_plan_rates(rates: dict) -> str:
    r = rates
    return (
        f"- {r['week']}ʀs - 1 ᴡᴇᴇᴋ\n"
        f"- {r['month']}ʀs - 1 ᴍᴏɴᴛʜs\n"
        f"- {r['3months']}ʀs - 3 ᴍᴏɴᴛʜs\n"
        f"- {r['6months']}ʀs - 6 ᴍᴏɴᴛʜs"
    )


# ── /myplan display formatting ─────────────────────────────────────────
def format_remaining_time(td: datetime.timedelta) -> str:
    """Turns a timedelta into '1 day 23 min 40 sec' style text."""
    total_seconds = max(0, int(td.total_seconds()))
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hr{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} min")
    if seconds or not parts:
        parts.append(f"{seconds} sec")
    return " ".join(parts)


def format_expiry_time(dt: datetime.datetime) -> str:
    """Turns a datetime into '13/07/2026 7:16 AM' style text."""
    date_part = dt.strftime("%d/%m/%Y")
    hour = dt.strftime("%I").lstrip("0") or "12"
    minute = dt.strftime("%M")
    ampm = dt.strftime("%p")
    return f"{date_part} {hour}:{minute} {ampm}"

async def deliver_resolved_file(client, chat_id, pre, file_id):
    """Directly deliver an already-resolved (pre, file_id) to chat_id, with
    no further deep-link round trip. Used to auto-resume a file right after
    verification succeeds — this is what used to be a 'Get Your File' button
    that re-parsed the original link and could fail with
    'File not found or link is invalid'.

    Same copyright-notice + 60s auto-delete + "Get File Again" button as
    every other single-file delivery in the bot (see the main file-delivery
    path below) — this was dropped in an earlier fix while chasing that
    'File not found' bug, but the actual bugs were the dead link-parsing
    and a missing `return` elsewhere, both fixed now, so it's safe to
    restore the normal behaviour here too."""
    files_ = await get_file_details(file_id)
    if not files_:
        await client.send_message(chat_id, "<b>❌ Sorry, that file could no longer be found. Please search again.</b>")
        return
    files = files_
    title = files["file_name"]
    size = get_size(files["file_size"])
    f_caption = files["caption"]
    if CUSTOM_FILE_CAPTION:
        try:
            f_caption = CUSTOM_FILE_CAPTION.format(
                file_name='' if title is None else title,
                file_size='' if size is None else size,
                file_lang=format_caption_language(title, f_caption),
                file_duration=extract_duration(f_caption),
                file_caption='' if f_caption is None else f_caption
            )
        except Exception:
            pass
    if f_caption is None:
        f_caption = f"{' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@'), files['file_name'].split()))}"

    reply_markup = await build_stream_reply_markup(chat_id, file_id)
    try:
        msg = await client.send_cached_media(
            chat_id=chat_id,
            file_id=file_id,
            caption=f_caption,
            protect_content=True if pre == 'filep' else False,
            reply_markup=reply_markup
        )
    except MediaEmpty:
        await client.send_message(chat_id, "❌ <b>File is no longer available.</b> The source file may have been deleted from the database channel.")
        return

    btn = [[InlineKeyboardButton("✅ ɢᴇᴛ ғɪʟᴇ ᴀɢᴀɪɴ ✅", callback_data=f'del#{file_id}')]]
    k = await msg.reply(text=f"<blockquote><b><u>❗️❗️❗️IMPORTANT❗️️❗️❗️</u></b>\n\nᴛʜɪs ᴍᴇssᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ɪɴ <b><u>1 mins</u> 🫥 <i></b>(ᴅᴜᴇ ᴛᴏ ᴄᴏᴘʏʀɪɢʜᴛ ɪssᴜᴇs)</i>.\n\n<b><i>ᴘʟᴇᴀsᴇ ғᴏʀᴡᴀʀᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ sᴀᴠᴇᴅ ᴍᴇssᴀɢᴇs ᴏʀ ᴀɴʏ ᴘʀɪᴠᴀᴛᴇ ᴄʜᴀᴛ.</i></b></blockquote>")
    await asyncio.sleep(60)
    await msg.delete()
    await k.edit_text("<b>✅ ʏᴏᴜʀ ᴍᴇssᴀɢᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ɪғ ʏᴏᴜ ᴡᴀɴᴛ ᴀɢᴀɪɴ ᴛʜᴇɴ ᴄʟɪᴄᴋ ᴏɴ ʙᴇʟᴏᴡ ʙᴜᴛᴛᴏɴ</b>", reply_markup=InlineKeyboardMarkup(btn))


BATCH_FILES = {}
join_db = JoinReqs


async def build_stream_reply_markup(user_id, file_id):
    """Stream/Watch button + Audio & Subs Info button.
    The buttons are always shown to everyone (as long as STREAM_MODE is on) —
    the premium check happens when the button is actually TAPPED, inside the
    generate_stream_link (plugins/pm_filter.py) and extract_data
    (plugins/extract.py) callback handlers, which alert non-premium users
    that it's a premium-only feature."""
    if not STREAM_MODE:
        return None
    button = [
        [InlineKeyboardButton(
            'sᴛʀᴇᴀᴍ ᴀɴᴅ ᴅᴏᴡɴʟᴏᴀᴅ',
            callback_data=f'generate_stream_link:{file_id}'
        )],
        [InlineKeyboardButton(
            'ℹ️ AUDIO & SUBS INFO',
            callback_data=f'extract_data:{file_id}'
        )]
    ]
    return InlineKeyboardMarkup(button)

@Client.on_chat_member_updated(filters.group)
async def bot_added_to_group_log(client, chat_member_updated):
    """Fires when the bot's own membership in a group changes.
    - Fresh join -> logs who added it (link included only if already admin).
    - Later promoted to admin -> sends a follow-up log with the invite link."""
    new_member = chat_member_updated.new_chat_member
    if not new_member or new_member.user.id != client.me.id:
        return

    old_status = chat_member_updated.old_chat_member.status if chat_member_updated.old_chat_member else None
    new_status = new_member.status
    chat = chat_member_updated.chat

    was_in_group = old_status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR]
    is_in_group = new_status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR]

    # Case 1: promoted to admin after already being in the group -> send the link now
    if was_in_group and new_status == enums.ChatMemberStatus.ADMINISTRATOR and old_status != enums.ChatMemberStatus.ADMINISTRATOR:
        try:
            invite = await client.create_chat_invite_link(chat.id)
            group_link = invite.invite_link
        except Exception as e:
            group_link = f"Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ ({e})"
        await client.send_message(
            LOG_CHANNEL,
            f"#AdminGranted\nGʀᴏᴜᴘ = {chat.title}(<code>{chat.id}</code>)\nI'ᴍ ɴᴏᴡ ᴀᴅᴍɪɴ. Gʀᴏᴜᴘ Lɪɴᴋ - {group_link}"
        )
        return

    # Case 2: fresh join (wasn't in the group before)
    if was_in_group or not is_in_group:
        return

    added_by = chat_member_updated.from_user.mention if chat_member_updated.from_user else "Unknown"

    try:
        total = await client.get_chat_members_count(chat.id)
    except Exception:
        total = "Unknown"

    try:
        invite = await client.create_chat_invite_link(chat.id)
        group_link = invite.invite_link
    except ChatAdminRequired:
        group_link = "Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ (I'ᴍ ɴᴏᴛ ᴀᴅᴍɪɴ ʏᴇᴛ)"
    except Exception as e:
        group_link = f"Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ ({e})"

    if not await db.get_chat(chat.id):
        await db.add_chat(chat.id, chat.title)

    await client.send_message(
        LOG_CHANNEL,
        script.LOG_TEXT_G.format(chat.title, chat.id, total, added_by, group_link)
    )

@Client.on_message(filters.command("start") & filters.incoming)
async def start(client, message):
    try:
        await message.react(emoji=random.choice(REACTIONS), big=True)
    except:
        pass
    if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        buttons = [[
            InlineKeyboardButton('⤬ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ ⤬', url=f'http://t.me/{temp.U_NAME}?startgroup=true')
        ],[
            InlineKeyboardButton('sᴜᴘᴘᴏʀᴛ ɢʀᴏᴜᴘ', url=f'https://t.me/{SUPPORT_CHAT}'),
            InlineKeyboardButton('ᴍᴏᴠɪᴇ ɢʀᴏᴜᴘ', url=GRP_LNK)
        ],[
            InlineKeyboardButton('ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ', url=CHNL_LNK)
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await message.reply(script.START_TXT.format(message.from_user.mention if message.from_user else message.chat.title, temp.U_NAME, temp.B_NAME), reply_markup=reply_markup, disable_web_page_preview=True)
        await asyncio.sleep(2) # 😢 https://github.com/EvamariaTG/EvaMaria/blob/master/plugins/p_ttishow.py#L17 😬 wait a bit, before checking.
        if not await db.get_chat(message.chat.id):
            total=await client.get_chat_members_count(message.chat.id)
            try:
                invite = await client.create_chat_invite_link(message.chat.id)
                fallback_link = invite.invite_link
            except Exception:
                fallback_link = "Not Available"
            await client.send_message(LOG_CHANNEL, script.LOG_TEXT_G.format(message.chat.title, message.chat.id, total, "Unknown", fallback_link))       
            await db.add_chat(message.chat.id, message.chat.title)
        return 
    if not await db.is_user_exist(message.from_user.id):
        await db.add_user(message.from_user.id, message.from_user.first_name)
        await client.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(message.from_user.id, message.from_user.mention))
    if len(message.command) != 2:
        if PREMIUM_AND_REFERAL_MODE == True:
            buttons = [[
                InlineKeyboardButton('⤬ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ ⤬', url=f'http://t.me/{temp.U_NAME}?startgroup=true')
            ],[
                InlineKeyboardButton('ᴇᴀʀɴ ᴍᴏɴᴇʏ', callback_data="shortlink_info"),
                InlineKeyboardButton('ᴍᴏᴠɪᴇ ɢʀᴏᴜᴘ', url=GRP_LNK)
            ],[
                InlineKeyboardButton('ʜᴇʟᴘ', callback_data='help'),
                InlineKeyboardButton('ᴀʙᴏᴜᴛ', callback_data='about')
            ],[
                InlineKeyboardButton('ᯓ★ ᴘʀᴇᴍɪᴜᴍ ᴀɴᴅ ʀᴇғᴇʀʀᴀʟ ᯓ★', callback_data='subscription')
            ],[
                InlineKeyboardButton('ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ', url=CHNL_LNK)
            ]]
        else:
            buttons = [[
                InlineKeyboardButton('⤬ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ ⤬', url=f'http://t.me/{temp.U_NAME}?startgroup=true')
            ],[
                InlineKeyboardButton('ᴇᴀʀɴ ᴍᴏɴᴇʏ', callback_data="shortlink_info"),
                InlineKeyboardButton('ᴍᴏᴠɪᴇ ɢʀᴏᴜᴘ', url=GRP_LNK)
            ],[
                InlineKeyboardButton('ʜᴇʟᴘ', callback_data='help'),
                InlineKeyboardButton('ᴀʙᴏᴜᴛ', callback_data='about')
            ],[
                InlineKeyboardButton('ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ', url=CHNL_LNK)
            ]]
        if CLONE_MODE == True:
            buttons.append([InlineKeyboardButton('ᴄʀᴇᴀᴛᴇ ᴏᴡɴ ᴄʟᴏɴᴇ ʙᴏᴛ', callback_data='clone')])
        reply_markup = InlineKeyboardMarkup(buttons)
        m=await message.reply_sticker("CAACAgUAAxkBAAEKVaxlCWGs1Ri6ti45xliLiUeweCnu4AACBAADwSQxMYnlHW4Ls8gQMAQ") 
        await asyncio.sleep(1)
        await m.delete()
        await message.reply_photo(
            photo=random.choice(PICS),
            caption=script.START_TXT.format(message.from_user.mention, temp.U_NAME, temp.B_NAME),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        return
    
    if AUTH_CHANNEL and not await is_subscribed(client, message):
        try:
            if REQUEST_TO_JOIN_MODE == True:
                invite_link = await client.create_chat_invite_link(chat_id=(int(AUTH_CHANNEL)), creates_join_request=True)
            else:
                invite_link = await client.create_chat_invite_link(int(AUTH_CHANNEL))
        except Exception as e:
            print(e)
            await message.reply_text("Make sure Bot is admin in Forcesub channel")
            return
        try:
            btn = [[InlineKeyboardButton("ʙᴀᴄᴋᴜᴘ ᴄʜᴀɴɴᴇʟ", url=invite_link.invite_link)]]
            if message.command[1] != "subscribe":
                if REQUEST_TO_JOIN_MODE == True:
                    if TRY_AGAIN_BTN == True:
                        try:
                            kk, file_id = message.command[1].split("_", 1)
                            btn.append([InlineKeyboardButton("↻ ᴛʀʏ ᴀɢᴀɪɴ", callback_data=f"checksub#{kk}#{file_id}")])
                        except (IndexError, ValueError):
                            btn.append([InlineKeyboardButton("↻ ᴛʀʏ ᴀɢᴀɪɴ", url=f"https://t.me/{temp.U_NAME}?start={message.command[1]}")])
                else:
                    try:
                        kk, file_id = message.command[1].split("_", 1)
                        btn.append([InlineKeyboardButton("↻ ᴛʀʏ ᴀɢᴀɪɴ", callback_data=f"checksub#{kk}#{file_id}")])
                    except (IndexError, ValueError):
                        btn.append([InlineKeyboardButton("↻ ᴛʀʏ ᴀɢᴀɪɴ", url=f"https://t.me/{temp.U_NAME}?start={message.command[1]}")])
    
            if REQUEST_TO_JOIN_MODE == True:
                if TRY_AGAIN_BTN == True:
                    text = "**🕵️ ʏᴏᴜ ᴅᴏ ɴᴏᴛ ᴊᴏɪɴ ᴍʏ ʙᴀᴄᴋᴜᴘ ᴄʜᴀɴɴᴇʟ ғɪʀsᴛ ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ ᴛʜᴇɴ ᴛʀʏ ᴀɢᴀɪɴ**"
                else:
                    await db.set_msg_command(message.from_user.id, com=message.command[1])
                    text = "**🕵️ ʏᴏᴜ ᴅᴏ ɴᴏᴛ ᴊᴏɪɴ ᴍʏ ʙᴀᴄᴋᴜᴘ ᴄʜᴀɴɴᴇʟ ғɪʀsᴛ ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ**"
            else:
                text = "**🕵️ ʏᴏᴜ ᴅᴏ ɴᴏᴛ ᴊᴏɪɴ ᴍʏ ʙᴀᴄᴋᴜᴘ ᴄʜᴀɴɴᴇʟ ғɪʀsᴛ ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ ᴛʜᴇɴ ᴛʀʏ ᴀɢᴀɪɴ**"
    
            await client.send_message(
                chat_id=message.from_user.id,
                text=text,
                reply_markup=InlineKeyboardMarkup(btn),
                parse_mode=enums.ParseMode.MARKDOWN
            )
            return
        except Exception as e:
            print(e)
            return await message.reply_text("something wrong with force subscribe.")
            
    if len(message.command) == 2 and message.command[1] in ["subscribe", "error", "okay", "help"]:
        if PREMIUM_AND_REFERAL_MODE == True:
            buttons = [[
                InlineKeyboardButton('⤬ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ ⤬', url=f'http://t.me/{temp.U_NAME}?startgroup=true')
            ],[
                InlineKeyboardButton('ᴇᴀʀɴ ᴍᴏɴᴇʏ', callback_data="shortlink_info"),
                InlineKeyboardButton('ᴍᴏᴠɪᴇ ɢʀᴏᴜᴘ', url=GRP_LNK)
            ],[
                InlineKeyboardButton('ʜᴇʟᴘ', callback_data='help'),
                InlineKeyboardButton('ᴀʙᴏᴜᴛ', callback_data='about')
            ],[
                InlineKeyboardButton('ᯓ★ ᴘʀᴇᴍɪᴜᴍ ᴀɴᴅ ʀᴇғᴇʀʀᴀʟ ᯓ★', callback_data='subscription')
            ],[
                InlineKeyboardButton('ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ', url=CHNL_LNK)
            ]]
        else:
            buttons = [[
                InlineKeyboardButton('⤬ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ ⤬', url=f'http://t.me/{temp.U_NAME}?startgroup=true')
            ],[
                InlineKeyboardButton('ᴇᴀʀɴ ᴍᴏɴᴇʏ', callback_data="shortlink_info"),
                InlineKeyboardButton('ᴍᴏᴠɪᴇ ɢʀᴏᴜᴘ', url=GRP_LNK)
            ],[
                InlineKeyboardButton('ʜᴇʟᴘ', callback_data='help'),
                InlineKeyboardButton('ᴀʙᴏᴜᴛ', callback_data='about')
            ],[
                InlineKeyboardButton('ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ', url=CHNL_LNK)
            ]]
        if CLONE_MODE == True:
            buttons.append([InlineKeyboardButton('ᴄʀᴇᴀᴛᴇ ᴏᴡɴ ᴄʟᴏɴᴇ ʙᴏᴛ', callback_data='clone')])
        reply_markup = InlineKeyboardMarkup(buttons)      
        await message.reply_photo(
            photo=random.choice(PICS),
            caption=script.START_TXT.format(message.from_user.mention, temp.U_NAME, temp.B_NAME),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        return
    data = message.command[1]
    if data.split("-", 1)[0] == "Goflix":
        user_id = int(data.split("-", 1)[1])
        if user_id == message.from_user.id:
            await message.reply("<b>❌ You can't use your own referral link. Share it with a friend instead!</b>")
            return
        vj = await referal_add_user(user_id, message.from_user.id)
        if vj and PREMIUM_AND_REFERAL_MODE == True:
            await message.reply(f"<b>You have joined using the referral link of user with ID {user_id}\n\nSend /start again to use the bot</b>")
            num_referrals = await get_referal_users_count(user_id)
            await client.send_message(chat_id = user_id, text = "<b>{} start the bot with your referral link\n\nTotal Referals - {}</b>".format(message.from_user.mention, num_referrals))

            # ✅ Reward BOTH sides immediately for every unique referral —
            # this used to only reward the REFERRER, and only once their
            # count hit an exact REFERAL_COUNT threshold (so a single
            # referral did nothing at all unless that threshold was
            # exactly 1). The referee (person who opened the link) never
            # got anything, ever, in any case. Also extends existing
            # premium instead of overwriting it, so this can never
            # accidentally shorten someone's remaining time.
            async def _grant_or_extend_premium(uid, seconds):
                existing = await db.get_user(uid)
                now = datetime.datetime.now()
                current_expiry = existing.get("expiry_time") if existing else None
                if isinstance(current_expiry, datetime.datetime) and current_expiry > now:
                    new_expiry = current_expiry + datetime.timedelta(seconds=seconds)
                else:
                    new_expiry = now + datetime.timedelta(seconds=seconds)
                await db.update_user({"id": uid, "expiry_time": new_expiry, "expiry_reminder_sent": False, "expired_notified": False})
                return new_expiry

            referrer_seconds = await get_seconds(REFERAL_PREMEIUM_TIME)  # 1 week, as advertised
            referee_seconds = await get_seconds("3day")                 # 3 days for joining via a referral link

            if referrer_seconds > 0:
                await _grant_or_extend_premium(user_id, referrer_seconds)
                try:
                    await client.send_message(
                        chat_id=user_id,
                        text="<b>🎉 You earned {} premium for referring {}!</b>".format(REFERAL_PREMEIUM_TIME, message.from_user.mention)
                    )
                except Exception:
                    pass

            if referee_seconds > 0:
                await _grant_or_extend_premium(message.from_user.id, referee_seconds)
                try:
                    await message.reply("<b>🎉 You got 3 days premium for joining via a referral link!</b>")
                except Exception:
                    pass
            return
        else:
            if PREMIUM_AND_REFERAL_MODE == True:
                buttons = [[
                    InlineKeyboardButton('⤬ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ ⤬', url=f'http://t.me/{temp.U_NAME}?startgroup=true')
                ],[
                    InlineKeyboardButton('ᴇᴀʀɴ ᴍᴏɴᴇʏ', callback_data="shortlink_info"),
                    InlineKeyboardButton('ᴍᴏᴠɪᴇ ɢʀᴏᴜᴘ', url=GRP_LNK)
                ],[
                    InlineKeyboardButton('ʜᴇʟᴘ', callback_data='help'),
                    InlineKeyboardButton('ᴀʙᴏᴜᴛ', callback_data='about')
                ],[
                    InlineKeyboardButton('ᯓ★ ᴘʀᴇᴍɪᴜᴍ ᴀɴᴅ ʀᴇғᴇʀʀᴀʟ ᯓ★', callback_data='subscription')
                ],[
                    InlineKeyboardButton('ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ', url=CHNL_LNK)
                ]]
            else:
                buttons = [[
                    InlineKeyboardButton('⤬ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ ⤬', url=f'http://t.me/{temp.U_NAME}?startgroup=true')
                ],[
                    InlineKeyboardButton('ᴇᴀʀɴ ᴍᴏɴᴇʏ', callback_data="shortlink_info"),
                    InlineKeyboardButton('ᴍᴏᴠɪᴇ ɢʀᴏᴜᴘ', url=GRP_LNK)
                ],[
                    InlineKeyboardButton('ʜᴇʟᴘ', callback_data='help'),
                    InlineKeyboardButton('ᴀʙᴏᴜᴛ', callback_data='about')
                ],[
                    InlineKeyboardButton('ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ', url=CHNL_LNK)
                ]]
            if CLONE_MODE == True:
                buttons.append([InlineKeyboardButton('ᴄʀᴇᴀᴛᴇ ᴏᴡɴ ᴄʟᴏɴᴇ ʙᴏᴛ', callback_data='clone')])
            reply_markup = InlineKeyboardMarkup(buttons)
            m=await message.reply_sticker("CAACAgUAAxkBAAEKVaxlCWGs1Ri6ti45xliLiUeweCnu4AACBAADwSQxMYnlHW4Ls8gQMAQ") 
            await asyncio.sleep(1)
            await m.delete()
            await message.reply_photo(
                photo=random.choice(PICS),
                caption=script.START_TXT.format(message.from_user.mention, temp.U_NAME, temp.B_NAME),
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML
            )
            return 
    try:
        pre, file_id = data.split('_', 1)
    except:
        file_id = data
        pre = ""
    if data.startswith("getfile-"):
        query = data.replace("getfile-", "").replace("-", " ").strip()
        reply_msg = await message.reply_text(f"<b><i>Searching For {query} 🔍</i></b>")
        try:
            await auto_filter(client, query, message, reply_msg, ai_search=True, from_deeplink=True)
        except Exception as e:
            logger.error(f"auto_filter (getfile deeplink) failed for query '{query}': {e}")
            try:
                await reply_msg.edit_text("⚠️ <b>Search took too long or failed.</b> Please try again in a moment.")
            except Exception:
                pass
        return
    if data.split("-", 1)[0] == "BATCH":
        sts = await message.reply("<b>ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ...</b>")
        file_id = data.split("-", 1)[1]
        msgs = BATCH_FILES.get(file_id)
        if not msgs:
            file = await client.download_media(file_id)
            try: 
                with open(file) as file_data:
                    msgs=json.loads(file_data.read())
            except:
                await sts.edit("FAILED")
                return await client.send_message(LOG_CHANNEL, "UNABLE TO OPEN FILE.")
            os.remove(file)
            BATCH_FILES[file_id] = msgs

        filesarr = []
        for msg in msgs:
            title = msg.get("title")
            size=get_size(int(msg.get("size", 0)))
            f_caption=msg.get("caption", "")
            if BATCH_FILE_CAPTION:
                try:
                    f_caption=BATCH_FILE_CAPTION.format(file_name= '' if title is None else title, file_size='' if size is None else size, file_lang=format_caption_language(title, f_caption), file_duration=extract_duration(f_caption), file_caption='' if f_caption is None else f_caption)
                except:
                    f_caption=f_caption
            if f_caption is None:
                f_caption = f"{title}"
            try:
                user_is_premium = await is_premium_user(message.from_user.id)
                if STREAM_MODE == True and user_is_premium:
                    log_msg = await client.send_cached_media(chat_id=LOG_CHANNEL, file_id=msg.get("file_id"))
                    fileName = {quote_plus(get_name(log_msg))}
                    stream = f"{URL}/watch/{str(log_msg.chat.id)}/{str(log_msg.id)}/{quote_plus(get_name(log_msg))}?hash={get_hash(log_msg)}"
                    download = f"{URL}/download/{str(log_msg.chat.id)}/{str(log_msg.id)}/{quote_plus(get_name(log_msg))}?hash={get_hash(log_msg)}"

                    button = [[
                        InlineKeyboardButton("• ᴅᴏᴡɴʟᴏᴀᴅ •", url=download),
                        InlineKeyboardButton('• ᴡᴀᴛᴄʜ •', url=stream)
                    ],[
                        InlineKeyboardButton("• ᴡᴀᴛᴄʜ ɪɴ ᴡᴇʙ ᴀᴘᴘ •", web_app=WebAppInfo(url=stream))
                    ]]
                    reply_markup = InlineKeyboardMarkup(button)
                else:
                    reply_markup = None
                    
                msg = await client.send_cached_media(
                    chat_id=message.from_user.id,
                    file_id=msg.get("file_id"),
                    caption=f_caption,
                    protect_content=msg.get('protect', False),
                    reply_markup=reply_markup
                )
                filesarr.append(msg)
                
            except FloodWait as e:
                await asyncio.sleep(e.value)
                msg = await client.send_cached_media(
                    chat_id=message.from_user.id,
                    file_id=msg.get("file_id"),
                    caption=f_caption,
                    protect_content=msg.get('protect', False),
                    reply_markup=InlineKeyboardMarkup(button)
                )
                filesarr.append(msg)
            except:
                continue
            await asyncio.sleep(1) 
        await sts.delete()
        k = await client.send_message(chat_id = message.from_user.id, text=f"<blockquote><b><u>❗️❗️❗️IMPORTANT❗️️❗️❗️</u></b>\n\nᴛʜɪs ᴍᴇssᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ɪɴ <b><u>1 mins</u> 🫥 <i></b>(ᴅᴜᴇ ᴛᴏ ᴄᴏᴘʏʀɪɢʜᴛ ɪssᴜᴇs)</i>.\n\n<b><i>ᴘʟᴇᴀsᴇ ғᴏʀᴡᴀʀᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ sᴀᴠᴇᴅ ᴍᴇssᴀɢᴇs ᴏʀ ᴀɴʏ ᴘʀɪᴠᴀᴛᴇ ᴄʜᴀᴛ.</i></b></blockquote>")
        await asyncio.sleep(60)
        for x in filesarr:
            await x.delete()
        await k.edit_text("<b>✅ ʏᴏᴜʀ ᴍᴇssᴀɢᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ</b>")  
        return

    elif data.split("-", 1)[0] == "DSTORE":
        sts = await message.reply("<b>ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ...</b>")
        b_string = data.split("-", 1)[1]
        decoded = (base64.urlsafe_b64decode(b_string + "=" * (-len(b_string) % 4))).decode("ascii")
        try:
            f_msg_id, l_msg_id, f_chat_id, protect = decoded.split("_", 3)
        except:
            f_msg_id, l_msg_id, f_chat_id = decoded.split("_", 2)
            protect = "/pbatch" if PROTECT_CONTENT else "batch"
        diff = int(l_msg_id) - int(f_msg_id)
        filesarr = []
        async for msg in client.iter_messages(int(f_chat_id), int(l_msg_id), int(f_msg_id)):
            if msg.media:
                media = getattr(msg, msg.media.value)
                file_type = msg.media
                file = getattr(msg, file_type.value)
                size = get_size(int(file.file_size))
                file_name = getattr(media, 'file_name', '')
                f_caption = getattr(msg, 'caption', file_name)
                if BATCH_FILE_CAPTION:
                    try:
                        f_caption=BATCH_FILE_CAPTION.format(file_name=file_name, file_size='' if size is None else size, file_lang=format_caption_language(file_name, f_caption), file_duration=extract_duration(f_caption), file_caption=f_caption)
                    except:
                        f_caption = getattr(msg, 'caption', '')
                file_id = file.file_id
                user_is_premium = await is_premium_user(message.from_user.id)
                if STREAM_MODE == True and user_is_premium:
                    log_msg = await client.send_cached_media(chat_id=LOG_CHANNEL, file_id=file_id)
                    fileName = {quote_plus(get_name(log_msg))}
                    stream = f"{URL}/watch/{str(log_msg.id)}/{quote_plus(get_name(log_msg))}?hash={get_hash(log_msg)}"
                    download = f"{URL}/download/{str(log_msg.id)}/{quote_plus(get_name(log_msg))}?hash={get_hash(log_msg)}"

                    button = [[
                        InlineKeyboardButton("• ᴅᴏᴡɴʟᴏᴀᴅ •", url=download),
                        InlineKeyboardButton('• ᴡᴀᴛᴄʜ •', url=stream)
                    ],[
                        InlineKeyboardButton("• ᴡᴀᴛᴄʜ ɪɴ ᴡᴇʙ ᴀᴘᴘ •", web_app=WebAppInfo(url=stream))
                    ]]
                    reply_markup = InlineKeyboardMarkup(button)
                else:
                    reply_markup = None
                try:
                    p = await msg.copy(message.chat.id, caption=f_caption, protect_content=True if protect == "/pbatch" else False, reply_markup=reply_markup)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    p = await msg.copy(message.chat.id, caption=f_caption, protect_content=True if protect == "/pbatch" else False, reply_markup=reply_markup)
                except:
                    continue
            elif msg.empty:
                continue
            else:
                try:
                    p = await msg.copy(message.chat.id, protect_content=True if protect == "/pbatch" else False)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    p = await msg.copy(message.chat.id, protect_content=True if protect == "/pbatch" else False)
                except:
                    continue
            filesarr.append(p)
            await asyncio.sleep(1)
        await sts.delete()
        k = await client.send_message(chat_id = message.from_user.id, text=f"<blockquote><b><u>❗️❗️❗️IMPORTANT❗️️❗️❗️</u></b>\n\nᴛʜɪs ᴍᴇssᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ɪɴ <b><u>1 mins</u> 🫥 <i></b>(ᴅᴜᴇ ᴛᴏ ᴄᴏᴘʏʀɪɢʜᴛ ɪssᴜᴇs)</i>.\n\n<b><i>ᴘʟᴇᴀsᴇ ғᴏʀᴡᴀʀᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ sᴀᴠᴇᴅ ᴍᴇssᴀɢᴇs ᴏʀ ᴀɴʏ ᴘʀɪᴠᴀᴛᴇ ᴄʜᴀᴛ.</i></b></blockquote>")
        await asyncio.sleep(60)
        for x in filesarr:
            await x.delete()
        await k.edit_text("<b>✅ ʏᴏᴜʀ ᴍᴇssᴀɢᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ</b>")
        return

    elif data.split("-", 1)[0] == "verify":
        userid = data.split("-", 2)[1]
        token = data.split("-", 3)[2]
        if str(message.from_user.id) != str(userid):
            return await message.reply_text(text="<b>ɪɴᴠᴀʟɪᴅ ʟɪɴᴋ ᴏʀ ᴇxᴘɪʀᴇᴅ ʟɪɴᴋ</b>", protect_content=True)
        try:
            is_valid = await check_token(client, userid, token)
        except Exception as e:
            logger.error(f"check_token failed for user {userid}: {e}")
            return await message.reply_text(text="<b>⚠️ Verification failed due to a temporary error. Please tap the verify link again.</b>", protect_content=True)
        if is_valid == "BYPASS":
            try:
                await client.send_message(
                    LOG_CHANNEL,
                    f"<b>🚫 Verification Bypass Detected</b>\n\n👤 User: {message.from_user.mention}\n🆔 ID: <code>{message.from_user.id}</code>\n\nVerified in under {MIN_VERIFY_SECONDS}s — flagged as a likely bypass tool."
                )
            except Exception as e:
                logger.error(f"Failed to log bypass detection for {userid}: {e}")

            try:
                new_link = await get_token(client, message.from_user.id, f"https://telegram.me/{temp.U_NAME}?start=")
            except Exception as e:
                logger.error(f"Failed to issue fresh verify link after bypass for {userid}: {e}")
                return await message.reply_text(
                    text="<b>🚫 You used a bypass tool to skip verification. Please try /start again to get a fresh verification link.</b>",
                    protect_content=True
                )
            btn = [[InlineKeyboardButton("ᴠᴇʀɪғʏ", url=new_link)]]
            return await message.reply_text(
                text="<b>🚫 You used a bypass tool to skip verification, don't spam here.\n\nPlease complete the verification properly using the fresh link below.</b>",
                reply_markup=InlineKeyboardMarkup(btn),
                protect_content=True
            )
        if is_valid == True:
            text = "<b>ʜᴇʏ {} 👋,\n\nʏᴏᴜ ʜᴀᴠᴇ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ᴛʜᴇ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ...\n\nɴᴏᴡ ʏᴏᴜ ʜᴀᴠᴇ ᴜɴʟɪᴍɪᴛᴇᴅ ᴀᴄᴄᴇss ᴛɪʟʟ ᴛᴏᴅᴀʏ ɴᴏᴡ ᴇɴᴊᴏʏ\n\n</b>"
            if PREMIUM_AND_REFERAL_MODE == True:
                text += "<b>ɪғ ʏᴏᴜ ᴡᴀɴᴛ ᴅɪʀᴇᴄᴛ ғɪʟᴇꜱ ᴡɪᴛʜᴏᴜᴛ ᴀɴʏ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴꜱ ᴛʜᴇɴ ʙᴜʏ ʙᴏᴛ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ ☺️\n\n💶 ꜱᴇɴᴅ /plan ᴛᴏ ʙᴜʏ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ</b>"           
            await message.reply_text(text=text.format(message.from_user.mention), protect_content=True)
            try:
                await client.send_message(
                    LOG_CHANNEL,
                    f"<b>✅ Verification Completed</b>\n\n👤 User: {message.from_user.mention}\n🆔 ID: <code>{message.from_user.id}</code>"
                )
            except Exception as e:
                logger.error(f"Failed to log verification completion for {userid}: {e}")
            resume_data = None
            try:
                resume_data = await verify_user(client, userid, token)
            except Exception as e:
                logger.error(f"verify_user failed for user {userid}: {e}")
            if resume_data:
                try:
                    r_pre, r_file_id = resume_data.split('_', 1)
                except ValueError:
                    r_pre, r_file_id = "", resume_data
                try:
                    if r_pre in ("file", "filep"):
                        # Deliver directly — no extra click, no deep-link round trip.
                        await deliver_resolved_file(client, message.from_user.id, r_pre, r_file_id)
                    else:
                        # Batch / shortlink-gated formats genuinely need another
                        # step (e.g. a fresh ad-shortlink), so fall back to a button.
                        await client.send_message(
                            chat_id=message.from_user.id,
                            text="<b>📥 Here's the file you requested:</b>",
                            reply_markup=InlineKeyboardMarkup(
                                [[InlineKeyboardButton("✅ ɢᴇᴛ ʏᴏᴜʀ ғɪʟᴇ", url=f"https://telegram.me/{temp.U_NAME}?start={resume_data}")]]
                            )
                        )
                except Exception as e:
                    logger.error(f"Failed to auto-resume file for user {userid}: {e}")
            return
        else:
            return await message.reply_text(text="<b>ɪɴᴠᴀʟɪᴅ ʟɪɴᴋ ᴏʀ ᴇxᴘɪʀᴇᴅ ʟɪɴᴋ</b>", protect_content=True)
            
    if data.startswith("sendfiles"):
        chat_id = int("-" + file_id.split("-")[1])
        userid = message.from_user.id if message.from_user else None
        settings = await get_settings(chat_id)
        pre = 'allfilesp' if settings['file_secure'] else 'allfiles'
        g = await get_shortlink(chat_id, f"https://telegram.me/{temp.U_NAME}?start={pre}_{file_id}")
        btn = [[
            InlineKeyboardButton('ᴅᴏᴡɴʟᴏᴀᴅ ɴᴏᴡ', url=g)
        ]]
        if settings['tutorial']:
            btn.append([InlineKeyboardButton('ʜᴏᴡ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ', url=await get_tutorial(chat_id))])
        text = "<b>✅ ʏᴏᴜʀ ғɪʟᴇ ʀᴇᴀᴅʏ ᴄʟɪᴄᴋ ᴏɴ ᴅᴏᴡɴʟᴏᴀᴅ ɴᴏᴡ ʙᴜᴛᴛᴏɴ ᴛʜᴇɴ ᴏᴘᴇɴ ʟɪɴᴋ ᴛᴏ ɢᴇᴛ ғɪʟᴇ\n\n</b>"
        if PREMIUM_AND_REFERAL_MODE == True:
            text += "<b>ɪғ ʏᴏᴜ ᴡᴀɴᴛ ᴅɪʀᴇᴄᴛ ғɪʟᴇꜱ ᴡɪᴛʜᴏᴜᴛ ᴀɴʏ ᴏᴘᴇɴɪɴɢ ʟɪɴᴋ ᴀɴᴅ ᴡᴀᴛᴄʜɪɴɢ ᴀᴅs ᴛʜᴇɴ ʙᴜʏ ʙᴏᴛ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ ☺️\n\n💶 ꜱᴇɴᴅ /plan ᴛᴏ ʙᴜʏ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ</b>"
        k = await client.send_message(chat_id=message.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(btn))
        await asyncio.sleep(300)
        await k.edit("<b>✅ ʏᴏᴜʀ ᴍᴇssᴀɢᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ</b>")
        return
        
    
    elif data.startswith("short"):
        user = message.from_user.id
        chat_id = temp.SHORT.get(user)
        settings = await get_settings(chat_id)
        pre = 'filep' if settings['file_secure'] else 'file'
        g = await get_shortlink(chat_id, f"https://telegram.me/{temp.U_NAME}?start={pre}_{file_id}")
        btn = [[
            InlineKeyboardButton('ᴅᴏᴡɴʟᴏᴀᴅ ɴᴏᴡ', url=g)
        ]]
        if settings['tutorial']:
            btn.append([InlineKeyboardButton('ʜᴏᴡ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ', url=await get_tutorial(chat_id))])
        text = "<b>✅ ʏᴏᴜʀ ғɪʟᴇ ʀᴇᴀᴅʏ ᴄʟɪᴄᴋ ᴏɴ ᴅᴏᴡɴʟᴏᴀᴅ ɴᴏᴡ ʙᴜᴛᴛᴏɴ ᴛʜᴇɴ ᴏᴘᴇɴ ʟɪɴᴋ ᴛᴏ ɢᴇᴛ ғɪʟᴇ\n\n</b>"
        if PREMIUM_AND_REFERAL_MODE == True:
            text += "<b>ɪғ ʏᴏᴜ ᴡᴀɴᴛ ᴅɪʀᴇᴄᴛ ғɪʟᴇꜱ ᴡɪᴛʜᴏᴜᴛ ᴀɴʏ ᴏᴘᴇɴɪɴɢ ʟɪɴᴋ ᴀɴᴅ ᴡᴀᴛᴄʜɪɴɢ ᴀᴅs ᴛʜᴇɴ ʙᴜʏ ʙᴏᴛ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ ☺️\n\n💶 ꜱᴇɴᴅ /plan ᴛᴏ ʙᴜʏ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ</b>"
        k = await client.send_message(chat_id=user, text=text, reply_markup=InlineKeyboardMarkup(btn))
        await asyncio.sleep(1200)
        await k.edit("<b>✅ ʏᴏᴜʀ ᴍᴇssᴀɢᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ</b>")
        return
        
    elif data.startswith("all"):
        files = temp.GETALL.get(file_id)
        if not files:
            return await message.reply('<b><i>No such file exist.</b></i>')
        filesarr = []
        for file in files:
            file_id = file["file_id"]
            files1 = await get_file_details(file_id)
            title = files1["file_name"]
            size=get_size(files1["file_size"])
            f_caption=files1["caption"]
            if CUSTOM_FILE_CAPTION:
                try:
                    f_caption=CUSTOM_FILE_CAPTION.format(file_name= '' if title is None else title, file_size='' if size is None else size, file_lang=format_caption_language(title, f_caption), file_duration=extract_duration(f_caption), file_caption='' if f_caption is None else f_caption)
                except:
                    f_caption=f_caption
            if f_caption is None:
                f_caption = f"{' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@'), files1['file_name'].split()))}"
            if not await db.has_premium_access(message.from_user.id):
                if not await check_verification(client, message.from_user.id) and VERIFY == True:
                    btn = [[
                        InlineKeyboardButton("ᴠᴇʀɪғʏ", url=await get_token(client, message.from_user.id, f"https://telegram.me/{temp.U_NAME}?start=", pending_data=data))
                    ],[
                        InlineKeyboardButton("ʜᴏᴡ ᴛᴏ ᴠᴇʀɪғʏ", url=VERIFY_TUTORIAL)
                    ]]
                    text = "<b>ʜᴇʏ {} 👋,\n\nʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴠᴇʀɪғɪᴇᴅ ᴛᴏᴅᴀʏ, ᴘʟᴇᴀꜱᴇ ᴄʟɪᴄᴋ ᴏɴ ᴠᴇʀɪғʏ & ɢᴇᴛ ᴜɴʟɪᴍɪᴛᴇᴅ ᴀᴄᴄᴇꜱꜱ ғᴏʀ ᴛᴏᴅᴀʏ</b>"
                    if PREMIUM_AND_REFERAL_MODE == True:
                        text += "<b>ɪғ ʏᴏᴜ ᴡᴀɴᴛ ᴅɪʀᴇᴄᴛ ғɪʟᴇꜱ ᴡɪᴛʜᴏᴜᴛ ᴀɴʏ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴꜱ ᴛʜᴇɴ ʙᴜʏ ʙᴏᴛ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ ☺️\n\n💶 ꜱᴇɴᴅ /plan ᴛᴏ ʙᴜʏ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ</b>"
                    await message.reply_text(
                        text=text.format(message.from_user.mention),
                        protect_content=True,
                        reply_markup=InlineKeyboardMarkup(btn)
                    )
                    return
                    
            reply_markup = await build_stream_reply_markup(message.from_user.id, file_id)

            msg = await client.send_cached_media(
                chat_id=message.from_user.id,
                file_id=file_id,
                caption=f_caption,
                protect_content=True if pre == 'allfilesp' else False,
                reply_markup=reply_markup
            )
            filesarr.append(msg)
        k = await client.send_message(chat_id = message.from_user.id, text=f"<blockquote><b><u>❗️❗️❗️IMPORTANT❗️️❗️❗️</u></b>\n\nᴛʜɪs ᴍᴇssᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ɪɴ <b><u>1 mins</u> 🫥 <i></b>(ᴅᴜᴇ ᴛᴏ ᴄᴏᴘʏʀɪɢʜᴛ ɪssᴜᴇs)</i>.\n\n<b><i>ᴘʟᴇᴀsᴇ ғᴏʀᴡᴀʀᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ sᴀᴠᴇᴅ ᴍᴇssᴀɢᴇs ᴏʀ ᴀɴʏ ᴘʀɪᴠᴀᴛᴇ ᴄʜᴀᴛ.</i></b></blockquote>")
        await asyncio.sleep(60)
        for x in filesarr:
            await x.delete()
        await k.edit_text("<b>✅ ʏᴏᴜʀ ᴍᴇssᴀɢᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ</b>")
        return    
        
    elif data.startswith("files"):
        user = message.from_user.id
        if temp.SHORT.get(user)==None:
            await message.reply_text(text="<b>Please Search Again in Group</b>")
        else:
            chat_id = temp.SHORT.get(user)
        settings = await get_settings(chat_id)
        pre = 'filep' if settings['file_secure'] else 'file'
        if settings['is_shortlink'] and not await db.has_premium_access(user):
            g = await get_shortlink(chat_id, f"https://telegram.me/{temp.U_NAME}?start={pre}_{file_id}")
            btn = [[
                InlineKeyboardButton('ᴅᴏᴡɴʟᴏᴀᴅ ɴᴏᴡ', url=g)
            ]]
            if settings['tutorial']:
                btn.append([InlineKeyboardButton('ʜᴏᴡ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ', url=await get_tutorial(chat_id))])
            text = "<b>✅ ʏᴏᴜʀ ғɪʟᴇ ʀᴇᴀᴅʏ ᴄʟɪᴄᴋ ᴏɴ ᴅᴏᴡɴʟᴏᴀᴅ ɴᴏᴡ ʙᴜᴛᴛᴏɴ ᴛʜᴇɴ ᴏᴘᴇɴ ʟɪɴᴋ ᴛᴏ ɢᴇᴛ ғɪʟᴇ\n\n</b>"
            if PREMIUM_AND_REFERAL_MODE == True:
                text += "<b>ɪғ ʏᴏᴜ ᴡᴀɴᴛ ᴅɪʀᴇᴄᴛ ғɪʟᴇꜱ ᴡɪᴛʜᴏᴜᴛ ᴀɴʏ ᴏᴘᴇɴɪɴɢ ʟɪɴᴋ ᴀɴᴅ ᴡᴀᴛᴄʜɪɴɢ ᴀᴅs ᴛʜᴇɴ ʙᴜʏ ʙᴏᴛ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ ☺️\n\n💶 ꜱᴇɴᴅ /plan ᴛᴏ ʙᴜʏ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ</b>"
            k = await client.send_message(chat_id=message.from_user.id, text=text, reply_markup=InlineKeyboardMarkup(btn))
            await asyncio.sleep(1200)
            await k.edit("<b>✅ ʏᴏᴜʀ ᴍᴇssᴀɢᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ</b>")
            return
    user = message.from_user.id
    files_ = await get_file_details(file_id)           
    if not files_:
        try:
            pre, file_id = ((base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))).decode("ascii")).split("_", 1)
        except Exception as e:
            logger.error(f"[start-fallback] Unrecognized /start payload data={data!r} (top-level file_id={file_id!r}, command={message.command!r}): {e}")
            return await message.reply_text("<b>❌ File not found or link is invalid. Please get a fresh link.</b>")
        try:
            if not await db.has_premium_access(message.from_user.id):
                if not await check_verification(client, message.from_user.id) and VERIFY == True:
                    btn = [[
                        InlineKeyboardButton("ᴠᴇʀɪғʏ", url=await get_token(client, message.from_user.id, f"https://telegram.me/{temp.U_NAME}?start=", pending_data=(f"{pre}_{file_id}" if pre else file_id)))
                    ],[
                        InlineKeyboardButton("ʜᴏᴡ ᴛᴏ ᴠᴇʀɪғʏ", url=VERIFY_TUTORIAL)
                    ]]
                    text = "<b>ʜᴇʏ {} 👋,\n\nʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴠᴇʀɪғɪᴇᴅ ᴛᴏᴅᴀʏ, ᴘʟᴇᴀꜱᴇ ᴄʟɪᴄᴋ ᴏɴ ᴠᴇʀɪғʏ & ɢᴇᴛ ᴜɴʟɪᴍɪᴛᴇᴅ ᴀᴄᴄᴇꜱꜱ ғᴏʀ ᴛᴏᴅᴀʏ</b>"
                    if PREMIUM_AND_REFERAL_MODE == True:
                        text += "<b>ɪғ ʏᴏᴜ ᴡᴀɴᴛ ᴅɪʀᴇᴄᴛ ғɪʟᴇꜱ ᴡɪᴛʜᴏᴜᴛ ᴀɴʏ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴꜱ ᴛʜᴇɴ ʙᴜʏ ʙᴏᴛ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ ☺️\n\n💶 ꜱᴇɴᴅ /plan ᴛᴏ ʙᴜʏ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ</b>"
                    await message.reply_text(
                        text=text.format(message.from_user.mention),
                        protect_content=True,
                        reply_markup=InlineKeyboardMarkup(btn)
                    )
                    return
                   
            reply_markup = await build_stream_reply_markup(message.from_user.id, file_id)

            msg = await client.send_cached_media(
                chat_id=message.from_user.id,
                file_id=file_id,
                protect_content=True if pre == 'filep' else False,
                reply_markup=reply_markup
            )
            filetype = msg.media
            file = getattr(msg, filetype.value)
            title = file.file_name
            size=get_size(file.file_size)
            f_caption = f"<code>{title}</code>"
            if CUSTOM_FILE_CAPTION:
                try:
                    f_caption=CUSTOM_FILE_CAPTION.format(file_name= '' if title is None else title, file_size='' if size is None else size, file_lang=format_caption_language(title), file_duration=extract_duration(None), file_caption='')
                except:
                    return
            await msg.edit_caption(caption=f_caption)
            btn = [[InlineKeyboardButton("✅ ɢᴇᴛ ғɪʟᴇ ᴀɢᴀɪɴ ✅", callback_data=f'del#{file_id}')]]
            k = await msg.reply(text=f"<blockquote><b><u>❗️❗️❗️IMPORTANT❗️️❗️❗️</u></b>\n\nᴛʜɪs ᴍᴇssᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ɪɴ <b><u>1 mins</u> 🫥 <i></b>(ᴅᴜᴇ ᴛᴏ ᴄᴏᴘʏʀɪɢʜᴛ ɪssᴜᴇs)</i>.\n\n<b><i>ᴘʟᴇᴀsᴇ ғᴏʀᴡᴀʀᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ sᴀᴠᴇᴅ ᴍᴇssᴀɢᴇs ᴏʀ ᴀɴʏ ᴘʀɪᴠᴀᴛᴇ ᴄʜᴀᴛ.</i></b></blockquote>")
            await asyncio.sleep(60)
            await msg.delete()
            await k.edit_text("<b>✅ ʏᴏᴜʀ ᴍᴇssᴀɢᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ɪғ ʏᴏᴜ ᴡᴀɴᴛ ᴀɢᴀɪɴ ᴛʜᴇɴ ᴄʟɪᴄᴋ ᴏɴ ʙᴇʟᴏᴡ ʙᴜᴛᴛᴏɴ</b>",reply_markup=InlineKeyboardMarkup(btn))
            return
        except:
            pass
        return await message.reply('No such file exist.')
    files = files_
    title = files["file_name"]
    size=get_size(files["file_size"])
    f_caption=files["caption"]
    if CUSTOM_FILE_CAPTION:
        try:
            f_caption=CUSTOM_FILE_CAPTION.format(file_name= '' if title is None else title, file_size='' if size is None else size, file_lang=format_caption_language(title, f_caption), file_duration=extract_duration(f_caption), file_caption='' if f_caption is None else f_caption)
        except:
            f_caption=f_caption
    if f_caption is None:
        f_caption = f"{' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@'), files['file_name'].split()))}"
    if not await db.has_premium_access(message.from_user.id):
        if not await check_verification(client, message.from_user.id) and VERIFY == True:
            btn = [[
                InlineKeyboardButton("ᴠᴇʀɪғʏ", url=await get_token(client, message.from_user.id, f"https://telegram.me/{temp.U_NAME}?start=", pending_data=(f"{pre}_{file_id}" if pre else file_id)))
            ],[
                InlineKeyboardButton("ʜᴏᴡ ᴛᴏ ᴠᴇʀɪғʏ", url=VERIFY_TUTORIAL)
            ]]
            text = "<b>ʜᴇʏ {} 👋,\n\nʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴠᴇʀɪғɪᴇᴅ ᴛᴏᴅᴀʏ, ᴘʟᴇᴀꜱᴇ ᴄʟɪᴄᴋ ᴏɴ ᴠᴇʀɪғʏ & ɢᴇᴛ ᴜɴʟɪᴍɪᴛᴇᴅ ᴀᴄᴄᴇꜱꜱ ғᴏʀ ᴛᴏᴅᴀʏ</b>"
            if PREMIUM_AND_REFERAL_MODE == True:
                text += "<b>ɪғ ʏᴏᴜ ᴡᴀɴᴛ ᴅɪʀᴇᴄᴛ ғɪʟᴇꜱ ᴡɪᴛʜᴏᴜᴛ ᴀɴʏ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴꜱ ᴛʜᴇɴ ʙᴜʏ ʙᴏᴛ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ ☺️\n\n💶 ꜱᴇɴᴅ /plan ᴛᴏ ʙᴜʏ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ</b>"
            await message.reply_text(
                text=text.format(message.from_user.mention),
                protect_content=True,
                reply_markup=InlineKeyboardMarkup(btn)
            )
            return
            
    reply_markup = await build_stream_reply_markup(message.from_user.id, file_id)

    try:
        msg = await client.send_cached_media(
            chat_id=message.from_user.id,
            file_id=file_id,
            caption=f_caption,
            protect_content=True if pre == 'filep' else False,
            reply_markup=reply_markup
        )
    except MediaEmpty:
        await message.reply("❌ <b>File is no longer available.</b> The source file may have been deleted from the database channel.")
        return
    btn = [[InlineKeyboardButton("✅ ɢᴇᴛ ғɪʟᴇ ᴀɢᴀɪɴ ✅", callback_data=f'del#{file_id}')]]
    k = await msg.reply(text=f"<blockquote><b><u>❗️❗️❗️IMPORTANT❗️️❗️❗️</u></b>\n\nᴛʜɪs ᴍᴇssᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ɪɴ <b><u>1 mins</u> 🫥 <i></b>(ᴅᴜᴇ ᴛᴏ ᴄᴏᴘʏʀɪɢʜᴛ ɪssᴜᴇs)</i>.\n\n<b><i>ᴘʟᴇᴀsᴇ ғᴏʀᴡᴀʀᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ sᴀᴠᴇᴅ ᴍᴇssᴀɢᴇs ᴏʀ ᴀɴʏ ᴘʀɪᴠᴀᴛᴇ ᴄʜᴀᴛ.</i></b></blockquote>")
    await asyncio.sleep(60)
    await msg.delete()
    await k.edit_text("<b>✅ ʏᴏᴜʀ ᴍᴇssᴀɢᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ɪғ ʏᴏᴜ ᴡᴀɴᴛ ᴀɢᴀɪɴ ᴛʜᴇɴ ᴄʟɪᴄᴋ ᴏɴ ʙᴇʟᴏᴡ ʙᴜᴛᴛᴏɴ</b>",reply_markup=InlineKeyboardMarkup(btn))
    return   

@Client.on_callback_query(filters.regex(r"^gfnext#"))
async def getfile_next(client, callback_query):
     _, query, grpid, offset = callback_query.data.split("#")
     grpid = int(grpid)
     offset = int(offset)
     files, next_offset, total_results = await get_search_results(grpid, query.lower(), offset=offset, max_results=8, filter=True)
     if not files:
         return await callback_query.answer("No more files!", show_alert=True)
     settings = await get_settings(grpid)
     pre = 'filep' if settings.get('file_secure', False) else 'file'
     btn = []
     for file in files:
         btn.append([
             InlineKeyboardButton(
                 text=f"[{get_size(file['file_size'])}] {' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@') and not x.startswith('www.'), file['file_name'].split()))}",
                 callback_data=f'{pre}#{file["file_id"]}'
             )
         ])
     nav = []
     if offset > 0:
         prev_offset = max(0, offset - 8)
         nav.append(InlineKeyboardButton("⬅️ PREV", callback_data=f"gfnext#{query}#{grpid}#{prev_offset}"))
     if next_offset:
         nav.append(InlineKeyboardButton("NEXT ➡️", callback_data=f"gfnext#{query}#{grpid}#{next_offset}"))
     if nav:
         btn.append(nav)
     await callback_query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
     await callback_query.answer()

@Client.on_message(filters.command('channel') & filters.user(ADMINS))
async def channel_info(bot, message):
    text = '📑 **Indexed channels/groups**\n'
    for channel in CHANNELS:
        chat = await bot.get_chat(channel)
        if chat.username:
            text += '\n@' + chat.username
        else:
            text += '\n' + chat.title or chat.first_name

    text += f'\n\n**Total:** {len(CHANNELS)}'

    if len(text) < 4096:
        await message.reply(text)
    else:
        file = 'Indexed channels.txt'
        with open(file, 'w') as f:
            f.write(text)
        await message.reply_document(file)
        os.remove(file)

# movie update on off start

@Client.on_message(filters.private & filters.command("movie_update") & filters.user(ADMINS))
async def set_movie_update_notification(client, message):
    bot_id = client.me.id
    try:
        option = message.text.split(" ", 1)[1].strip().lower()
        enable_status = option in ['on', 'true']
    except (IndexError, ValueError):
        await message.reply_text("<b>💔 Invalid option. Please send 'on' or 'off' after the command.</b>")
        return
    try:
        await db.update_movie_update_status(bot_id, enable_status)
        response_text = (
            "<b>ᴍᴏᴠɪᴇ ᴜᴘᴅᴀᴛᴇ ɴᴏᴛɪꜰɪᴄᴀᴛɪᴏɴ ᴇɴᴀʙʟᴇᴅ ✅</b>" if enable_status
            else "<b>ᴍᴏᴠɪᴇ ᴜᴘᴅᴀᴛᴇ ɴᴏᴛɪꜰɪᴄᴀᴛɪᴏɴ ᴅɪꜱᴀʙʟᴇᴅ ❌</b>"
        )
        await message.reply_text(response_text)
    except Exception as e:
        logger.error(f"Error in set_movie_update_notification: {e}")
        await message.reply_text(f"<b>❗ An error occurred: {e}</b>")

#end here 


@Client.on_message(filters.command('extend_premium') & filters.user(ADMINS))
async def extend_premium_cmd(client, message):
    """Extend EVERY currently-active premium user's expiry by N days, and DM
    each of them a notice — with a reason line only if one was given.

    Usage:
      /extend_premium 2                         -> extends 2 days, no reason shown
      /extend_premium 2 Server was down today   -> extends 2 days, shows the reason
      /extend_premium 1day Server down          -> "1day" also works
    """
    if len(message.command) < 2:
        return await message.reply_text(
            "<b>Usage:</b> <code>/extend_premium 2 [reason]</code>\n\n"
            "Extends every currently-active premium user's expiry by that many days.\n"
            "The reason is optional — if you don't add one, no reason line is shown to users."
        )

    days_match = re.search(r'\d+', message.command[1])
    if not days_match:
        return await message.reply_text(
            "<b>❌ Please specify a valid number of days.</b>\n\nExample: <code>/extend_premium 2 Server down for maintenance</code>"
        )
    days = int(days_match.group())
    if days <= 0:
        return await message.reply_text("<b>❌ Days must be a positive number.</b>")

    reason = ""
    if len(message.command) > 2:
        reason = " ".join(message.command[2:]).strip()

    sts = await message.reply_text(f"<b>⏳ Extending premium by {days} day(s) for all premium users...</b>")

    try:
        extended_ids = await db.extend_all_premium_users(days)
    except Exception as e:
        logger.error(f"extend_all_premium_users failed: {e}")
        return await sts.edit_text("<b>❌ Something went wrong while extending premium. Check the logs.</b>")

    if not extended_ids:
        return await sts.edit_text("<b>ℹ️ No active premium users found to extend.</b>")

    day_word = "day" if days == 1 else "days"
    notice = f"<b>🎉 Your premium has been extended by {days} {day_word}!</b>"
    if reason:
        notice += f"\n\n<b>Reason:</b> {reason}"

    sent = failed = 0
    for uid in extended_ids:
        try:
            await client.send_message(uid, notice)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # gentle pacing to avoid flood-wait

    await sts.edit_text(
        f"<b>✅ Extended premium by {days} {day_word} for {len(extended_ids)} user(s).</b>\n\n"
        f"📨 Notified: {sent}\n"
        f"⚠️ Failed to notify (blocked bot etc.): {failed}"
    )


@Client.on_message(filters.command('verification') & filters.user(ADMINS))
async def verification_list_cmd(client, message):
    """Owner-only: lists every user whose daily verification is still
    valid today — name, id, and their verified-until date."""
    try:
        users = await db.get_all_verified_users()
    except Exception as e:
        logger.error(f"verification_list: get_all_verified_users failed: {e}")
        return await message.reply_text("<b>❌ Something went wrong fetching the verification list. Check the logs.</b>")

    if not users:
        return await message.reply_text("<b>ℹ️ No users are verified today.</b>")

    lines = [f"<b>✅ Verified Users — {len(users)} today</b>\n"]
    for i, u in enumerate(users, 1):
        uid = u.get("id")
        verified_until = u.get("verified_until")

        try:
            user_obj = await client.get_users(uid)
            name = user_obj.mention
        except Exception:
            name = "Unknown"

        lines.append(f"{i}. {name} (<code>{uid}</code>)\n   ✅ Verified until {verified_until}")
        await asyncio.sleep(0.03)  # gentle pacing for client.get_users calls

    text = "\n\n".join(lines)
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)] or [text]
    for chunk in chunks:
        await message.reply_text(chunk, disable_web_page_preview=True)


@Client.on_message(filters.command('premium_list') & filters.user(ADMINS))
async def premium_list_cmd(client, message):
    """Owner-only: lists every user with currently-active premium — name,
    id, and remaining time — soonest-expiring first."""
    if PREMIUM_AND_REFERAL_MODE == False:
        return

    try:
        users = await db.get_all_premium_users()
    except Exception as e:
        logger.error(f"premium_list: get_all_premium_users failed: {e}")
        return await message.reply_text("<b>❌ Something went wrong fetching the premium list. Check the logs.</b>")

    if not users:
        return await message.reply_text("<b>ℹ️ No active premium users right now.</b>")

    now = datetime.datetime.now()
    lines = [f"<b>👑 Premium Users — {len(users)} active</b>\n"]
    for i, u in enumerate(users, 1):
        uid = u.get("id")
        expiry = u.get("expiry_time")
        remaining = expiry - now
        days = remaining.days
        hours, rem_secs = divmod(remaining.seconds, 3600)
        minutes = rem_secs // 60

        try:
            user_obj = await client.get_users(uid)
            name = user_obj.mention
        except Exception:
            name = "Unknown"

        lines.append(
            f"{i}. {name} (<code>{uid}</code>)\n"
            f"   ⏳ {days}d {hours}h {minutes}m left — expires {expiry.strftime('%Y-%m-%d %H:%M')}"
        )
        await asyncio.sleep(0.03)  # gentle pacing for client.get_users calls

    text = "\n\n".join(lines)
    # Telegram caps messages at 4096 chars — split into safe chunks if the list is long.
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)] or [text]
    for chunk in chunks:
        await message.reply_text(chunk, disable_web_page_preview=True)


async def premium_expiry_notifier(client):
    """Background loop — call once at bot startup, e.g.:
        asyncio.create_task(premium_expiry_notifier(app))
    right after your Client is created/started in the main bot file.

    Every hour, checks for:
      1) Active-premium users expiring within the next 24h -> sends a
         one-time 'ends tomorrow, renew with /plan' reminder.
      2) Users whose premium expired within the last 24h -> sends a
         one-time thank-you + 'buy again with /plan' message.
    Each user is only ever notified once per cycle via the
    expiry_reminder_sent / expired_notified flags in their DB record,
    which get reset automatically whenever their premium is renewed.

    The two checks below are independent try/except blocks on purpose —
    a failure fetching/sending one (e.g. the reminder list) must never
    prevent the other (e.g. the expired list) from running in the same
    cycle."""
    # Only send the "ends tomorrow" reminder when a user genuinely has
    # roughly a day left — NOT for short trials (e.g. a 2 min test grant)
    # or a plan that's about to expire in the next few minutes, where
    # saying "tomorrow" would be misleading. Those users still get the
    # "premium has ended" message once they actually lapse.
    REMINDER_MIN_LEAD_SECONDS = 20 * 3600  # 20h — so it fires once, ~20-24h out

    while True:
        try:
            expiring_soon = await db.get_users_expiring_within(86400)  # next 24h
            now = datetime.datetime.now()
            for u in expiring_soon:
                uid = u.get("id")
                exp = u.get("expiry_time")
                remaining = (exp - now).total_seconds() if exp else 0
                if remaining < REMINDER_MIN_LEAD_SECONDS:
                    continue  # too soon for a "tomorrow" message to make sense
                try:
                    await client.send_message(
                        uid,
                        "<b>⏳ Your Goflix premium ends tomorrow!</b>\n\n"
                        "Renew now to keep your ad-free, direct-download access.\n\n"
                        "💎 Send /plan to buy again."
                    )
                except Exception as e:
                    logger.error(f"[premium_expiry_notifier] reminder failed for {uid}: {e}")
                try:
                    await db.mark_expiry_reminder_sent(uid)
                except Exception as e:
                    logger.error(f"[premium_expiry_notifier] mark_expiry_reminder_sent failed for {uid}: {e}")
                await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"[premium_expiry_notifier] reminder-block error: {e}")

        try:
            recently_expired = await db.get_recently_expired_users(86400)  # last 24h
            for u in recently_expired:
                uid = u.get("id")
                try:
                    await client.send_message(
                        uid,
                        "😔 <b>Your Goflix Premium Has Ended</b>\n\n"
                        "All premium features are now closed — no more ad-free, "
                        "no more direct downloads, no more high-speed links.\n\n"
                        "Whenever you're ready to come back, just send /plan to buy again.\n\n"
                        "🙏 Thank you for being a Goflix member!"
                    )
                except Exception as e:
                    logger.error(f"[premium_expiry_notifier] expired-notice failed for {uid}: {e}")
                try:
                    await db.mark_expired_notified(uid)
                except Exception as e:
                    logger.error(f"[premium_expiry_notifier] mark_expired_notified failed for {uid}: {e}")
                await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"[premium_expiry_notifier] expired-block error: {e}")

        await asyncio.sleep(300)  # re-check every 5 minutes


@Client.on_message(filters.command('logs') & filters.user(ADMINS))
async def log_file(bot, message):
    try:
        await message.reply_document('TELEGRAM BOT.LOG')
    except Exception as e:
        await message.reply(str(e))

@Client.on_message(filters.command('delete') & filters.user(ADMINS))
async def delete(bot, message):
    reply = await bot.ask(message.from_user.id, "Now Send Me Media Which You Want to delete")
    if reply.media:
        msg = await message.reply("Processing...⏳", quote=True)
    else:
        await message.reply('Send Me Video, File Or Document.', quote=True)
        return
    for file_type in ("document", "video", "audio"):
        media = getattr(reply, file_type, None)
        if media is not None:
            break
    else:
        await msg.edit('This is not supported file format')
        return
    
    file_id, file_ref, *_ = unpack_new_file_id(media.file_id)  # <-- FIXED
    result = col.delete_one({
        'file_id': file_id,
    })
    if not result.deleted_count:
        result = sec_col.delete_one({
            'file_id': file_id,
        })
    if result.deleted_count:
        await msg.edit('File is successfully deleted from database')
    else:
        file_name = re.sub(r"(_|\-|\.|\+)", " ", str(media.file_name))
        unwanted_chars = ['[', ']', '(', ')']
        for char in unwanted_chars:
            file_name = file_name.replace(char, '')
        file_name = ' '.join(filter(lambda x: not x.startswith('@'), file_name.split()))
    
        result = col.delete_many({
            'file_name': file_name,
            'file_size': media.file_size
        })
        if not result.deleted_count:
            result = sec_col.delete_many({
                'file_name': file_name,
                'file_size': media.file_size
            })
        if result.deleted_count:
            await msg.edit('File is successfully deleted from database')
        else:
            result = col.delete_many({
                'file_name': media.file_name,
                'file_size': media.file_size
            })
            if not result.deleted_count:
                result = sec_col.delete_many({
                    'file_name': media.file_name,
                    'file_size': media.file_size
                })
            if result.deleted_count:
                await msg.edit('File is successfully deleted from database')
            else:
                await msg.edit('File not found in database')


@Client.on_message(filters.command('deleteall') & filters.user(ADMINS))
async def delete_all_index(bot, message):
    await message.reply_text(
        'This will delete all indexed files.\nDo you want to continue??',
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(text="YES", callback_data="autofilter_delete")
            ],[
                InlineKeyboardButton(text="CANCEL", callback_data="close_data")
            ]]
        ),
        quote=True,
    )


@Client.on_callback_query(filters.regex(r'^autofilter_delete'))
async def delete_all_index_confirm(bot, query):
    col.drop()
    sec_col.drop()
    await query.answer('Piracy Is Crime')
    await query.message.edit('Succesfully Deleted All The Indexed Files.')


@Client.on_message(filters.command('settings'))
async def settings(client, message):
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return await message.reply(f"You are anonymous admin. Use /connect {message.chat.id} in PM")
    chat_type = message.chat.type

    if chat_type == enums.ChatType.PRIVATE:
        grpid = await active_connection(str(userid))
        if grpid is not None:
            grp_id = grpid
            try:
                chat = await client.get_chat(grpid)
                title = chat.title
            except:
                await message.reply_text("Make sure I'm present in your group!!", quote=True)
                return
        else:
            await message.reply_text("I'm not connected to any groups!", quote=True)
            return

    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grp_id = message.chat.id
        title = message.chat.title

    else:
        return

    st = await client.get_chat_member(grp_id, userid)
    if (
            st.status != enums.ChatMemberStatus.ADMINISTRATOR
            and st.status != enums.ChatMemberStatus.OWNER
            and str(userid) not in ADMINS
    ):
        return
    
    settings = await get_settings(grp_id)

    try:
        if settings['max_btn']:
            settings = await get_settings(grp_id)
    except KeyError:
    #    await save_group_settings(grp_id, 'fsub', None)
        await save_group_settings(grp_id, 'max_btn', False)
        settings = await get_settings(grp_id)
    if 'is_shortlink' not in settings.keys():
        await save_group_settings(grp_id, 'is_shortlink', False)
    else:
        pass

    if settings is not None:
        buttons = [
            [
                InlineKeyboardButton(
                    'Rᴇsᴜʟᴛ Pᴀɢᴇ',
                    callback_data=f'setgs#button#{settings["button"]}#{grp_id}',
                ),
                InlineKeyboardButton(
                    'Bᴜᴛᴛᴏɴ' if settings["button"] else 'Tᴇxᴛ',
                    callback_data=f'setgs#button#{settings["button"]}#{grp_id}',
                ),
            ],
            [
                InlineKeyboardButton(
                    'Pʀᴏᴛᴇᴄᴛ Cᴏɴᴛᴇɴᴛ',
                    callback_data=f'setgs#file_secure#{settings["file_secure"]}#{grp_id}',
                ),
                InlineKeyboardButton(
                    '✔ Oɴ' if settings["file_secure"] else '✘ Oғғ',
                    callback_data=f'setgs#file_secure#{settings["file_secure"]}#{grp_id}',
                ),
            ],
            [
                InlineKeyboardButton(
                    'Iᴍᴅʙ',
                    callback_data=f'setgs#imdb#{settings["imdb"]}#{grp_id}',
                ),
                InlineKeyboardButton(
                    '✔ Oɴ' if settings["imdb"] else '✘ Oғғ',
                    callback_data=f'setgs#imdb#{settings["imdb"]}#{grp_id}',
                ),
            ],
            [
                InlineKeyboardButton(
                    'Sᴘᴇʟʟ Cʜᴇᴄᴋ',
                    callback_data=f'setgs#spell_check#{settings["spell_check"]}#{grp_id}',
                ),
                InlineKeyboardButton(
                    '✔ Oɴ' if settings["spell_check"] else '✘ Oғғ',
                    callback_data=f'setgs#spell_check#{settings["spell_check"]}#{grp_id}',
                ),
            ],
            [
                InlineKeyboardButton(
                    'Wᴇʟᴄᴏᴍᴇ Msɢ',
                    callback_data=f'setgs#welcome#{settings["welcome"]}#{grp_id}',
                ),
                InlineKeyboardButton(
                    '✔ Oɴ' if settings["welcome"] else '✘ Oғғ',
                    callback_data=f'setgs#welcome#{settings["welcome"]}#{grp_id}',
                ),
            ],
            [
                InlineKeyboardButton(
                    'Aᴜᴛᴏ-Dᴇʟᴇᴛᴇ',
                    callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{grp_id}',
                ),
                InlineKeyboardButton(
                    '10 Mɪɴs' if settings["auto_delete"] else '✘ Oғғ',
                    callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{grp_id}',
                ),
            ],
            [
                InlineKeyboardButton(
                    'Aᴜᴛᴏ-Fɪʟᴛᴇʀ',
                    callback_data=f'setgs#auto_ffilter#{settings["auto_ffilter"]}#{grp_id}',
                ),
                InlineKeyboardButton(
                    '✔ Oɴ' if settings["auto_ffilter"] else '✘ Oғғ',
                    callback_data=f'setgs#auto_ffilter#{settings["auto_ffilter"]}#{grp_id}',
                ),
            ],
            [
                InlineKeyboardButton(
                    'Mᴀx Bᴜᴛᴛᴏɴs',
                    callback_data=f'setgs#max_btn#{settings["max_btn"]}#{grp_id}',
                ),
                InlineKeyboardButton(
                    '10' if settings["max_btn"] else f'{MAX_B_TN}',
                    callback_data=f'setgs#max_btn#{settings["max_btn"]}#{grp_id}',
                ),
            ],
            [
                InlineKeyboardButton(
                    'ShortLink',
                    callback_data=f'setgs#is_shortlink#{settings["is_shortlink"]}#{grp_id}',
                ),
                InlineKeyboardButton(
                    '✔ Oɴ' if settings["is_shortlink"] else '✘ Oғғ',
                    callback_data=f'setgs#is_shortlink#{settings["is_shortlink"]}#{grp_id}',
                ),
            ],
        ]
        btn = [[
            InlineKeyboardButton("Oᴘᴇɴ Hᴇʀᴇ ↓", callback_data=f"opnsetgrp#{grp_id}"),
            InlineKeyboardButton("Oᴘᴇɴ Iɴ PM ⇲", callback_data=f"opnsetpm#{grp_id}")
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        if chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            await message.reply_text(
                text="<b>Dᴏ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴏᴘᴇɴ sᴇᴛᴛɪɴɢs ʜᴇʀᴇ ?</b>",
                reply_markup=InlineKeyboardMarkup(btn),
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.HTML,
                reply_to_message_id=message.id
            )
        else:
            await message.reply_text(
                text=f"<b>Cʜᴀɴɢᴇ Yᴏᴜʀ Sᴇᴛᴛɪɴɢs Fᴏʀ {title} As Yᴏᴜʀ Wɪsʜ ⚙</b>",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.HTML,
                reply_to_message_id=message.id
            )



@Client.on_message(filters.command('set_template'))
async def save_template(client, message):
    sts = await message.reply("Checking template")
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return await message.reply(f"You are anonymous admin. Use /connect {message.chat.id} in PM")
    chat_type = message.chat.type

    if chat_type == enums.ChatType.PRIVATE:
        grpid = await active_connection(str(userid))
        if grpid is not None:
            grp_id = grpid
            try:
                chat = await client.get_chat(grpid)
                title = chat.title
            except:
                await message.reply_text("Make sure I'm present in your group!!", quote=True)
                return
        else:
            await message.reply_text("I'm not connected to any groups!", quote=True)
            return

    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grp_id = message.chat.id
        title = message.chat.title

    else:
        return

    st = await client.get_chat_member(grp_id, userid)
    if (
            st.status != enums.ChatMemberStatus.ADMINISTRATOR
            and st.status != enums.ChatMemberStatus.OWNER
            and str(userid) not in ADMINS
    ):
        return

    if len(message.command) < 2:
        return await sts.edit("No Input!!")
    template = message.text.split(" ", 1)[1]
    await save_group_settings(grp_id, 'template', template)
    await sts.edit(f"Successfully changed template for {title} to\n\n{template}")


@Client.on_message((filters.command(["request", "Request"]) | filters.regex("#request") | filters.regex("#Request")) & (filters.group | filters.private))
async def requests(bot, message):
    if REQST_CHANNEL is None: return # Must add REQST_CHANNEL to use this feature
    requester_is_premium = PREMIUM_AND_REFERAL_MODE and await is_premium_user(message.from_user.id)
    request_prefix = "🌟🔥 <u><b>PREMIUM USER REQUEST — HANDLE FAST</b></u> 🔥🌟\n\n" if requester_is_premium else ""
    if message.reply_to_message:
        chat_id = message.chat.id
        reporter = str(message.from_user.id)
        mention = message.from_user.mention
        success = True
        content = message.reply_to_message.text
        try:
            if REQST_CHANNEL is not None:
                btn_row = [InlineKeyboardButton('Show Options', callback_data=f'show_option#{reporter}')]
                if message.reply_to_message.link:
                    btn_row.insert(0, InlineKeyboardButton('View Request', url=message.reply_to_message.link))
                btn = [btn_row]
                reported_post = await bot.send_message(chat_id=REQST_CHANNEL, text=f"{request_prefix}<b>𝖱𝖾𝗉𝗈𝗋𝗍𝖾𝗋 : {mention} ({reporter})\n\n𝖬𝖾𝗌𝗌𝖺𝗀𝖾 : {content}</b>", reply_markup=InlineKeyboardMarkup(btn))
                if requester_is_premium:
                    try:
                        await bot.pin_chat_message(REQST_CHANNEL, reported_post.id, disable_notification=False)
                    except Exception:
                        pass
                success = True
            elif len(content) >= 3:
                for admin in ADMINS:
                    btn_row = [InlineKeyboardButton('Show Options', callback_data=f'show_option#{reporter}')]
                    if message.reply_to_message.link:
                        btn_row.insert(0, InlineKeyboardButton('View Request', url=message.reply_to_message.link))
                    btn = [btn_row]
                    reported_post = await bot.send_message(chat_id=admin, text=f"{request_prefix}<b>𝖱𝖾𝗉𝗈𝗋𝗍𝖾𝗋 : {mention} ({reporter})\n\n𝖬𝖾𝗌𝗌𝖺𝗀𝖾 : {content}</b>", reply_markup=InlineKeyboardMarkup(btn))
                    success = True
            else:
                if len(content) < 3:
                    await message.reply_text("<b>You must type about your request [Minimum 3 Characters]. Requests can't be empty.</b>\n\n<b>Example:</b> /request kantara")
            if len(content) < 3:
                success = False
        except Exception as e:
            await message.reply_text(f"Error: {e}")
            pass
        
    elif message.text:
        chat_id = message.chat.id
        reporter = str(message.from_user.id)
        mention = message.from_user.mention
        success = True
        content = message.text
        
        # Strip command keywords AND bot username mention
        import re
        bot_username = (await bot.get_me()).username
        keywords = ["#request", "/request", "#Request", "/Request"]
        for keyword in keywords:
            if keyword in content:
                content = content.replace(keyword, "")
        # Remove @BotUsername from content (case-insensitive)
        content = re.sub(rf"@{re.escape(bot_username)}", "", content, flags=re.IGNORECASE).strip()
        try:
            if REQST_CHANNEL is not None and len(content) >= 3:
                btn_row = [InlineKeyboardButton('Show Options', callback_data=f'show_option#{reporter}')]
                if message.link:
                    btn_row.insert(0, InlineKeyboardButton('View Request', url=message.link))
                btn = [btn_row]
                reported_post = await bot.send_message(chat_id=REQST_CHANNEL, text=f"{request_prefix}<b>𝖱𝖾𝗉𝗈𝗋𝗍𝖾𝗋 : {mention} ({reporter})\n\n𝖬𝖾𝗌𝗌𝖺𝗀𝖾 : {content}</b>", reply_markup=InlineKeyboardMarkup(btn))
                if requester_is_premium:
                    try:
                        await bot.pin_chat_message(REQST_CHANNEL, reported_post.id, disable_notification=False)
                    except Exception:
                        pass
                success = True
            elif len(content) >= 3:
                for admin in ADMINS:
                    btn_row = [InlineKeyboardButton('Show Options', callback_data=f'show_option#{reporter}')]
                    if message.link:
                        btn_row.insert(0, InlineKeyboardButton('View Request', url=message.link))
                    btn = [btn_row]
                    reported_post = await bot.send_message(chat_id=admin, text=f"{request_prefix}<b>𝖱𝖾𝗉𝗈𝗋𝗍𝖾𝗋 : {mention} ({reporter})\n\n𝖬𝖾𝗌𝗌𝖺𝗀𝖾 : {content}</b>", reply_markup=InlineKeyboardMarkup(btn))
                    success = True
            else:
                if len(content) < 3:
                    await message.reply_text("<b>You must type about your request [Minimum 3 Characters]. Requests can't be empty.</b>\n\n<b>Example:</b> /request kantara")
            if len(content) < 3:
                success = False
        except Exception as e:
            await message.reply_text(f"Error: {e}")
            pass

    else:
        success = False
    
    if success:
        link = await bot.create_chat_invite_link(int(REQST_CHANNEL))
        btn_row = [InlineKeyboardButton('Join Channel', url=link.invite_link)]
        if reported_post.link:
            btn_row.append(InlineKeyboardButton('View Request', url=reported_post.link))
        btn = [btn_row]
        confirm_text = (
            "<b>🌟 Your PREMIUM request has been fast-tracked & pinned! Our team will handle it on priority.\n\nJoin Channel First & View Request</b>"
            if requester_is_premium else
            "<b>Your request has been added! Please wait for some time.\n\nJoin Channel First & View Request</b>"
        )
        await message.reply_text(confirm_text, reply_markup=InlineKeyboardMarkup(btn))
    
@Client.on_message(filters.command("send") & filters.user(ADMINS))
async def send_msg(bot, message):
    if message.reply_to_message:
        target_id = message.text.split(" ", 1)[1]
        out = "Users Saved In DB Are:\n\n"
        success = False
        try:
            user = await bot.get_users(target_id)
            users = await db.get_all_users()
            async for usr in users:
                out += f"{usr['id']}"
                out += '\n'
            if str(user.id) in str(out):
                await message.reply_to_message.copy(int(user.id))
                success = True
            else:
                success = False
            if success:
                await message.reply_text(f"<b>Your message has been successfully send to {user.mention}.</b>")
            else:
                await message.reply_text("<b>This user didn't started this bot yet !</b>")
        except Exception as e:
            await message.reply_text(f"<b>Error: {e}</b>")
    else:
        await message.reply_text("<b>Use this command as a reply to any message using the target chat id. For eg: /send userid</b>")

@Client.on_message(filters.command("deletefiles") & filters.user(ADMINS))
async def deletemultiplefiles(bot, message):
    chat_type = message.chat.type
    if chat_type != enums.ChatType.PRIVATE:
        return await message.reply_text(f"<b>Hey {message.from_user.mention}, This command won't work in groups. It only works on my PM !</b>")
    else:
        pass
    try:
        keyword = message.text.split(" ", 1)[1]
    except:
        return await message.reply_text(f"<b>Hey {message.from_user.mention}, Give me a keyword along with the command to delete files.</b>")
    k = await bot.send_message(chat_id=message.chat.id, text=f"<b>Fetching Files for your query {keyword} on DB... Please wait...</b>")
    files, total = await get_bad_files(keyword)
    await k.delete()
    #await k.edit_text(f"<b>Found {total} files for your query {keyword} !\n\nFile deletion process will start in 5 seconds !</b>")
    #await asyncio.sleep(5)
    btn = [[
       InlineKeyboardButton("Yes, Continue !", callback_data=f"killfilesdq#{keyword}")
    ],[
       InlineKeyboardButton("No, Abort operation !", callback_data="close_data")
    ]]
    await message.reply_text(
        text=f"<b>Found {total} files for your query {keyword} !\n\nDo you want to delete?</b>",
        reply_markup=InlineKeyboardMarkup(btn),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.command("shortlink"))
async def shortlink(bot, message):
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return await message.reply(f"You are anonymous admin. Turn off anonymous admin and try again this command")
    chat_type = message.chat.type
    if chat_type == enums.ChatType.PRIVATE:
        return await message.reply_text(f"<b>Hey {message.from_user.mention}, This command only works on groups !\n\n<u>Follow These Steps to Connect Shortener:</u>\n\n1. Add Me in Your Group with Full Admin Rights\n\n2. After Adding in Grp, Set your Shortener\n\nSend this command in your group\n\n—> /shortlink ""{your_shortener_website_name} {your_shortener_api}\n\n#Sample:-\n/shortlink kpslink.in CAACAgUAAxkBAAEJ4GtkyPgEzpIUC_DSmirN6eFWp4KInAACsQoAAoHSSFYub2D15dGHfy8E\n\nThat's it!!! Enjoy Earning Money 💲\n\n[[[ Trusted Earning Site - https://kpslink.in]]]\n\nIf you have any Doubts, Feel Free to Ask me - @kingvj01\n\n(Puriyala na intha contact la message pannunga - @kngvj01)</b>")
    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grpid = message.chat.id
        title = message.chat.title
    else:
        return
    data = message.text
    userid = message.from_user.id
    user = await bot.get_chat_member(grpid, userid)
    if user.status != enums.ChatMemberStatus.ADMINISTRATOR and user.status != enums.ChatMemberStatus.OWNER and str(userid) not in ADMINS:
        return await message.reply_text("<b>You don't have access to use this command!\n\nAdd Me to Your Own Group as Admin and Try This Command\n\nFor More PM Me With This Command</b>")
    else:
        pass
    try:
        command, shortlink_url, api = data.split(" ")
    except:
        return await message.reply_text("<b>Command Incomplete :(\n\nGive me a shortener website link and api along with the command !\n\nFormat: <code>/shortlink kpslink.in e3d82cdf8f9f4783c42170b515d1c271fb1c4500</code></b>")
    reply = await message.reply_text("<b>Please Wait...</b>")
    shortlink_url = re.sub(r"https?://?", "", shortlink_url)
    shortlink_url = re.sub(r"[:/]", "", shortlink_url)
    await save_group_settings(grpid, 'shortlink', shortlink_url)
    await save_group_settings(grpid, 'shortlink_api', api)
    await save_group_settings(grpid, 'is_shortlink', True)
    await reply.edit_text(f"<b>Successfully added shortlink API for {title}.\n\nCurrent Shortlink Website: <code>{shortlink_url}</code>\nCurrent API: <code>{api}</code></b>")
    
@Client.on_message(filters.command("setshortlinkoff"))
async def offshortlink(bot, message):
    chat_type = message.chat.type
    if chat_type == enums.ChatType.PRIVATE:
        return await message.reply_text("I will Work Only in group")
    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grpid = message.chat.id
        title = message.chat.title
    else:
        return
    userid = message.from_user.id
    user = await bot.get_chat_member(grpid, userid)
    if user.status != enums.ChatMemberStatus.ADMINISTRATOR and user.status != enums.ChatMemberStatus.OWNER and str(userid) not in ADMINS:
        return await message.reply_text("<b>You don't have access to use this command!\n\nAdd Me to Your Own Group as Admin and Try This Command\n\nFor More PM Me With This Command</b>")
    else:
        pass
    await save_group_settings(grpid, 'is_shortlink', False)
    # ENABLE_SHORTLINK = False
    return await message.reply_text("Successfully disabled shortlink")
    
@Client.on_message(filters.command("setshortlinkon"))
async def onshortlink(bot, message):
    chat_type = message.chat.type
    if chat_type == enums.ChatType.PRIVATE:
        return await message.reply_text("I will Work Only in group")
    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grpid = message.chat.id
        title = message.chat.title
    else:
        return
    userid = message.from_user.id
    user = await bot.get_chat_member(grpid, userid)
    if user.status != enums.ChatMemberStatus.ADMINISTRATOR and user.status != enums.ChatMemberStatus.OWNER and str(userid) not in ADMINS:
        return await message.reply_text("<b>You don't have access to use this command!\n\nAdd Me to Your Own Group as Admin and Try This Command\n\nFor More PM Me With This Command</b>")
    else:
        pass
    settings = await get_settings(grpid)
    if not settings['shortlink']:
        return await message.reply_text("**First Add Your Shortlink Url And Api By /shortlink Command, Then Turn Me On.**")
    await save_group_settings(grpid, 'is_shortlink', True)
    # ENABLE_SHORTLINK = True
    return await message.reply_text("Successfully enabled shortlink")

@Client.on_message(filters.command("shortlink_info"))
async def showshortlink(bot, message):
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return await message.reply(f"You are anonymous admin. Turn off anonymous admin and try again this command")
    chat_type = message.chat.type
    if chat_type == enums.ChatType.PRIVATE:
        return await message.reply_text(f"<b>Hey {message.from_user.mention}, This Command Only Works in Group\n\nTry this command in your own group, if you are using me in your group</b>")
    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grpid = message.chat.id
        title = message.chat.title
    else:
        return
    chat_id=message.chat.id
    userid = message.from_user.id
    user = await bot.get_chat_member(grpid, userid)
    if user.status != enums.ChatMemberStatus.ADMINISTRATOR and user.status != enums.ChatMemberStatus.OWNER and str(userid) not in ADMINS:
        return await message.reply_text("<b>Tʜɪs ᴄᴏᴍᴍᴀɴᴅ Wᴏʀᴋs Oɴʟʏ Fᴏʀ ᴛʜɪs Gʀᴏᴜᴘ Oᴡɴᴇʀ/Aᴅᴍɪɴ\n\nTʀʏ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ɪɴ ʏᴏᴜʀ Oᴡɴ Gʀᴏᴜᴘ, Iғ Yᴏᴜ Aʀᴇ Usɪɴɢ Mᴇ Iɴ Yᴏᴜʀ Gʀᴏᴜᴘ</b>")
    else:
        settings = await get_settings(chat_id) #fetching settings for group
        if 'shortlink' in settings.keys() and 'tutorial' in settings.keys():
            su = settings['shortlink']
            sa = settings['shortlink_api']
            st = settings['tutorial']
            return await message.reply_text(f"<b>Shortlink Website: <code>{su}</code>\n\nApi: <code>{sa}</code>\n\nTutorial: <code>{st}</code></b>")
        elif 'shortlink' in settings.keys() and 'tutorial' not in settings.keys():
            su = settings['shortlink']
            sa = settings['shortlink_api']
            return await message.reply_text(f"<b>Shortener Website: <code>{su}</code>\n\nApi: <code>{sa}</code>\n\nTutorial Link Not Connected\n\nYou can Connect Using /set_tutorial command</b>")
        elif 'shortlink' not in settings.keys() and 'tutorial' in settings.keys():
            st = settings['tutorial']
            return await message.reply_text(f"<b>Tutorial: <code>{st}</code>\n\nShortener Url Not Connected\n\nYou can Connect Using /shortlink command</b>")
        else:
            return await message.reply_text("Shortener url and Tutorial Link Not Connected. Check this commands, /shortlink and /set_tutorial")
        

@Client.on_message(filters.command("set_tutorial"))
async def settutorial(bot, message):
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return await message.reply(f"You are anonymous admin. Turn off anonymous admin and try again this command")
    chat_type = message.chat.type
    if chat_type == enums.ChatType.PRIVATE:
        return await message.reply_text("This Command Work Only in group\n\nTry it in your own group")
    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grpid = message.chat.id
        title = message.chat.title
    else:
        return
    userid = message.from_user.id
    user = await bot.get_chat_member(grpid, userid)
    if user.status != enums.ChatMemberStatus.ADMINISTRATOR and user.status != enums.ChatMemberStatus.OWNER and str(userid) not in ADMINS:
        return
    else:
        pass
    if len(message.command) == 1:
        return await message.reply("<b>Give me a tutorial link along with this command\n\nCommand Usage: /set_tutorial your tutorial link</b>")
    elif len(message.command) == 2:
        reply = await message.reply_text("<b>Please Wait...</b>")
        tutorial = message.command[1]
        await save_group_settings(grpid, 'tutorial', tutorial)
        await save_group_settings(grpid, 'is_tutorial', True)
        await reply.edit_text(f"<b>Successfully Added Tutorial\n\nHere is your tutorial link for your group {title} - <code>{tutorial}</code></b>")
    else:
        return await message.reply("<b>You entered Incorrect Format\n\nFormat: /set_tutorial your tutorial link</b>")

@Client.on_message(filters.command("remove_tutorial"))
async def removetutorial(bot, message):
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return await message.reply(f"You are anonymous admin. Turn off anonymous admin and try again this command")
    chat_type = message.chat.type
    if chat_type == enums.ChatType.PRIVATE:
        return await message.reply_text("This Command Work Only in group\n\nTry it in your own group")
    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grpid = message.chat.id
        title = message.chat.title
    else:
        return
    userid = message.from_user.id
    user = await bot.get_chat_member(grpid, userid)
    if user.status != enums.ChatMemberStatus.ADMINISTRATOR and user.status != enums.ChatMemberStatus.OWNER and str(userid) not in ADMINS:
        return
    else:
        pass
    reply = await message.reply_text("<b>Please Wait...</b>")
    await save_group_settings(grpid, 'tutorial', "")
    await save_group_settings(grpid, 'is_tutorial', False)
    await reply.edit_text(f"<b>Successfully Removed Your Tutorial Link!!!</b>")

@Client.on_message(filters.command("restart") & filters.user(ADMINS))
async def stop_button(bot, message):
    msg = await bot.send_message(text="**🔄 𝙿𝚁𝙾𝙲𝙴𝚂𝚂𝙴𝚂 𝚂𝚃𝙾𝙿𝙴𝙳. 𝙱𝙾𝚃 𝙸𝚂 𝚁𝙴𝚂𝚃𝙰𝚁𝚃𝙸𝙽𝙶...**", chat_id=message.chat.id)       
    await asyncio.sleep(3)
    await msg.edit("**✅️ 𝙱𝙾𝚃 𝙸𝚂 𝚁𝙴𝚂𝚃𝙰𝚁𝚃𝙴𝙳. 𝙽𝙾𝚆 𝚈𝙾𝚄 𝙲𝙰𝙽 𝚄𝚂𝙴 𝙼𝙴**")
    os.execl(sys.executable, sys.executable, *sys.argv)

@Client.on_message(filters.command("nofsub"))
async def nofsub(client, message):
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return await message.reply(f"<b>You are anonymous admin. Turn off anonymous admin and try again this command</b>")
    chat_type = message.chat.type
    if chat_type == enums.ChatType.PRIVATE:
        return await message.reply_text("<b>This Command Work Only in group\n\nTry it in your own group</b>")
    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grpid = message.chat.id
        title = message.chat.title
    else:
        return
    userid = message.from_user.id
    user = await client.get_chat_member(grpid, userid)
    if user.status != enums.ChatMemberStatus.ADMINISTRATOR and user.status != enums.ChatMemberStatus.OWNER and str(userid) not in ADMINS:
        return
    else:
        pass
    await save_group_settings(grpid, 'fsub', None)
    await message.reply_text(f"<b>Successfully removed force subscribe from {title}.</b>")

@Client.on_message(filters.command('fsub'))
async def fsub(client, message):
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return await message.reply(f"<b>You are anonymous admin. Turn off anonymous admin and try again this command</b>")
    chat_type = message.chat.type
    if chat_type == enums.ChatType.PRIVATE:
        return await message.reply_text("<b>This Command Work Only in group\n\nTry it in your own group</b>")
    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grpid = message.chat.id
        title = message.chat.title
    else:
        return
    userid = message.from_user.id
    user = await client.get_chat_member(grpid, userid)
    if user.status != enums.ChatMemberStatus.ADMINISTRATOR and user.status != enums.ChatMemberStatus.OWNER and str(userid) not in ADMINS:
        return
    else:
        pass
    try:
        ids = message.text.split(" ", 1)[1]
        fsub_ids = [int(id) for id in ids.split()]
    except IndexError:
        return await message.reply_text("<b>Command Incomplete!\n\nAdd Multiple Channel By Seperate Space. Like: /fsub id1 id2 id3</b>")
    except ValueError:
        return await message.reply_text('<b>Make Sure Ids are Integer.</b>')        
    channels = "Channels:\n"
    for id in fsub_ids:
        try:
            chat = await client.get_chat(id)
        except Exception as e:
            return await message.reply_text(f"<b>{id} is invalid!\nMake sure this bot admin in that channel.\n\nError - {e}</b>")
        if chat.type != enums.ChatType.CHANNEL:
            return await message.reply_text(f"<b>{id} is not channel.</b>")
        channels += f'{chat.title}\n'
    await save_group_settings(grpid, 'fsub', fsub_ids)
    await message.reply_text(f"<b>Successfully set force channels for {title} to\n\n{channels}\n\nYou can remove it by /nofsub.</b>")
        

@Client.on_message(filters.command("add_premium"))
async def give_premium_cmd_handler(client, message):
    if PREMIUM_AND_REFERAL_MODE == False:
        return 
    user_id = message.from_user.id
    if user_id not in ADMINS:
        await message.delete()
        return
    if len(message.command) == 3:
        user_id = int(message.command[1])  # Convert the user_id to integer
        time = message.command[2]        
        seconds = await get_seconds(time)
        if seconds > 0:
            expiry_time = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
            user_data = {"id": user_id, "expiry_time": expiry_time, "expiry_reminder_sent": False, "expired_notified": False} 
            await db.update_user(user_data)  # Use the update_user method to update or insert user data
            await message.reply_text("Premium access added to the user.")            
            await client.send_message(
                chat_id=user_id,
                text = f"""
<b>👑 ᴄᴏɴɢʀᴀᴛꜱ 👑</b>

💎 <b>ᴘʀᴇᴍɪᴜᴍ ᴜɴʟᴏᴄᴋᴇᴅ ꜰᴏʀ {time}</b>  
🌟 ᴀʟʟ ᴘʀᴇᴍɪᴜᴍ ꜰᴇᴀᴛᴜʀᴇꜱ ᴀʀᴇ ɴᴏᴡ ᴀᴄᴄᴇꜱꜱɪʙʟᴇ  
🎬 ᴇɴᴊᴏʏ ᴜʟᴛʀᴀ ꜱᴘᴇᴇᴅ, ᴀᴅ-ꜰʀᴇᴇ ꜱᴛʀᴇᴀᴍɪɴɢ & ᴘʀᴏ ᴛᴏᴏʟꜱ  
🎉 ᴇɴᴊᴏʏ ᴀ ʀᴏʏᴀʟ ᴇxᴘᴇʀɪᴇɴᴄᴇ  

🚀 <b>ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ ɢᴏꜰʟɪx!</b>  
⚡ <b>ᴘᴏᴡᴇʀᴇᴅ ʙʏ ɢᴏꜰʟɪx ⚡</b>
"""                
            )
        else:
            await message.reply_text("Invalid time format. Please use '1day for days', '1hour for hours', or '1min for minutes', or '1month for months' or '1year for year'")
    else:
        await message.reply_text("<b>Usage: /add_premium user_id time \n\nExample /add_premium 1252789 10day \n\n(e.g. for time units '1day for days', '1hour for hours', or '1min for minutes', or '1month for months' or '1year for year')</b>")
        
@Client.on_message(filters.command("remove_premium"))
async def remove_premium_cmd_handler(client, message):
    if PREMIUM_AND_REFERAL_MODE == False:
        return 
    user_id = message.from_user.id
    if user_id not in ADMINS:
        await message.delete()
        return
    if len(message.command) == 2:
        user_id = int(message.command[1])  # Convert the user_id to integer
      #  time = message.command[2]
        time = "1s"
        seconds = await get_seconds(time)
        if seconds > 0:
            expiry_time = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
            user_data = {"id": user_id, "expiry_time": expiry_time, "expiry_reminder_sent": True, "expired_notified": True}  # Using "id" instead of "user_id"
            await db.update_user(user_data)  # Use the update_user method to update or insert user data
            await message.reply_text("Premium access removed to the user.")
            await client.send_message(
                chat_id=user_id,
                text="<b>premium removed by admins \n\n Contact Admin if this is mistake \n\n 👮 Admin : {} \n</b>".format(OWNER_LNK),                
            )
        else:
            await message.reply_text("Invalid time format.'")
    else:
        await message.reply_text("Usage: /remove_premium user_id")
        
@Client.on_message(filters.command("plan"))
async def plans_cmd_handler(client, message): 
    if PREMIUM_AND_REFERAL_MODE == False:
        return 
    btn = [            
        [InlineKeyboardButton("ꜱᴇɴᴅ ᴘᴀʏᴍᴇɴᴛ ʀᴇᴄᴇɪᴘᴛ 🧾", url=OWNER_LNK)],
        [InlineKeyboardButton("⚠️ ᴄʟᴏsᴇ / ᴅᴇʟᴇᴛᴇ ⚠️", callback_data="close_data")]
    ]
    reply_markup = InlineKeyboardMarkup(btn)
    rates = await load_plan_rates(client.me.id)
    caption_text = PAYMENT_TEXT.format(plan_rates=format_plan_rates(rates))
    await message.reply_photo(
        photo=PAYMENT_QR,
        caption=caption_text,
        parse_mode=enums.ParseMode.HTML,
        has_spoiler=True,
        reply_markup=reply_markup
    )


@Client.on_message(filters.command("plan_rate") & filters.private)
async def plan_rate_cmd_handler(client, message):
    # Explicit check (instead of filters.user(ADMINS)) so a non-admin gets
    # a visible "access denied" reply rather than the command silently
    # doing nothing.
    if message.from_user.id not in ADMINS:
        return await message.reply_text("⛔ This command is for the bot owner/admins only.")

    try:
        bot_id = client.me.id
        current = await load_plan_rates(bot_id)
        current_text = (
            "💰 <b>Current Plan Rates</b>\n\n"
            f"- {current['week']}Rs - 1 Week\n"
            f"- {current['month']}Rs - 1 Months\n"
            f"- {current['3months']}Rs - 3 Months\n"
            f"- {current['6months']}Rs - 6 Months\n\n"
            "Send the new rates as <b>4 numbers</b>, one per line, in this exact order "
            "(1 week, 1 month, 3 months, 6 months) — numbers only, e.g.:\n\n"
            "<code>15\n40\n110\n200</code>\n\n"
            "Send /cancel to keep the current rates."
        )
        reply = await client.ask(message.from_user.id, current_text, parse_mode=enums.ParseMode.HTML)

        if reply.text and reply.text.strip().lower() == "/cancel":
            return await reply.reply_text("❌ Cancelled — rates unchanged.")

        lines = [ln.strip() for ln in (reply.text or "").splitlines() if ln.strip()]
        if len(lines) != 4 or not all(ln.isdigit() for ln in lines):
            return await reply.reply_text(
                "⚠️ Invalid format. Send exactly 4 numbers, one per line "
                "(week, month, 3 months, 6 months). Run /plan_rate again to retry."
            )

        new_rates = {"week": lines[0], "month": lines[1], "3months": lines[2], "6months": lines[3]}
        await save_plan_rates(bot_id, new_rates)
        await reply.reply_text(
            "✅ Plan rates updated!\n\n" + format_plan_rates(new_rates).replace("ʀs", "Rs").replace("ᴡᴇᴇᴋ", "Week").replace("ᴍᴏɴᴛʜs", "Months")
        )
    except Exception as e:
        logger.exception(e)
        await message.reply_text(f"⚠️ /plan_rate failed:\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("myplan"))
async def check_plans_cmd(client, message):
    if PREMIUM_AND_REFERAL_MODE == False:
        return 
    user_id  = message.from_user.id
    if await db.has_premium_access(user_id):         
        remaining_time = await db.check_remaining_uasge(user_id)             
        expiry_time = remaining_time + datetime.datetime.now()
        sent = await message.reply_text(
            "✨ <b>Your Plan Details</b> ✨\n\n"
            f"⏳ <b>Remaining Time :</b> {format_remaining_time(remaining_time)}\n"
            f"📅 <b>Expires On :</b> {format_expiry_time(expiry_time)}\n\n"
            "🔄 Extend your plan : /plan\n\n"
            "Have a great day! 😊"
        )
        await asyncio.sleep(180)
        await sent.delete()
    else:
        btn = [ 
            [InlineKeyboardButton("ɢᴇᴛ ғʀᴇᴇ ᴛʀᴀɪʟ ғᴏʀ 𝟻 ᴍɪɴᴜᴛᴇꜱ ☺️", callback_data="get_trail")],
            [InlineKeyboardButton("ʙᴜʏ sᴜʙsᴄʀɪᴘᴛɪᴏɴ : ʀᴇᴍᴏᴠᴇ ᴀᴅs", callback_data="buy_premium")],
            [InlineKeyboardButton("⚠️ ᴄʟᴏsᴇ / ᴅᴇʟᴇᴛᴇ ⚠️", callback_data="close_data")]
        ]
        reply_markup = InlineKeyboardMarkup(btn)
        m=await message.reply_sticker("CAACAgIAAxkBAAIBTGVjQbHuhOiboQsDm35brLGyLQ28AAJ-GgACglXYSXgCrotQHjibHgQ")         
        await message.reply_text(f"**😢 You Don't Have Any Premium Subscription.\n\n Check Out Our Premium /plan**",reply_markup=reply_markup)
        await asyncio.sleep(2)
        await m.delete()

@Client.on_message(filters.command("totalrequests") & filters.private & filters.user(ADMINS))
async def total_requests(client, message):
    if join_db().isActive():
        total = await join_db().get_all_users_count()
        await message.reply_text(
            text=f"Total Requests: {total}",
            parse_mode=enums.ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

@Client.on_message(filters.command("purgerequests") & filters.private & filters.user(ADMINS))
async def purge_requests(client, message):   
    if join_db().isActive():
        await join_db().delete_all_users()
        await message.reply_text(
            text="Purged All Requests.",
            parse_mode=enums.ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )


# ═══════════════════════════════════════════════════════════════════════════════
#   GOFLIX GUARD — MODERATION COMMANDS (add to bottom of commands.py)
# ═══════════════════════════════════════════════════════════════════════════════

from pyrogram.types import ChatPermissions
from pyrogram.enums import ChatMemberStatus
from database.guard_db import (
    reset_warns  as guard_reset_warns,
    remove_ban_log,
    get_all_banned,
    log_ban
)

async def _is_admin(client, chat_id, user_id):
    try:
        m = await client.get_chat_member(chat_id, user_id)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except:
        return False

async def _do_unmute(client, chat_id, user_id):
    await client.restrict_chat_member(
        chat_id, user_id,
        ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_add_web_page_previews=True,
        )
    )

# ── /mute ─────────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("mute") & filters.group, group=1)
async def mute_cmd(client, message):
    if not await _is_admin(client, message.chat.id, message.from_user.id):
        return await message.reply("❌ Admins only!")

    target  = None
    minutes = None

    if message.reply_to_message:
        target = message.reply_to_message.from_user
        if len(message.command) > 1:
            try:
                minutes = int(message.command[1])
            except:
                return await message.reply("❌ Usage: reply + `/mute <minutes>`")
    elif len(message.command) > 1:
        try:
            target = await client.get_users(message.command[1].lstrip("@"))
            if len(message.command) > 2:
                minutes = int(message.command[2])
        except:
            return await message.reply("❌ User not found or invalid duration.")
    else:
        return await message.reply(
            "❌ **Usage:**\n"
            "• Reply + `/mute <minutes>`\n"
            "• `/mute @user <minutes>`\n"
            "• No minutes = permanent"
        )

    if not target:
        return await message.reply("❌ User not found.")
    if await _is_admin(client, message.chat.id, target.id):
        return await message.reply("❌ Cannot mute an admin.")

    chat_id = message.chat.id

    if minutes:
        from datetime import datetime, timedelta
        until = datetime.utcnow() + timedelta(minutes=minutes)
        await client.restrict_chat_member(chat_id, target.id, ChatPermissions(), until_date=until)
        duration_text = f"`{minutes}` min — until `{until.strftime('%d.%m.%y %H:%M')} UTC`"
    else:
        await client.restrict_chat_member(chat_id, target.id, ChatPermissions())
        duration_text = "Permanent"

    await message.reply(
        f"🔇 **Muted**\n\n"
        f"👤 **User:** {target.mention}\n"
        f"🆔 **ID:** `{target.id}`\n"
        f"⏱ **Duration:** {duration_text}\n"
        f"👮 **By:** {message.from_user.mention}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔓 Unmute", callback_data=f"cmd_unmute_{target.id}_{chat_id}")
        ]])
    )
    message.stop_propagation()


# ── /unmute ───────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("unmute") & filters.group, group=1)
async def unmute_cmd(client, message):
    if not await _is_admin(client, message.chat.id, message.from_user.id):
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
        return await message.reply("❌ Reply to user or `/unmute @user`")

    await _do_unmute(client, message.chat.id, target.id)
    await message.reply(
        f"🔓 **Unmuted**\n\n"
        f"👤 **User:** {target.mention}\n"
        f"🆔 **ID:** `{target.id}`\n"
        f"👮 **By:** {message.from_user.mention}"
    )
    message.stop_propagation()


# ── /ban ──────────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("ban") & filters.group, group=1)
async def ban_cmd(client, message):
    if not await _is_admin(client, message.chat.id, message.from_user.id):
        return await message.reply("❌ Admins only!")

    target = None
    reason = "No reason provided"
    chat_id = message.chat.id

    if message.reply_to_message:
        target = message.reply_to_message.from_user
        if len(message.command) > 1:
            reason = " ".join(message.command[1:])
    elif len(message.command) > 1:
        try:
            target = await client.get_users(message.command[1].lstrip("@"))
            if len(message.command) > 2:
                reason = " ".join(message.command[2:])
        except:
            return await message.reply("❌ User not found.")
    else:
        return await message.reply(
            "❌ **Usage:**\n"
            "• Reply + `/ban <reason>`\n"
            "• `/ban @user <reason>`"
        )

    if not target:
        return await message.reply("❌ User not found.")
    if await _is_admin(client, chat_id, target.id):
        return await message.reply("❌ Cannot ban an admin.")

    await client.ban_chat_member(chat_id, target.id)
    await log_ban(chat_id, target.id)

    await message.reply(
        f"🚫 **Banned**\n\n"
        f"👤 **User:** {target.mention}\n"
        f"🆔 **ID:** `{target.id}`\n"
        f"📝 **Reason:** {reason}\n"
        f"👮 **By:** {message.from_user.mention}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔓 Unban", callback_data=f"cmd_unban_{target.id}_{chat_id}")
        ]])
    )
    message.stop_propagation()


# ── /unban ────────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("unban") & filters.group, group=1)
async def unban_cmd(client, message):
    if not await _is_admin(client, message.chat.id, message.from_user.id):
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
        return await message.reply("❌ Reply to user or `/unban @user`")

    await client.unban_chat_member(message.chat.id, target.id)
    await guard_reset_warns(message.chat.id, target.id)
    await remove_ban_log(message.chat.id, target.id)

    await message.reply(
        f"✅ **Unbanned**\n\n"
        f"👤 **User:** {target.mention}\n"
        f"🆔 **ID:** `{target.id}`\n"
        f"👮 **By:** {message.from_user.mention}"
    )
    message.stop_propagation()


# ── Callbacks: Unmute / Unban buttons ─────────────────────────────────────────
# Handles both: cmd_unmute_USERID_CHATID and old cmd_unmute_USERID formats

@Client.on_callback_query(filters.regex(r"^cmd_(unmute|unban)_(\d+)(?:_(-\d+))?$"))
async def cmd_action_callback(client, callback):
    action  = callback.matches[0].group(1)
    user_id = int(callback.matches[0].group(2))
    chat_id_str = callback.matches[0].group(3)

    # Determine chat_id — from callback data or from message chat
    if chat_id_str:
        chat_id = int(chat_id_str)
    else:
        chat_id = callback.message.chat.id

    if not await _is_admin(client, chat_id, callback.from_user.id):
        return await callback.answer("❌ Admins only!", show_alert=True)

    try:
        user = await client.get_users(user_id)
        name = user.mention
    except:
        name = f"`{user_id}`"

    if action == "unmute":
        await _do_unmute(client, chat_id, user_id)
        try:
            await callback.message.edit_text(
                callback.message.text + f"\n\n✅ **Unmuted by Admin**"
            )
        except:
            pass
        await callback.answer("✅ User unmuted!")

    else:  # unban
        await client.unban_chat_member(chat_id, user_id)
        await guard_reset_warns(chat_id, user_id)
        await remove_ban_log(chat_id, user_id)
        try:
            await callback.message.edit_text(
                callback.message.text + f"\n\n✅ **Unbanned by Admin**"
            )
        except:
            pass
        await callback.answer("✅ User unbanned!")
