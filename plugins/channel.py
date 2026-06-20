# Don't Remove Credit @VJ_Botz
# Subscribe YouTube Channel For Amazing Bot @Tech_VJ
# Ask Doubt on telegram @KingVJ01
import re
import logging
import asyncio
from datetime import datetime
from collections import defaultdict
from plugins.Dreamxfutures.Imdbposter import fetch_image, get_movie_details, get_movie_detailsx
from database.users_chats_db import db
from pyrogram import Client, filters, enums
from info import (
    CHANNELS, MOVIE_UPDATE_CHANNEL, LINK_PREVIEW, ABOVE_PREVIEW,
    LANDSCAPE_POSTER, TMDB_POSTER, MOVIE_UPDATE_NOTIFICATION
)
from Script import script
from database.ia_filterdb import save_file
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import temp
from pymongo.errors import PyMongoError, DuplicateKeyError
from pyrogram.errors import MessageIdInvalid, MessageNotModified, FloodWait
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from info import BAD_WORDS
except ImportError:
    BAD_WORDS = set()

IGNORE_WORDS = {
    "rarbg", "dub", "sub", "sample", "mkv", "aac", "combined",
    "action", "adventure", "animation", "biography", "comedy", "crime",
    "documentary", "drama", "fantasy", "film-noir", "history",
    "horror", "music", "musical", "mystery", "romance", "sci-fi", "sport",
    "thriller", "war", "western", "hdcam", "hdtc", "camrip", "ts", "tc",
    "telesync", "dvdscr", "dvdrip", "predvd", "webrip", "web-dl", "tvrip",
    "hdtv", "web dl", "webdl", "bluray", "brrip", "bdrip", "360p", "480p",
    "720p", "1080p", "2160p", "4k", "1440p", "540p", "240p", "140p", "hevc",
    "hdrip", "hin", "hindi", "tam", "tamil", "kan", "kannada", "tel", "telugu",
    "mal", "malayalam", "eng", "english", "pun", "punjabi", "ben", "bengali",
    "mar", "marathi", "guj", "gujarati", "urd", "urdu", "kor", "korean", "jpn",
    "japanese", "nf", "netflix", "sonyliv", "sony", "sliv", "amzn", "prime",
    "primevideo", "hotstar", "zee5", "jio", "jhs", "aha", "hbo", "paramount",
    "apple", "hoichoi", "sunnxt", "viki"
} | BAD_WORDS

CAPTION_LANGUAGES = {
    "hin": "Hindi", "hindi": "Hindi",
    "tam": "Tamil", "tamil": "Tamil",
    "kan": "Kannada", "kannada": "Kannada",
    "tel": "Telugu", "telugu": "Telugu",
    "mal": "Malayalam", "malayalam": "Malayalam",
    "eng": "English", "english": "English",
    "pun": "Punjabi", "punjabi": "Punjabi",
    "ben": "Bengali", "bengali": "Bengali",
    "mar": "Marathi", "marathi": "Marathi",
    "guj": "Gujarati", "gujarati": "Gujarati",
    "urd": "Urdu", "urdu": "Urdu",
    "kor": "Korean", "korean": "Korean",
    "jpn": "Japanese", "japanese": "Japanese",
}

OTT_PLATFORMS = {
    "nf": "Netflix", "netflix": "Netflix",
    "sonyliv": "SonyLiv", "sony": "SonyLiv", "sliv": "SonyLiv",
    "amzn": "Amazon Prime Video", "prime": "Amazon Prime Video", "primevideo": "Amazon Prime Video",
    "hotstar": "Disney+ Hotstar", "zee5": "Zee5",
    "jio": "JioHotstar", "jhs": "JioHotstar",
    "aha": "Aha", "hbo": "HBO Max", "paramount": "Paramount+",
    "apple": "Apple TV+", "hoichoi": "Hoichoi", "sunnxt": "Sun NXT", "viki": "Viki"
}

CLEAN_PATTERN = re.compile(r'@[^ \n\r\t\.,:;!?()\[\]{}<>\\/"\'=_%]+|\bwww\.[^\s\]\)]+|\([\@^]+\)|\[[\@^]+\]')
WATERMARK_PATTERN = re.compile(r'(?i)^(?:www\.)?[a-z0-9_\-]{3,20}(?:boss|botz|hub|dl|zone|movies|flix|hd|world|net|site|tv|pro|xyz|store|official|team|media|wap|links?)\s+', re.IGNORECASE)
NORMALIZE_PATTERN = re.compile(r"[._]+|[()\[\]{}:;'–!,.?_]")
QUALITY_PATTERN = re.compile(
    r"\b(?:HDCam|HDTC|CamRip|TS|TC|TeleSync|DVDScr|DVDRip|PreDVD|"
    r"WEBRip|WEB-DL|TVRip|HDTV|WEB DL|WebDl|BluRay|BRRip|BDRip|"
    r"360p|480p|720p|1080p|2160p|4K|1440p|540p|240p|140p|HEVC|HDRip)\b",
    re.IGNORECASE
)
YEAR_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:19|20)\d{2}(?![A-Za-z0-9])")
RANGE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,2})\s*(?:to|-)\s*(?:E(?:p(?:isode)?)?)?0*(\d{1,2})', re.IGNORECASE)
SINGLE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,3})', re.IGNORECASE)
NAMED_REGEX = re.compile(r'Season\s*0*(\d{1,2})[\s\-,:]*Ep(?:isode)?\s*0*(\d{1,3})', re.IGNORECASE)
EP_ONLY_RANGE = re.compile(r'\b(?:EP|Episode)0*(\d{1,3})\s*-\s*0*(\d{1,3})\b', re.IGNORECASE)

MEDIA_FILTER = filters.document | filters.video | filters.audio
locks = defaultdict(asyncio.Lock)
pending_updates = {}


def clean_mentions_links(text: str) -> str:
    text = CLEAN_PATTERN.sub("", text or "").strip()
    text = WATERMARK_PATTERN.sub("", text).strip()
    return text


def normalize(s: str) -> str:
    s = NORMALIZE_PATTERN.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def remove_ignored_words(text: str) -> str:
    IGNORE_WORDS_LOWER = {w.lower() for w in IGNORE_WORDS}
    return " ".join(word for word in text.split() if word.lower() not in IGNORE_WORDS_LOWER)


def get_qualities(text: str) -> str:
    qualities = QUALITY_PATTERN.findall(text)
    return ", ".join(qualities) if qualities else "N/A"


def extract_ott_platform(text: str) -> str:
    text = text.lower()
    platforms = {plat for key, plat in OTT_PLATFORMS.items() if key in text}
    return " | ".join(platforms) if platforms else "N/A"


def extract_season_episode(filename: str) -> Tuple[Optional[int], Optional[str]]:
    if m := EP_ONLY_RANGE.search(filename):
        return 1, f"{int(m.group(1))}-{int(m.group(2))}"
    for pattern in (RANGE_REGEX, SINGLE_REGEX, NAMED_REGEX):
        if m := pattern.search(filename):
            season = int(m.group(1))
            if pattern == RANGE_REGEX:
                ep = f"{m.group(2)}-{m.group(3)}"
            else:
                ep = m.group(2)
            return season, ep
    return None, None


def schedule_update(bot, base_name, delay=5):
    if handle := pending_updates.get(base_name):
        if not handle.cancelled():
            handle.cancel()
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            return
        pending_updates[base_name] = loop.call_later(
            delay,
            lambda: asyncio.ensure_future(
                _safe_update(bot, base_name), loop=loop
            )
        )
    except Exception as e:
        logger.warning(f"schedule_update failed for {base_name}: {e}")


async def _safe_update(bot, base_name):
    try:
        await update_movie_message(bot, base_name)
    except Exception as e:
        logger.warning(f"_safe_update silenced error for {base_name}: {e}")
    finally:
        pending_updates.pop(base_name, None)


def extract_media_info(filename: str, caption: str):
    filename = normalize(clean_mentions_links(filename).title())
    caption_clean = clean_mentions_links(caption).lower() if caption else ""
    unified = f"{caption_clean} {filename.lower()}".strip()

    season = episode = year = None
    tag = "#MOVIE"
    processed_raw = base_raw = filename
    quality = get_qualities(caption_clean) or get_qualities(filename.lower()) or "N/A"
    ott_platform = extract_ott_platform(f"{filename} {caption_clean}")

    lang_keys = {k for k in CAPTION_LANGUAGES if k in caption_clean or k in filename.lower()}
    language = ", ".join(sorted({CAPTION_LANGUAGES[k] for k in lang_keys})) if lang_keys else "N/A"

    season, episode = extract_season_episode(filename)
    if season is not None:
        tag = "#SERIES"
        if m := (RANGE_REGEX.search(filename) or SINGLE_REGEX.search(filename) or NAMED_REGEX.search(filename) or EP_ONLY_RANGE.search(filename)):
            match_str = m.group(0)
            start_idx = filename.lower().find(match_str.lower())
            end_idx = start_idx + len(match_str)
            processed_raw = filename[:end_idx]
            base_raw = filename[:start_idx]
            if year_match := YEAR_PATTERN.search(filename.lower()[end_idx:]):
                y = year_match.group(0)
                yi = filename.lower().find(y, end_idx)
                if yi != -1:
                    processed_raw = filename[:yi + 4]
                    base_raw += f" {y}"
    else:
        if year_match := YEAR_PATTERN.search(unified):
            year = year_match.group(0)
            year_idx = filename.lower().find(year.lower())
            if year_idx != -1:
                processed_raw = filename[:year_idx + 4]
                base_raw = processed_raw
        else:
            if qual_match := QUALITY_PATTERN.search(unified):
                qual_str = qual_match.group(0)
                qual_idx = filename.lower().find(qual_str.lower())
                if qual_idx != -1:
                    processed_raw = filename[:qual_idx]
                    base_raw = processed_raw

    base_name = normalize(remove_ignored_words(normalize(base_raw)))
    if year and year not in base_name:
        base_name += f" {year}"

    if base_name.endswith(")"):
        base_name = re.sub(r"\s+\(\d{4}\)$", "", base_name)
        if year:
            base_name += f" {year}"

    def _strip_season_episode_tokens(name: str) -> str:
        if not name:
            return name
        year_match = re.search(r'\(?\b(19|20)\d{2}\b\)?\s*$', name)
        year_part = ""
        if year_match:
            year_part = year_match.group(0)
            name = name[:year_match.start()].strip()
        patterns = [
            r'\bS\d{1,2}E\d{1,2}\b',
            r'\bS\d{1,2}\b',
            r'\bE\d{1,2}\b',
            r'\b\d{1,2}x\d{1,2}\b',
            r'\bSeason\s*\d{1,2}\b',
            r'\bEp(?:isode)?\.?\s*\d{1,3}\b',
            r'\bEpisode\s*\d{1,3}\b',
            r'\bPart\s*\d{1,2}\b'
        ]
        for p in patterns:
            name = re.sub(p, ' ', name, flags=re.IGNORECASE)
        name = re.sub(r'[_\.\-]+', ' ', name)
        name = re.sub(r'\s+', ' ', name).strip()
        if year_part:
            y = re.search(r'(19|20)\d{2}', year_part)
            if y:
                name = f"{name} {y.group(0)}"
        return name.strip()

    base_name = _strip_season_episode_tokens(base_name)
    if not base_name:
        base_name = normalize(remove_ignored_words(normalize(processed_raw))) or filename

    return {
        "processed": normalize(processed_raw),
        "base_name": base_name,
        "tag": tag,
        "season": season,
        "episode": episode,
        "year": year,
        "quality": quality,
        "ott_platform": ott_platform,
        "language": language
    }


@Client.on_message(filters.chat(CHANNELS) & MEDIA_FILTER)
async def media_handler(bot, message):
    media = next(
        (getattr(message, ft) for ft in ("document", "video", "audio")
         if getattr(message, ft, None)),
        None
    )
    if not media:
        return

    media.file_type = next(ft for ft in ("document", "video", "audio") if hasattr(message, ft))
    media.caption = message.caption or ""

    await save_file(media)

    try:
        if await db.movie_update_status(bot.me.id):
            await process_and_send_update(bot, media.file_name, media.caption)
    except Exception:
        logger.exception("Error processing media for movie update")


async def process_and_send_update(bot, filename, caption):
    try:
        media_info = extract_media_info(filename, caption)
        base_name = media_info["base_name"]
        processed = media_info["processed"]

        lock = locks[base_name]
        async with lock:
            await _process_with_lock(bot, filename, caption, media_info, base_name, processed)
    except PyMongoError as e:
        logger.error(f"Database error in process_and_send_update: {e}")
    except Exception as e:
        logger.exception(f"Processing failed in process_and_send_update: {e}")


async def _process_with_lock(bot, filename, caption, media_info, base_name, processed):
    if not hasattr(db, 'movie_updates'):
        db.movie_updates = db.db.movie_updates

    movie_doc = await db.movie_updates.find_one({"_id": base_name})
    file_data = {
        "filename": filename,
        "processed": processed,
        "quality": media_info["quality"],
        "language": media_info["language"],
        "ott_platform": media_info["ott_platform"],
        "timestamp": datetime.now(),
        "tag": media_info["tag"],
        "season": media_info["season"],
        "episode": media_info["episode"]
    }

    if not movie_doc:
        poster_url = None
        trailer_url = None
        is_backdrop = False
        backdrop_url = None

        # Clean search name (strip episode/part tokens)
        imdb_search_name = re.sub(
            r'\b(?:Ep|Episode|Part|P)\s*\d+\b', '', base_name, flags=re.IGNORECASE
        ).strip()
        imdb_search_name = re.sub(r'\s+', ' ', imdb_search_name).strip()

        # ── Step 1: IMDB — PRIMARY source for poster, rating, genres, plot, languages, url ──
        imdb_data = await get_movie_details(imdb_search_name) or {}
        logger.info(
            f"IMDB result for '{base_name}': "
            f"rating={imdb_data.get('rating')!r} "
            f"url={imdb_data.get('url')!r} "
            f"lang={imdb_data.get('languages')!r}"
        )

        # ── Step 2: TMDB — FALLBACK / supplemental (trailer, backdrop) ──
        tmdb_data = await get_movie_detailsx(base_name) if not imdb_data else None
        # Always fetch TMDB for trailer + backdrop regardless (they aren't on IMDB scraper)
        tmdb_data_full = await get_movie_detailsx(base_name)
        if tmdb_data_full:
            trailer_url = tmdb_data_full.get("trailer_url")
            backdrop_url = tmdb_data_full.get("backdrop_url")
            is_backdrop = bool(backdrop_url)
            logger.info(f"TMDB supplemental for '{base_name}' | trailer: {trailer_url}")

        # ── POSTER: IMDB primary, TMDB fallback ──
        DEFAULT_POSTER = "https://i.ibb.co/0RQMzgyB/default.jpg"  # replace with your direct ibb.co image URL
        imdb_poster = imdb_data.get("poster_url", "").strip()
        tmdb_poster = (tmdb_data_full or {}).get("poster_url", "").strip()

        if imdb_poster:
            # IMDB poster found — always use it, never override with backdrop
            poster_url = imdb_poster
            final_poster = imdb_poster
        elif LANDSCAPE_POSTER and TMDB_POSTER and backdrop_url:
            # No IMDB poster — use TMDB landscape backdrop
            poster_url = backdrop_url
            final_poster = backdrop_url
        else:
            # No IMDB poster, no backdrop — use TMDB portrait poster
            poster_url = tmdb_poster or DEFAULT_POSTER
            final_poster = poster_url

        # ── RATING: IMDB primary, TMDB fallback ──
        rating = None
        imdb_rating_raw = str(imdb_data.get("rating", "")).strip()
        if imdb_rating_raw and imdb_rating_raw not in ("N/A", "None", ""):
            try:
                imdb_rating_val = float(imdb_rating_raw)
                rating = f"{imdb_rating_val:.1f}" if imdb_rating_val > 0 else None
                if rating:
                    logger.info(f"Using IMDB rating for '{base_name}': {rating}")
            except (ValueError, TypeError):
                pass
        if not rating:
            tmdb_rating_val = (tmdb_data_full or {}).get("rating", 0) or 0
            rating = f"{tmdb_rating_val:.1f}" if tmdb_rating_val > 0 else "N/A"
            logger.info(f"Using TMDB rating for '{base_name}': {rating}")

        # ── GENRES: IMDB primary, TMDB fallback ──
        raw_genres = imdb_data.get("genres") or (tmdb_data_full or {}).get("genres", "N/A")
        if isinstance(raw_genres, list):
            genres = ", ".join(str(g) for g in raw_genres if g) or "N/A"
        elif isinstance(raw_genres, str):
            genres = raw_genres.strip() or "N/A"
        else:
            genres = "N/A"

        # ── LANGUAGES: IMDB primary + filename supplement ──
        imdb_languages = imdb_data.get("languages", "")
        lang_from_file = media_info["language"]
        lang_parts = set()
        if imdb_languages:
            lang_parts.update(l.strip() for l in imdb_languages.split(",") if l.strip())
        if lang_from_file and lang_from_file != "N/A":
            lang_parts.update(l.strip() for l in lang_from_file.split(",") if l.strip())
        language = ", ".join(sorted(lang_parts)) if lang_parts else "N/A"

        # ── PLOT: IMDB primary, TMDB fallback ──
        raw_plot = imdb_data.get("plot") or (tmdb_data_full or {}).get("plot") or ""
        plot_text = raw_plot.strip() if raw_plot else ""

        # ── IMDB URL: IMDB data primary, TMDB external_ids fallback ──
        imdb_url = imdb_data.get("url", "") or (tmdb_data_full or {}).get("imdb_url", "")
        if not imdb_url:
            imdb_id = imdb_data.get("imdb_id", "") or (tmdb_data_full or {}).get("imdb_id", "")
            if imdb_id:
                imdb_url = f"https://www.imdb.com/title/{imdb_id}"

        # ── YEAR: IMDB primary, filename, TMDB fallback ──
        year = imdb_data.get("year") or media_info["year"] or (tmdb_data_full or {}).get("year")

        movie_doc = {
            "_id": base_name,
            "files": [file_data],
            "poster_url": final_poster,
            "genres": genres,
            "rating": rating,
            "imdb_url": imdb_url,
            "plot": plot_text,
            "year": year,
            "language": language,
            "tag": media_info["tag"],
            "ott_platform": media_info["ott_platform"],
            "trailer_url": trailer_url,
            "message_id": None,
            "is_photo": False,
            "is_backdrop": bool(not imdb_poster and LANDSCAPE_POSTER and TMDB_POSTER and backdrop_url)
        }
        try:
            await db.movie_updates.insert_one(movie_doc)
            await send_movie_update(bot, base_name)
        except DuplicateKeyError:
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
            if movie_doc:
                if any(f["filename"] == filename for f in movie_doc["files"]):
                    return
                await db.movie_updates.update_one(
                    {"_id": base_name},
                    {"$push": {"files": file_data}}
                )
                schedule_update(bot, base_name)
    else:
        if any(f["filename"] == filename for f in movie_doc["files"]):
            return
        await db.movie_updates.update_one(
            {"_id": base_name},
            {"$push": {"files": file_data}}
        )
        schedule_update(bot, base_name)


def build_buttons(base_name: str, trailer_url: str = None) -> InlineKeyboardMarkup:
    get_files_btn = InlineKeyboardButton(
        '🎬 ɢᴇᴛ ғɪʟᴇs',
        url=f"https://t.me/{temp.U_NAME}?start=getfile-{base_name.replace(' ', '-')}"
    )
    if trailer_url:
        trailer_btn = InlineKeyboardButton('▶️ ᴛʀᴀɪʟᴇʀ', url=trailer_url)
        return InlineKeyboardMarkup([[get_files_btn, trailer_btn]])
    return InlineKeyboardMarkup([[get_files_btn]])


async def send_movie_update(bot, base_name):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
            if not movie_doc:
                return None

            text = generate_movie_message(movie_doc, base_name)
            buttons = build_buttons(base_name, movie_doc.get("trailer_url"))
            size = (2560, 1440) if LANDSCAPE_POSTER and TMDB_POSTER and movie_doc.get("is_backdrop") else (853, 1280)

            resized_poster = None
            if movie_doc.get("poster_url") and not LINK_PREVIEW:
                try:
                    resized_poster = await fetch_image(movie_doc["poster_url"], size)
                except Exception as fe:
                    logger.warning(f"fetch_image failed for '{base_name}': {fe}")

            if resized_poster:
                msg = await bot.send_photo(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    photo=resized_poster,
                    caption=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML
                )
                is_photo = True
            else:
                send_params = {
                    "chat_id": MOVIE_UPDATE_CHANNEL,
                    "text": text,
                    "reply_markup": buttons,
                    "parse_mode": enums.ParseMode.HTML
                }
                if movie_doc.get("poster_url") and LINK_PREVIEW:
                    send_params["invert_media"] = ABOVE_PREVIEW
                msg = await bot.send_message(**send_params)
                is_photo = False

            await db.movie_updates.update_one(
                {"_id": base_name},
                {"$set": {"message_id": msg.id, "is_photo": is_photo}}
            )
            return msg

        except FloodWait as e:
            await asyncio.sleep(e.value + 2)
        except Exception as e:
            logger.error(f"Failed to send movie update (attempt {attempt+1}): {e}")
            break
    return None


async def update_movie_message(bot, base_name):
    try:
        movie_doc = await db.movie_updates.find_one({"_id": base_name})
        if not movie_doc:
            return

        text = generate_movie_message(movie_doc, base_name)
        buttons = build_buttons(base_name, movie_doc.get("trailer_url"))

        message_id = movie_doc.get("message_id")
        is_photo = movie_doc.get("is_photo", False)

        if not message_id:
            await send_movie_update(bot, base_name)
            return

        try:
            if is_photo:
                await bot.edit_message_caption(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=message_id,
                    caption=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML
                )
            else:
                await bot.edit_message_text(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_id=message_id,
                    text=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML,
                    invert_media=ABOVE_PREVIEW,
                    disable_web_page_preview=not LINK_PREVIEW
                )
        except (MessageIdInvalid, MessageNotModified):
            pass
        except Exception:
            try:
                await bot.delete_messages(
                    chat_id=MOVIE_UPDATE_CHANNEL,
                    message_ids=message_id
                )
                await db.movie_updates.update_one(
                    {"_id": base_name},
                    {"$set": {"message_id": None, "is_photo": False}}
                )
            except Exception as e:
                logger.error(f"Error during message deletion in recovery: {e}")
            await send_movie_update(bot, base_name)
    except Exception as e:
        logger.error(f"Failed to update movie message for {base_name}: {e}")


def generate_movie_message(movie_doc, base_name):
    all_languages = set()
    all_ott_platforms = set()
    all_tags = set()

    stored_language = movie_doc.get("language", "")
    if stored_language and stored_language != "N/A":
        all_languages.update(l.strip() for l in stored_language.split(",") if l.strip())

    for file in movie_doc["files"]:
        if file["language"] != "N/A":
            all_languages.update(l.strip() for l in file["language"].split(",") if l.strip())
        if file["ott_platform"] != "N/A":
            platforms = [p.strip() for p in file["ott_platform"].split("|") if p.strip()]
            all_ott_platforms.update(platforms)
        if file["tag"]:
            all_tags.add(file["tag"])

    primary_tag = "🎬 New #SERIES Added" if "#SERIES" in all_tags else "🎬 New #MOVIE Added"
    language_str = " | ".join(sorted(all_languages)) if all_languages else "N/A"
    ott_str = ", ".join(sorted(all_ott_platforms)) if all_ott_platforms else "N/A"

    return script.MOVIE_UPDATE_NOTIFY_TXT.format(
        poster_url=movie_doc.get("poster_url", ""),
        imdb_url=movie_doc.get("imdb_url", ""),
        filename=base_name,
        tag=primary_tag,
        genres=movie_doc.get("genres", "N/A"),
        ott=ott_str,
        language=language_str,
        plot=movie_doc.get("plot", ""),
        rating=movie_doc.get("rating", "N/A"),
        year=movie_doc.get("year") or "N/A",
        trailer_url=movie_doc.get("trailer_url") or "",
        search_link=temp.U_NAME
    )
