# plugins/next_episode.py
# ---------------------------------------------------------------------
# Goflix "Next Episode" feature
#  - A separate "Last watched + Next Episode" message is posted after every
#    episode file. It is NOT auto-deleted (the file message itself still
#    deletes after 1 min). Only ONE such message per user: a new one replaces
#    the previous one.
#  - Next button: S13E3 -> S13E4 (same quality)
#  - Same season, next episode; same language + same quality preferred
#  - If that quality is missing -> closest other quality
#  - After the last episode of a season -> next season, episode 1
#  - Remembers each user's last watched episode (stored on the user doc)
#  - /last shows only the last watched name (with a Next button)
#  - Same premium / verification rules as every other file delivery
# ---------------------------------------------------------------------
import re
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
from info import MULTIPLE_DATABASE, VERIFY, VERIFY_TUTORIAL, PREMIUM_AND_REFERAL_MODE
from utils import temp, check_verification, get_token

logger = logging.getLogger(__name__)

# ------------------------- CONFIG -------------------------
MAX_CANDIDATES = 400        # max DB rows examined per lookup
CARD_DELAY = 2              # seconds to wait so the card lands AFTER the file message
# ----------------------------------------------------------

# S13E4, S13 E04, S13.E04, S08 EP20, S8 EP2  (DB names already have . _ - as spaces)
SE_RE = re.compile(r"\bS(\d{1,2})[\s._-]*E(?:P)?[\s._-]*(\d{1,3})\b", re.I)
Q_RE = re.compile(r"\b(2160p|1440p|1080p|720p|480p|360p)\b", re.I)
LANGS = (
    "hindi", "tamil", "telugu", "malayalam", "kannada", "english", "bengali",
    "marathi", "punjabi", "gujarati", "urdu", "korean", "japanese", "chinese",
    "spanish", "french", "german", "russian", "turkish", "thai",
)
STOP = {"the", "a", "an", "of", "and", "in", "on", "to", "is"}


# ========================== PARSING ==========================
def parse_name(name):
    """(stem, season, episode, quality_int|None, {langs}) or None if not an episode."""
    if not name:
        return None
    clean = re.sub(r"[._]+", " ", str(name))
    m = SE_RE.search(clean)
    if not m:
        return None
    q = Q_RE.search(clean)
    stem = re.sub(r"[^a-z0-9 ]", "", clean[: m.start()].lower())
    stem = re.sub(r"\s+", " ", stem).strip()
    if not stem:
        return None
    low = clean.lower()
    langs = {l for l in LANGS if re.search(rf"\b{l}\b", low)}
    quality = int(q.group(1)[:-1]) if q else None
    return stem, int(m.group(1)), int(m.group(2)), quality, langs


def fmt_se(season, ep):
    return f"S{season:02d}E{ep:02d}"


def fmt_q(q):
    return f"{q}p" if q else "unknown quality"


# ========================== BUTTON ==========================
def _button(file_id):
    text = "⏭ ɴᴇxᴛ ᴇᴘɪsᴏᴅᴇ"
    data = f"nxt#{file_id}"
    if ButtonStyle is not None:
        try:
            return InlineKeyboardButton(text, callback_data=data, style=ButtonStyle.SUCCESS)
        except Exception:
            pass
    return InlineKeyboardButton(text, callback_data=data)


_locks = {}          # per-user lock so batch deliveries don't race
_notes = {}          # (user_id, file_id) -> extra line for the card (quality fallback etc.)


def _lock(user_id):
    if user_id not in _locks:
        _locks[user_id] = asyncio.Lock()
    return _locks[user_id]


async def prepare_episode_button(user_id, file_id):
    """
    Called every time a file is delivered (from build_stream_reply_markup).
      - if the file is a series episode: saves it as the user's last watched and
        schedules the persistent "Last watched + Next Episode" message
      - always returns None: the Next button is NOT put on the file message,
        because that message is auto-deleted after 1 minute.
    Never raises.
    """
    try:
        f = await get_file_details(file_id)
        if not f:
            return None
        p = parse_name(f.get("file_name"))
        if not p:
            return None
        stem, s, e, q, _ = p
        await _save_last_watched(user_id, stem, s, e, q, file_id)
        asyncio.create_task(_send_card(temp.BOT, user_id, stem, s, e, q, file_id, delay=CARD_DELAY))
    except Exception as ex:
        logger.warning(f"[next_episode] prepare failed: {ex}")
    return None


# ====================== LAST WATCHED ======================
async def _save_last_watched(user_id, stem, s, e, q, file_id):
    try:
        await db.col.update_one(
            {"id": int(user_id)},
            {"$set": {
                "last_watched.stem": stem, "last_watched.season": s,
                "last_watched.episode": e, "last_watched.quality": q,
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


async def _send_card(bot, user_id, stem, s, e, q, file_id, delay=0):
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
                    f"<b>{stem.title()} {fmt_se(s, e)}</b> · {fmt_q(q)}")
            note = _notes.pop((user_id, file_id), None)
            if note:
                text += f"\n{note}"
            msg = await bot.send_message(
                user_id, text,
                reply_markup=InlineKeyboardMarkup([[_button(file_id)]]))
            await db.col.update_one({"id": int(user_id)},
                                    {"$set": {"last_watched.card": msg.id}})
        except Exception as ex:
            logger.warning(f"[next_episode] send card failed: {ex}")


# ===================== FIND NEXT EPISODE =====================
def _search_sync(stem, s, e):
    """Blocking pymongo lookup - always run through asyncio.to_thread."""
    words = [w for w in stem.split() if w not in STOP] or stem.split()
    text_q = " ".join(f'"{w}"' for w in words)  # every word required
    p_same = rf"\bS0*{s}[\s._-]*E(?:P)?[\s._-]*0*{e + 1}\b"
    p_next = rf"\bS0*{s + 1}[\s._-]*E(?:P)?[\s._-]*0*1\b"
    query = {
        "$text": {"$search": text_q},
        "$or": [
            {"file_name": {"$regex": p_same, "$options": "i"}},
            {"file_name": {"$regex": p_next, "$options": "i"}},
        ],
    }
    out = []
    for c in ([col, sec_col] if MULTIPLE_DATABASE else [col]):
        try:
            out.extend(c.find(query).limit(MAX_CANDIDATES))
        except Exception as ex:
            logger.warning(f"[next_episode] search failed on {c.full_name}: {ex}")
    return out


def _pick(cands, season, episode, quality, langs):
    pool = [(d, p) for d, p in cands if p[1] == season and p[2] == episode]
    if not pool:
        return None

    def rank(item):
        _, p = item
        lang_ok = (not langs) or (not p[4]) or bool(langs & p[4])
        if quality is None or p[3] is None:
            qd = 0 if quality == p[3] else 5000
        else:
            qd = abs(p[3] - quality)
        return (0 if lang_ok else 1, qd)

    best = sorted(pool, key=rank)[0]
    exact_q = best[1][3] == quality
    return best[0], best[1], exact_q


async def find_next(file_name):
    """dict(doc, parsed, same_quality, new_season) or None."""
    p = parse_name(file_name)
    if not p:
        return None
    stem, s, e, q, langs = p
    docs = await asyncio.to_thread(_search_sync, stem, s, e)
    cands = []
    for d in docs:
        dp = parse_name(d.get("file_name"))
        if dp and (dp[0] == stem or dp[0].endswith(stem)):
            cands.append((d, dp))
    if not cands:
        return None

    hit = _pick(cands, s, e + 1, q, langs)
    if hit:
        return {"doc": hit[0], "parsed": hit[1], "same_quality": hit[2], "new_season": False}
    hit = _pick(cands, s + 1, 1, q, langs)
    if hit:
        return {"doc": hit[0], "parsed": hit[1], "same_quality": hit[2], "new_season": True}
    return None


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

    res = await find_next(cur["file_name"])
    if not res:
        return await say(f"Last watched: {cur_se}\nNext episode is not uploaded yet.", True)

    nd, np_ = res["doc"], res["parsed"]
    nid = nd["file_id"]
    next_se = fmt_se(np_[1], np_[2])

    # ---- same rules as the rest of the bot: premium OR verified today ----
    if not await db.has_premium_access(user.id):
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

    if answer:
        await answer(f"Last watched: {cur_se}")

    # extra info shown on the (persistent) card that is posted after the file
    extra = []
    if res["new_season"]:
        extra.append("🆕 <i>Next season started</i>")
    if not res["same_quality"]:
        extra.append(f"⚠️ <i>{fmt_q(cp[3])} not available, sent {fmt_q(np_[3])}</i>")
    if extra:
        _notes[(user.id, nid)] = "\n".join(extra)

    # deliver with the exact same flow as every other file (caption, stream
    # buttons, copyright notice, 60s auto-delete, "get file again" button).
    # The delivery also saves this episode as the new "last watched" and posts
    # a fresh persistent "Last watched + Next Episode" message.
    from plugins.commands import deliver_resolved_file  # lazy: avoids circular import
    asyncio.create_task(deliver_resolved_file(client, chat_id, "file", nid))


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
                     d["episode"], d.get("quality"), d["fid"])
