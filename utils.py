# Don't Remove Credit @VJ_Bots
# Subscribe YouTube Channel For Amazing Bot @Tech_VJ
# Ask Doubt on telegram @KingVJ01
import logging, asyncio, os, re, random, pytz, aiohttp, requests, string, json
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

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
join_db = JoinReqs

# Fixed regex (your original had $$$$ — removed)
BTN_URL_REGEX = re.compile(r"(\[([^\[]+?)\]\((buttonurl|buttonalert):(?:/{0,2})(.+?)(:same)?\))")

imdb = Cinemagoer()
TOKENS = {}
VERIFIED = {}
BANNED = {}
SMART_OPEN = '“'
SMART_CLOSE = '”'
START_CHAR = ('\'', '"', SMART_OPEN)

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
        except Exception:
            pass
    return btn


async def is_subscribed(bot, query):
    if REQUEST_TO_JOIN_MODE and join_db().isActive():
        try:
            user = await join_db().get_user(query.from_user.id)
            if user and user["user_id"] == query.from_user.id:
                return True
            try:
                user_data = await bot.get_chat_member(AUTH_CHANNEL, query.from_user.id)
            except UserNotParticipant:
                pass
            except Exception as e:
                logger.exception(e)
            else:
                if user_data.status != enums.ChatMemberStatus.BANNED:
                    return True
        except Exception as e:
            logger.exception(e)
            return False
    else:
        try:
            user = await bot.get_chat_member(AUTH_CHANNEL, query.from_user.id)
        except UserNotParticipant:
            pass
        except Exception as e:
            logger.exception(e)
        else:
            if user.status != enums.ChatMemberStatus.BANNED:
                return True
        return False


async def get_poster(query, bulk=False, id=False, file=None):
    if not id:
        query = query.strip().lower()
        title = query
        year = re.findall(r'[1-2]\d{3}$', query, re.IGNORECASE)
        if year:
            year = list_to_str(year[:1])
            title = query.replace(year, "").strip()
        elif file:
            year = re.findall(r'[1-2]\d{3}', file, re.IGNORECASE)
            if year:
                year = list_to_str(year[:1])
        else:
            year = None
        movieid = imdb.search_movie(title.lower(), results=10)
        if not movieid:
            return None
        if year:
            filtered = [k for k in movieid if str(k.get('year')) == str(year)]
            if not filtered:
                filtered = movieid
        else:
            filtered = movieid
        movieid = [k for k in filtered if k.get('kind') in ['movie', 'tv series']]
        if not movieid:
            movieid = filtered
        if bulk:
            return movieid
        movieid = movieid[0].movieID
    else:
        movieid = query
    movie = imdb.get_movie(movieid)
    if not movie:
        return None
    date = movie.get("original air date") or movie.get("year") or "N/A"
    plot = ""
    if not LONG_IMDB_DESCRIPTION:
        plot = movie.get('plot')
        if plot:
            plot = plot[0]
    else:
        plot = movie.get('plot outline')
    if plot and len(plot) > 800:
        plot = plot[:800] + "..."
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
    except (InputUserDeactivated, UserIsBlocked, PeerIdInvalid):
        await db.delete_user(int(user_id))
        return False, "Deleted/Blocked"
    except Exception:
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
    except Exception:
        return False, "Error"


async def search_gagala(text):
    usr_agent = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/61.0.3163.100 Safari/537.36'
    }
    text = text.replace(" ", '+')
    url = f'https://www.google.com/search?q={text}'
    response = requests.get(url, headers=usr_agent)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    return [title.getText() for title in soup.find_all('h3')]


async def get_settings(group_id):
    return await db.get_settings(group_id)


async def save_group_settings(group_id, key, value):
    current = await get_settings(group_id)
    current[key] = value
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
        for t in ("photo", "animation", "audio", "document", "video", "video_note", "voice", "sticker"):
            obj = getattr(msg, t)
            if obj:
                setattr(obj, "message_type", t)
                return obj


def extract_user(message: Message) -> Union[int, str]:
    user_id = message.from_user.id
    user_first_name = message.from_user.first_name
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        user_first_name = message.reply_to_message.from_user.first_name
    elif len(message.command) > 1:
        if len(message.entities) > 1 and message.entities[1].type == enums.MessageEntityType.TEXT_MENTION:
            user_id = message.entities[1].user.id
            user_first_name = message.entities[1].user.first_name
        else:
            user_id = message.command[1]
            user_first_name = user_id
        try:
            user_id = int(user_id)
        except ValueError:
            pass
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
    if from_user.is_bot:
        return "Bot"
    status = from_user.status
    if status == enums.UserStatus.RECENTLY:
        return "Recently"
    elif status == enums.UserStatus.LAST_WEEK:
        return "Within the last week"
    elif status == enums.UserStatus.LAST_MONTH:
        return "Within the last month"
    elif status == enums.UserStatus.LONG_AGO:
        return "A long time ago"
    elif status == enums.UserStatus.ONLINE:
        return "Currently Online"
    elif status == enums.UserStatus.OFFLINE:
        return from_user.last_online_date.strftime("%a, %d %b %Y, %H:%M:%S")
    return ""


def split_quotes(text: str) -> List:
    if not any(text.startswith(c) for c in START_CHAR):
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


def remove_escapes(text: str) -> str:
    res = ""
    escaped = False
    for c in text:
        if escaped:
            res += c
            escaped = False
        elif c == "\\":
            escaped = True
        else:
            res += c
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
    return f"{round(size, 2)} {Dic_powerN[n]}B"


async def get_clone_shortlink(link, url, api):
    shortzy = Shortzy(api_key=api, base_site=url)
    return await shortzy.convert(link)


async def get_shortlink(chat_id, link):
    settings = await get_settings(chat_id)
    URL = settings.get('shortlink', SHORTLINK_URL)
    API = settings.get('shortlink_api', SHORTLINK_API)
    if URL == "api.shareus.io":
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f'https://{URL}/easy_api', params={"key": API, "link": link}, ssl=False) as resp:
                    return await resp.text()
        except:
            return link
    else:
        shortzy = Shortzy(api_key=API, base_site=URL)
        return await shortzy.convert(link)


async def get_tutorial(chat_id):
    return (await get_settings(chat_id)).get('tutorial', '')


# ================== [INDIAEARNX VERIFICATION SHORTENER] ==================

async def get_verify_shorted_link(link, url, api):
    API = api.strip()
    URL = url.lower().strip()
    logger.info(f"[VERIFY] API: {API[-6:]}... | URL: {URL} | Link: {link[:50]}...")

    if "indiaearnx.com" not in URL:
        logger.warning("[VERIFY] Not IndiaEarnX, skipping")
        return link

    api_url = "https://indiaearnx.com/api"
    params = {"api": API, "url": quote(link)}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, params=params, timeout=10) as resp:
                logger.info(f"[INDIAEARNX] Status: {resp.status}")
                if resp.status != 200:
                    logger.error(f"[INDIAEARNX] HTTP {resp.status}")
                    return link
                data = await resp.json()
                short = data.get("shortenedUrl") or data.get("short_url")
                if short and short.startswith("http"):
                    short = short.replace("\\/", "/")
                    logger.info(f"[INDIAEARNX] SUCCESS: {short}")
                    return short
                else:
                    logger.warning(f"[INDIAEARNX] Invalid short URL: {data}")
    except Exception as e:
        logger.error(f"[INDIAEARNX] Exception: {e}")

    logger.warning("[INDIAEARNX] Failed → fallback")
    return link


# ================== [INDIAEARNX FILE SHORTENER] ==================

async def shorten_with_indiaearnx(link):
    if not VERIFY_SHORTLINK_API:
        return link
    api_url = "https://indiaearnx.com/api"
    params = {"api": VERIFY_SHORTLINK_API, "url": quote(link)}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, params=params) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    short = result.get("shortenedUrl")
                    if short:
                        return short.replace("\\/", "/")
    except Exception as e:
        logger.error(f"IndiaEarnX file error: {e}")
    return link


# ================== [VERIFICATION SYSTEM] ==================

async def check_token(bot, userid, token):
    user = await bot.get_users(userid)
    if not await db.is_user_exist(user.id):
        await db.add_user(user.id, user.first_name)
        await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))
    return user.id in TOKENS and token in TOKENS[user.id] and not TOKENS[user.id][token]


async def get_token(bot, userid, base_tme_link):
    user = await bot.get_users(userid)
    if not await db.is_user_exist(user.id):
        await db.add_user(user.id, user.first_name)
        await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))
    
    token = ''.join(random.choices(string.ascii_letters + string.digits, k=7))
    raw_link = f"{base_tme_link}verify-{userid}-{token}"
    TOKENS[userid] = {token: False}

    short_link = await get_verify_shorted_link(raw_link, VERIFY_SHORTLINK_URL, VERIFY_SHORTLINK_API)
    
    return short_link if "indiaearnx.com" in short_link else raw_link


async def verify_user(bot, userid, token):
    user = await bot.get_users(userid)
    if not await db.is_user_exist(user.id):
        await db.add_user(user.id, user.first_name)
        await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))
    TOKENS[userid] = {token: True}
    VERIFIED[userid] = date.today().isoformat()


async def check_verification(bot, userid):
    user = await bot.get_users(userid)
    if not await db.is_user_exist(user.id):
        await db.add_user(user.id, user.first_name)
        await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))
    if userid in VERIFIED:
        exp = datetime.strptime(VERIFIED[userid], "%Y-%m-%d").date()
        return exp >= date.today()
    return False


# ================== [SEND ALL & CAPTION] ==================

async def send_all(bot, userid, files, ident, chat_id, user_name, query):
    settings = await get_settings(chat_id)
    ENABLE_SHORTLINK = settings.get('is_shortlink', False)

    try:
        if ENABLE_SHORTLINK and not await db.has_premium_access(userid) and SHORTLINK_MODE:
            for file in files:
                file_link = f"https://telegram.me/{temp.U_NAME}?start=files_{file['file_id']}"
                short_link = await shorten_with_indiaearnx(file_link)
                await bot.send_message(
                    chat_id=userid,
                    text=f"<b>Hey {user_name}\n\nFile: {file['file_name']}\nSize: {get_size(file['file_size'])}</b>",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Download", url=short_link)]])
                )
        else:
            for file in files:
                caption = file.get("caption") or file["file_name"]
                if CUSTOM_FILE_CAPTION:
                    try:
                        caption = CUSTOM_FILE_CAPTION.format(
                            file_name=file["file_name"],
                            file_size=get_size(file["file_size"]),
                            file_caption=file.get("caption", "")
                        )
                    except:
                        pass
                await bot.send_cached_media(
                    chat_id=userid,
                    file_id=file["file_id"],
                    caption=caption,
                    protect_content=(ident == "filep"),
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton('Support Group', url=GRP_LNK), InlineKeyboardButton('Updates Channel', url=CHNL_LNK)],
                        [InlineKeyboardButton("Bot Owner", url=OWNER_LNK)]
                    ])
                )
    except Exception as e:
        await query.answer('Start Bot First!', show_alert=True)


async def get_cap(settings, remaining_seconds, files, query, total_results, search):
    short_links = {}
    for file in files:
        orig = f"https://telegram.me/{temp.U_NAME}?start=files_{file['file_id']}"
        short_links[file['file_id']] = await shorten_with_indiaearnx(orig)

    cap = ""
    if settings.get("imdb"):
        imdb_data = await get_poster(search, file=files[0]["file_name"]) if settings["imdb"] else None
        if imdb_data:
            cap = script.IMDB_TEMPLATE_TXT.format(**imdb_data, **locals())
        else:
            cap = f"<b>Results for: {search}\nBy: {query.from_user.mention}\nAuto-delete in {remaining_seconds}s</b>"
    else:
        cap = f"<b>Results for: {search}\nBy: {query.from_user.mention}\nAuto-delete in {remaining_seconds}s</b>"

    cap += "\n\n<u>Your Files</u>\n\n"
    for file in files:
        name = ' '.join([x for x in file['file_name'].split() if not x.startswith(('@', '[', 'www.'))])
        cap += f"<b><a href='{short_links[file['file_id']]}'>[{get_size(file['file_size'])}] {name}</a></b>\n\n"
    return cap


async def get_seconds(time_string):
    value = ""
    i = 0
    while i < len(time_string) and time_string[i].isdigit():
        value += time_string[i]
        i += 1
    unit = time_string[i:].lower()
    value = int(value) if value else 0
    return value * {'s': 1, 'min': 60, 'hour': 3600, 'day': 86400}.get(unit, 0)
