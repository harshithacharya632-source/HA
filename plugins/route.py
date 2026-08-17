import re
import os
import math
import logging
import hashlib
import hmac
import json as _json
from urllib.parse import parse_qsl

import aiohttp
from aiohttp import web
from info import *

from TechVJ.bot import multi_clients, work_loads
from TechVJ.server.exceptions import FIleNotFound, InvalidHash
from TechVJ.util.custom_dl import ByteStreamer
from TechVJ.util.render_template import render_page
from TechVJ.util.file_properties import get_hash, get_name

from database.ia_filterdb import get_search_results
from utils import is_premium_user

WEBAPP_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webapp")
)

routes = web.RouteTableDef()
class_cache = {}

# ---------------- ROOT (Health Check) ----------------
@routes.get("/", allow_head=True)
async def root(request):
    return web.Response(text="OK", status=200)


# ---------------- WATCH PAGE (HTML PLAYER) ----------------
@routes.get(r"/watch/{path:\S+}", allow_head=True)
async def watch_page(request: web.Request):
    path = request.match_info["path"]
    match = re.search(r"(\d+)", path)
    if not match:
        raise web.HTTPNotFound()
    try:
        id = int(match.group(1))
        secure_hash = request.rel_url.query.get("hash")

        return web.Response(
            text=await render_page(id, secure_hash),
            content_type="text/html"
        )
    except InvalidHash:
        raise web.HTTPForbidden()
    except FIleNotFound:
        raise web.HTTPNotFound()
    except Exception as e:
        # Was silently turning every failure (network hiccup, TMDB timeout,
        # etc.) into an indistinguishable 404 with no trace in the logs.
        logging.exception(f"watch_page failed for id={id}: {e}")
        raise web.HTTPNotFound()


# ---------------- MINI APP (GOFLIX HOME PAGE) ----------------
@routes.get("/app", allow_head=True)
async def goflix_app(request: web.Request):
    return web.FileResponse(os.path.join(WEBAPP_DIR, "goflix_home.html"))


# ---------------- CONFIG: tell the frontend the real, configured stream URL ----------------
# URL comes from info.py's environment variable — this is the source of truth
# for wherever streaming/watch actually lives, set by you on Koyeb. The
# frontend should never hardcode or guess this.
@routes.get("/api/config", allow_head=True)
async def app_config(request: web.Request):
    return web.json_response({"stream_base_url": URL.rstrip("/")})


# ---------------- TMDB PROXY (keeps TMDB_API_KEY off the client) ----------------
@routes.get(r"/api/tmdb/{endpoint:\S+}", allow_head=True)
async def tmdb_proxy(request: web.Request):
    endpoint = request.match_info["endpoint"]
    query = dict(request.rel_url.query)
    query["api_key"] = TMDB_API_KEY
    url = f"https://api.themoviedb.org/3/{endpoint}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=query, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                return web.json_response(data, status=resp.status)
    except Exception as e:
        logging.exception(e)
        raise web.HTTPBadGateway(text="TMDB request failed")


# ---------------- TELEGRAM MINI APP initData VALIDATION ----------------
def validate_init_data(init_data: str):
    """Verify Telegram signed the Mini App's initData and return the user dict,
    or None if it's missing/invalid/tampered. See:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data or not BOT_TOKEN:
        return None
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        return None
    user_json = parsed.get("user")
    if not user_json:
        return None
    try:
        return _json.loads(user_json)
    except ValueError:
        return None


# ---------------- ME: profile / premium status for the Mini App ----------------
@routes.post("/api/me")
async def me(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="expected JSON body")

    user = validate_init_data(body.get("init_data", ""))
    if not user:
        raise web.HTTPUnauthorized(text="invalid or missing Telegram initData")

    user_id = user.get("id")
    is_premium = True
    if PREMIUM_AND_REFERAL_MODE:
        is_premium = await is_premium_user(user_id)

    return web.json_response(
        {
            "name": (user.get("first_name", "") + " " + user.get("last_name", "")).strip(),
            "username": user.get("username"),
            "is_premium": is_premium,
        }
    )


# ---------------- QUALITY LABEL PARSING (by file size) ----------------
# Same thresholds as plugins/pm_filter.py's QUALITY_RANGES, kept in sync
# manually to avoid importing the whole pm_filter module here.
_MB = 1024 * 1024
QUALITY_RANGES = {
    "4k":   (3000 * _MB, 40000 * _MB),
    "2k":   (2000 * _MB, 3000 * _MB),
    "1080": (1300 * _MB, 2000 * _MB),
    "720":  (500 * _MB, 1300 * _MB),
    "480":  (0, 500 * _MB),
}
QUALITY_LABELS = {"4k": "4K", "2k": "2K", "1080": "1080p", "720": "720p", "480": "480p"}
QUALITY_ORDER = ["4k", "2k", "1080", "720", "480"]

def parse_quality(file_size):
    try:
        size = int(file_size)
    except (TypeError, ValueError):
        return "SD"
    for qkey in QUALITY_ORDER:
        lo, hi = QUALITY_RANGES[qkey]
        if lo <= size < hi:
            return QUALITY_LABELS[qkey]
    return "SD"

def format_size(num_bytes):
    if not num_bytes:
        return ""
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}TB"

# ---------------- LANGUAGE TAGS (for language browsing) ----------------
# "aliases" are the words that actually show up in your uploaded filenames
# (e.g. "...HIN ENG..." or "...Kannada..."). Add/edit freely — the frontend
# pulls this list live from GET /api/languages, nothing is hardcoded there.
LANGUAGES = [
    {"code": "eng", "label": "English",   "aliases": ["english", "eng"]},
    {"code": "hin", "label": "Hindi",     "aliases": ["hindi", "hin"]},
    {"code": "tam", "label": "Tamil",     "aliases": ["tamil", "tam"]},
    {"code": "tel", "label": "Telugu",    "aliases": ["telugu", "tel"]},
    {"code": "kan", "label": "Kannada",   "aliases": ["kannada", "kan"]},
    {"code": "mal", "label": "Malayalam", "aliases": ["malayalam", "mal"]},
    {"code": "guj", "label": "Gujarati",  "aliases": ["gujarati", "guj"]},
    {"code": "mar", "label": "Marathi",   "aliases": ["marathi", "mar"]},
    {"code": "ben", "label": "Bengali",   "aliases": ["bengali", "bangla", "ben"]},
    {"code": "pun", "label": "Punjabi",   "aliases": ["punjabi", "pun"]},
    {"code": "jap", "label": "Japanese",  "aliases": ["japanese", "jap", "jpn"]},
    {"code": "kor", "label": "Korean",    "aliases": ["korean", "kor"]},
    {"code": "chi", "label": "Chinese",   "aliases": ["chinese", "mandarin", "chi"]},
    {"code": "spa", "label": "Spanish",   "aliases": ["spanish", "esp", "spa"]},
    {"code": "fre", "label": "French",    "aliases": ["french", "fra", "fre"]},
]
_LANGUAGES_BY_CODE = {l["code"]: l for l in LANGUAGES}


def normalize_query(title):
    """Match the normalization auto_filter applies to search text, so a
    TMDB title with punctuation Telegram filenames don't have (colons,
    dashes) doesn't silently fail to match a file that's actually there."""
    q = (title or "").lower()
    q = q.replace("-", " ").replace(":", "").replace(".", "")
    q = re.sub(r"\s+", " ", q).strip()
    return q

async def _check_premium_or_402(user_id):
    """Returns a 402 JSON response if the user isn't premium, else None."""
    if PREMIUM_AND_REFERAL_MODE and not await is_premium_user(user_id):
        return web.json_response(
            {
                "premium_required": True,
                "message": "🔒 Stream & Download link is a Premium-only feature.\n\nBuy premium with /plan to unlock it.",
            },
            status=402,
        )
    return None

def _build_queries(title, year, season, episode):
    clean = normalize_query(title)
    queries = []
    if season and episode:
        queries.append(f"{clean} S{int(season):02d}E{int(episode):02d}")
    if year:
        queries.append(f"{clean} {year}")
    queries.append(clean)
    return queries


_LANGUAGE_PATTERNS = [
    (l["code"], [re.compile(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", re.IGNORECASE) for a in l["aliases"]])
    for l in LANGUAGES
]

def _detect_languages(filename):
    """Best-effort: which configured languages are tagged in this filename
    (e.g. "...HIN ENG..." -> ["hin","eng"]). A file can match more than one
    (dual-audio releases)."""
    name = filename or ""
    found = []
    for code, patterns in _LANGUAGE_PATTERNS:
        if any(p.search(name) for p in patterns):
            found.append(code)
    return found


# ---------------- QUALITIES: list every matching file for a title ----------------
@routes.post("/api/qualities")
async def list_qualities(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="expected JSON body")

    title = (body.get("title") or "").strip()
    if not title:
        raise web.HTTPBadRequest(text="missing 'title'")

    user = validate_init_data(body.get("init_data", ""))
    if not user:
        raise web.HTTPUnauthorized(text="invalid or missing Telegram initData")
    user_id = user.get("id")

    premium_block = await _check_premium_or_402(user_id)
    if premium_block:
        return premium_block

    queries = _build_queries(title, body.get("year"), body.get("season"), body.get("episode"))
    files = []
    for q in queries:
        files, _, _ = await get_search_results(user_id, q, max_results=10, need_count=False)
        if files:
            break

    results = [
        {
            "file_id": f["file_id"],
            "name": f.get("file_name", ""),
            "quality": parse_quality(f.get("file_size")),
            "size": format_size(f.get("file_size")),
            "languages": _detect_languages(f.get("file_name", "")),
        }
        for f in files
    ]
    # Best quality first.
    order = {"4K": 0, "2K": 1, "1080p": 2, "720p": 3, "480p": 4, "SD": 5}
    results.sort(key=lambda r: order.get(r["quality"], 9))

    return web.json_response({"files": results})



# ---------------- LANGUAGES: list configured languages for the picker ----------------
@routes.get("/api/languages", allow_head=True)
async def list_languages(request: web.Request):
    return web.json_response(
        {"languages": [{"code": l["code"], "label": l["label"]} for l in LANGUAGES]}
    )


async def _search_language_files(user_id, aliases, limit):
    """Search every alias for a language and merge the hits, de-duplicated by
    file_id. get_search_results does a fuzzy/text match, so we re-check each
    hit's actual filename for a whole-word alias match (not just a substring
    match) to avoid e.g. "kan" matching inside an unrelated word."""
    seen = {}
    for alias in aliases:
        try:
            batch, _, _ = await get_search_results(
                user_id, alias, max_results=max(limit * 3, 30), need_count=False
            )
        except Exception as e:
            logging.warning(f"language search failed for alias={alias!r}: {e}")
            continue
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", re.IGNORECASE)
        for f in batch:
            name = f.get("file_name", "") or ""
            if not pattern.search(name):
                continue
            fid = f.get("file_id")
            if fid and fid not in seen:
                seen[fid] = f
        if len(seen) >= limit:
            break
    return list(seen.values())[:limit]


# ---------------- LANGUAGE: browse files by language tag ----------------
@routes.post("/api/language")
async def browse_language(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="expected JSON body")

    code = (body.get("code") or "").strip().lower()
    lang = _LANGUAGES_BY_CODE.get(code)
    if not lang:
        raise web.HTTPBadRequest(text="unknown language code")

    user = validate_init_data(body.get("init_data", ""))
    if not user:
        raise web.HTTPUnauthorized(text="invalid or missing Telegram initData")
    user_id = user.get("id")

    # Browsing/listing doesn't need Premium — same as browsing TMDB genres.
    # The Premium gate stays where it already is: /api/resolve, when the
    # user actually taps to stream or download one of these files.
    try:
        limit = min(int(body.get("limit") or 30), 60)
    except (TypeError, ValueError):
        limit = 30

    files = await _search_language_files(user_id, lang["aliases"], limit)
    results = [
        {
            "file_id": f["file_id"],
            "name": f.get("file_name", ""),
            "quality": parse_quality(f.get("file_size")),
            "size": format_size(f.get("file_size")),
        }
        for f in files
    ]
    return web.json_response({"code": code, "label": lang["label"], "files": results})


# ---------------- RESOLVE: TMDB title -> live stream id/hash ----------------
# Mirrors what plugins/pm_filter.py's "generate_stream_link" callback does:
# find the matching stored file, check premium, forward it to LOG_CHANNEL,
# and hand back the fresh message id + hash that /watch and the direct
# stream route expect. Nothing is pre-stored — this happens live per tap,
# same as the bot's own Watch button.
@routes.post("/api/resolve")
async def resolve_stream(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="expected JSON body")

    init_data = body.get("init_data", "")
    explicit_file_id = body.get("file_id")
    title = (body.get("title") or "").strip()

    if not explicit_file_id and not title:
        raise web.HTTPBadRequest(text="missing 'file_id' or 'title'")

    user = validate_init_data(init_data)
    if not user:
        raise web.HTTPUnauthorized(text="invalid or missing Telegram initData")
    user_id = user.get("id")

    premium_block = await _check_premium_or_402(user_id)
    if premium_block:
        return premium_block

    if explicit_file_id:
        # A specific quality was already chosen via /api/qualities — just send that one.
        candidates = [{"file_id": explicit_file_id}]
    else:
        # Fallback: no quality picker was used, search and try matches in order.
        queries = _build_queries(title, body.get("year"), body.get("season"), body.get("episode"))
        candidates = []
        for q in queries:
            candidates, _, _ = await get_search_results(user_id, q, max_results=5, need_count=False)
            if candidates:
                break
        if not candidates:
            raise web.HTTPNotFound(text="no matching file found")

    index = min(work_loads, key=work_loads.get)
    client = multi_clients[index]
    log_msg = None
    last_error = None
    for candidate in candidates:
        try:
            log_msg = await client.send_cached_media(chat_id=LOG_CHANNEL, file_id=candidate["file_id"])
            break
        except Exception as e:
            # This specific match's stored file_id is bad/corrupted (e.g. MediaEmpty) —
            # try the next matching file instead of failing the whole request.
            last_error = e
            logging.warning(f"send_cached_media failed for a candidate, trying next: {e}")
            continue

    # If the exact file the user tapped was bad (stale/corrupted file_id —
    # this is what "MEDIA_EMPTY" means) and we haven't already tried a
    # title search, fall back to one now so a single bad copy doesn't kill
    # the whole request when a working copy of the same title exists.
    if log_msg is None and title:
        already_tried = {c.get("file_id") for c in candidates}
        queries = _build_queries(title, body.get("year"), body.get("season"), body.get("episode"))
        fallback_candidates = []
        for q in queries:
            found, _, _ = await get_search_results(user_id, q, max_results=8, need_count=False)
            fallback_candidates = [f for f in found if f.get("file_id") not in already_tried]
            if fallback_candidates:
                break
        for candidate in fallback_candidates:
            try:
                log_msg = await client.send_cached_media(chat_id=LOG_CHANNEL, file_id=candidate["file_id"])
                break
            except Exception as e:
                last_error = e
                logging.warning(f"send_cached_media failed for a fallback candidate, trying next: {e}")
                continue

    if log_msg is None:
        logging.exception(last_error)
        # Every candidate we tried was rejected by Telegram (MEDIA_EMPTY etc.)
        # — that's "found it, but it's corrupted/deleted", not a server crash,
        # so tell the frontend clearly instead of a bare 500.
        return web.json_response(
            {"message": "This file looks corrupted or was removed from Telegram. Try another quality, or contact the admin to re-upload it."},
            status=502,
        )

    return web.json_response(
        {
            "id": log_msg.id,
            "hash": get_hash(log_msg),
            "name": get_name(log_msg),
        }
    )


# ---------------- DOWNLOAD ----------------
# NOTE: this MUST be registered before the catch-all direct_stream_handler
# below. aiohttp matches routes in registration order, not by specificity —
# if the catch-all "/{path:\S+}" came first (as it previously did), it would
# swallow every "/download/..." request itself, streaming it inline instead
# of as an attachment. That's why Download was opening the web player.
@routes.get(r"/download/{path:\S+}", allow_head=True)
async def download_handler(request: web.Request):
    path = request.match_info["path"]
    match = re.search(r"(\d+)", path)
    if not match:
        raise web.HTTPNotFound()
    try:
        id = int(match.group(1))
        secure_hash = request.rel_url.query.get("hash")
        return await media_streamer(request, id, secure_hash, inline=False)
    except InvalidHash:
        raise web.HTTPForbidden()
    except FIleNotFound:
        raise web.HTTPNotFound()
    except Exception as e:
        logging.exception(e)
        raise web.HTTPInternalServerError()


# ---------------- DIRECT STREAM (ROOT PATH – REQUIRED FOR VLC/MX) ----------------
@routes.get(r"/{path:\S+}", allow_head=True)
async def direct_stream_handler(request: web.Request):
    path = request.match_info["path"]
    match = re.search(r"(\d+)", path)
    if not match:
        # Not a valid stream path (favicon.ico, robots.txt, browser probes, etc.)
        # — reject quietly instead of logging a fake server error.
        raise web.HTTPNotFound()
    try:
        id = int(match.group(1))
        secure_hash = request.rel_url.query.get("hash")
        return await media_streamer(request, id, secure_hash, inline=True)
    except InvalidHash:
        raise web.HTTPForbidden()
    except FIleNotFound:
        raise web.HTTPNotFound()
    except Exception as e:
        logging.exception(e)
        raise web.HTTPInternalServerError()


# ---------------- CORE STREAMER ----------------
async def media_streamer(
    request: web.Request,
    id: int,
    secure_hash: str,
    inline: bool
):
    range_header = request.headers.get("Range", None)

    # pick least-loaded client, but don't let one flaky client session turn
    # into a false 404 — if it can't resolve the file, try the others before
    # giving up. (This is the fix for "file forwarded to the log channel
    # fine, but the stream/watch URL still 404s": a single client's session
    # occasionally can't resolve a very-recently-forwarded message yet.)
    tried = set()
    file_id = None
    last_err = None
    ordered_indexes = sorted(work_loads, key=work_loads.get)
    for index in ordered_indexes:
        if index in tried:
            continue
        tried.add(index)
        client = multi_clients[index]
        tg = class_cache.get(client)
        if not tg:
            tg = ByteStreamer(client)
            class_cache[client] = tg
        try:
            file_id = await tg.get_file_properties(id)
            break
        except FIleNotFound:
            raise
        except Exception as e:
            last_err = e
            logging.warning(f"get_file_properties failed on client {index} for id={id}, trying next: {e}")
            continue

    if file_id is None:
        logging.exception(last_err)
        raise FIleNotFound

    if file_id.unique_id[:6] != secure_hash:
        raise InvalidHash

    file_size = file_id.file_size
    file_name = file_id.file_name or "file.bin"

    # ---------------- RANGE PARSING (SAFE) ----------------
    if range_header:
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            status = 206
        else:
            start = 0
            end = file_size - 1
            status = 200
    else:
        start = 0
        end = file_size - 1
        status = 200

    if start < 0 or end >= file_size or start > end:
        return web.Response(
            status=416,
            headers={"Content-Range": f"bytes */{file_size}"}
        )

    # ---------------- STREAM SETUP ----------------
    chunk_size = 1024 * 1024
    offset = start - (start % chunk_size)
    first_cut = start - offset
    last_cut = end % chunk_size + 1
    part_count = math.ceil((end + 1) / chunk_size) - math.floor(offset / chunk_size)
    length = end - start + 1

    # ---------------- MIME FIX (CRITICAL FOR VLC/MX AND THE IN-BROWSER PLAYER) ----------------
    # Telegram frequently drops/mangles mime_type on video uploads (or hands
    # back a generic "application/octet-stream"). The old fallback only
    # covered .mp4/.mkv/.avi and defaulted everything else — including a
    # perfectly playable .mov/.webm/.m4v/.ts, or an mp4 with a weird mime —
    # to application/octet-stream. Browsers refuse to even attempt inline
    # playback for that content-type, so the <video> tag fires an instant
    # "error" event and the app wrongly falls back to "open in VLC/MX" for
    # files that could have played fine in-browser. Trust an existing
    # video/* mime as-is; otherwise map by extension; otherwise assume
    # video/mp4 (this bot only ever serves videos) instead of a type that
    # guarantees browser refusal.
    mime = file_id.mime_type
    if not mime or not mime.startswith("video/"):
        ext_map = {
            ".mp4": "video/mp4", ".m4v": "video/mp4",
            ".mkv": "video/x-matroska",
            ".avi": "video/x-msvideo",
            ".mov": "video/quicktime",
            ".webm": "video/webm",
            ".ts": "video/mp2t",
            ".flv": "video/x-flv",
            ".3gp": "video/3gpp",
        }
        lower_name = file_name.lower()
        mime = next((v for ext, v in ext_map.items() if lower_name.endswith(ext)), "video/mp4")

    response = web.StreamResponse(
        status=status,
        headers={
            "Content-Type": mime,
            "Content-Length": str(length),
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Disposition": (
                f'inline; filename="{file_name}"'
                if inline else
                f'attachment; filename="{file_name}"'
            ),
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
        }
    )

    await response.prepare(request)

    async for chunk in tg.yield_file(
        file_id,
        index,
        offset,
        first_cut,
        last_cut,
        part_count,
        chunk_size
    ):
        await response.write(chunk)

    await response.write_eof()
    return response
