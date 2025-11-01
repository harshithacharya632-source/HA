# utils.py
# Complete utilities for Movie/File Bot
# Using ShrinkMe.io as the only shortener & verification provider
#
# Keep credit lines if you want:
# Don't Remove Credit @VJ_Botz
# Subscribe YouTube Channel For Amazing Bot @Tech_VJ
# Ask Doubt on telegram @KingVJ01

import os
import re
import io
import json
import time
import html
import math
import random
import string
import logging
import asyncio
import pytz
import aiohttp
import requests
from datetime import datetime, date
from typing import List, Union, Tuple, Optional

# Bot project imports (unchanged)
from info import *        # expects many constants: SHORTLINK_MODE, SHORTLINK_URL, etc.
from Script import script
from imdb import Cinemagoer
from pyrogram import enums
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import *
from database.users_chats_db import db
from database.join_reqs import JoinReqs
from bs4 import BeautifulSoup

# Logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Globals
BTN_URL_REGEX = re.compile(r"(\[([^\[]+?)\]\((buttonurl|buttonalert):(?:/{0,2})(.+?)(:same)?\))")
imdb = Cinemagoer()
join_db = JoinReqs

TOKENS = {}       # structure: { user_id: { token: used_bool } }
VERIFIED = {}     # structure: { user_id: "YYYY-MM-DD" }  (simple expiry store)
BANNED = {}
SECOND_SHORTENER = {}
SMART_OPEN = '“'
SMART_CLOSE = '”'
START_CHAR = ('\'', '"', SMART_OPEN)

# temp local container used across functions (keeps previous structure)
class temp:
    BANNED_USERS = []
    BANNED_CHATS = []
    ME = None
    BOT = None
    CURRENT = int(os.environ.get("SKIP", 2)) if os.environ.get("SKIP") else 2
    CANCEL = False
    MELCOW = {}
    U_NAME = None
    B_NAME = None
    GETALL = {}
    SHORT = {}
    SETTINGS = {}
    IMDB_CAP = {}

# =========================
# Helper / compatibility functions
# =========================

def remove_escapes(text: str) -> str:
    res = ""
    is_escaped = False
    for ch in text:
        if is_escaped:
            res += ch
            is_escaped = False
        elif ch == "\\":
            is_escaped = True
        else:
            res += ch
    return res

def list_to_str(k: Optional[List]) -> str:
    if not k:
        return "N/A"
    if len(k) == 1:
        return str(k[0])
    if MAX_LIST_ELM:
        k = k[:int(MAX_LIST_ELM)]
    return ' '.join(f'{elem}, ' for elem in k)

def get_size(size) -> str:
    try:
        size = float(size)
    except Exception:
        return "0 B"
    units = ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB"]
    i = 0
    while size >= 1024.0 and i < len(units) - 1:
        size /= 1024.0
        i += 1
    return "%.2f %s" % (size, units[i])

def split_list(l, n):
    for i in range(0, len(l), n):
        yield l[i:i + n]

def get_file_id(msg: Message):
    if msg.media:
        for message_type in ("photo", "animation", "audio", "document", "video", "video_note", "voice", "sticker"):
            obj = getattr(msg, message_type)
            if obj:
                setattr(obj, "message_type", message_type)
                return obj
    return None

def extract_user(message: Message) -> Tuple[Union[int, str], str]:
    user_id = None
    user_first_name = None
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        user_first_name = message.reply_to_message.from_user.first_name
    elif len(message.command) > 1:
        if len(message.entities) > 1 and message.entities[1].type == enums.MessageEntityType.TEXT_MENTION:
            required_entity = message.entities[1]
            user_id = required_entity.user.id
            user_first_name = required_entity.user.first_name
        else:
            user_id = message.command[1]
            user_first_name = user_id
        try:
            user_id = int(user_id)
        except Exception:
            pass
    else:
        user_id = message.from_user.id
        user_first_name = message.from_user.first_name
    return user_id, user_first_name

def last_online(from_user) -> str:
    time_str = ""
    try:
        if from_user.is_bot:
            time_str = "🤖 Bot :("
        elif from_user.status == enums.UserStatus.RECENTLY:
            time_str = "Recently"
        elif from_user.status == enums.UserStatus.LAST_WEEK:
            time_str = "Within the last week"
        elif from_user.status == enums.UserStatus.LAST_MONTH:
            time_str = "Within the last month"
        elif from_user.status == enums.UserStatus.LONG_AGO:
            time_str = "A long time ago :("
        elif from_user.status == enums.UserStatus.ONLINE:
            time_str = "Currently Online"
        elif from_user.status == enums.UserStatus.OFFLINE:
            time_str = from_user.last_online_date.strftime("%a, %d %b %Y, %H:%M:%S")
    except Exception:
        time_str = ""
    return time_str

# gfilterparser / parser copied but cleaned
def gfilterparser(text, keyword):
    if "buttonalert" in text:
        text = text.replace("\n", "\\n").replace("\t", "\\t")
    buttons = []
    note_data = ""
    prev = 0
    i = 0
    alerts = []
    for match in BTN_URL_REGEX.finditer(text):
        n_escapes = 0
        to_check = match.start(1) - 1
        while to_check > 0 and text[to_check] == "\\":
            n_escapes += 1
            to_check -= 1
        if n_escapes % 2 == 0:
            note_data += text[prev:match.start(1)]
            prev = match.end(1)
            if match.group(3) == "buttonalert":
                if bool(match.group(5)) and buttons:
                    buttons[-1].append(InlineKeyboardButton(
                        text=match.group(2),
                        callback_data=f"gfilteralert:{i}:{keyword}"
                    ))
                else:
                    buttons.append([InlineKeyboardButton(
                        text=match.group(2),
                        callback_data=f"gfilteralert:{i}:{keyword}"
                    )])
                i += 1
                alerts.append(match.group(4))
            elif bool(match.group(5)) and buttons:
                buttons[-1].append(InlineKeyboardButton(
                    text=match.group(2),
                    url=match.group(4).replace(" ", "")
                ))
            else:
                buttons.append([InlineKeyboardButton(
                    text=match.group(2),
                    url=match.group(4).replace(" ", "")
                )])
        else:
            note_data += text[prev:to_check]
            prev = match.start(1) - 1
    else:
        note_data += text[prev:]
    try:
        return note_data, buttons, alerts
    except:
        return note_data, buttons, None

def parser(text, keyword):
    if "buttonalert" in text:
        text = text.replace("\n", "\\n").replace("\t", "\\t")
    buttons = []
    note_data = ""
    prev = 0
    i = 0
    alerts = []
    for match in BTN_URL_REGEX.finditer(text):
        n_escapes = 0
        to_check = match.start(1) - 1
        while to_check > 0 and text[to_check] == "\\":
            n_escapes += 1
            to_check -= 1
        if n_escapes % 2 == 0:
            note_data += text[prev:match.start(1)]
            prev = match.end(1)
            if match.group(3) == "buttonalert":
                if bool(match.group(5)) and buttons:
                    buttons[-1].append(InlineKeyboardButton(
                        text=match.group(2),
                        callback_data=f"alertmessage:{i}:{keyword}"
                    ))
                else:
                    buttons.append([InlineKeyboardButton(
                        text=match.group(2),
                        callback_data=f"alertmessage:{i}:{keyword}"
                    )])
                i += 1
                alerts.append(match.group(4))
            elif bool(match.group(5)) and buttons:
                buttons[-1].append(InlineKeyboardButton(
                    text=match.group(2),
                    url=match.group(4).replace(" ", "")
                ))
            else:
                buttons.append([InlineKeyboardButton(
                    text=match.group(2),
                    url=match.group(4).replace(" ", "")
                )])
        else:
            note_data += text[prev:to_check]
            prev = match.start(1) - 1
    else:
        note_data += text[prev:]
    try:
        return note_data, buttons, alerts
    except:
        return note_data, buttons, None

# =========================
# ShrinkMe integration (ONLY)
# =========================

# Utility to call ShrinkMe. ShrinkMe sometimes returns plain text; handle both.
async def _shrinkme_call(api_base: str, api_key: str, long_url: str) -> str:
    """
    Low-level call to ShrinkMe-like API.
    Accepts either:
      - api_base like "https://shrinkme.io/api"
      - api_key: API key
      - long_url: URL to shorten
    Returns shortened URL or original on failure.
    """
    if not api_base or not api_key:
        logger.debug("[ShrinkMe] API base or key missing, returning original link")
        return long_url

    # Build request URL. ShrinkMe supports both query param and path; many wrappers accept ?api=<key>&url=<url>
    # We send request and try to extract JSON or plain text.
    params = {"api": api_key, "url": long_url}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_base, params=params, ssl=False, timeout=20) as resp:
                text = await resp.text()
                ct = resp.headers.get("Content-Type", "")
                # If JSON
                if "application/json" in ct or text.strip().startswith("{"):
                    try:
                        data = json.loads(text)
                        # Common shrinkme response contains keys like 'shortenedUrl' or 'shortened' or 'short'
                        for key in ("shortenedUrl", "short", "short_link", "shorturl", "shortened"):
                            if key in data:
                                return data[key]
                        # some return result->shortenedUrl
                        if "result" in data:
                            for key in ("shortenedUrl", "short"):
                                if key in data["result"]:
                                    return data["result"][key]
                        # fallback: if 'url' key present
                        if "url" in data:
                            return data["url"]
                    except Exception:
                        # not JSON parseable
                        pass
                # if not JSON, sometimes ShrinkMe returns plain link text
                text = text.strip()
                # if text looks like a URL, return it
                if text.startswith("http://") or text.startswith("https://"):
                    return text
                # else fallback: try parse short_url from "shortenedUrl":"..."
                m = re.search(r"https?://[^\s'\"<>]+", text)
                if m:
                    return m.group(0)
    except Exception as e:
        logger.exception(f"[ShrinkMe call] exception: {e}")
    return long_url

async def shrinkme_shorten(long_url: str, api_base: Optional[str] = None, api_key: Optional[str] = None) -> str:
    """
    Public wrapper to create a ShrinkMe shortlink.
    - If api_base/api_key None, uses SHORTLINK_URL/SHORTLINK_API from info.py
    """
    base = api_base if api_base else SHORTLINK_URL
    key = api_key if api_key else SHORTLINK_API
    # Accept base given as 'shrinkme.io' or full URL. Normalize
    if base and not base.startswith("http"):
        base = f"https://{base}"
    return await _shrinkme_call(base, key, long_url)

async def get_verify_shorted_link(long_url: str, api_base: Optional[str] = None, api_key: Optional[str] = None) -> str:
    """
    Primary verification shortener.
    Uses VERIFY_SHORTLINK_URL & VERIFY_SHORTLINK_API by default.
    Supports second shortener chain if VERIFY_SECOND_SHORTNER True (uses VERIFY_SND_SHORTLINK_*).
    """
    # respect explicit args or info.py constants
    base = api_base if api_base else VERIFY_SHORTLINK_URL
    key = api_key if api_key else VERIFY_SHORTLINK_API

    if base and not base.startswith("http"):
        base = f"https://{base}"

    short = await _shrinkme_call(base, key, long_url)

    # optional second shortener
    try:
        if VERIFY_SECOND_SHORTNER:
            snd_base = VERIFY_SND_SHORTLINK_URL
            snd_key = VERIFY_SND_SHORTLINK_API
            if snd_base and not snd_base.startswith("http"):
                snd_base = f"https://{snd_base}"
            short = await _shrinkme_call(snd_base, snd_key, short)
    except Exception as e:
        logger.exception("[get_verify_shorted_link] second shortener failed: %s", e)

    return short

# =========================
# Token / verification helpers
# =========================

async def check_token(bot, userid: int, token: str) -> bool:
    """
    Checks whether token is valid and unused for user.
    """
    try:
        user = await bot.get_users(userid)
    except Exception:
        user = None
    # ensure user present in DB
    try:
        if user and not await db.is_user_exist(user.id):
            await db.add_user(user.id, user.first_name)
            await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))
    except Exception:
        pass

    if userid in TOKENS:
        tdict = TOKENS[userid]
        if token in tdict:
            return not tdict[token]  # True if not used
    return False

async def get_token(bot, userid: int, base_link: str) -> str:
    """
    Generates a token for the user, stores it in TOKENS dict as unused (False),
    builds verify link like: base_link + "verify-<userid>-<token>" then shortens it using ShrinkMe.
    Returns final shortened (verification) link.
    """
    try:
        user = await bot.get_users(userid)
    except Exception:
        user = None
    try:
        if user and not await db.is_user_exist(user.id):
            await db.add_user(user.id, user.first_name)
            await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))
    except Exception:
        pass

    token = ''.join(random.choices(string.ascii_letters + string.digits, k=7))
    TOKENS.setdefault(userid, {})[token] = False
    # construct verify URL; ensure it ends with slash or parameter separator
    sep = "" if base_link.endswith("/") or "?" in base_link else "/"
    verify_url = f"{base_link}{sep}verify-{userid}-{token}"

    shortened_verify = await get_verify_shorted_link(verify_url)
    return shortened_verify

async def mark_token_used(userid: int, token: str) -> bool:
    """
    Marks token used in memory. Also try to mark verified in DB (if method present).
    Returns True on success.
    """
    try:
        if userid in TOKENS and token in TOKENS[userid]:
            TOKENS[userid][token] = True
        # store verification date in VERIFIED dict and DB (if exists)
        today = date.today().isoformat()
        VERIFIED[userid] = today
        try:
            # if DB has a dedicated verified collection or update, attempt to call it
            if hasattr(db, "mark_user_verified"):
                await db.mark_user_verified(userid, today)
            else:
                # fallback: store in user settings (creates key 'verified_until' as example)
                settings = await db.get_settings(userid) if hasattr(db, "get_settings") else {}
                if isinstance(settings, dict):
                    # set a default expiry maybe 1 day or month; here store today's date (no expiry)
                    settings['verified_on'] = today
                    await db.update_settings(userid, settings)
        except Exception:
            # ignore DB errors
            pass
        return True
    except Exception as e:
        logger.exception("[mark_token_used] %s", e)
        return False

async def verify_user(bot, userid: int, token: str):
    """
    Mark user verified (used by your callback or webhook when verification confirmed).
    This function is kept to be used by your verify endpoint/callback.
    """
    try:
        await mark_token_used(userid, token)
        # optionally, log verification
        try:
            user = await bot.get_users(userid)
            await bot.send_message(LOG_CHANNEL, f"User Verified: {user.id} - {user.mention}")
        except Exception:
            pass
    except Exception as e:
        logger.exception("[verify_user] %s", e)

async def check_verification(bot, userid: int) -> bool:
    """
    Check if user is verified and not expired.
    Current simple logic: if user in VERIFIED dict and date >= today => True.
    If not present, returns False.
    You can improve to use DB-stored expiry.
    """
    try:
        if userid in VERIFIED:
            exp = VERIFIED[userid]
            try:
                y, m, d = map(int, exp.split("-"))
                comp = date(y, m, d)
                today = date.today()
                return not (comp < today)  # True if comp >= today
            except Exception:
                return True
        # fallback: check DB if method exists
        try:
            if hasattr(db, "is_user_verified"):
                return await db.is_user_verified(userid)
        except Exception:
            pass
    except Exception:
        pass
    return False

# =========================
# Shortlink helpers for sending files
# =========================

async def get_shortlink_for_file(chat_id: int, file_id: str) -> str:
    """
    Build the long telegram deep-link then shorten it using ShrinkMe.
    """
    # prefer using bot username if set in temp
    u_name = temp.U_NAME or os.environ.get("BOT_USERNAME", "")
    base_link = f"https://t.me/{u_name}?start=files_{file_id}" if u_name else f"https://t.me/{temp.U_NAME}?start=files_{file_id}"
    if SHORTLINK_MODE:
        try:
            short = await shrinkme_shorten(base_link)
            return short
        except Exception:
            return base_link
    return base_link

# =========================
# send_all implementation
# =========================

async def send_all(bot, userid: int, files: List[dict], ident: str, chat_id: int, user_name: str, query):
    """
    Send files to a user.
    When group settings enable shortlink mode, it sends a ShrinkMe shortlink to download (verification flow).
    Otherwise, it sends cached media directly.
    files: list of dicts with keys: file_id, file_name, file_size, caption
    ident: "filep" or other
    query: incoming query object (used for answering errors)
    """
    settings = {}
    try:
        settings = await get_settings(chat_id)
    except Exception:
        settings = {}
    ENABLE_SHORTLINK = False
    if settings and 'is_shortlink' in settings:
        ENABLE_SHORTLINK = settings['is_shortlink']
    else:
        # ensure existence
        try:
            await save_group_settings(chat_id, 'is_shortlink', False)
        except Exception:
            pass
        ENABLE_SHORTLINK = False

    try:
        if ENABLE_SHORTLINK:
            for file in files:
                title = file.get("file_name", "Unknown")
                size = get_size(file.get("file_size", 0))
                if not await db.has_premium_access(userid) and SHORTLINK_MODE:
                    # build shortlink (this returns final shrinkme short link)
                    deep_link = f"https://telegram.me/{temp.U_NAME}?start=files_{file['file_id']}"
                    short_url = await get_verify_shorted_link(deep_link)
                    # send verification flow link: you may wish to send verify link first (if you implement separate verify)
                    await bot.send_message(
                        chat_id=userid,
                        text=f"<b>Hᴇʏ {user_name} 👋\n\n✅ Secure link generated for your file.\n\n🗃️ File: {title}\n🔖 Size: {size}\n\nClick download below (complete the ad verification page):</b>",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📤 Download", url=short_url)]]),
                    )
        else:
            for file in files:
                f_caption = file.get("caption")
                title = file.get("file_name", "")
                size = get_size(file.get("file_size", 0))
                if CUSTOM_FILE_CAPTION:
                    try:
                        f_caption = CUSTOM_FILE_CAPTION.format(
                            file_name='' if title is None else title,
                            file_size='' if size is None else size,
                            file_caption='' if f_caption is None else f_caption
                        )
                    except Exception as e:
                        logger.exception("[send_all] caption format error: %s", e)
                        f_caption = f_caption
                if f_caption is None:
                    f_caption = f"{title}"
                try:
                    await bot.send_cached_media(
                        chat_id=userid,
                        file_id=file["file_id"],
                        caption=f_caption,
                        protect_content=True if ident == "filep" else False,
                        reply_markup=InlineKeyboardMarkup(
                            [[
                                InlineKeyboardButton('Support Group', url=GRP_LNK),
                                InlineKeyboardButton('Updates Channel', url=CHNL_LNK)
                            ], [
                                InlineKeyboardButton("Bot Owner", url=OWNER_LNK)
                            ]]
                        )
                    )
                except UserIsBlocked:
                    # If the user blocked the bot, remove them from DB
                    try:
                        await db.delete_user(int(userid))
                    except Exception:
                        pass
                except Exception as e:
                    logger.exception("[send_all send_cached_media] %s", e)
    except UserIsBlocked:
        try:
            await query.answer('Unblock the bot to receive files!', show_alert=True)
        except Exception:
            pass
    except PeerIdInvalid:
        try:
            await query.answer('Start the bot and click send all!', show_alert=True)
        except Exception:
            pass
    except Exception as e:
        logger.exception("[send_all] %s", e)
        try:
            await query.answer('Something went wrong. Try again later.', show_alert=True)
        except Exception:
            pass

# =========================
# get_cap (IMDB caption builder)
# =========================

async def get_poster(query_text: str, bulk: bool = False, id: bool = False, file: Optional[str] = None):
    """
    Fetch movie metadata using Cinemagoer (IMDB) — adapted from original code.
    Returns a dict or None.
    """
    try:
        if not id:
            q = (query_text.strip()).lower()
            title = q
            year = re.findall(r'[1-2]\d{3}$', q, re.IGNORECASE)
            if year:
                year = year[0]
                title = (q.replace(year, "")).strip()
            elif file is not None:
                year = re.findall(r'[1-2]\d{3}', file, re.IGNORECASE)
                year = year[0] if year else None
            else:
                year = None

            movieid_list = imdb.search_movie(title.lower(), results=10)
            if not movieid_list:
                return None
            if year:
                filtered = list(filter(lambda k: str(k.get('year')) == str(year), movieid_list))
                filtered = filtered if filtered else movieid_list
            else:
                filtered = movieid_list
            movie_candidates = list(filter(lambda k: k.get('kind') in ['movie', 'tv series'], filtered))
            movie_candidates = movie_candidates if movie_candidates else filtered
            if bulk:
                return movie_candidates
            movieid = movie_candidates[0].movieID
        else:
            movieid = query_text

        movie = imdb.get_movie(movieid)
        if not movie:
            return None

        if movie.get("original air date"):
            d = movie["original air date"]
        elif movie.get("year"):
            d = movie.get("year")
        else:
            d = "N/A"

        if not LONG_IMDB_DESCRIPTION:
            plot = movie.get('plot')
            if plot and len(plot) > 0:
                plot = plot[0]
        else:
            plot = movie.get('plot outline')

        if plot and len(plot) > 800:
            plot = plot[:800] + "..."

        return {
            'title': movie.get('title'),
            'votes': movie.get('votes'),
            'aka': list_to_str(movie.get("akas")),
            'seasons': movie.get("number of seasons"),
            'box_office': movie.get('box office'),
            'localized_title': movie.get('localized title'),
            'kind': movie.get("kind"),
            'imdb_id': f"tt{movie.get('imdbID')}",
            'cast': list_to_str(movie.get("cast")),
            'runtime': list_to_str(movie.get("runtimes")),
            'countries': list_to_str(movie.get("countries")),
            'certificates': list_to_str(movie.get("certificates")),
            'languages': list_to_str(movie.get("languages")),
            'director': list_to_str(movie.get("director")),
            'writer': list_to_str(movie.get("writer")),
            'producer': list_to_str(movie.get("producer")),
            'composer': list_to_str(movie.get("composer")),
            'cinematographer': list_to_str(movie.get("cinematographer")),
            'music_team': list_to_str(movie.get("music department")),
            'distributors': list_to_str(movie.get("distributors")),
            'release_date': d,
            'year': movie.get('year'),
            'genres': list_to_str(movie.get("genres")),
            'poster': movie.get('full-size cover url'),
            'plot': plot,
            'rating': str(movie.get("rating")),
            'url': f'https://www.imdb.com/title/tt{movieid}'
        }
    except Exception as e:
        logger.exception("[get_poster] %s", e)
        return None

async def get_cap(settings: dict, remaining_seconds: int, files: List[dict], query, total_results: int, search: str) -> str:
    """
    Build the caption sent to users after search.
    Re-uses your existing IMDB_TEMPLATE_TXT from Script.
    """
    try:
        if settings.get("imdb"):
            IMDB_CAP = temp.IMDB_CAP.get(query.from_user.id)
            if IMDB_CAP:
                cap = IMDB_CAP
                cap += "<b>\n\n<u>🍿 Your Movie Files 👇</u></b>\n\n"
                for file in files:
                    cap += f"<b>📁 <a href='https://telegram.me/{temp.U_NAME}?start=files_{file['file_id']}'>[{get_size(file['file_size'])}] {' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@') and not x.startswith('www.'), file['file_name'].split()))}\n\n</a></b>"
                return cap
            imdb = await get_poster(search, file=(files[0])["file_name"]) if settings.get("imdb") else None
            if imdb:
                TEMPLATE = script.IMDB_TEMPLATE_TXT
                cap = TEMPLATE.format(
                    qurey=search,
                    title=imdb['title'],
                    votes=imdb['votes'],
                    aka=imdb["aka"],
                    seasons=imdb["seasons"],
                    box_office=imdb['box_office'],
                    localized_title=imdb['localized_title'],
                    kind=imdb['kind'],
                    imdb_id=imdb["imdb_id"],
                    cast=imdb["cast"],
                    runtime=imdb["runtime"],
                    countries=imdb["countries"],
                    certificates=imdb["certificates"],
                    languages=imdb["languages"],
                    director=imdb["director"],
                    writer=imdb["writer"],
                    producer=imdb["producer"],
                    composer=imdb["composer"],
                    cinematographer=imdb["cinematographer"],
                    music_team=imdb["music_team"],
                    distributors=imdb["distributors"],
                    release_date=imdb['release_date'],
                    year=imdb['year'],
                    genres=imdb['genres'],
                    poster=imdb['poster'],
                    plot=imdb['plot'],
                    rating=imdb['rating'],
                    url=imdb['url'],
                    **locals()
                )
                cap += "<b>\n\n<u>🍿 Your Movie Files 👇</u></b>\n\n"
                for file in files:
                    file_label = ' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@') and not x.startswith('www.'), file['file_name'].split()))
                    cap += f"<b>📁 <a href='https://telegram.me/{temp.U_NAME}?start=files_{file['file_id']}'>[{get_size(file['file_size'])}] {file_label}\n\n</a></b>"
                return cap
            else:
                cap = f"<b>Tʜᴇ Rᴇꜱᴜʟᴛꜱ Fᴏʀ ☞ {search}\n\nRᴇǫᴜᴇsᴛᴇᴅ Bʏ ☞ {query.from_user.mention}\n\nʀᴇsᴜʟᴛ sʜᴏᴡ ɪɴ ☞ {remaining_seconds} sᴇᴄᴏɴᴅs\n\nᴘᴏᴡᴇʀᴇᴅ ʙʏ ☞ : {query.message.chat.title}\n\n⚠️ ᴀꜰᴛᴇʀ 5 ᴍɪɴᴜᴛᴇꜱ ᴛʜɪꜱ ᴍᴇꜱꜱᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ᴅᴇʟᴇᴛᴇᴅ 🗑️\n\n</b>"
                cap += "<b><u>🍿 Your Movie Files 👇</u></b>\n\n"
                for file in files:
                    file_label = ' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@') and not x.startswith('www.'), file['file_name'].split()))
                    cap += f"<b>📁 <a href='https://telegram.me/{temp.U_NAME}?start=files_{file['file_id']}'>[{get_size(file['file_size'])}] {file_label}\n\n</a></b>"
                return cap
        else:
            cap = f"<b>Tʜᴇ Rᴇꜱᴜʟᴛꜱ Fᴏʀ ☞ {search}\n\nRᴇǫᴜᴇsᴛᴇᴅ Bʏ ☞ {query.from_user.mention}\n\nʀᴇsᴜʟᴛ sʜᴏᴡ ɪɴ ☞ {remaining_seconds} sᴇᴄᴏɴᴅs\n\nᴘᴏᴡᴇʀᴇᴅ ʙʏ ☞ : {query.message.chat.title}\n\n⚠️ ᴀꜰᴛᴇʀ 5 ᴍɪɴᴜᴛᴇꜱ ᴛʜɪꜱ ᴍᴇꜱꜱᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ᴅᴇʟᴇᴛᴇᴅ 🗑️\n\n</b>"
            cap += "<b><u>🍿 Your Movie Files 👇</u></b>\n\n"
            for file in files:
                file_label = ' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@') and not x.startswith('www.'), file['file_name'].split()))
                cap += f"<b>📁 <a href='https://telegram.me/{temp.U_NAME}?start=files_{file['file_id']}'>[{get_size(file['file_size'])}] {file_label}\n\n</a></b>"
            return cap
    except Exception as e:
        logger.exception("[get_cap] %s", e)
        # fallback simple caption
        return f"Results for {search}"

# =========================
# Search / web helper (kept)
# =========================

async def search_gagala(text: str) -> List[str]:
    usr_agent = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/61.0.3163.100 Safari/537.36'
    }
    text_q = text.replace(" ", '+')
    url = f'https://www.google.com/search?q={text_q}'
    try:
        response = requests.get(url, headers=usr_agent, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        titles = soup.find_all('h3')
        return [title.getText() for title in titles]
    except Exception as e:
        logger.exception("[search_gagala] %s", e)
        return []

# =========================
# Settings helpers (DB wrapper)
# =========================

async def get_settings(group_id: int) -> dict:
    try:
        settings = await db.get_settings(group_id)
        return settings or {}
    except Exception:
        return {}

async def save_group_settings(group_id: int, key: str, value):
    try:
        current = await get_settings(group_id)
        current.update({key: value})
        await db.update_settings(group_id, current)
    except Exception as e:
        logger.exception("[save_group_settings] %s", e)

# =========================
# Time/format helpers
# =========================

async def get_seconds(time_string: str) -> int:
    def extract_value_and_unit(ts):
        value = ""
        unit = ""
        index = 0
        while index < len(ts) and ts[index].isdigit():
            value += ts[index]
            index += 1
        unit = ts[index:]
        if value:
            value = int(value)
        return value, unit
    value, unit = extract_value_and_unit(time_string)
    if unit == 's':
        return value
    elif unit == 'min':
        return value * 60
    elif unit == 'hour':
        return value * 3600
    elif unit == 'day':
        return value * 86400
    elif unit == 'month':
        return value * 86400 * 30
    elif unit == 'year':
        return value * 86400 * 365
    else:
        return 0

# =========================
# Misc: admin & subscription helpers
# =========================

async def pub_is_subscribed(bot, query, channel):
    btn = []
    for id in channel:
        try:
            chat = await bot.get_chat(int(id))
            try:
                await bot.get_chat_member(id, query.from_user.id)
            except UserNotParticipant:
                # create join button
                invite = None
                try:
                    invite = chat.invite_link
                except Exception:
                    invite = None
                if invite:
                    btn.append([InlineKeyboardButton(f'Join {chat.title}', url=invite)])
        except Exception:
            pass
    return btn

async def is_subscribed(bot, query):
    if REQUEST_TO_JOIN_MODE == True and join_db().isActive():
        try:
            user = await join_db().get_user(query.from_user.id)
            if user and user.get("user_id") == query.from_user.id:
                return True
            else:
                try:
                    user_data = await bot.get_chat_member(AUTH_CHANNEL, query.from_user.id)
                except UserNotParticipant:
                    return False
                except Exception:
                    return False
                else:
                    if user_data.status != enums.ChatMemberStatus.BANNED:
                        return True
        except Exception:
            return False
    else:
        try:
            user = await bot.get_chat_member(AUTH_CHANNEL, query.from_user.id)
        except UserNotParticipant:
            return False
        except Exception:
            return False
        else:
            if user.status != enums.ChatMemberStatus.BANNED:
                return True
    return False

# =========================
# Final notes & helpers
# =========================

# Utility to create verification message for sending to user
async def generate_verification_message(user_id: int, target_link: str) -> Tuple[str, str]:
    """
    Returns tuple: (short_verification_link, message_text)
    Short verification link is created via ShrinkMe (VERIFY_SHORTLINK_URL/API).
    Message instructs user to click link and shows tutorial fallback.
    """
    short = await get_verify_shorted_link(target_link)
    message = (
        "<b>🔐 Verification Required</b>\n\n"
        "To access this file you must complete a short verification step (open the link and view the page):\n\n"
        f"👉 {short}\n\n"
        f"If you need help, check: {VERIFY_TUTORIAL}"
    )
    return short, message

# Exported small helper to shorten any link (useful elsewhere)
async def shorten_link(link: str) -> str:
    if SHORTLINK_MODE:
        return await shrinkme_shorten(link)
    return link
