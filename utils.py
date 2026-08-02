import logging, asyncio, os, re, random, pytz, aiohttp, requests, string, json, http.client
from info import *
from imdb import Cinemagoer
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram import enums
from pyrogram.errors import *
from typing import Union
from Script import script
from datetime import datetime, date
from typing import List
from database.users_chats_db import db
from database.join_reqs import JoinReqs
from bs4 import BeautifulSoup
from shortzy import Shortzy
from urllib.parse import quote
import time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
join_db = JoinReqs

BTN_URL_REGEX = re.compile(r"(([^\[]+?) $$$$ (buttonurl|buttonalert):(?:/{0,2})(.+?)(:same)?)")

imdb = Cinemagoer(accessSystem="https")
TOKENS = {}
# Records when each verify token was issued (user.id -> {token: unix_time}).
# Used to detect "bypass bots" that skip the shortener's ad/timer steps and
# resolve straight to the final deep link almost instantly — a genuine
# shortener flow always takes real human time.
TOKEN_ISSUED_AT = {}
MIN_VERIFY_SECONDS = 100  # verifications completed faster than this (seconds) are flagged as bypassed
VERIFIED = {}
# Remembers the deep-link payload (e.g. "file_XXXX") a user was trying to
# open right before they got sent to verify. So once verification succeeds,
# the bot can auto-resume delivering that exact file instead of leaving the
# user with nothing but a "verification complete" message.
PENDING = {}
BANNED = {}
SECOND_SHORTENER = {}
SMART_OPEN = '“'
SMART_CLOSE = '”'
START_CHAR = ("'", '"', SMART_OPEN)  # FIXED: Single quotes

class temp(object):
    BANNED_USERS = []
    BANNED_CHATS = []
    ME = None
    BOT = None
    CURRENT = int(os.environ.get("SKIP", 2))
    CANCEL = False
    MELCOW = {}
    U_NAME = None
    B_NAME = None
    B_LINK = None
    GETALL = {}
    SHORT = {}
    SETTINGS = {}
    IMDB_CAP = {}


async def pub_is_subscribed(bot, query, channel):
    btn = []
    for id in channel:
        chat = await bot.get_chat(int(id))
        try:
            await bot.get_chat_member(id, query.from_user.id)
        except UserNotParticipant:
            btn.append([InlineKeyboardButton(f'Join {chat.title}', url=chat.invite_link)])
        except Exception as e:
            pass
    return btn


async def is_subscribed(bot, query):
    try:
        user_data = await bot.get_chat_member(AUTH_CHANNEL, query.from_user.id)
    except UserNotParticipant:
        return False
    except Exception as e:
        logger.exception(e)
        return False
    else:
        if user_data.status != enums.ChatMemberStatus.BANNED:
            return True
    return False


async def get_poster(query, bulk=False, id=False, file=None):
    if not id:
        query = (query.strip()).lower()
        title = query
        year = re.findall(r'[1-2]\d{3}$', query, re.IGNORECASE)
        if year:
            year = list_to_str(year[:1])
            title = (query.replace(year, "")).strip()
        elif file is not None:
            year = re.findall(r'[1-2]\d{3}', file, re.IGNORECASE)
            if year:
                year = list_to_str(year[:1])
        else:
            year = None
        movieid = await asyncio.to_thread(lambda: imdb.search_movie(title.lower(), results=10))
        if not movieid:
            return None
        if year:
            filtered = list(filter(lambda k: str(k.get('year')) == str(year), movieid))
            if not filtered:
                filtered = movieid
        else:
            filtered = movieid
        movieid = list(filter(lambda k: k.get('kind') in ['movie', 'tv series'], filtered))
        if not movieid:
            movieid = filtered
        if bulk:
            return movieid
        movieid = movieid[0].movieID
    else:
        movieid = query
    movie = await asyncio.to_thread(imdb.get_movie, movieid)
    if not movie:
        return None
    if movie.get("original air date"):
        date = movie["original air date"]
    elif movie.get("year"):
        date = movie.get("year")
    else:
        date = "N/A"
    plot = ""
    if not LONG_IMDB_DESCRIPTION:
        plot = movie.get('plot')
        if plot and len(plot) > 0:
            plot = plot[0]
    else:
        plot = movie.get('plot outline')
    if plot and len(plot) > 800:
        plot = plot[0:800] + "..."
    return {
        'title': movie.get('title'),
        'votes': movie.get('votes'),
        "aka": list_to_str(movie.get("akas")),
        "seasons": movie.get("number of seasons"),
        "box_office": movie.get('box office'),
        'localized_title': movie.get('localized title'),
        'kind': movie.get("kind"),
        "imdb_id": f"tt{movie.get('imdbID')}",
        "cast": list_to_str(movie.get("cast")),
        "runtime": list_to_str(movie.get("runtimes")),
        "countries": list_to_str(movie.get("countries")),
        "certificates": list_to_str(movie.get("certificates")),
        "languages": list_to_str(movie.get("languages")),
        "director": list_to_str(movie.get("director")),
        "writer": list_to_str(movie.get("writer")),
        "producer": list_to_str(movie.get("producer")),
        "composer": list_to_str(movie.get("composer")),
        "cinematographer": list_to_str(movie.get("cinematographer")),
        "music_team": list_to_str(movie.get("music department")),
        "distributors": list_to_str(movie.get("distributors")),
        'release_date': date,
        'year': movie.get('year'),
        'genres': list_to_str(movie.get("genres")),
        'poster': movie.get('full-size cover url'),
        'plot': plot,
        'rating': str(movie.get("rating")),
        'url': f'https://www.imdb.com/title/tt{movieid}'
    }


async def broadcast_messages(user_id, message):
    try:
        await message.copy(chat_id=user_id)
        return True, "Success"
    except FloodWait as e:
        await asyncio.sleep(e.x)
        return await broadcast_messages(user_id, message)
    except InputUserDeactivated:
        await db.delete_user(int(user_id))
        logging.info(f"{user_id}-Removed from Database, since deleted account.")
        return False, "Deleted"
    except UserIsBlocked:
        await db.delete_user(int(user_id))
        logging.info(f"{user_id} -Blocked the bot.")
        return False, "Blocked"
    except PeerIdInvalid:
        await db.delete_user(int(user_id))
        logging.info(f"{user_id} - PeerIdInvalid")
        return False, "Error"
    except Exception as e:
        return False, "Error"


async def broadcast_messages_group(chat_id, message):
    try:
        kd = await message.copy(chat_id=chat_id)
        try:
            await kd.pin()
        except:
            pass
        return True, "Success"
    except FloodWait as e:
        await asyncio.sleep(e.x)
        return await broadcast_messages_group(chat_id, message)
    except Exception as e:
        return False, "Error"


async def search_gagala(text):
    usr_agent = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/61.0.3163.100 Safari/537.36'
    }
    text = text.replace(" ", '+')
    url = f'https://www.google.com/search?q={text}'
    response = requests.get(url, headers=usr_agent)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    titles = soup.find_all('h3')
    return [title.getText() for title in titles]


async def get_settings(group_id):
    settings = await db.get_settings(group_id)
    return settings


async def save_group_settings(group_id, key, value):
    current = await get_settings(group_id)
    current.update({key: value})
    await db.update_settings(group_id, current)


def get_size(size):
    units = ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB"]
    size = float(size)
    i = 0
    while size >= 1024.0 and i < len(units):
        i += 1
        size /= 1024.0
    return "%.2f %s" % (size, units[i])


def split_list(l, n):
    for i in range(0, len(l), n):
        yield l[i:i + n]


def get_file_id(msg: Message):
    if msg.media:
        for message_type in (
            "photo",
            "animation",
            "audio",
            "document",
            "video",
            "video_note",
            "voice",
            "sticker"
        ):
            obj = getattr(msg, message_type)
            if obj:
                setattr(obj, "message_type", message_type)
                return obj


def extract_user(message: Message) -> Union[int, str]:
    user_id = None
    user_first_name = None
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        user_first_name = message.reply_to_message.from_user.first_name
    elif len(message.command) > 1:
        if (
            len(message.entities) > 1 and
            message.entities[1].type == enums.MessageEntityType.TEXT_MENTION
        ):
            required_entity = message.entities[1]
            user_id = required_entity.user.id
            user_first_name = required_entity.user.first_name
        else:
            user_id = message.command[1]
            user_first_name = user_id
        try:
            user_id = int(user_id)
        except ValueError:
            pass
    else:
        user_id = message.from_user.id
        user_first_name = message.from_user.first_name
    return (user_id, user_first_name)


def list_to_str(k):
    if not k:
        return "N/A"
    elif len(k) == 1:
        return str(k[0])
    elif MAX_LIST_ELM:
        k = k[:int(MAX_LIST_ELM)]
        return ' '.join(f'{elem}, ' for elem in k)
    else:
        return ' '.join(f'{elem}, ' for elem in k)


def last_online(from_user):
    time = ""
    if from_user.is_bot:
        time += "Bot :("
    elif from_user.status == enums.UserStatus.RECENTLY:
        time += "Recently"
    elif from_user.status == enums.UserStatus.LAST_WEEK:
        time += "Within the last week"
    elif from_user.status == enums.UserStatus.LAST_MONTH:
        time += "Within the last month"
    elif from_user.status == enums.UserStatus.LONG_AGO:
        time += "A long time ago :("
    elif from_user.status == enums.UserStatus.ONLINE:
        time += "Currently Online"
    elif from_user.status == enums.UserStatus.OFFLINE:
        time += from_user.last_online_date.strftime("%a, %d %b %Y, %H:%M:%S")
    return time


def split_quotes(text: str) -> List:
    if not any(text.startswith(char) for char in START_CHAR):
        return text.split(None, 1)
    counter = 1
    while counter < len(text):
        if text[counter] == "\\":
            counter += 1
        elif text[counter] == text[0] or (text[0] == SMART_OPEN and text[counter] == SMART_CLOSE):
            break
        counter += 1
    else:
        return text.split(None, 1)
    key = remove_escapes(text[1:counter].strip())
    rest = text[counter + 1:].strip()
    if not key:
        key = text[0] + text[0]
    return list(filter(None, [key, rest]))


def gfilterparser(text, keyword):
    if "buttonalert" in text:
        text = (text.replace("\n", "\n").replace("\t", "\t"))
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
        text = (text.replace("\n", "\n").replace("\t", "\t"))
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


def remove_escapes(text: str) -> str:
    res = ""
    is_escaped = False
    for counter in range(len(text)):
        if is_escaped:
            res += text[counter]
            is_escaped = False
        elif text[counter] == "\\":
            is_escaped = True
        else:
            res += text[counter]
    return res


def humanbytes(size):
    if not size:
        return ""
    power = 2**10
    n = 0
    Dic_powerN = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + Dic_powerN[n] + 'B'


async def get_clone_shortlink(link, url, api):
    shortzy = Shortzy(api_key=api, base_site=url)
    link = await shortzy.convert(link)
    return link


async def get_shortlink(chat_id, link):
    settings = await get_settings(chat_id)
    if 'shortlink' in settings.keys():
        URL = settings['shortlink']
        API = settings['shortlink_api']
    else:
        URL = SHORTLINK_URL
        API = SHORTLINK_API
    if URL.startswith("shorturllink") or URL.startswith("terabox.in") or URL.startswith("urlshorten.in"):
        URL = SHORTLINK_URL
        API = SHORTLINK_API
    if URL == "api.shareus.io":
        url = f'https://{URL}/easy_api'
        params = {
            "key": API,
            "link": link,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, raise_for_status=True, ssl=False) as response:
                    data = await response.text()
                    return data
        except Exception as e:
            logger.error(e)
            return link
    else:
        shortzy = Shortzy(api_key=API, base_site=URL)
        link = await shortzy.convert(link)
        return link


async def get_tutorial(chat_id):
    settings = await get_settings(chat_id)
    return settings['tutorial']


# ================== [INDIAEARNX VERIFICATION SHORTENER] ==================

async def get_verify_shorted_link(link, url, api):
    API = api.strip()
    URL = url.lower().strip()
    logger.info(f"[VERIFY] Using API: {API[-6:]}... URL: {URL}")

    if "indiaearnx.com" in URL:
        api_url = "https://indiaearnx.com/api"
        params = {"api": API, "url": quote(link)}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, params=params) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get("status") == "success":
                            shortened = result.get("shortenedUrl")
                            if shortened:
                                shortened = shortened.replace("\\/", "/")
                                logger.info(f"[INDIAEARNX] Shortened: {shortened}")
                                return shortened
        except Exception as e:
            logger.error(f"IndiaEarnX error: {e}")
        return link
    else:
        return link

# ================== [VERIFICATION SYSTEM] ==================

class _FallbackUser:
    """Minimal stand-in for a Pyrogram User when bot.get_users() can't
    resolve the peer (e.g. Telegram raises BOT_METHOD_INVALID /
    'Phone number not found' for peers the bot hasn't cached yet).
    Only `.id`, `.first_name` and `.mention` are used by the verification
    functions below, so this is enough to keep verification working even
    when the live lookup fails."""
    def __init__(self, uid):
        self.id = int(uid)
        self.first_name = "User"
        self.mention = f"[User](tg://user?id={self.id})"


async def _resolve_user(bot, userid):
    try:
        return await bot.get_users(userid)
    except Exception as e:
        logger.warning(f"[VERIFY] bot.get_users({userid}) failed ({e}); using fallback user object.")
        return _FallbackUser(userid)


async def check_token(bot, userid, token):
    user = await _resolve_user(bot, userid)
    if not await db.is_user_exist(user.id):
        await db.add_user(user.id, user.first_name)
        try:
            await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))
        except Exception:
            pass
    if user.id in TOKENS.keys():
        TKN = TOKENS[user.id]
        if token in TKN.keys():
            is_used = TKN[token]
            if is_used == True:
                return False
            issued_at = TOKEN_ISSUED_AT.get(user.id, {}).get(token)
            if issued_at is not None and (time.time() - issued_at) < MIN_VERIFY_SECONDS:
                # Redeemed way too fast for a real shortener flow (ad page +
                # timer) — almost certainly a bypass tool. Consume the token
                # so it can't be retried, and tell the caller to reject it.
                TKN[token] = True
                return "BYPASS"
            return True
    else:
        return False


async def get_token(bot, userid, link, pending_data=None):
    user = await _resolve_user(bot, userid)
    if not await db.is_user_exist(user.id):
        await db.add_user(user.id, user.first_name)
        try:
            await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))
        except Exception:
            pass
    if pending_data:
        PENDING[user.id] = pending_data
    token = ''.join(random.choices(string.ascii_letters + string.digits, k=7))
    TOKENS[user.id] = {token: False}
    TOKEN_ISSUED_AT[user.id] = {token: time.time()}
    link = f"{link}verify-{user.id}-{token}"
    shortened_verify_url = await get_verify_shorted_link(link, VERIFY_SHORTLINK_URL, VERIFY_SHORTLINK_API)
    if VERIFY_SECOND_SHORTNER == True:
        snd_link = await get_verify_shorted_link(shortened_verify_url, VERIFY_SND_SHORTLINK_URL, VERIFY_SND_SHORTLINK_API)
        return str(snd_link)
    else:
        return str(shortened_verify_url)


async def verify_user(bot, userid, token):
    user = await _resolve_user(bot, userid)
    if not await db.is_user_exist(user.id):
        await db.add_user(user.id, user.first_name)
        try:
            await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))
        except Exception:
            pass
    TOKENS[user.id] = {token: True}
    tz = pytz.timezone('Asia/Kolkata')
    today = date.today()
    VERIFIED[user.id] = str(today)
    # Persist to DB too, so verification survives a bot restart/redeploy —
    # the in-memory VERIFIED dict alone is wiped whenever the process restarts.
    try:
        await db.set_verified(user.id, str(today))
    except Exception as e:
        logger.warning(f"[VERIFY] Failed to persist verification to DB for {user.id}: {e}")
    return PENDING.pop(user.id, None)


async def check_verification(bot, userid):
    user = await _resolve_user(bot, userid)
    if not await db.is_user_exist(user.id):
        await db.add_user(user.id, user.first_name)
        try:
            await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))
        except Exception:
            pass
    tz = pytz.timezone('Asia/Kolkata')
    today = date.today()
    if user.id in VERIFIED.keys():
        EXP = VERIFIED[user.id]
        years, month, day = EXP.split('-')
        comp = date(int(years), int(month), int(day))
        if comp >= today:
            return True
        else:
            return False
    # Not in the in-memory cache (most likely the bot restarted and lost it) —
    # fall back to the persistent DB record before concluding "not verified".
    try:
        if await db.is_verified_today(user.id):
            VERIFIED[user.id] = str(today)  # repopulate the fast in-memory cache
            return True
    except Exception as e:
        logger.warning(f"[VERIFY] DB fallback check failed for {user.id}: {e}")
    return False

# ================== [PREMIUM FEATURE GATE] ==================
# Master switch: PREMIUM_AND_REFERAL_MODE (info.py / env var).
#
#   PREMIUM_AND_REFERAL_MODE = False -> premium system is OFF completely.
#       Every premium-only feature (stream button, audio/subs info,
#       PM search, request priority) behaves as if EVERY user is
#       premium, i.e. it works the same for all users.
#
#   PREMIUM_AND_REFERAL_MODE = True -> premium system is ON.
#       Only users with an active premium subscription (db.has_premium_access)
#       get the premium-only features; everyone else is treated as a normal user.
async def is_premium_user(user_id):
    if not PREMIUM_AND_REFERAL_MODE:
        return True
    return await db.has_premium_access(user_id)

# ================== [FILE SHORTENER — INDIAEARNX] ==================

async def shorten_with_shrinkme(link):
    api_key = VERIFY_SHORTLINK_API
    if not api_key:
        return link
    api_url = "https://indiaearnx.com/api"
    params = {"api": api_key, "url": quote(link)}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, params=params) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get("status") == "success":
                        shortened = result.get("shortenedUrl")
                        if shortened:
                            return shortened.replace("\\/", "/")
    except Exception as e:
        logger.error(f"IndiaEarnX file error: {e}")
    return link


# ================== [SEND ALL & CAPTION] ==================

async def send_all(bot, userid, files, ident, chat_id, user_name, query):
    settings = await get_settings(chat_id)
    if 'is_shortlink' in settings.keys():
        ENABLE_SHORTLINK = settings['is_shortlink']
    else:
        await save_group_settings(chat_id, 'is_shortlink', False)
        ENABLE_SHORTLINK = False
    try:
        if ENABLE_SHORTLINK:
            for file in files:
                title = file["file_name"]
                size = get_size(file["file_size"])
                if not await db.has_premium_access(userid) and SHORTLINK_MODE == True:
                    file_link = f"https://telegram.me/{temp.U_NAME}?start=files_{file['file_id']}"
                    ######111111
                    shortened_link = await shorten_with_shrinkme(file_link)
                    web_url = f"https://married-viper-goflixbots-a375dc8b.koyeb.app/?url={shortened_link}"  
                    await bot.send_message(
                        chat_id=userid,
                        text=f"<b>Hey there {user_name}\n\nSecure link to your file has successfully been generated please click download button\n\nFile Name : {title}\nFile Size : {size}</b>",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔐 Verify & Download", url=web_url)]
                        ])
                    )
                    #####222222222222
                    
                    f_caption = file["caption"]
                    title = file["file_name"]
                    size = get_size(file["file_size"])
                    if CUSTOM_FILE_CAPTION:
                        try:
                            f_caption = CUSTOM_FILE_CAPTION.format(
                                file_name='' if title is None else title,
                                file_size='' if size is None else size,
                                file_caption='' if f_caption is None else f_caption
                            )
                        except Exception as e:
                            print(e)
                            f_caption = f_caption
                    if f_caption is None:
                        f_caption = f"{title}"
                    await bot.send_cached_media(
                        chat_id=userid,
                        file_id=file["file_id"],
                        caption=f_caption,
                        protect_content=True if ident == "filep" else False,
                        reply_markup=InlineKeyboardMarkup(
                            [[InlineKeyboardButton('Support Group', url=GRP_LNK), InlineKeyboardButton('Updates Channel', url=CHNL_LNK)],
                             [InlineKeyboardButton("Bot Owner", url=OWNER_LNK)]]
                        )
                    )
        else:
            for file in files:
                f_caption = file["caption"]
                title = file["file_name"]
                size = get_size(file["file_size"])
                if CUSTOM_FILE_CAPTION:
                    try:
                        f_caption = CUSTOM_FILE_CAPTION.format(
                            file_name='' if title is None else title,
                            file_size='' if size is None else size,
                            file_caption='' if f_caption is None else f_caption
                        )
                    except Exception as e:
                        print(e)
                        f_caption = f_caption
                if f_caption is None:
                    f_caption = f"{title}"
                await bot.send_cached_media(
                    chat_id=userid,
                    file_id=file["file_id"],
                    caption=f_caption,
                    protect_content=True if ident == "filep" else False,
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton('Support Group', url=GRP_LNK), InlineKeyboardButton('Updates Channel', url=CHNL_LNK)],
                         [InlineKeyboardButton("Bot Owner", url=OWNER_LNK)]]
                    )
                )
    except UserIsBlocked:
        await query.answer('Unblock the bot mahn!', show_alert=True)
    except PeerIdInvalid:
        await query.answer('Hey, Start Bot First And Click Send All', show_alert=True)
    except Exception as e:
        await query.answer('Hey, Start Bot First And Click Send All', show_alert=True)


async def get_cap(settings, remaining_seconds, files, query, total_results, search):
    short_links = {}
    chat_id = query.message.chat.id
    for file in files:
        orig_link = f"https://telegram.me/{temp.U_NAME}?start=files_{file['file_id']}"
        shortened = await shorten_with_shrinkme(orig_link)
        short_links[file['file_id']] = shortened

    if settings["imdb"]:
        IMDB_CAP = temp.IMDB_CAP.get(query.from_user.id)
        if IMDB_CAP:
            cap = IMDB_CAP
            cap += "<b>\n\n<u>Your Movie Files</u></b>\n\n"
            for file in files:
                cap += f"<b><a href='{short_links[file['file_id']]}'>[{get_size(file['file_size'])}] {' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@') and not x.startswith('www.'), file['file_name'].split()))}\n\n</a></b>"
        else:
            imdb = await get_poster(search, file=(files[0])["file_name"]) if settings["imdb"] else None
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
                cap += "<b>\n\n<u>Your Movie Files</u></b>\n\n"
                for file in files:
                    cap += f"<b><a href='{short_links[file['file_id']]}'>[{get_size(file['file_size'])}] {' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@') and not x.startswith('www.'), file['file_name'].split()))}\n\n</a></b>"
            else:
                cap = f"<b>The Results For {search}\n\nRequested By {query.from_user.mention}\n\nResult show in {remaining_seconds} seconds\n\nPowered by : {query.message.chat.title}\n\nAfter 5 minutes this message will be automatically deleted\n\n</b>"
                cap += "<b><u>Your Movie Files</u></b>\n\n"
                for file in files:
                    cap += f"<b><a href='{short_links[file['file_id']]}'>[{get_size(file['file_size'])}] {' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@') and not x.startswith('www.'), file['file_name'].split()))}\n\n</a></b>"
    else:
        cap = f"<b>The Results For {search}\n\nRequested By {query.from_user.mention}\n\nResult show in {remaining_seconds} seconds\n\nPowered by : {query.message.chat.title} \n\nAfter 5 minutes this message will be automatically deleted\n\n</b>"
        cap += "<b><u>Your Movie Files</u></b>\n\n"
        for file in files:
            cap += f"<b><a href='{short_links[file['file_id']]}'>[{get_size(file['file_size'])}] {' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@') and not x.startswith('www.'), file['file_name'].split()))}\n\n</a></b>"
    return cap


async def get_seconds(time_string):
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
    elif unit == 'week' or unit == 'w':
        return value * 86400 * 7
    elif unit == 'month':
        return value * 86400 * 30
    elif unit == 'year':
        return value * 86400 * 365
    else:
        return 0
