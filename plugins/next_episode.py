# plugins/next_episode.py
# ---------------------------------------------------------------------
# Goflix "Next Episode" feature
#  - A separate "Last watched + Next Episode" message is posted after every
#    episode file. It is NOT auto-deleted (the file message itself still
#    deletes after 1 min). Only ONE such message per user: a new one replaces
#    the previous one.
#  - Next button: S13E3 -> S13E4 (same quality)
#  - "Quality" is decided by FILE SIZE, not by the 1080p/720p tag in the name:
#        480p  : 0 - 500 MB        720p : 500 MB - 1.2 GB
#        1080p : 1.2 - 2 GB        2K   : 2 - 3 GB        4K : 3 - 4 GB
#  - Same season, next episode; same language + same size range preferred
#  - If that range is missing -> the closest available file (nearest range,
#    then nearest size). "Not uploaded yet" only if the episode truly is missing
#  - After the last episode of a season -> next season, episode 1
#  - Remembers each user's last watched episode (stored on the user doc)
#  - /last shows only the last watched name (with a Next button)
#  - Same premium / verification rules as every other file delivery
#  - FREE users: FREE_NEXT_LIMIT Next Episode uses in a row (default 2, set
#    via Koyeb env var). Searching a fresh file in the group resets it.
#    Delivering the NEXT episode is explicitly flagged as "from_next" by the
#    caller (see deliver_resolved_file/build_stream_reply_markup in
#    commands.py) so the reset logic never has to guess from timing.
#    PREMIUM users: unlimited.
# ---------------------------------------------------------------------
import os
import re
import time
import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from pyrogram.enums import ButtonStyle
except Exception:  # very old pyrogram
    ButtonStyle = None

from database.ia_filterdb import col, sec_col, get_file_details
from database.users_chats_db import db
from info import MULTIPLE_DATABASE, VERIFY, VERIFY_TUTORIAL, PREMIUM_AND_REFERAL_MODE, ADMINS
from database.ia_filterdb import clean_file_name
from utils import temp, check_verification, get_token, get_size

logger = logging.getLogger(__name__)

# ------------------------- CONFIG -------------------------
MAX_CANDIDATES = 400        # max DB rows examined per lookup
CARD_DELAY = 2              # seconds to wait so the card lands AFTER the file message
FREE_NEXT_LIMIT = int(os.environ.get("FREE_NEXT_LIMIT", 2))   # free users: Next Episodes per search
# ----------------------------------------------------------

# Same naming styles as the bot's own season/episode navigation:
#   S13E4, S13 E04, S13.E04, S08 EP20, S03.Episode.04, Season 4 Episode 3, 1x03
# (DB names already have . _ - turned into spaces)
SE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:season\s*|s)(\d{1,2})[\s._-]*(?:episode|ep|e)[\s._-]*(\d{1,3})(?!\d)",
    re.I)
X_RE = re.compile(r"(?<![A-Za-z0-9])(\d{1,2})x(\d{2,3})(?!\d)", re.I)
YEAR_RE = re.compile(r"^(19|20)\d\d$")
LANGS = (
    "hindi", "tamil", "telugu", "malayalam", "kannada", "english", "bengali",
    "marathi", "punjabi", "gujarati", "urdu", "korean", "japanese", "chinese",
    "spanish", "french", "german", "russian", "turkish", "thai",
)
# MongoDB's English text index ignores these words. A show called "From" or
# "The Boys" must not send them to $text search (that returns nothing).
STOP = set("""a about above after again against all am an and any are as at be because
been before being below between both but by can did do does doing down during each few
for from further had has have having he her here hers herself him himself his how i if
in into is it its itself just me more most my myself no nor not now of off on once only
or other our ours ourselves out over own s same she should so some such t than that the
their theirs them themselves then there these they this those through to too under until
up very was we were what when where which while who whom why will with you your yours
yourself yourselves""".split())

MB = 1024 * 1024
GB = 1024 * MB


def tier_of(size):
    """File size (bytes) -> quality tier 1..5, or None if unknown."""
    try:
        size = float(size)
    except (TypeError, ValueError):
        return None
    if size <= 0:
        return None
    if size < 500 * MB:
        return 1        # 480p
    if size < 1.2 * GB:
        return 2        # 720p
    if size < 2 * GB:
        return 3        # 1080p
    if size < 3 * GB:
        return 4        # 2K
    return 5            # 4K (3-4 GB and above)


# ========================== PARSING ==========================
def parse_name(name):
    """(stem, season, episode, {langs}) or None if the name is not an episode."""
    if not name:
        return None
    clean = re.sub(r"[._]+", " ", str(name))
    m = SE_RE.search(clean) or X_RE.search(clean)
    if not m:
        return None
    stem = re.sub(r"[^a-z0-9 ]", "", clean[: m.start()].lower())
    stem = re.sub(r"\s+", " ", stem).strip()
    if not stem:
        return None
    low = clean.lower()
    langs = {l for l in LANGS if re.search(rf"\b{l}\b", low)}
    return stem, int(m.group(1)), int(m.group(2)), langs


def fmt_se(season, ep):
    return f"S{season:02d}E{ep:02d}"


def fmt_size(size):
    try:
        return get_size(size)
    except Exception:
        return "unknown size"


# ========================== BUTTON ==========================
def _button(file_id, left=None):
    """left: None = premium/unknown, int = free uses remaining."""
    data = f"nxt#{file_id}"
    locked = left is not None and left <= 0
    if locked:
        text, style = "🔒 ɴᴇxᴛ ᴇᴘɪsᴏᴅᴇ", "DANGER"
    elif left is not None:
        text, style = f"⏭ ɴᴇxᴛ ᴇᴘɪsᴏᴅᴇ ({left} ʟᴇꜰᴛ)", "SUCCESS"
    else:
        text, style = "⏭ ɴᴇxᴛ ᴇᴘɪsᴏᴅᴇ", "SUCCESS"
    if ButtonStyle is not None:
        try:
            return InlineKeyboardButton(text, callback_data=data, style=getattr(ButtonStyle, style))
        except Exception:
            pass
    return InlineKeyboardButton(text, callback_data=data)


# ============ FREE-USER LIMIT (premium = unlimited) ============


async def _next_status(user_id):
    """(is_premium, uses_left). uses_left is None for premium users."""
    try:
        if await db.has_premium_access(user_id):
            return True, None
        u = await db.col.find_one({"id": int(user_id)}, {"next_uses": 1})
        used = int((u or {}).get("next_uses", 0))
        return False, max(0, FREE_NEXT_LIMIT - used)
    except Exception:
        return False, None


async def _reset_next_uses(user_id):
    try:
        await db.col.update_one({"id": int(user_id)}, {"$set": {"next_uses": 0}})
    except Exception as ex:
        logger.warning(f"[next_episode] reset counter failed: {ex}")


_locks = {}          # per-user lock so batch deliveries don't race
_notes = {}          # (user_id, file_id) -> extra line for the card (quality fallback etc.)


def _lock(user_id):
    if user_id not in _locks:
        _locks[user_id] = asyncio.Lock()
    return _locks[user_id]


async def prepare_episode_button(user_id, file_id, from_next=False):
    """
    Called every time a file is delivered (from build_stream_reply_markup).
      - if the file is a series episode: saves it as the user's last watched and
        schedules the persistent "Last watched + Next Episode" message
      - always returns None: the Next button is NOT put on the file message,
        because that message is auto-deleted after 1 minute.
    `from_next=True` is passed explicitly by _send_next() below (via
    deliver_resolved_file) when this delivery IS the next episode the user
    just requested; any other delivery (a normal search) resets the free-use
    counter to a fresh FREE_NEXT_LIMIT. This is a direct flag, not a guess
    from timing, so it can't misfire.
    Never raises.
    """
    try:
        if not from_next:
            await _reset_next_uses(user_id)
            logger.info(f"[next_episode] counter RESET user={user_id} file={file_id} "
                        f"-> treated as a fresh search")
        else:
            logger.info(f"[next_episode] counter kept user={user_id} file={file_id} "
                        f"(delivered as the Next episode)")
        f = await get_file_details(file_id)
        if not f:
            return None
        p = parse_name(f.get("file_name"))
        if not p:
            return None
        stem, s, e, _ = p
        size = f.get("file_size")
        await _save_last_watched(user_id, stem, s, e, size, file_id)
        asyncio.create_task(_send_card(temp.BOT, user_id, stem, s, e, size, file_id, delay=CARD_DELAY))
    except Exception as ex:
        logger.warning(f"[next_episode] prepare failed: {ex}")
    return None


# ====================== LAST WATCHED ======================
async def _save_last_watched(user_id, stem, s, e, size, file_id):
    try:
        await db.col.update_one(
            {"id": int(user_id)},
            {"$set": {
                "last_watched.stem": stem, "last_watched.season": s,
                "last_watched.episode": e, "last_watched.size": size,
                "last_watched.fid": file_id,
            }},
        )
    except Exception as ex:
        logger.warning(f"[next_episode] save last watched failed: {ex}")


async def _get_last_watched(user_id):
    try:
        u = await db.col.find_one({"id": int(user_id)}, {"last_watched": 1})
        return (u or {}).get("last_watched")
    except Exception:
        return None


async def _send_card(bot, user_id, stem, s, e, size, file_id, delay=0):
    """
    Posts the persistent message: last watched name + Next Episode button.
    Deletes the user's previous card first, so there is always only one.
    This message is never auto-deleted.
    """
    if delay:
        await asyncio.sleep(delay)
    async with _lock(user_id):
        try:
            old = await _get_last_watched(user_id)
            old_mid = (old or {}).get("card")
            if old_mid:
                try:
                    await bot.delete_messages(user_id, old_mid)
                except Exception:
                    pass
            text = (f"▶️ <b>Last watched:</b>\n"
                    f"<b>{stem.title()} {fmt_se(s, e)}</b>"
                    + (f" · {fmt_size(size)}" if size else ""))
            note = _notes.pop((user_id, file_id), None)
            if note:
                text += f"\n{note}"
            premium, left = await _next_status(user_id)
            if premium:
                text += "\n💎 <i>Premium: unlimited next episodes</i>"
            elif left is not None and left > 0:
                text += f"\n🎟 <i>Free next episodes left: {left}/{FREE_NEXT_LIMIT}</i>"
            elif left is not None:
                text += ("\n🔒 <i>Free limit reached. Search again in the group to continue, "
                         "or get Premium for unlimited next episodes.</i>")
            msg = await bot.send_message(
                user_id, text,
                reply_markup=InlineKeyboardMarkup([[_button(file_id, left)]]))
            await db.col.update_one({"id": int(user_id)},
                                    {"$set": {"last_watched.card": msg.id}})
        except Exception as ex:
            logger.warning(f"[next_episode] send card failed: {ex}")


# ===================== FIND NEXT EPISODE =====================
def _se_regex(season, episode):
    """DB regex for one season/episode in any supported naming style."""
    return (rf"(?:(?:season\s*|s)0*{season}[\s._-]*(?:episode|ep|e)[\s._-]*0*{episode}(?![0-9])"
            rf"|(?<![a-z0-9])0*{season}x0*{episode}(?![0-9]))")


def _season_regex(season):
    """Matches ANY episode of one season (episode number left open)."""
    return (rf"(?:(?:season\s*|s)0*{season}[\s._-]*(?:episode|ep|e)[\s._-]*\d{{1,3}}(?![0-9])"
            rf"|(?<![a-z0-9])0*{season}x\d{{2,3}}(?![0-9]))")


def _season_episodes_sync(stem, season):
    """Blocking: {episode: [file names]} of `stem` found for `season`. Run via asyncio.to_thread."""
    toks = _tokens(stem) or stem.split()
    stem_pat = r"[\s._-]+".join(re.escape(w) for w in toks)
    pre = rf"(?:^|[^a-z0-9]){stem_pat}(?:[\s._-]+(?:19|20)[0-9]{{2}})?[\s._-]+"
    docs = _run({"file_name": {"$regex": pre + _season_regex(season), "$options": "i"}})
    eps = {}    # episode number -> [file names]  (len(eps) = number of episodes)
    for d in docs:
        dp = parse_name(d.get("file_name"))
        if dp and _same_show(dp[0], stem) and dp[1] == season and dp[2] > 0:   # E00 = extras/specials
            eps.setdefault(dp[2], []).append(d.get("file_name"))
    return eps


def _tokens(stem):
    return [w for w in stem.split() if not YEAR_RE.match(w)]


def _same_show(a, b):
    """Lenient show-name match: ignores years and extra leading/trailing tags."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return a == b
    if ta == tb:
        return True
    n = min(len(ta), len(tb))
    return ta[-n:] == tb[-n:] or ta[:n] == tb[:n]


def _pick(cands, season, episode, cur_tier, cur_size, langs):
    """cands: [(doc, parsed)]. Returns (doc, parsed, same_tier) for that episode."""
    pool = [(d, p) for d, p in cands if p[1] == season and p[2] == episode]
    if not pool:
        return None

    def rank(item):
        d, p = item
        lang_ok = (not langs) or (not p[3]) or bool(langs & p[3])
        t = tier_of(d.get("file_size"))
        tier_diff = abs(t - cur_tier) if (t and cur_tier) else 0
        try:
            size_diff = abs(float(d.get("file_size") or 0) - float(cur_size or 0))
        except (TypeError, ValueError):
            size_diff = 0
        return (0 if lang_ok else 1, tier_diff, size_diff)

    best = sorted(pool, key=rank)[0]
    same = (cur_tier is not None) and tier_of(best[0].get("file_size")) == cur_tier
    return best[0], best[1], same


def _run(query):
    """Blocking pymongo find on col (+ sec_col). Run through asyncio.to_thread."""
    out = []
    for c in ([col, sec_col] if MULTIPLE_DATABASE else [col]):
        try:
            out.extend(c.find(query).limit(MAX_CANDIDATES))
        except Exception as ex:
            logger.warning(f"[next_episode] search failed on {c.full_name}: {ex}")
    return out


def _search_text_sync(stem, s, e):
    """Fast: indexed $text search (every real word required)."""
    words = [w for w in _tokens(stem) if w not in STOP]
    if not words:
        return []   # e.g. a show called "From": MongoDB's text index ignores that word
    text_q = " ".join(f'"{w}"' for w in words)
    return _run({
        "$text": {"$search": text_q},
        "$or": [
            {"file_name": {"$regex": rf"(?<![a-z0-9]){_se_regex(s, e + 1)}", "$options": "i"}},
            {"file_name": {"$regex": rf"(?<![a-z0-9]){_se_regex(s + 1, 1)}", "$options": "i"}},
        ],
    })


def _search_regex_sync(stem, s, e):
    """Fallback: plain regex scan (slower, only used when the fast search finds nothing)."""
    toks = _tokens(stem) or stem.split()
    stem_pat = r"[\s._-]+".join(re.escape(w) for w in toks)
    pre = rf"(?:^|[^a-z0-9]){stem_pat}(?:[\s._-]+(?:19|20)[0-9]{{2}})?[\s._-]+"
    return _run({"$or": [
        {"file_name": {"$regex": pre + _se_regex(s, e + 1), "$options": "i"}},
        {"file_name": {"$regex": pre + _se_regex(s + 1, 1), "$options": "i"}},
    ]})


def _candidates(docs, stem):
    out = []
    for d in docs:
        dp = parse_name(d.get("file_name"))
        if dp and _same_show(dp[0], stem):
            out.append((d, dp))
    return out


async def find_next(cur_doc):
    """dict(doc, parsed, same_quality, new_season) or None."""
    p = parse_name(cur_doc.get("file_name"))
    if not p:
        return None
    stem, s, e, langs = p
    cur_size = cur_doc.get("file_size")
    cur_tier = tier_of(cur_size)

    def choose(cands):
        hit = _pick(cands, s, e + 1, cur_tier, cur_size, langs)
        if hit:
            return {"doc": hit[0], "parsed": hit[1], "same_quality": hit[2], "new_season": False}
        hit = _pick(cands, s + 1, 1, cur_tier, cur_size, langs)
        if hit:
            return {"doc": hit[0], "parsed": hit[1], "same_quality": hit[2], "new_season": True}
        return None

    async def add_season_total(res):
        if res and res["new_season"]:
            # count the NEW season and the season that was just finished
            new_eps, old_eps = await asyncio.gather(
                asyncio.to_thread(_season_episodes_sync, stem, res["parsed"][1]),
                asyncio.to_thread(_season_episodes_sync, stem, s))
            res["season_total"] = len(new_eps)
            # the episode just watched was the last one, so its number is the
            # minimum the season can have (covers gaps in the DB)
            res["prev_total"] = max(len(old_eps), e)
        return res

    # 1) fast indexed search
    docs1 = await asyncio.to_thread(_search_text_sync, stem, s, e)
    res = choose(_candidates(docs1, stem))
    if not res:
        # 2) fallback regex scan (also runs when step 1 found files but none matched)
        docs2 = await asyncio.to_thread(_search_regex_sync, stem, s, e)
        res = choose(_candidates(docs2, stem))
    else:
        docs2 = []
    res = await add_season_total(res)
    if res:
        logger.info(
            f"[next_episode] found: stem={stem!r} S{s}E{e} -> "
            f"{res['parsed'][0]!r} S{res['parsed'][1]}E{res['parsed'][2]} "
            f"new_season={res['new_season']} season_total={res.get('season_total')} "
            f"same_quality={res['same_quality']} "
            f"file={res['doc'].get('file_name')!r}")
    else:
        logger.warning(
            f"[next_episode] no next episode: stem={stem!r} S{s} E{e} "
            f"text_docs={len(docs1)} regex_docs={len(docs2)} "
            f"sample={[d.get('file_name') for d in (docs1 + docs2)[:5]]}")
    return res


# ========================== HANDLERS ==========================
async def _send_next(client, user, chat_id, cur_file_id, answer=None):
    """Shared by the button and /last. `answer` is callback_query.answer or None."""
    async def say(text, alert=False):
        if answer:
            await answer(text, show_alert=alert)
        else:
            await client.send_message(chat_id, text)

    cur = await get_file_details(cur_file_id)
    cp = parse_name(cur.get("file_name")) if cur else None
    if not cp:
        return await say("Could not detect the season/episode of this file.", True)
    cur_se = fmt_se(cp[1], cp[2])

    # ---- free users: limited Next Episodes per search; premium unlimited ----
    premium, left = await _next_status(user.id)
    if not premium and left is not None and left <= 0:
        msg = (f"Free limit reached ({FREE_NEXT_LIMIT}/{FREE_NEXT_LIMIT} next episodes used).\n"
               f"Search again in the group to continue, or get Premium for unlimited.")
        return await say(msg, True)

    res = await find_next(cur)
    if not res:
        return await say(
            f"Last watched: {cur_se}\n"
            f"🏁 This is the last episode uploaded so far (Season {cp[1]}).\n"
            f"The next one isn't uploaded yet.", True)

    nd, np_ = res["doc"], res["parsed"]
    nid = nd["file_id"]
    next_se = fmt_se(np_[1], np_[2])

    # ---- same rules as the rest of the bot: premium OR verified today ----
    if not premium:
        if not await check_verification(client, user.id) and VERIFY == True:
            btn = [
                [InlineKeyboardButton(
                    "ᴠᴇʀɪғʏ",
                    url=await get_token(client, user.id,
                                        f"https://telegram.me/{temp.U_NAME}?start=",
                                        pending_data=f"file_{nid}"))],
                [InlineKeyboardButton("ʜᴏᴡ ᴛᴏ ᴠᴇʀɪғʏ", url=VERIFY_TUTORIAL)],
            ]
            text = (f"<b>ʜᴇʏ {user.mention} 👋,\n\nʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴠᴇʀɪғɪᴇᴅ ᴛᴏᴅᴀʏ, ᴘʟᴇᴀꜱᴇ ᴄʟɪᴄᴋ ᴏɴ "
                    f"ᴠᴇʀɪғʏ & ɢᴇᴛ ᴜɴʟɪᴍɪᴛᴇᴅ ᴀᴄᴄᴇꜱꜱ ғᴏʀ ᴛᴏᴅᴀʏ</b>")
            if PREMIUM_AND_REFERAL_MODE == True:
                text += ("\n\n<b>ɪғ ʏᴏᴜ ᴡᴀɴᴛ ᴅɪʀᴇᴄᴛ ғɪʟᴇꜱ ᴡɪᴛʜᴏᴜᴛ ᴀɴʏ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴꜱ ᴛʜᴇɴ ʙᴜʏ ʙᴏᴛ "
                         "ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ ☺️\n\n💶 ꜱᴇɴᴅ /plan ᴛᴏ ʙᴜʏ ꜱᴜʙꜱᴄʀɪᴘᴛɪᴏɴ</b>")
            if answer:
                await answer(f"Last watched: {cur_se}")
            await client.send_message(chat_id, text, protect_content=True,
                                      reply_markup=InlineKeyboardMarkup(btn))
            return

    # gating passed -> this Next Episode counts for free users
    if not premium:
        try:
            # atomic: only counts while still under the limit (safe against double taps)
            r = await db.col.update_one(
                {"id": int(user.id),
                 "$or": [{"next_uses": {"$lt": FREE_NEXT_LIMIT}}, {"next_uses": {"$exists": False}}]},
                {"$inc": {"next_uses": 1}})
            logger.info(f"[next_episode] counter inc user={user.id} matched={r.matched_count} "
                        f"limit={FREE_NEXT_LIMIT}")
            if r.matched_count == 0 and await db.col.find_one({"id": int(user.id)}, {"_id": 1}) is not None:
                return await say(
                    f"Free limit reached ({FREE_NEXT_LIMIT}/{FREE_NEXT_LIMIT} next episodes used).\n"
                    f"Search again in the group to continue, or get Premium for unlimited.", True)
        except Exception as ex:
            logger.warning(f"[next_episode] counter update failed: {ex}")
    if answer:
        await answer(f"Last watched: {cur_se}")

    # extra info shown on the (persistent) card that is posted after the file
    extra = []
    if res["new_season"]:
        total = res.get("season_total")
        prev = res.get("prev_total")
        extra.append(f"🏁 <i>This was the last episode of Season {cp[1]}</i>")
        if prev:
            extra.append(f"<i>{prev} episode{'s' if prev != 1 else ''} finished</i>")
        if total:
            extra.append(f"🆕 <i>Season {np_[1]} started · {total} episodes available · "
                         f"starting with Episode 1</i>")
        else:
            extra.append(f"🆕 <i>Season {np_[1]} started</i>")
    if not res["same_quality"]:
        extra.append(f"⚠️ <i>Same quality not available, sent the closest one "
                     f"({fmt_size(nd.get('file_size'))})</i>")
    if extra:
        _notes[(user.id, nid)] = "\n".join(extra)

    # deliver with the exact same flow as every other file (caption, stream
    # buttons, copyright notice, 60s auto-delete, "get file again" button).
    # The delivery also saves this episode as the new "last watched" and posts
    # a fresh persistent "Last watched + Next Episode" message.
    from plugins.commands import deliver_resolved_file  # lazy: avoids circular import
    asyncio.create_task(deliver_resolved_file(client, chat_id, "file", nid, from_next=True))


@Client.on_callback_query(filters.regex(r"^nxt#"), group=-1)
async def next_episode_cb(client, query):
    file_id = query.data.split("#", 1)[1]
    try:
        await _send_next(client, query.from_user, query.message.chat.id,
                         file_id, answer=query.answer)
    except Exception as ex:
        logger.exception("[next_episode] callback failed")
        try:
            await query.answer("Something went wrong, try again.", show_alert=True)
        except Exception:
            pass
    query.stop_propagation()


@Client.on_message(filters.command("last") & filters.private)
async def last_watched_cmd(client, message):
    d = await _get_last_watched(message.from_user.id)
    if not d or not d.get("fid"):
        return await message.reply_text("<b>You have not watched any episode yet.</b>")
    await _send_card(client, message.from_user.id, d["stem"], d["season"],
                     d["episode"], d.get("size"), d["fid"])


@Client.on_message(filters.command("nextdebug") & filters.private)
async def next_debug_cmd(client, message):
    """Admin only. Reply /nextdebug to a file the bot sent (or /nextdebug <file name>)
    to see how the bot parses it and which next-episode files it finds."""
    if message.from_user.id not in ADMINS:
        return
    name = None
    r = message.reply_to_message
    if r and r.media:
        media = getattr(r, r.media.value, None)
        name = clean_file_name(getattr(media, "file_name", None) or (r.caption or ""))
    elif len(message.command) > 1:
        name = clean_file_name(" ".join(message.command[1:]))
    if not name:
        return await message.reply_text("Reply to a file, or send: /nextdebug <file name>")
    p = parse_name(name)
    if not p:
        return await message.reply_text(f"<b>Not detected as an episode:</b>\n<code>{name}</code>")
    stem, s, e, langs = p
    fsize = None
    if r and r.media:
        fsize = getattr(getattr(r, r.media.value, None), "file_size", None)
    fake_doc = {"file_name": name, "file_size": fsize}
    res = await find_next(fake_doc)
    d1 = await asyncio.to_thread(_search_text_sync, stem, s, e)
    d2 = await asyncio.to_thread(_search_regex_sync, stem, s, e)
    c1, c2 = _candidates(d1, stem), _candidates(d2, stem)
    sample = "\n".join(f"• <code>{d.get('file_name')}</code> ({fmt_size(d.get('file_size'))})"
                       for d, _ in (c1 + c2)[:6]) or "none"
    cur_eps = await asyncio.to_thread(_season_episodes_sync, stem, s)
    nxt_eps = await asyncio.to_thread(_season_episodes_sync, stem, s + 1)
    season_info = (f"<b>Season {s} episodes in DB ({len(cur_eps)}):</b> {sorted(cur_eps)}\n"
                   f"<b>Season {s + 1} episodes in DB ({len(nxt_eps)}):</b> {sorted(nxt_eps)}\n")
    if nxt_eps:
        top = max(nxt_eps)
        season_info += (f"<b>Highest S{s + 1:02d} = E{top:02d}:</b>\n"
                        + "\n".join(f"• <code>{n}</code>" for n in nxt_eps[top][:3]) + "\n")
    picked = (f"<code>{res['doc'].get('file_name')}</code>\n"
              f"new_season={res['new_season']} same_quality={res['same_quality']}") if res else "NONE FOUND"
    await message.reply_text(
        f"<b>Parsed:</b> stem=<code>{stem}</code> S{s:02d}E{e:02d} langs={sorted(langs)}\n"
        f"<b>Looking for:</b> S{s:02d}E{e + 1:02d} or S{s + 1:02d}E01\n"
        f"<b>Fast search:</b> {len(d1)} found, {len(c1)} matched show\n"
        f"<b>Fallback search:</b> {len(d2)} found, {len(c2)} matched show\n\n"
        f"{season_info}\n"
        f"<b>Matches:</b>\n{sample}\n\n"
        f"<b>find_next() would pick:</b>\n{picked}")


@Client.on_message(filters.command("nextstatus") & filters.private)
async def next_status_cmd(client, message):
    """Shows the user's Next Episode allowance (handy to check the limit is working)."""
    premium, left = await _next_status(message.from_user.id)
    if premium:
        text = "💎 <b>Premium:</b> unlimited next episodes."
    elif left is None:
        text = "Could not read your next-episode status. Try again."
    else:
        text = (f"🎟 <b>Free next episodes left:</b> {left}/{FREE_NEXT_LIMIT}\n"
                f"<i>Search again in the group to get a fresh {FREE_NEXT_LIMIT}.</i>")
    await message.reply_text(text)
