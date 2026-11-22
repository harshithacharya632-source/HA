import re
import os
import sys
import math
import time
import json as _json
import asyncio
import logging
import hashlib
import hmac
from urllib.parse import parse_qsl, quote

import aiohttp
from aiohttp import web
from info import *

from TechVJ.bot import multi_clients, work_loads
from TechVJ.server.exceptions import FIleNotFound, InvalidHash
from TechVJ.util.custom_dl import ByteStreamer, file_id_from_message
from TechVJ.util.render_template import render_page
from TechVJ.util.file_properties import get_hash, get_name

from database.ia_filterdb import get_search_results
from utils import is_premium_user
from upi_qr import upi_enabled, verify_pay_sig, render_pay_page, render_open_page, UPI_APPS

WEBAPP_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webapp")
)

routes = web.RouteTableDef()
class_cache = {}


def _get_streamer(client):
    tg = class_cache.get(client)
    if not tg:
        tg = ByteStreamer(client)
        class_cache[client] = tg
    return tg


# ---------------- ROOT (Health Check) ----------------
@routes.get("/", allow_head=True)
async def root(request):
    return web.Response(text="OK", status=200)


# ---------------- WATCH PAGE (HTML PLAYER) ----------------
# NOTE on the "{path:.+}" patterns in this file: they used to be "{path:\S+}".
# Newer aiohttp versions match the route against the DECODED path, where a
# file name like "My%20Movie.mkv" has become "My Movie.mkv" - and \S+ refuses
# spaces, so every file with a space in its name answered 404.
@routes.get(r"/watch/{path:.+}", allow_head=True)
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
        logging.exception(f"watch_page failed for id={id}: {e}")
        raise web.HTTPNotFound()


# ---------------- MINI APP (GOFLIX HOME PAGE) ----------------
# The new player lives in webapp/goflix_player.css + webapp/goflix_player.js.
# They are injected into goflix_home.html right here, when the page is served,
# so goflix_home.html itself does not have to be edited. If either file is
# missing the page is served exactly as before.
_APP_ASSETS = ("goflix_player.css", "goflix_player.js")
_app_cache = {"key": None, "html": None}


def _app_html():
    page = os.path.join(WEBAPP_DIR, "goflix_home.html")
    paths = [page] + [os.path.join(WEBAPP_DIR, n) for n in _APP_ASSETS]
    try:
        key = tuple(os.stat(p).st_mtime_ns for p in paths)
    except OSError:
        return None
    if _app_cache["key"] != key:
        with open(page, encoding="utf-8") as f:
            html_ = f.read()
        v = str(max(key))
        inject = (
            f'<link rel="stylesheet" href="/app-assets/goflix_player.css?v={v}">\n'
            f'<script src="/app-assets/goflix_player.js?v={v}"></script>\n'
        )
        idx = html_.rfind("</body>")
        html_ = html_ + inject if idx == -1 else html_[:idx] + inject + html_[idx:]
        _app_cache.update(key=key, html=html_)
    return _app_cache["html"]


@routes.get("/app", allow_head=True)
async def goflix_app(request: web.Request):
    page = _app_html()
    if page is None:
        return web.FileResponse(os.path.join(WEBAPP_DIR, "goflix_home.html"))
    return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache"})


@routes.get(r"/app-assets/{name}", allow_head=True)
async def goflix_app_asset(request: web.Request):
    name = request.match_info["name"]
    if name not in _APP_ASSETS:
        raise web.HTTPNotFound()
    return web.FileResponse(os.path.join(WEBAPP_DIR, name), headers={"Cache-Control": "no-cache"})


# ---------------- UPI APP REDIRECT PAGE (PhonePe / GPay / Paytm / Navi buttons) ----------------
# Telegram buttons only accept https links, so the "pay with your UPI app" buttons
# land here and this page hands the payment over to the app. The link is signed
# (see upi_qr.sign_pay): the amount can't be edited and the payee is always OUR
# UPI ID from the environment, so this can't be used to send anyone elsewhere.
# Registered before the catch-all "/{path}" stream route below, which would
# otherwise answer it first.
@routes.get("/pay", allow_head=True)
async def upi_pay_page(request: web.Request):
    q = request.rel_url.query
    amount, note, sig, app = q.get("am", ""), q.get("tn", ""), q.get("sig", ""), q.get("app", "")
    if not upi_enabled() or not verify_pay_sig(amount, note, sig):
        raise web.HTTPNotFound()
    try:
        page = render_pay_page(amount, note, app, request.headers.get("User-Agent", ""))
    except ValueError:
        raise web.HTTPNotFound()
    return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store"})


# ---------------- "JUST OPEN THE UPI APP" PAGE (the PhonePe / GPay / Paytm buttons under the QR) ----------------
# Only opens the app - no amount or UPI id is passed to it. Must stay above the catch-all "/{path}" route.
@routes.get("/open", allow_head=True)
@routes.get("/open/{app_key}", allow_head=True)
async def upi_open_page(request: web.Request):
    app_key = request.match_info.get("app_key", "")
    if app_key and app_key not in UPI_APPS:
        raise web.HTTPNotFound()
    page = render_open_page(app_key or None, request.headers.get("User-Agent", ""))
    return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store"})


# ---------------- CONFIG: tell the frontend the real, configured stream URL ----------------
# URL comes from info.py's environment variable — this is the source of truth
# for wherever streaming/watch actually lives, set by you on Koyeb. The
# frontend should never hardcode or guess this.
@routes.get("/api/config", allow_head=True)
async def app_config(request: web.Request):
    return web.json_response({"stream_base_url": URL.rstrip("/")})


# ---------------- DIAGNOSTICS: why is a stream slow or failing? ----------------
# GET /api/diag                      -> environment (is TgCrypto active? versions, clients)
# GET /api/diag?id=<msg id>&hash=<h> -> also downloads a few MB of that file from Telegram right here
#                                       on the server and reports MB/s and first-byte time, which
#                                       separates "Telegram -> server is slow" from "server -> phone
#                                       is slow". The player's "Details" button calls this.
# The file test needs a valid id + hash (the same secret every stream link carries) and is capped at 16 MB.
def _has_tgcrypto():
    try:
        import tgcrypto  # noqa: F401
        return True
    except ImportError:
        return False


@routes.get("/api/diag", allow_head=True)
async def diag(request: web.Request):
    q = request.rel_url.query
    out = {
        "python": sys.version.split()[0],
        "aiohttp": aiohttp.__version__,
        "tgcrypto": _has_tgcrypto(),
        "clients": len(multi_clients),
        "work_loads": {str(k): v for k, v in work_loads.items()},
        "stream_url": URL.rstrip("/"),
    }
    try:
        import pyrogram
        out["pyrogram"] = pyrogram.__version__
    except Exception:
        pass
    if q.get("id") and q.get("hash"):
        try:
            id = int(q["id"])
            mb = max(1, min(int(q.get("mb", "4")), 16))
            idx = min(work_loads, key=work_loads.get)
            streamer = _get_streamer(multi_clients[idx])
            file_id = await streamer.get_file_properties(id)
            if file_id.unique_id[:6] != q["hash"]:
                out["test"] = {"error": "bad hash"}
            else:
                parts = max(1, min(mb, math.ceil((file_id.file_size or 0) / 1048576)))
                t0 = time.monotonic()
                first = None
                got = 0
                async for chunk in streamer.yield_file(file_id, idx, 0, 0, 1048576, parts, 1048576):
                    if first is None:
                        first = time.monotonic() - t0
                    got += len(chunk)
                dt = time.monotonic() - t0
                out["test"] = {
                    "mb": round(got / 1048576, 1),
                    "seconds": round(dt, 2),
                    "mb_per_s": round(got / 1048576 / dt, 2) if dt else None,
                    "first_byte_s": round(first or 0, 2),
                }
        except Exception as e:
            out["test"] = {"error": f"{type(e).__name__}: {e}"}
    return web.json_response(out, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})


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
    (dual-audio releases). They come back in the order they appear in the
    filename, so the first one is the language the file most likely plays by
    default."""
    name = filename or ""
    found = []
    for code, patterns in _LANGUAGE_PATTERNS:
        starts = [m.start() for m in (p.search(name) for p in patterns) if m]
        if starts:
            found.append((min(starts), code))
    found.sort()
    return [code for _, code in found]


def guess_title_from_filename(filename):
    """Best-effort: turn a raw uploaded filename into a human title guess,
    by cutting everything off starting at the first quality/year/season/
    codec/release-group tag. Used by /api/search to turn real DB filenames
    into title strings the frontend can cross-check against TMDB."""
    name = (filename or "").rsplit(".", 1)[0]  # drop extension
    name = name.replace(".", " ").replace("_", " ").replace("-", " ")
    cut_pattern = re.compile(
        r"\b(19\d{2}|20\d{2}|S\d{1,2}E\d{1,3}|S\d{1,2}|E\d{1,3}|"
        r"480p|720p|1080p|2160p|4K|HDRip|WEB[- ]?DL|WEBRip|BluRay|BRRip|"
        r"DVDRip|HDTV|x264|x265|HEVC|AVC|AAC|DDP?5?\.?1|ESub|Esubs?)\b",
        re.IGNORECASE,
    )
    m = cut_pattern.search(name)
    if m:
        name = name[: m.start()]
    name = re.sub(r"\s+", " ", name).strip(" -_.")
    return name.title()


# ---------------- SEARCH: query the GoFlix file database directly ----------------
# This is the fix for search showing a TMDB poster + title for something that
# isn't actually uploaded: instead of only searching TMDB and hoping a file
# exists, we search the bot's own Mongo file DB first (the same engine the
# /api/qualities lookup and the bot's inline search use) and only return
# titles that are genuinely present, for the frontend to then enrich with a
# TMDB poster. No file in the DB -> it never shows up in search at all.
@routes.post("/api/search")
async def search_database(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="expected JSON body")

    query = (body.get("query") or "").strip()
    if len(query) < 2:
        return web.json_response({"titles": []})

    user = validate_init_data(body.get("init_data", ""))
    if not user:
        raise web.HTTPUnauthorized(text="invalid or missing Telegram initData")
    user_id = user.get("id")

    try:
        files, _, _ = await get_search_results(user_id, normalize_query(query), max_results=40, need_count=False)
    except Exception as e:
        logging.warning(f"/api/search DB lookup failed for query={query!r}: {e}")
        files = []

    seen = {}
    for f in files:
        guess = guess_title_from_filename(f.get("file_name", ""))
        key = guess.lower()
        if key and key not in seen:
            seen[key] = guess

    return web.json_response({"titles": list(seen.values())[:15]})


# ---------------- QUALITIES: list every matching file for a title ----------------
def _cmp_title(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# Words that may sit between a title and its year/quality tag without making it
# a different title ("Avatar Tamil 720p" is still Avatar; "Avatar The Way Of
# Water 2022" is not).
_TITLE_HARD_STOP = re.compile(
    r"(19\d{2}|20\d{2}|\d{3,4}p|4k|2k|s\d{1,2}e\d{1,3}|s\d{1,2}|hdrip|webrip|web|bluray|brrip|"
    r"dvdrip|hdtv|hdcam|x264|x265|hevc|avc|aac|dd|ddp\d*|esubs?|mkv|mp4|avi|\d+gb|\d+mb)"
)
_TITLE_SOFT_WORDS = {a for l in LANGUAGES for a in l["aliases"]} | {
    "dual", "audio", "multi", "org", "original", "hq", "uncut", "extended", "proper", "hd",
    "full", "movie", "film", "amzn", "nf", "zee5", "hotstar", "dsnp",
}


def _same_title(file_name, want_tokens):
    toks = _cmp_title(file_name).split()
    n = len(want_tokens)
    for i in range(len(toks) - n + 1):
        if toks[i:i + n] == want_tokens:
            for t in toks[i + n:]:
                if _TITLE_HARD_STOP.fullmatch(t):
                    return True
                if t not in _TITLE_SOFT_WORDS:
                    return False
            return True
    return False


async def _collect_files(user_id, title, year, season, episode, limit=60):
    """Every file in the DB that belongs to this title (up to `limit`).

    Episodes: the first query that finds anything wins (SxxEyy, then year, then
    plain title), exactly as before. Movies: the "title year" hit is used, and
    files of the SAME title whose name just lacks the year are added to it -
    before, those were silently dropped, which made languages and qualities
    that really exist in the DB disappear from the picker."""
    queries = _build_queries(title, year, season, episode)
    results = await asyncio.gather(*[
        get_search_results(user_id, q, max_results=limit, need_count=False) for q in queries
    ])
    results = [r[0] for r in results]
    first = next((r for r in results if r), [])
    if season and episode or not year or not results[0] or len(results) < 2:
        return first[:limit]
    want = _cmp_title(normalize_query(title)).split()
    merged = {f["file_id"]: f for f in results[0]}
    for f in results[-1]:
        if _same_title(f.get("file_name", ""), want):
            merged.setdefault(f["file_id"], f)
    return list(merged.values())[:limit]


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

    files = await _collect_files(user_id, title, body.get("year"), body.get("season"), body.get("episode"))

    results = []
    for f in files:
        try:
            nbytes = int(f.get("file_size") or 0)
        except (TypeError, ValueError):
            nbytes = 0
        results.append({
            "file_id": f["file_id"],
            "name": f.get("file_name", ""),
            "quality": parse_quality(f.get("file_size")),
            "size": format_size(f.get("file_size")),
            "bytes": nbytes,
            "languages": _detect_languages(f.get("file_name", "")),
        })
    # Best quality first, biggest file first inside the same quality.
    order = {"4K": 0, "2K": 1, "1080p": 2, "720p": 3, "480p": 4, "SD": 5}
    results.sort(key=lambda r: (order.get(r["quality"], 9), -r["bytes"]))

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
            "languages": _detect_languages(f.get("file_name", "")),
        }
        for f in files
    ]
    return web.json_response({"code": code, "label": lang["label"], "files": results})


# ---------------- RESOLVE: TMDB title -> live stream id/hash ----------------
# Mirrors what plugins/pm_filter.py's "generate_stream_link" callback does:
# find the matching stored file, check premium, forward it to LOG_CHANNEL,
# and hand back the fresh message id + hash that /watch and the direct
# stream route expect.
#
# Forwarding a file to LOG_CHANNEL takes a second or more and posts a new
# message every time. Switching language/quality in the player (or just
# reopening a movie) used to forward the very same file again and again, which
# was slow and could trigger Telegram flood limits. The result is now cached
# per file for a few hours, and the stream is "warmed up" (file properties +
# Telegram media session) before the player asks for the first byte.
_RESOLVE_TTL = 6 * 3600
_resolve_cache = {}   # file_id -> (timestamp, {"id", "hash", "name"})
_resolve_locks = {}   # file_id -> asyncio.Lock (one forward per file at a time)


async def _warm_stream(client, file_id):
    try:
        await _get_streamer(client).generate_media_session(client, file_id)
    except Exception as e:
        logging.debug(f"stream warm-up skipped: {e!r}")


async def _forward_to_log(client, file_id):
    log_msg = await client.send_cached_media(chat_id=LOG_CHANNEL, file_id=file_id)
    try:
        fid = file_id_from_message(log_msg)
        if fid:
            _get_streamer(client).remember(log_msg.id, fid)
            asyncio.ensure_future(_warm_stream(client, fid))
    except Exception as e:  # warming is only an optimisation - never fail the request over it
        logging.debug(f"stream warm-up skipped: {e!r}")
    return {"id": log_msg.id, "hash": get_hash(log_msg), "name": get_name(log_msg)}


async def _resolve_one(client, file_id, fresh=False):
    def cached():
        hit = _resolve_cache.get(file_id)
        if hit and not fresh and time.time() - hit[0] < _RESOLVE_TTL:
            return hit[1]
        return None

    data = cached()
    if data:
        return data
    if len(_resolve_locks) > 2000:
        _resolve_locks.clear()
    async with _resolve_locks.setdefault(file_id, asyncio.Lock()):
        data = cached()
        if data:
            return data
        data = await _forward_to_log(client, file_id)
        if len(_resolve_cache) > 2000:
            for key in sorted(_resolve_cache, key=lambda k: _resolve_cache[k][0])[:500]:
                _resolve_cache.pop(key, None)
        _resolve_cache[file_id] = (time.time(), data)
        return data


@routes.post("/api/resolve")
async def resolve_stream(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="expected JSON body")

    init_data = body.get("init_data", "")
    explicit_file_id = body.get("file_id")
    title = (body.get("title") or "").strip()
    fresh = bool(body.get("fresh"))  # player asks for this after a dead link

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
    result = None
    last_error = None
    for candidate in candidates:
        try:
            result = await _resolve_one(client, candidate["file_id"], fresh)
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
    if result is None and title:
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
                result = await _resolve_one(client, candidate["file_id"], fresh)
                break
            except Exception as e:
                last_error = e
                logging.warning(f"send_cached_media failed for a fallback candidate, trying next: {e}")
                continue

    if result is None:
        logging.error(f"resolve failed: {last_error!r}")
        # Every candidate we tried was rejected by Telegram (MEDIA_EMPTY etc.)
        # — that's "found it, but it's corrupted/deleted", not a server crash,
        # so tell the frontend clearly instead of a bare 500.
        return web.json_response(
            {"message": "This file looks corrupted or was removed from Telegram. Try another quality, or contact the admin to re-upload it."},
            status=502,
        )

    return web.json_response(result)


# ---------------- DOWNLOAD ----------------
# NOTE: this MUST be registered before the catch-all direct_stream_handler
# below. aiohttp matches routes in registration order, not by specificity —
# if the catch-all "/{path:\S+}" came first (as it previously did), it would
# swallow every "/download/..." request itself, streaming it inline instead
# of as an attachment. That's why Download was opening the web player.
@routes.get(r"/download/{path:.+}", allow_head=True)
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
@routes.get(r"/{path:.+}", allow_head=True)
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
def _parse_range(header, file_size):
    """-> (start, end, is_partial), or None if the range can't be satisfied.
    Handles bytes=a-b, bytes=a- and bytes=-n, clamps an end past the file (as
    browsers expect) and ignores anything it doesn't understand (multi-range)
    by serving the whole file."""
    if not header:
        return 0, file_size - 1, False
    m = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", header)
    if not m or not (m.group(1) or m.group(2)):
        return 0, file_size - 1, False
    first, last = m.groups()
    if first == "":
        n = int(last)
        if n == 0:
            return None
        start, end = max(0, file_size - n), file_size - 1
    else:
        start = int(first)
        end = min(int(last), file_size - 1) if last else file_size - 1
    if start >= file_size or start > end:
        return None
    return start, end, True


def _content_disposition(inline, name):
    ascii_name = re.sub(r'[^\x20-\x7e]|["\\]', "_", name) or "file"
    return f"{'inline' if inline else 'attachment'}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def _log_stream(id, sent, t0, first):
    """One line per stream in the Koyeb logs: how much was sent, how fast, and how long the first byte took.
    A slow first byte means Telegram / the media session is slow; a low MB/s while the player is buffering
    means the Telegram -> server or server -> phone link is the bottleneck."""
    dt = time.monotonic() - t0
    if first is not None and first > 3:
        logging.warning(f"stream id={id}: slow first byte ({first:.1f}s)")
    if sent >= 2 * 1024 * 1024 and dt > 0.5:
        logging.info(f"stream id={id}: {sent / 1048576:.1f} MB in {dt:.1f}s = {sent / 1048576 / dt:.2f} MB/s (first byte {first or 0:.2f}s)")


async def media_streamer(
    request: web.Request,
    id: int,
    secure_hash: str,
    inline: bool
):
    range_header = request.headers.get("Range", None)

    # pick least-loaded client, but don't let one flaky client session turn
    # into a false 404 — if it can't resolve the file, try the others before
    # giving up.
    file_id = None
    last_err = None
    index = tg = None
    for idx in sorted(work_loads, key=work_loads.get):
        client = multi_clients[idx]
        streamer = _get_streamer(client)
        try:
            file_id = await streamer.get_file_properties(id)
            index, tg = idx, streamer
            break
        except FIleNotFound:
            raise
        except Exception as e:
            last_err = e
            logging.warning(f"get_file_properties failed on client {idx} for id={id}, trying next: {e}")
            continue

    if file_id is None:
        logging.warning(f"no client could resolve id={id}: {last_err!r}")
        raise FIleNotFound

    if file_id.unique_id[:6] != secure_hash:
        raise InvalidHash

    file_size = file_id.file_size
    file_name = file_id.file_name or "file.bin"
    if not file_size:
        raise FIleNotFound

    # ---------------- RANGE PARSING ----------------
    parsed = _parse_range(range_header, file_size)
    if parsed is None:
        return web.Response(
            status=416,
            headers={"Content-Range": f"bytes */{file_size}"}
        )
    start, end, partial = parsed
    status = 206 if partial else 200

    # ---------------- STREAM SETUP ----------------
    chunk_size = 1024 * 1024
    offset = start - (start % chunk_size)
    first_cut = start - offset
    last_cut = end % chunk_size + 1
    part_count = math.ceil((end + 1) / chunk_size) - math.floor(offset / chunk_size)
    length = end - start + 1

    # ---------------- MIME FIX (CRITICAL FOR VLC/MX AND THE IN-BROWSER PLAYER) ----------------
    # Telegram frequently drops/mangles mime_type on video uploads (or hands
    # back a generic "application/octet-stream"). Browsers refuse to even
    # attempt inline playback for that content-type, so trust an existing
    # video/* mime as-is; otherwise map by extension; otherwise assume
    # video/mp4 (this bot only ever serves videos).
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

    headers = {
        "Content-Type": mime,
        "Content-Length": str(length),
        "Accept-Ranges": "bytes",
        "Content-Disposition": _content_disposition(inline, file_name),
        "Cache-Control": "no-store",
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    # HEAD: headers only. The player uses it to find out whether a link is
    # still alive (404/403) or the file just can't be decoded.
    if request.method == "HEAD":
        return web.Response(status=status, headers=headers)

    response = web.StreamResponse(status=status, headers=headers)
    await response.prepare(request)

    sent = 0
    t0 = time.monotonic()
    first = None
    agen = tg.yield_file(
        file_id,
        index,
        offset,
        first_cut,
        last_cut,
        part_count,
        chunk_size
    )
    try:
        async for chunk in agen:
            if first is None:
                first = time.monotonic() - t0
            await response.write(chunk)
            sent += len(chunk)
    except (ConnectionResetError, ConnectionError):
        # The player left (seek, quality switch, closed the video). Normal.
        return response
    finally:
        await agen.aclose()
        _log_stream(id, sent, t0, first)

    if sent < length:
        # Telegram stopped delivering part-way. Closing the connection makes the
        # player notice at once and re-request from where it stopped; ending the
        # response quietly would leave it waiting for bytes that never come.
        logging.warning(f"stream for id={id} ended early: sent {sent} of {length} bytes")
        if request.transport is not None:
            request.transport.close()
        return response

    await response.write_eof()
    return response
