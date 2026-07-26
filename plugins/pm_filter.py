import os, logging, string, asyncio, time, re, ast, random, math, pytz, pyrogram, functools, difflib
from datetime import datetime, timedelta, date, time
from Script import script
from info import *
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputMediaPhoto, ChatPermissions, WebAppInfo
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, UserIsBlocked, MessageNotModified, PeerIdInvalid, QueryIdInvalid, MessageIdInvalid
from pyrogram.errors.exceptions.bad_request_400 import MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty
from utils import get_size, is_subscribed, pub_is_subscribed, get_poster, search_gagala, temp, get_settings, save_group_settings, get_shortlink, get_tutorial, send_all, get_cap, is_premium_user
from database.users_chats_db import db
from database.ia_filterdb import col, sec_col, db as vjdb, sec_db, get_file_details, get_search_results, get_bad_files
from database.filters_mdb import del_all, find_filter, get_filters
from database.connections_mdb import mydb, active_connection, all_connections, delete_connection, if_active, make_active, make_inactive
from database.gfilters_mdb import find_gfilter, get_gfilters, del_allg
from urllib.parse import quote_plus
from TechVJ.util.file_properties import get_name, get_hash, get_media_file_size
import urllib.parse

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)
lock = asyncio.Lock()

BUTTON = {}
BUTTONS = {}
FRESH = {}
SEASON_OWNER = {}
BUTTONS0 = {}
BUTTONS1 = {}
BUTTONS2 = {}
SPELL_CHECK = {}

async def build_searching_text(message):
    """Searching... placeholder text, with an extra-attention premium badge
    on top when the searcher is a premium user."""
    if message.from_user and await is_premium_user(message.from_user.id):
        return (
            f"👑 <b>PREMIUM USER SEARCH</b> 👑\n"
            f"⭐ {message.from_user.mention} ⭐\n\n"
            f"<b><i>Searching For {message.text} 🔍</i></b>"
        )
    return f"<b><i>Searching For {message.text} 🔍</i></b>"

@Client.on_message(filters.group & filters.text & filters.incoming)
async def give_filter(client, message):
    if message.text and message.text.startswith("/"):
        return
    try:
        from database.guard_db import get_settings as guard_get_settings
        guard_s = await guard_get_settings(message.chat.id)
        if guard_s.get("enabled", False) and guard_s.get("longmsg_guard", True):
            word_limit = guard_s.get("word_limit", 100)
            if word_limit > 0 and len((message.text or "").split()) >= word_limit:
                return
    except:
        pass
    if message.chat.id != SUPPORT_CHAT_ID:
        settings = await get_settings(message.chat.id)
        chatid = message.chat.id 
        user_id = message.from_user.id if message.from_user else 0
        if settings['fsub'] != None:
            try:
                btn = await pub_is_subscribed(client, message, settings['fsub'])
                if btn:
                    btn.append([InlineKeyboardButton("Unmute Me 🔕", callback_data=f"unmuteme#{int(user_id)}")])
                    await client.restrict_chat_member(chatid, message.from_user.id, ChatPermissions(can_send_messages=False))
                    await message.reply_photo(photo=random.choice(PICS), caption=f"👋 Hello {message.from_user.mention},\n\nPlease join the channel then click on unmute me button. 😇", reply_markup=InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)
                    return
            except Exception as e:
                print(e)
            
        manual = await manual_filters(client, message)
        if manual == False:
            settings = await get_settings(message.chat.id)
            try:
                if settings['auto_ffilter']:
                    ai_search = True
                    reply_msg = await message.reply_text(await build_searching_text(message))
                    try:
                        await auto_filter(client, message.text, message, reply_msg, ai_search)
                    except Exception as e:
                        logging.error(f"auto_filter (group) failed for query '{message.text}': {e}")
                        try:
                            await reply_msg.edit_text("⚠️ <b>Search took too long or failed.</b> Please try again in a moment.")
                        except Exception:
                            pass
            except KeyError:
                grpid = await active_connection(str(message.from_user.id))
                await save_group_settings(grpid, 'auto_ffilter', True)
                settings = await get_settings(message.chat.id)
                if settings['auto_ffilter']:
                    ai_search = True
                    reply_msg = await message.reply_text(await build_searching_text(message))
                    try:
                        await auto_filter(client, message.text, message, reply_msg, ai_search)
                    except Exception as e:
                        logging.error(f"auto_filter (group) failed for query '{message.text}': {e}")
                        try:
                            await reply_msg.edit_text("⚠️ <b>Search took too long or failed.</b> Please try again in a moment.")
                        except Exception:
                            pass
    else: #a better logic to avoid repeated lines of code in auto_filter function
        search = message.text
        temp_files, temp_offset, total_results = await get_search_results(chat_id=message.chat.id, query=search.lower(), offset=0, filter=True)
        if total_results == 0:
            return
        else:
            return await message.reply_text(f"<b>Hᴇʏ {message.from_user.mention}, {str(total_results)} ʀᴇsᴜʟᴛs ᴀʀᴇ ғᴏᴜɴᴅ ɪɴ ᴍʏ ᴅᴀᴛᴀʙᴀsᴇ ғᴏʀ ʏᴏᴜʀ ᴏ̨ᴜᴇʀʏ {search}. \n\nTʜɪs ɪs ᴀ sᴜᴘᴘᴏʀᴛ ɢʀᴏᴜᴘ sᴏ ᴛʜᴀᴛ ʏᴏᴜ ᴄᴀɴ'ᴛ ɢᴇᴛ ғɪʟᴇs ғʀᴏᴍ ʜᴇʀᴇ...\n\nJᴏɪɴ ᴀɴᴅ Sᴇᴀʀᴄʜ Hᴇʀᴇ - {GRP_LNK}</b>")

@Client.on_message(filters.private & filters.text & filters.incoming)
async def pm_text(bot, message):
    try:
        from database.guard_db import get_pending_chats
        pending = await get_pending_chats(message.from_user.id)
        if pending:
            return
    except:
        pass
    content = message.text
    user = message.from_user.first_name
    user_id = message.from_user.id
    if content.startswith("/") or content.startswith("#"): return  # ignore commands and hashtags

    # PM search rule:
    #  - PREMIUM_AND_REFERAL_MODE == False -> legacy behaviour, PM_SEARCH toggle
    #    decides for everyone (unchanged from before).
    #  - PREMIUM_AND_REFERAL_MODE == True  -> only premium users can search in
    #    PM. Verified (VERIFY-passed) but non-premium users must search in group.
    if PREMIUM_AND_REFERAL_MODE:
        pm_search_allowed = await is_premium_user(user_id)
    else:
        pm_search_allowed = PM_SEARCH

    if pm_search_allowed:
        ai_search = True
        reply_msg = await bot.send_message(message.from_user.id, await build_searching_text(message), reply_to_message_id=message.id)
        try:
            await auto_filter(bot, content, message, reply_msg, ai_search)
        except Exception as e:
            logging.error(f"auto_filter (PM) failed for query '{content}': {e}")
            try:
                await reply_msg.edit_text(
                    "⚠️ <b>Search took too long or failed.</b> Please try again in a moment."
                )
            except Exception:
                pass
    elif PREMIUM_AND_REFERAL_MODE:
        await message.reply_text(
            f"🔒 <b>PM Search Is A Premium Feature</b>\n\n"
            f"ʜᴇʏ {message.from_user.mention}, sᴇᴀʀᴄʜɪɴɢ ɪɴ ᴘᴍ ɪs ᴏɴʟʏ ᴀᴠᴀɪʟᴀʙʟᴇ ғᴏʀ ᴘʀᴇᴍɪᴜᴍ ᴜsᴇʀs.\n\n"
            f"➜ Search in our group instead, or buy premium with /plan to unlock PM search.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 Search In Group", url=GRP_LNK)],
                [InlineKeyboardButton("ᯓ★ Get Premium ★ᯓ", callback_data="subscription")]
            ])
        )
    else:
        await message.reply_text(
            f"👋 <b>Hello {message.from_user.mention}!</b>\n\n"
            f"🎬 I am <b>Goflix</b> File Search Bot!\n\n"
            f"📌 <b>How to use me?</b>\n"
            f"➜ Join our group and search any movie or series!\n\n"
            f"⚡️ I only work in our group, not in PM!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 Search Files", url=GRP_LNK)],
                [InlineKeyboardButton("📢 Updates Channel", url=CHNL_LNK)]
            ])
        )
    
@Client.on_callback_query(filters.regex(r"^next"))
async def next_page(bot, query):
    ident, req, key, offset = query.data.split("_")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    if int(req) not in [query.from_user.id, 0]:
        return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    try:
        offset = int(offset)
    except:
        offset = 0
    search = FRESH.get(key)
    if not search:
        try:
            await query.answer("⚠️ This search has expired. Please search again.", show_alert=True)
        except Exception:
            pass
        return

    files, n_offset, total = await get_ranked_page(query.message.chat.id, key, search, offset=offset, max_results=8)
    try:
        n_offset = int(n_offset)
    except:
        n_offset = 0

    if not files:
        return
    temp.GETALL[key] = files
    temp.SHORT[query.from_user.id] = query.message.chat.id
    settings = await get_settings(query.message.chat.id)
    pre = 'filep' if settings.get('file_secure', False) else 'file'
    # ✅ Speed: the ❌-availability scan needs the FULL matching pool
    # (up to 50,000 files), which is what was adding 1.5-2s to every
    # single search. Don't block the results on it here — fire it off
    # in the background so SEASON_CACHE is warm by the time the user
    # taps into a season/episode/quality screen (where ❌ marks do show).
    asyncio.create_task(get_cached_season_files(query.message.chat.id, key, search))
    if settings.get('button', False):
        btn = [
            [
                InlineKeyboardButton(
                    text=format_file_button_text(file), callback_data=f'{pre}#{file["file_id"]}'
                ),
            ]
            for file in files
        ]

        btn.insert(0, [
            InlineKeyboardButton("🍃 ꜱᴇʀɪᴇꜱ ᴄʟɪᴄᴋ 🍃", callback_data=f"seasons#{key}", style=enums.ButtonStyle.PRIMARY),
            build_language_button("all", key, req)[0]
        ])
        btn.insert(1, build_quality_row("all", key, req))
    else:
        btn = [
            [
                InlineKeyboardButton("🍃 ꜱᴇʀɪᴇꜱ ᴄʟɪᴄᴋ 🍃", callback_data=f"seasons#{key}", style=enums.ButtonStyle.PRIMARY),
                build_language_button("all", key, req)[0]
            ],
            build_quality_row("all", key, req)
        ]
    try:
        if settings['max_btn']:
            if 0 < offset <= 10:
                off_set = 0
            elif offset == 0:
                off_set = None
            else:
                off_set = offset - 10
            if n_offset == 0:
                btn.append(
                    [InlineKeyboardButton("⌫ 𝐁𝐀𝐂𝐊", callback_data=f"next_{req}_{key}_{off_set}", style=enums.ButtonStyle.PRIMARY), InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages", style=enums.ButtonStyle.SUCCESS)]
                )
            elif off_set is None:
                btn.append([InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton("𝐍𝐄𝐗𝐓 ➪", callback_data=f"next_{req}_{key}_{n_offset}", style=enums.ButtonStyle.PRIMARY)])
            else:
                btn.append(
                    [
                        InlineKeyboardButton("⌫ 𝐁𝐀𝐂𝐊", callback_data=f"next_{req}_{key}_{off_set}", style=enums.ButtonStyle.PRIMARY),
                        InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages", style=enums.ButtonStyle.SUCCESS),
                        InlineKeyboardButton("𝐍𝐄𝐗𝐓 ➪", callback_data=f"next_{req}_{key}_{n_offset}", style=enums.ButtonStyle.PRIMARY)
                    ],
                )
        else:
            if 0 < offset <= int(MAX_B_TN):
                off_set = 0
            elif offset == 0:
                off_set = None
            else:
                off_set = offset - int(MAX_B_TN)
            if n_offset == 0:
                btn.append(
                    [InlineKeyboardButton("⌫ 𝐁𝐀𝐂𝐊", callback_data=f"next_{req}_{key}_{off_set}", style=enums.ButtonStyle.PRIMARY), InlineKeyboardButton(f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages", style=enums.ButtonStyle.SUCCESS)]
                )
            elif off_set is None:
                btn.append([InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton("𝐍𝐄𝐗𝐓 ➪", callback_data=f"next_{req}_{key}_{n_offset}", style=enums.ButtonStyle.PRIMARY)])
            else:
                btn.append(
                    [
                        InlineKeyboardButton("⌫ 𝐁𝐀𝐂𝐊", callback_data=f"next_{req}_{key}_{off_set}", style=enums.ButtonStyle.PRIMARY),
                        InlineKeyboardButton(f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages", style=enums.ButtonStyle.SUCCESS),
                        InlineKeyboardButton("𝐍𝐄𝐗𝐓 ➪", callback_data=f"next_{req}_{key}_{n_offset}", style=enums.ButtonStyle.PRIMARY)
                    ],
                )
    except KeyError:
        await save_group_settings(query.message.chat.id, 'max_btn', True)
        if 0 < offset <= 10:
            off_set = 0
        elif offset == 0:
            off_set = None
        else:
            off_set = offset - 10
        if n_offset == 0:
            btn.append(
                [InlineKeyboardButton("⌫ 𝐁𝐀𝐂𝐊", callback_data=f"next_{req}_{key}_{off_set}", style=enums.ButtonStyle.PRIMARY), InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages", style=enums.ButtonStyle.SUCCESS)]
            )
        elif off_set is None:
            btn.append([InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton("𝐍𝐄𝐗𝐓 ➪", callback_data=f"next_{req}_{key}_{n_offset}", style=enums.ButtonStyle.PRIMARY)])
        else:
            btn.append(
                [
                    InlineKeyboardButton("⌫ 𝐁𝐀𝐂𝐊", callback_data=f"next_{req}_{key}_{off_set}", style=enums.ButtonStyle.PRIMARY),
                    InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages", style=enums.ButtonStyle.SUCCESS),
                    InlineKeyboardButton("𝐍𝐄𝐗𝐓 ➪", callback_data=f"next_{req}_{key}_{n_offset}", style=enums.ButtonStyle.PRIMARY)
                ],
            )
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        cap = await get_cap(settings, remaining_seconds, files, query, total, search)
        try:
            await query.message.edit_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), disable_web_page_preview=True)
        except MessageNotModified:
            pass
    else:
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(btn)
            )
        except (MessageNotModified, MessageIdInvalid):
            pass
    try:
        await query.answer()
    except QueryIdInvalid:
        pass
#1234567
@Client.on_callback_query(filters.regex(r"^spol"))
async def advantage_spoll_choker(bot, query):
    _, user, movie_ = query.data.split('#')
    movies = SPELL_CHECK.get(query.message.reply_to_message.id)
  #  if not movies:
     #   return await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    if int(user) != 0 and query.from_user.id != int(user):
        return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    if movie_ == "close_spellcheck":
        return await query.message.delete()
    movie = movies[(int(movie_))]
    movie = re.sub(r"[:\-()]", " ", movie)
    movie = re.sub(r"\s+", " ", movie).strip()
    await query.answer(script.TOP_ALRT_MSG)
    gl = await global_filters(bot, query.message, text=movie)
    if gl == False:
        k = await manual_filters(bot, query.message, text=movie)
        if k == False:
            # ⚠️ Must NOT reuse the original message's key here. auto_filter()
            # already cached an EMPTY result under "{chat_id}-{original_msg_id}"
            # for the misspelled query (that's WHY this suggestion screen showed
            # up at all) — reusing that same key just returns the stale empty
            # cache hit instead of actually searching for the corrected name,
            # so the button looked like it did nothing / said "not found" even
            # though the corrected name is right. A key unique to this specific
            # suggestion click guarantees a fresh search.
            spoll_key = f"{query.message.chat.id}-{query.message.reply_to_message.id}-spol-{movie_}"
            files, offset, total_results = await get_ranked_page(query.message.chat.id, spoll_key, movie, offset=0, max_results=8)
            # ✅ Close the "Did you mean" suggestion prompt before showing
            # the fresh search, instead of editing it in place.
            try:
                await query.message.delete()
            except Exception:
                pass
            if files:
                k = (spoll_key, movie, files, offset, total_results)
                ai_search = True
                reply_msg = await bot.send_message(query.message.chat.id, f"<b><i>Searching For {movie} 🔍</i></b>")
                await auto_filter(bot, movie, query, reply_msg, ai_search, k)
            else:
                reqstr1 = query.from_user.id if query.from_user else 0
                reqstr = await bot.get_users(reqstr1)
                if NO_RESULTS_MSG:
                    await bot.send_message(chat_id=LOG_CHANNEL, text=(script.NORSLTS.format(reqstr.id, reqstr.mention, movie)))
                k = await bot.send_message(query.message.chat.id, script.MVE_NT_FND)
                await asyncio.sleep(10)
                await k.delete()
                
#1234567
@Client.on_callback_query(filters.regex(r"^fy#"))
async def filter_yearss_cb_handler(client: Client, query: CallbackQuery):
    _, lang, key = query.data.split("#")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    search = FRESH.get(key)
    try:
        search = search.replace(' ', '_')
    except:
        pass
    baal = lang in search
    if baal:
        search = search.replace(lang, "")
    else:
        search = search
    req = query.from_user.id
    chat_id = query.message.chat.id
    message = query.message
    try:
        if int(req) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ{query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇQᴜᴇꜱᴛ,\nʀᴇQᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass
    if lang != "homepage":
        search = f"{search} {lang}" 
    BUTTONS[key] = search

    files, offset, total_results = await get_search_results(chat_id, search, offset=0, filter=True)
    if not files:
        await query.answer("🚫 𝗡𝗼 𝗙𝗶𝗹𝗲 𝗪𝗲𝗿𝗲 𝗙𝗼𝘂𝗻𝗱 🚫", show_alert=1)
        return
    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    pre = 'filep' if settings.get('file_secure', False) else 'file'
    if settings.get("button", False):
        btn = [
            [
                InlineKeyboardButton(
                    text=format_file_button_text(file),
                    callback_data=f'{pre}#{file["file_id"]}'
                )
            ]
            for file in files
        ]
        btn.insert(0, [InlineKeyboardButton("🍃 ꜱᴇʀɪᴇꜱ ᴄʟɪᴄᴋ 🍃", callback_data=f"seasons#{key}", style=enums.ButtonStyle.PRIMARY)])
    else:
        btn = [
            [InlineKeyboardButton("🍃 ꜱᴇʀɪᴇꜱ ᴄʟɪᴄᴋ 🍃", callback_data=f"seasons#{key}", style=enums.ButtonStyle.PRIMARY)]
        ]

    if offset != "":
        try:
            if settings['max_btn']:
                btn.append(
                    [InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="𝐍𝐄𝐗𝐓 ➪",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
                )
    
            else:
                btn.append(
                    [InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="𝐍𝐄𝐗𝐓 ➪",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
                )
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append(
                [InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="𝐍𝐄𝐗𝐓 ➪",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
            )
    else:
        btn.append(
            [InlineKeyboardButton(text="𝐍𝐎 𝐌𝐎𝐑𝐄 𝐏𝐀𝐆𝐄𝐒 𝐀𝐕𝐀𝐈𝐋𝐀𝐁𝐋𝐄",callback_data="pages")]
        )
    if lang != "homepage":
        req = query.from_user.id
        offset = 0
        btn.append([InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ↭", callback_data=f"fy#homepage#{key}")])
    
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, search)
        try:
            await query.message.edit_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), disable_web_page_preview=True)
        except MessageNotModified:
            pass
    else:
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(btn)
            )
        except MessageNotModified:
            pass
    await query.answer()  

@Client.on_callback_query(filters.regex(r"^fe#"))
async def filter_episodes_cb_handler(client: Client, query: CallbackQuery):
    _, lang, key = query.data.split("#")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    search = FRESH.get(key)
    try:
        search = search.replace(' ', '_')
    except:
        pass
    baal = lang in search
    if baal:
        search = search.replace(lang, "")
    else:
        search = search
    req = query.from_user.id
    chat_id = query.message.chat.id
    message = query.message
    try:
        if int(req) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ{query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇQᴜᴇꜱᴛ,\nʀᴇQᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass
    if lang != "homepage":
        search = f"{search} {lang}" 
    BUTTONS[key] = search

    files, offset, total_results = await get_search_results(chat_id, search, offset=0, filter=True)
    if not files:
        await query.answer("🚫 𝗡𝗼 𝗙𝗶𝗹𝗲 𝗪𝗲𝗿𝗲 𝗙𝗼𝘂𝗻𝗱 🚫", show_alert=1)
        return
    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    pre = 'filep' if settings.get('file_secure', False) else 'file'
    if settings["button"]:
        btn = [
            [
                InlineKeyboardButton(
                    text=format_file_button_text(file), callback_data=f'{pre}#{file["file_id"]}'
                ),
            ]
            for file in files
        ]
        btn.insert(0, [InlineKeyboardButton("🍃 ꜱᴇʀɪᴇꜱ ᴄʟɪᴄᴋ 🍃", callback_data=f"seasons#{key}", style=enums.ButtonStyle.PRIMARY)])
    else:
        btn = [
            [InlineKeyboardButton("🍃 ꜱᴇʀɪᴇꜱ ᴄʟɪᴄᴋ 🍃", callback_data=f"seasons#{key}", style=enums.ButtonStyle.PRIMARY)]
        ]

    if offset != "":
        try:
            if settings['max_btn']:
                btn.append(
                    [InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="𝐍𝐄𝐗𝐓 ➪",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
                )
    
            else:
                btn.append(
                    [InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="𝐍𝐄𝐗𝐓 ➪",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
                )
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append(
                [InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="𝐍𝐄𝐗𝐓 ➪",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
            )
    else:
        btn.append(
            [InlineKeyboardButton(text="𝐍𝐎 𝐌𝐎𝐑𝐄 𝐏𝐀𝐆𝐄𝐒 𝐀𝐕𝐀𝐈𝐋𝐀𝐁𝐋𝐄",callback_data="pages")]
        )
    if lang != "homepage":
        req = query.from_user.id
        offset = 0
        btn.append([InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ↭", callback_data=f"fe#homepage#{key}")])
    
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, search)
        try:
            await query.message.edit_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), disable_web_page_preview=True)
        except MessageNotModified:
            pass
    else:
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(btn)
            )
        except MessageNotModified:
            pass
    await query.answer()
    

@Client.on_callback_query(filters.regex(r"^fl#"))
async def filter_languages_cb_handler(client: Client, query: CallbackQuery):
    _, lang, key = query.data.split("#")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    search = FRESH.get(key)
    try:
        search = search.replace(' ', '_')
    except:
        pass
    baal = lang in search
    if baal:
        search = search.replace(lang, "")
    else:
        search = search
    req = query.from_user.id
    chat_id = query.message.chat.id
    message = query.message
    try:
        if int(req) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ{query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇQᴜᴇꜱᴛ,\nʀᴇQᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass
    if lang != "homepage":
        search = f"{search} {lang}" 
    BUTTONS[key] = search

    files, offset, total_results = await get_search_results(chat_id, search, offset=0, filter=True)
    if not files:
        await query.answer("🚫 𝗡𝗼 𝗙𝗶𝗹𝗲 𝗪𝗲𝗿𝗲 𝗙𝗼𝘂𝗻𝗱 🚫", show_alert=1)
        return
    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    pre = 'filep' if settings.get('file_secure', False) else 'file'
    if settings["button"]:
        btn = [
            [
                InlineKeyboardButton(
                    text=format_file_button_text(file), callback_data=f'{pre}#{file["file_id"]}'
                ),
            ]
            for file in files
        ]
        btn.insert(0, 
            [
                #InlineKeyboardButton(f'ǫᴜᴀʟɪᴛʏ', callback_data=f"qualities#{key}"),
                #InlineKeyboardButton("ᴇᴘɪsᴏᴅᴇs", callback_data=f"episodes#{key}"),
                InlineKeyboardButton("🍃 ꜱᴇʀɪᴇꜱ ᴄʟɪᴄᴋ 🍃", callback_data=f"seasons#{key}", style=enums.ButtonStyle.PRIMARY)
            ]
        )
    else:
        btn = []
        btn.insert(0, 
            [
                #InlineKeyboardButton(f'ǫᴜᴀʟɪᴛʏ', callback_data=f"qualities#{key}"),
                #InlineKeyboardButton("ᴇᴘɪsᴏᴅᴇs", callback_data=f"episodes#{key}"),
                InlineKeyboardButton("🍃 ꜱᴇʀɪᴇꜱ ᴄʟɪᴄᴋ 🍃", callback_data=f"seasons#{key}", style=enums.ButtonStyle.PRIMARY)
            ]
        )

    if offset != "":
        try:
            if settings['max_btn']:
                btn.append(
                    [InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="𝐍𝐄𝐗𝐓 ➪",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
                )
    
            else:
                btn.append(
                    [InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="𝐍𝐄𝐗𝐓 ➪",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
                )
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append(
                [InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="𝐍𝐄𝐗𝐓 ➪",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
            )
    else:
        btn.append(
            [InlineKeyboardButton(text="𝐍𝐎 𝐌𝐎𝐑𝐄 𝐏𝐀𝐆𝐄𝐒 𝐀𝐕𝐀𝐈𝐋𝐀𝐁𝐋𝐄",callback_data="pages")]
        )
    if lang != "homepage":
        req = query.from_user.id
        offset = 0
        btn.append([InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ↭", callback_data=f"fl#homepage#{key}")])
    
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, search)
        try:
            await query.message.edit_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), disable_web_page_preview=True)
        except MessageNotModified:
            pass
    else:
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(btn)
            )
        except MessageNotModified:
            pass
    await query.answer()
    
# SEASON START HERE

import re
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from pyrogram.errors import MessageNotModified

# ===============================
# REGEX HELPERS
# ===============================

SEASON_RE = re.compile(r"(?<![A-Za-z0-9])(?:season\s*|s)(\d{1,2})(?=e\d|$|\D)", re.IGNORECASE)
EPISODE_RE = re.compile(r"(?:s\d{1,2}[.\s_-]*)?(?:episode|ep|e)[.\s_-]*(\d{1,3})(?!\d)", re.IGNORECASE)
RANGE_EP_RE = re.compile(r"[ex]\d{1,3}\s*-\s*[ex]\d{1,3}", re.IGNORECASE)

# Rewrites any season+episode marker actually present in a filename to the
# canonical no-space "S03E04" form for on-screen display — so "S03EP04",
# "S03 EP 04", "S03.Episode.04" etc. all show up as "S03E04" wherever a
# filename is shown to the user, instead of leaving the original "EP04"
# spelling sitting there next to the already-normalized [S03E04] tag.
_EPTAG_DISPLAY_RE = re.compile(
    r"(?<![A-Za-z0-9])s(\d{1,2})[.\s_-]*(?:episode|ep|e)[.\s_-]*(\d{1,3})(?!\d)",
    re.IGNORECASE,
)


def normalize_episode_marker(name: str) -> str:
    return _EPTAG_DISPLAY_RE.sub(
        lambda m: f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}", name
    )

MAX_SEASON  = 30
MAX_EPISODE = 300

# Strip quality/language tags for clean name comparison
STRIP_RE = re.compile(
    r'[\[\(].*?[\]\)]'
    r'|\b(720p|1080p|480p|2160p|4k|hdrip|webrip|bluray|hdtv|mkv|mp4|avi'
    r'|hindi|tamil|english|telugu|malayalam|kannada'
    r'|chinese|mandarin|cantonese|japanese|gujarati|marathi|bengali|bangla|urdu|tulu'
    r'|dubbed|multi|esub'
    r'|x264|x265|hevc|avc|aac|dd5|dolby|atmos|hdr|sdr|web|dl|rip'
    r'|season|episode|complete|batch|pack|combined'
    r'|s\d{1,2}ep\d{1,3}|s\d{1,2}e\d{1,3}|ep\d{1,3}|s\d{1,2})\b',
    re.IGNORECASE
)


def extract_season(filename: str):
    m = SEASON_RE.search(filename)
    if not m:
        return None
    val = int(m.group(1))
    return val if val <= MAX_SEASON else None


def extract_episode(filename: str):
    m = EPISODE_RE.search(filename)
    if not m:
        return None
    val = int(m.group(1))
    return val if val <= MAX_EPISODE else None


def is_combined_file(name: str):
    name = name.lower()
    return any(k in name for k in ("combined", "complete", "batch", "pack")) or bool(RANGE_EP_RE.search(name))


def clean_name(filename: str) -> str:
    """Strip quality tags, season/episode codes, brackets for clean comparison"""
    name = STRIP_RE.sub(' ', filename)
    name = re.sub(r'\s+', ' ', name).strip().lower()
    return name


def format_file_button_text(file):
    """File list label — '{size} ▷ [S01E02] Clean Name' for series files,
    '{size} ▷ Clean Name' for movies (no season/episode = no tag).
    Uses the same extract_season/extract_episode detectors as the rest of
    the bot (season lists, episode grouping) so it also catches spaced
    formats like 'S08 E03', not just tight 'S08E03' — and always renders
    the tag normalized to the no-space 'S08E03' form either way."""
    size = get_size(file['file_size'])
    name = file['file_name']
    season  = extract_season(name)
    episode = extract_episode(name)
    tag = f"[S{season:02d}E{episode:02d}] " if season is not None and episode is not None else ""
    clean = re.sub(r'\[.*?\]', '', name).replace("WEBRip", "").strip()
    clean = normalize_episode_marker(clean)
    return f"{size} ▷ {tag}{clean}"


# ===============================
# LOCAL FUZZY TITLE SUGGESTIONS (typo fallback that doesn't need the
# external poster/TMDB API)
# ===============================
# get_poster() sometimes finds nothing for a typo (wrong API match, API
# down, obscure/new title not indexed there yet) — in that case we had
# NO suggestions at all, just a dead "Google" button. This fuzzy-matches
# the typo against titles that actually exist in OUR OWN file database,
# so "Durandhar" can still surface "Dhurandhar" if it's really in the
# library, without depending on any external service.
_KNOWN_TITLES_CACHE = {"titles": [], "ts": 0}
_KNOWN_TITLES_TTL   = 3600     # refresh once an hour
_KNOWN_TITLES_LIMIT = 20000    # sample size, not the whole DB — keeps this cheap


_YEAR_TOKEN_RE = re.compile(r'^(19|20)\d{2}$')


def _extract_title_stem(words):
    """Everything up to and including the release year IS the movie
    title; everything after it is almost always source/audio/encoder
    junk (AMZN, WEB-DL, H264, 6CH, DDP5.1, 60FPS, PAHE, YIFY...) that
    would otherwise make one movie look like five different suggestions.
    Falls back to a flat 6-word cap only if no year token is found."""
    for i, w in enumerate(words):
        if _YEAR_TOKEN_RE.match(w):
            return words[:i + 1]
    return words[:6]


def _series_title_from_raw(name: str):
    """For a series file, everything BEFORE the season/episode marker
    (S01E02, Season 3, ...) in the ORIGINAL filename IS the title —
    quality/audio/codec/release-group junk (10bit, DDP5.1, AMZN, H265,
    2CH, EON, or whatever a new group tags its files with) always comes
    AFTER that marker. Cutting there gives one clean, stable title no
    matter how the trailing junk varies from file to file, instead of
    relying on STRIP_RE ever covering every possible tag (it can't) or
    a flat word-count cap (which lets leftover junk words sneak into the
    stem when the real title is short, e.g. 'House Of The Dragon').
    Returns None if this doesn't look like a series file at all."""
    m = SEASON_RE.search(name)
    if not m:
        return None
    return name[:m.start()]


def _sync_fetch_known_titles(limit):
    """col/sec_col are plain synchronous PyMongo collections (same as
    col.delete_one() / col.count_documents() used elsewhere in this file
    — no await, no motor). Run with a normal blocking cursor, called via
    asyncio.to_thread so it doesn't stall the event loop.
    Only touches sec_col when MULTIPLE_DATABASE is on — same rule
    get_search_results() already follows — so this doesn't hang on a
    second DB that isn't actually configured for use."""
    titles = set()
    collections = (col, sec_col) if MULTIPLE_DATABASE else (col,)
    for collection in collections:
        try:
            # Newest-first: without a sort, Mongo returns natural/insertion
            # order, which on a library bigger than `limit` silently cuts
            # off recently-added titles (e.g. a brand-new release like
            # "Dhurandhar" never making it into the sample at all — no
            # amount of fuzzy-matching helps if the title isn't even in
            # the pool). Sorting by _id descending guarantees the newest
            # additions are always included, which is exactly what users
            # are most likely to be typo-searching for.
            cursor = collection.find({}, {"file_name": 1}).sort("_id", -1).limit(limit)
            for doc in cursor:
                name = doc.get("file_name")
                if not name:
                    continue

                series_raw = _series_title_from_raw(name)
                base = clean_name(series_raw if series_raw is not None else name)
                # Drop stray punctuation-only leftovers (e.g. a lone "."
                # or "-" where a stripped extension/tag used to be).
                words = [
                    w.strip(".-_") for w in base.split()
                    if any(c.isalnum() for c in w)
                ]
                if not words:
                    continue

                if series_raw is not None:
                    # Already cut at the season/episode marker — no
                    # trailing junk possible, so keep every word as-is
                    # (don't run the movie-only 6-word/year cap on it).
                    title = " ".join(words).strip()
                else:
                    title = " ".join(_extract_title_stem(words)).strip()

                if len(title) >= 2:
                    titles.add(title)
        except Exception as e:
            # print() as well as logging — ia_filterdb.py's own save_file()
            # uses print() for its status messages, so if Koyeb's log view
            # is tuned to stdout, logging.error() alone might not surface.
            print(f"[spell-fuzzy] _sync_fetch_known_titles ERROR on {collection}: {e}")
            logging.error(f"_sync_fetch_known_titles error: {e}")
    return titles


async def _get_known_titles(force_refresh=False):
    now = datetime.now().timestamp()
    if not force_refresh and _KNOWN_TITLES_CACHE["titles"] and (now - _KNOWN_TITLES_CACHE["ts"] < _KNOWN_TITLES_TTL):
        return _KNOWN_TITLES_CACHE["titles"]

    try:
        titles = await asyncio.to_thread(_sync_fetch_known_titles, _KNOWN_TITLES_LIMIT)
        print(f"[spell-fuzzy] loaded {len(titles)} known titles for fuzzy suggestions (force_refresh={force_refresh})")
    except Exception as e:
        print(f"[spell-fuzzy] _get_known_titles ERROR: {e}")
        logging.error(f"_get_known_titles error: {e}")
        titles = set()

    _KNOWN_TITLES_CACHE["titles"] = list(titles)
    _KNOWN_TITLES_CACHE["ts"] = now
    return _KNOWN_TITLES_CACHE["titles"]


def _title_similarity(query_clean: str, candidate_clean: str) -> float:
    """More precise than a raw whole-string difflib ratio:
    1. Requires the FIRST word to actually resemble the query's first
       word — real typos essentially never swap out the entire first
       word, so this alone kills lookalike-but-unrelated titles (e.g.
       'Cocktaile 2' matching 'Love Mocktail 2020' just because the
       middle letters happen to overlap).
    2. Trims the candidate down to roughly the query's own word count
       (+1 buffer) before scoring, so a short query isn't unfairly
       diluted by a long padded candidate title (year, "chapter 1", etc)
       — and a short unrelated candidate doesn't get an inflated ratio
       just because there's little left to disagree on.
    """
    qw, cw = query_clean.split(), candidate_clean.split()
    if not qw or not cw:
        return 0.0
    first_word_ratio = difflib.SequenceMatcher(None, qw[0], cw[0]).ratio()
    if first_word_ratio < 0.5:
        return 0.0
    trimmed = " ".join(cw[:len(qw) + 1])
    return difflib.SequenceMatcher(None, query_clean, trimmed).ratio()


async def get_similar_titles(query, limit=5):
    """Up to `limit` titles from our own DB that closely resemble `query`
    (typo-tolerant). Returns nicely title-cased strings, or [] if nothing
    is close enough to be a confident suggestion."""
    q = clean_name(query)
    if not q:
        return []

    def _score(titles):
        scored = [(_title_similarity(q, t), t) for t in titles]
        scored = [(s, t) for s, t in scored if s >= 0.6]
        scored.sort(key=lambda x: -x[0])
        return [t for _, t in scored[:limit]]

    titles = await _get_known_titles()
    if not titles:
        print("[spell-fuzzy] no known titles available — can't suggest anything")
        return []

    matches = _score(titles)
    if not matches:
        # Nothing in the (possibly stale, up to an hour old) cached pool —
        # before giving up, force one fresh reload straight from the DB.
        # This is what makes a movie uploaded 5 minutes ago still
        # suggestible immediately, instead of waiting for the hourly
        # cache refresh to happen to pick it up.
        fresh_titles = await _get_known_titles(force_refresh=True)
        if fresh_titles is not titles:
            matches = _score(fresh_titles)
            titles = fresh_titles

    print(f"[spell-fuzzy] query='{query}' cleaned='{q}' pool_size={len(titles)} matches={matches}")
    return [m.title() for m in matches]


def filter_and_rank(files: list, search: str) -> list:
    """
    ✅ Only keep files whose cleaned name STARTS WITH the search term.
    This prevents middle/end matches from polluting season & episode lists.
    """
    # Clean the search term too
    search_clean = STRIP_RE.sub(' ', search.lower().strip())
    search_clean = re.sub(r'\s+', ' ', search_clean).strip()
    search_lower = search.lower().strip()

    scored = []
    for f in files:
        name = f["file_name"]
        cleaned = clean_name(name)

        # ✅ PREFIX MATCH ONLY — must start with search name
        if not cleaned.startswith(search_clean):
            continue

        # Rank: exact prefix > contains
        name_lower = name.lower()
        if name_lower.startswith(search_lower):
            score = 0
        else:
            score = 1

        scored.append((score, f))

    scored.sort(key=lambda x: x[0])
    return [f for _, f in scored]


# ===============================
# SPEED FIX: CACHE FILTERED FILE LIST PER SEARCH KEY
# ===============================
# Previously every season/episode/combined button click re-ran a fresh
# DB query for up to 50,000 files PLUS the regex-based filter_and_rank()
# pass on EVERY click. That's why series buttons felt slow. Now we fetch
# + filter ONCE per search key and reuse the cached, already-filtered
# list for all subsequent season/episode/combined clicks.
SEASON_CACHE = {}          # key -> {"files": [...], "ts": epoch_seconds}
SEASON_CACHE_TTL = 900     # 15 minutes


async def get_cached_season_files(chat_id, key, search):
    entry = SEASON_CACHE.get(key)
    if entry and (datetime.now().timestamp() - entry["ts"] < SEASON_CACHE_TTL):
        return entry["files"]
    files, _, _ = await get_search_results(chat_id, search, max_results=50000)
    files = filter_and_rank(files, search)
    SEASON_CACHE[key] = {"files": files, "ts": datetime.now().timestamp()}
    return files


# ===============================
# SPEED FIX #2: SMALLER RANKED POOL FOR THE MAIN RESULTS LIST
# ===============================
# get_cached_season_files() fetches + ranks up to 50,000 files, which is
# correct for the season/episode/quality button pool (it needs full
# coverage so the ❌-missing-episode marks are accurate) but is overkill
# — and slow (~1-2s) — for just showing page 1 of the main results.
# This is a separate, much smaller pool (a few hundred files is plenty
# to find the true prefix match among), with its own cache, so a fresh
# search stays fast while the full 50k pool still warms in the background
# for whenever the user taps into series/season/quality buttons.
DISPLAY_CACHE = {}          # key -> {"files": [...], "ts": epoch_seconds}
DISPLAY_CACHE_TTL = 900     # 15 minutes
DISPLAY_POOL_SIZE = 500     # fetch/rank cap for the main results list


async def get_display_ranked_files(chat_id, key, search, pool_size=DISPLAY_POOL_SIZE):
    entry = DISPLAY_CACHE.get(key)
    if entry and (datetime.now().timestamp() - entry["ts"] < DISPLAY_CACHE_TTL):
        return entry["files"]
    files, _, _ = await get_search_results(chat_id, search, max_results=pool_size)
    files = filter_and_rank(files, search)
    DISPLAY_CACHE[key] = {"files": files, "ts": datetime.now().timestamp()}
    return files


async def get_ranked_page(chat_id, key, search, offset=0, max_results=8):
    """Prefix-ranked, paginated search results for the main results list
    (page 1 in auto_filter, page N+ in next_page).

    get_search_results() alone sorts by Mongo's '$natural' order (insertion
    order), not relevance, so a true prefix match like "Master 2021" could
    land on page 4+ just because some unrelated "... Master 2021 ..." file
    was inserted into the DB more recently. This pulls from the smaller,
    fast DISPLAY_CACHE pool (ranked via filter_and_rank) instead of the
    full 50k season/quality pool, so prefix matches sort to the front
    without paying the full pool's fetch cost.
    """
    all_files = await get_display_ranked_files(chat_id, key, search)
    total = len(all_files)
    files = all_files[offset:offset + max_results]
    next_offset = offset + max_results if (offset + max_results) < total else ""
    return files, next_offset, total


# ===============================
# QUALITY (BY FILE SIZE) BUTTONS
# ===============================
# 4K    : 3000 MB - 40000 MB
# 2K    : 2000 MB - 3000 MB
# 1080p : 1300 MB - 2000 MB
# 720p  : 500 MB  - 1300 MB
# 480p  : 0 MB    - 500 MB
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


def get_quality_label(file_size):
    """Classify a file by size (bytes) into a quality bucket, or None."""
    try:
        size = int(file_size)
    except (TypeError, ValueError):
        return None
    for qkey in QUALITY_ORDER:
        lo, hi = QUALITY_RANGES[qkey]
        if lo <= size < hi:
            return QUALITY_LABELS[qkey]
    return None


def get_available_qualities(pool):
    """Which of the 5 quality buckets actually have at least one file in
    this pool. Used to mark the missing ones with ❌ instead of pretending
    every quality is always available."""
    avail = set()
    for f in pool:
        try:
            size = int(f.get("file_size", 0))
        except (TypeError, ValueError):
            continue
        for qkey in QUALITY_ORDER:
            lo, hi = QUALITY_RANGES[qkey]
            if lo <= size < hi:
                avail.add(qkey)
                break
    return avail


def build_quality_row(scope, key, uid, selected=None, available=None):
    """Row of 4K / 2K / 1080p / 720p / 480p buttons, scoped to a specific
    context so the filter only searches within that context's files:
      scope = "s{season}e{episode}"  -> that single episode's files
      scope = "c{season}"            -> that season's combined/batch files
    If `selected` is set (e.g. "2k"), that button gets a ✅.
    If `available` is given (a set of quality keys), any quality NOT in
    it gets a ❌ instead of the plain label, since there's nothing to
    show for it here."""
    buttons = []
    for q in QUALITY_ORDER:
        if available is not None and q not in available:
            label = f"❌ {QUALITY_LABELS[q]}"
        elif q == selected:
            label = f"✅ {QUALITY_LABELS[q]}"
        else:
            label = QUALITY_LABELS[q]
        buttons.append(InlineKeyboardButton(
            label, callback_data=f"qual#{q}#{scope}#{key}#0#{uid}", style=enums.ButtonStyle.SUCCESS
        ))
    return buttons


# ===============================
# LANGUAGE (BY FILENAME TAG) BUTTON
# ===============================
# Works the same way as the QUALITY system above, except instead of
# classifying by file size it detects language tags inside the file name
# (kan/kannada, eng/english, mal/malayalam, tel/telugu, tam/tamil,
# hin/hindi, multi audio, dual audio).
#
# UI difference from Quality: quality always shows all 5 buttons up
# front. Language instead shows ONE "🌐 Language" button — tapping it
# opens a submenu built on the fly from only the languages that are
# actually present in that scope's files, so users never see a dead
# button for a language that isn't available.
LANGUAGE_DEFS = [
    ("hin",    "Hindi",       {"hindi", "hin"}),
    ("eng",    "English",     {"english", "eng"}),
    ("tam",    "Tamil",       {"tamil", "tam"}),
    ("tel",    "Telugu",      {"telugu", "tel"}),
    ("kan",    "Kannada",     {"kannada", "kan"}),
    ("mal",    "Malayalam",   {"malayalam", "mal"}),
    ("chi",    "Chinese",     {"chinese", "chi", "mandarin", "cantonese"}),
    ("jap",    "Japanese",    {"japanese", "jap", "jpn"}),
    ("guj",    "Gujarati",    {"gujarati", "guj"}),
    ("mar",    "Marathi",     {"marathi", "mar"}),
    ("ben",    "Bengali",     {"bengali", "ben", "bangla"}),
    ("urd",    "Urdu",        {"urdu", "urd"}),
    ("tul",    "Tulu",        {"tulu", "tul"}),
    ("multi",  "Multi Audio", {"multi", "dual"}),
    # ✅ Not a real filename tag — matched specially (see file_matches_lang)
    # for files where get_file_languages() finds nothing at all, so those
    # files still show up under a "No Lang Tag" button instead of being
    # invisible in the language menu.
    ("nolang", "No Lang Tag", set()),
]
LANGUAGE_LABELS = {k: v for k, v, _ in LANGUAGE_DEFS}
LANGUAGE_TOKENS = {k: t for k, _, t in LANGUAGE_DEFS}
LANGUAGE_ORDER  = [k for k, _, _ in LANGUAGE_DEFS]

_LANG_TOKEN_SPLIT_RE = re.compile(r'[^A-Za-z0-9]+')


@functools.lru_cache(maxsize=8192)
def get_file_languages(filename: str) -> tuple:
    """Detect which language tags exist in a filename, e.g.
    'Movie.2024.Tam.Tel.Kan.Mal.Eng.WEBRip.mkv' -> ('tam','tel','kan','mal','eng').
    Matches whole tokens only (split on any non-alphanumeric char) so it
    won't false-positive on tags buried inside other words. 'dual' tags
    are folded into the single 'Multi Audio' bucket."""
    tokens = set(_LANG_TOKEN_SPLIT_RE.split(filename.lower()))
    if not tokens:
        return ()
    found = tuple(
        key for key in LANGUAGE_ORDER
        if tokens & LANGUAGE_TOKENS[key]
    )
    return found


def file_matches_lang(lkey: str, filename: str) -> bool:
    """Like `lkey in get_file_languages(filename)`, except lkey == "nolang"
    matches files where NO language tag was detected at all."""
    if lkey == "nolang":
        return not get_file_languages(filename)
    return lkey in get_file_languages(filename)


def build_language_button(scope, key, uid):
    """Single 🌐 Language entry button — opens the dynamic submenu."""
    return [InlineKeyboardButton("🌐 Language", callback_data=f"langmenu#{scope}#{key}#{uid}", style=enums.ButtonStyle.PRIMARY)]


def build_language_row(lang_keys, scope, key, uid, selected=None):
    """Row(s) of language buttons for the languages actually detected in
    this scope. If `selected` is set (e.g. 'tam'), that button gets a ✅."""
    return [
        InlineKeyboardButton(
            f"✅ {LANGUAGE_LABELS[lk]}" if lk == selected else LANGUAGE_LABELS[lk],
            callback_data=f"lang#{lk}#{scope}#{key}#0#{uid}", style=enums.ButtonStyle.PRIMARY
        )
        for lk in lang_keys
    ]


def _language_menu_rows(available, scope, key, uid, selected=None):
    """Chunk detected languages 2-per-row."""
    rows = []
    for i in range(0, len(available), 2):
        rows.append(build_language_row(available[i:i + 2], scope, key, uid, selected=selected))
    return rows


def build_language_quality_row(lkey, scope, key, uid, selected=None, available=None):
    """Quality row shown AFTER a language is picked, so the user can
    refine within that language without losing the language filter.
    Routes through the combined lq# handler, not the plain qual# one.
    `available` (a set of quality keys) marks the missing ones with ❌."""
    buttons = []
    for q in QUALITY_ORDER:
        if available is not None and q not in available:
            label = f"❌ {QUALITY_LABELS[q]}"
        elif q == selected:
            label = f"✅ {QUALITY_LABELS[q]}"
        else:
            label = QUALITY_LABELS[q]
        buttons.append(InlineKeyboardButton(
            label, callback_data=f"lq#{lkey}#{q}#{scope}#{key}#0#{uid}", style=enums.ButtonStyle.SUCCESS
        ))
    return buttons


def resolve_scope_pool(scope, key, uid, all_files):
    """Shared by quality & language filters: narrows the full cached file
    list down to whatever context (all / a season's combined files / one
    episode's files) a filter button was opened from, and returns the
    matching 'Back' destination for that context."""
    if scope == "all":
        return all_files, f"next_{uid}_{key}_0"
    if scope.startswith("c"):
        season_no = int(scope[1:])
        pool = [
            f for f in all_files
            if extract_season(f["file_name"]) == season_no
            and is_combined_file(f["file_name"])
        ]
        return pool, f"combined#s{season_no}#{key}#0#{uid}"
    if scope.startswith("s") and "e" in scope:
        season_no  = int(scope[1:scope.index("e")])
        episode_no = int(scope[scope.index("e") + 1:])
        pool = [
            f for f in all_files
            if extract_season(f["file_name"]) == season_no
            and extract_episode(f["file_name"]) == episode_no
            and not is_combined_file(f["file_name"])
        ]
        return pool, f"fs#s{season_no}e{episode_no}#{key}#0#{uid}"
    return all_files, f"seasons#{key}"


# ===============================
# SEASON LIST
# ===============================
@Client.on_callback_query(filters.regex(r"^seasons#"))
async def seasons_cb_handler(client, query: CallbackQuery):
    try:
        _, key = query.data.split("#")
        search  = FRESH.get(key)
        chat_id = query.message.chat.id
        uid     = query.from_user.id

        # 🔒 Only the person who searched can open the seasons list
        owner = SEASON_OWNER.get(key)
        if owner and uid != owner:
            return await query.answer(
                "⚠️ This is not your search. Please search your own.",
                show_alert=True
            )

        if not search:
            return await query.answer("⚠️ Session expired. Please search again.", show_alert=True)

        # ✅ Fetch + filter ONCE, then reuse cached list on later clicks (fast)
        files = await get_cached_season_files(chat_id, key, search)

        if not files:
            return await query.answer("🚫 No matching files found.", show_alert=True)

        # Collect unique seasons
        season_set = set()
        for f in files:
            s = extract_season(f["file_name"])
            if s:
                season_set.add(s)

        if not season_set:
            return await query.answer("🚫 No seasons found in filenames.", show_alert=True)

        seasons = sorted(season_set)

        btn = [[InlineKeyboardButton("📺 SELECT SEASON", callback_data="ident", style=enums.ButtonStyle.PRIMARY)]]

        # 2 season buttons per row
        for i in range(0, len(seasons), 2):
            row = [
                InlineKeyboardButton(
                    f"🎬 Season {seasons[i]:02d}",
                    callback_data=f"eps#s{seasons[i]}#{key}#{uid}"
                )
            ]
            if i + 1 < len(seasons):
                row.append(
                    InlineKeyboardButton(
                        f"🎬 Season {seasons[i+1]:02d}",
                        callback_data=f"eps#s{seasons[i+1]}#{key}#{uid}"
                    )
                )
            btn.append(row)

        btn.append([
            InlineKeyboardButton("🏠 Back to Home", callback_data=f"next_{uid}_{key}_0", style=enums.ButtonStyle.DANGER)
        ])

        try:
            await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass

        # ✅ Answer LAST — keeps the button's loading spinner active
        # for the whole time the seasons are being processed.
        await query.answer()

    except Exception as e:
        await query.answer(f"❌ Error: {e}", show_alert=True)


# ===============================
# EPISODE LIST
# ===============================
@Client.on_callback_query(filters.regex(r"^eps#"))
async def episode_selector(client, query: CallbackQuery):
    try:
        _, season_tag, key, user = query.data.split("#")

        if int(user) != query.from_user.id:
            return await query.answer(
                "⚠️ This is not your search. Please search your own.",
                show_alert=True
            )

        season_no = int(season_tag[1:])
        search    = FRESH.get(key)
        chat_id   = query.message.chat.id
        uid       = query.from_user.id

        if not search:
            return await query.answer("⚠️ Session expired. Please search again.", show_alert=True)

        # ✅ Cached — no repeat DB fetch / regex filter on every click
        files = await get_cached_season_files(chat_id, key, search)

        episode_set    = set()
        combined_exist = False

        for f in files:
            name = f["file_name"]
            if extract_season(name) == season_no:
                # ✅ Combined files skip episode detection
                if is_combined_file(name):
                    combined_exist = True
                    continue  # ← skip, don't add to episodes
                ep = extract_episode(name)
                if ep:
                    episode_set.add(ep)

        episodes = sorted(episode_set)

        btn = [
            [InlineKeyboardButton(f"📺 {search} — Season {season_no:02d}", callback_data="ident")]
        ]

        if combined_exist:
            btn.append([
                InlineKeyboardButton(
                    "📦 COMBAINED FILE ",
                    callback_data=f"combined#s{season_no}#{key}#0#{uid}", style=enums.ButtonStyle.SUCCESS
                )
            ])

        if not episodes:
            btn.append([InlineKeyboardButton("🚫 No episodes found", callback_data="ident")])
        else:
            # 3 episode buttons per row
            for i in range(0, len(episodes), 3):
                row = [
                    InlineKeyboardButton(
                        f"EP {str(ep).zfill(2)}",
                        callback_data=f"fs#s{season_no}e{ep}#{key}#0#{uid}"
                    )
                    for ep in episodes[i:i+3]
                ]
                btn.append(row)

        btn.append([
            InlineKeyboardButton("↩️ Back to Seasons", callback_data=f"seasons#{key}")
        ])

        try:
            await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass

        # ✅ Answer LAST — keeps the button's loading spinner active
        # for the whole time the episodes are being processed.
        await query.answer()

    except Exception as e:
        await query.answer(f"❌ Error: {e}", show_alert=True)


# ===============================
# FILE LIST FOR SPECIFIC EPISODE
# ===============================
@Client.on_callback_query(filters.regex(r"^fs#"))
async def filter_files(client, query: CallbackQuery):
    try:
        _, tag, key, page, user = query.data.split("#")

        if int(user) != query.from_user.id:
            return await query.answer(
                "⚠️ This is not your search. Please search your own.",
                show_alert=True
            )

        season_no  = int(tag.split("e")[0][1:])
        episode_no = int(tag.split("e")[1])
        page       = int(page)
        search     = FRESH.get(key)
        chat_id    = query.message.chat.id
        uid        = query.from_user.id

        if not search:
            return await query.answer("⚠️ Session expired. Please search again.", show_alert=True)

        # ✅ Cached — no repeat DB fetch / regex filter on every click
        files = await get_cached_season_files(chat_id, key, search)

        # Filter exact season + episode
        filtered = [
            f for f in files
            if extract_season(f["file_name"]) == season_no
            and extract_episode(f["file_name"]) == episode_no
            and not is_combined_file(f["file_name"])  # ✅ exclude combined files
        ]
        if not filtered:
            return await query.answer("🚫 No files found for this episode.", show_alert=True)

        FILES_PER_PAGE = 8
        total_pages    = max(1, (len(filtered) - 1) // FILES_PER_PAGE + 1)
        start          = page * FILES_PER_PAGE
        end            = start + FILES_PER_PAGE

        btn = [
            [
                InlineKeyboardButton(
                    f"📁 S{season_no:02d}E{episode_no:02d} — {len(filtered)} file(s)",
                    callback_data="ident", style=enums.ButtonStyle.PRIMARY
                ),
                build_language_button(f"s{season_no}e{episode_no}", key, uid)[0]
            ]
        ]

        # ✅ Quality buttons at the TOP, scoped to just this episode's files
        btn.append(build_quality_row(
            f"s{season_no}e{episode_no}", key, uid,
            available=get_available_qualities(filtered)
        ))

        for f in filtered[start:end]:
            btn.append([InlineKeyboardButton(
                format_file_button_text(f),
                callback_data=f"file#{f['file_id']}"
            )])

        # Pagination nav
        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(
                    "⬅️ Prev",
                    callback_data=f"fs#s{season_no}e{episode_no}#{key}#{page-1}#{uid}"
                ))
            nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="ident", style=enums.ButtonStyle.PRIMARY))
            if page + 1 < total_pages:
                nav.append(InlineKeyboardButton(
                    "Next ➡️",
                    callback_data=f"fs#s{season_no}e{episode_no}#{key}#{page+1}#{uid}"
                ))
            btn.append(nav)

        btn.append([
            InlineKeyboardButton("↩️ Episodes", callback_data=f"eps#s{season_no}#{key}#{uid}", style=enums.ButtonStyle.DANGER),
            InlineKeyboardButton("🏠 Home",     callback_data=f"next_{uid}_{key}_0", style=enums.ButtonStyle.DANGER)
        ])

        try:
            await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass

        # ✅ Answer LAST — keeps the button's loading spinner active
        # for the whole time the file list is being processed.
        await query.answer()

    except Exception as e:
        await query.answer(f"❌ Error: {e}", show_alert=True)


# ===============================
# COMBINED / BATCH FILES
# ===============================
@Client.on_callback_query(filters.regex(r"^combined#"))
async def combined_files(client, query: CallbackQuery):
    try:
        _, season_tag, key, page, user = query.data.split("#")

        if int(user) != query.from_user.id:
            return await query.answer(
                "⚠️ This is not your search. Please search your own.",
                show_alert=True
            )

        season_no = int(season_tag[1:])
        page      = int(page)
        search    = FRESH.get(key)
        chat_id   = query.message.chat.id
        uid       = query.from_user.id

        if not search:
            return await query.answer("⚠️ Session expired. Please search again.", show_alert=True)

        # ✅ Cached — no repeat DB fetch / regex filter on every click
        files = await get_cached_season_files(chat_id, key, search)

        combined = [
            f for f in files
            if extract_season(f["file_name"]) == season_no
            and is_combined_file(f["file_name"])
        ]

        if not combined:
            return await query.answer("🚫 No combined/batch files found.", show_alert=True)

        FILES_PER_PAGE = 8
        total_pages    = max(1, (len(combined) - 1) // FILES_PER_PAGE + 1)
        start          = page * FILES_PER_PAGE
        end            = start + FILES_PER_PAGE

        btn = [
            [
                InlineKeyboardButton(
                    f"📦 Season {season_no:02d} — Batch/Complete ({len(combined)} files)",
                    callback_data="ident"
                ),
                build_language_button(f"c{season_no}", key, uid)[0]
            ]
        ]

        # ✅ Quality buttons at the TOP, scoped to just this season's combined files
        btn.append(build_quality_row(
            f"c{season_no}", key, uid,
            available=get_available_qualities(combined)
        ))

        for f in combined[start:end]:
            btn.append([InlineKeyboardButton(
                format_file_button_text(f),
                callback_data=f"file#{f['file_id']}"
            )])

        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(
                    "⬅️ Prev",
                    callback_data=f"combined#s{season_no}#{key}#{page-1}#{uid}"
                ))
            nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="ident", style=enums.ButtonStyle.PRIMARY))
            if page + 1 < total_pages:
                nav.append(InlineKeyboardButton(
                    "Next ➡️",
                    callback_data=f"combined#s{season_no}#{key}#{page+1}#{uid}"
                ))
            btn.append(nav)

        btn.append([
            InlineKeyboardButton("↩️ Episodes", callback_data=f"eps#s{season_no}#{key}#{uid}", style=enums.ButtonStyle.DANGER),
            InlineKeyboardButton("🏠 Home",     callback_data=f"next_{uid}_{key}_0", style=enums.ButtonStyle.DANGER)
        ])

        try:
            await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass

        # ✅ Answer LAST — keeps the button's loading spinner active
        # for the whole time the combined files are being processed.
        await query.answer()

    except Exception as e:
        await query.answer(f"❌ Error: {e}", show_alert=True)


# ===============================
# QUALITY FILTER (4K / 2K / 1080p / 720p / 480p) — by file size
# ===============================
@Client.on_callback_query(filters.regex(r"^qual#"))
async def quality_filter_cb_handler(client, query: CallbackQuery):
    try:
        _, qkey, scope, key, page, uid = query.data.split("#")
        page = int(page)

        if int(uid) != query.from_user.id:
            return await query.answer(
                "⚠️ This is not your search. Please search your own.",
                show_alert=True
            )

        search  = FRESH.get(key)
        chat_id = query.message.chat.id

        if not search:
            return await query.answer("⚠️ Session expired. Please search again.", show_alert=True)

        lo, hi = QUALITY_RANGES.get(qkey, (0, float("inf")))
        label  = QUALITY_LABELS.get(qkey, qkey)

        all_files = await get_cached_season_files(chat_id, key, search)

        # ✅ Scope down to just the episode / combined-batch context this
        # quality row was opened from, not the whole show's file list.
        # scope == "all" means it was opened from the main results screen,
        # so it searches across everything and Back returns to that
        # same starting search screen.
        if scope == "all":
            pool = all_files
            back_cb = f"next_{uid}_{key}_0"
        elif scope.startswith("c"):
            season_no = int(scope[1:])
            pool = [
                f for f in all_files
                if extract_season(f["file_name"]) == season_no
                and is_combined_file(f["file_name"])
            ]
            back_cb = f"combined#s{season_no}#{key}#0#{uid}"
        elif scope.startswith("s") and "e" in scope:
            season_no  = int(scope[1:scope.index("e")])
            episode_no = int(scope[scope.index("e")+1:])
            pool = [
                f for f in all_files
                if extract_season(f["file_name"]) == season_no
                and extract_episode(f["file_name"]) == episode_no
                and not is_combined_file(f["file_name"])
            ]
            back_cb = f"fs#s{season_no}e{episode_no}#{key}#0#{uid}"
        else:
            pool = all_files
            back_cb = f"seasons#{key}"

        matched = [f for f in pool if lo <= f.get("file_size", 0) < hi]

        if not matched:
            return await query.answer(f"🚫 No {label} files found here.", show_alert=True)

        settings = await get_settings(chat_id)
        pre = 'filep' if settings.get('file_secure', False) else 'file'

        FILES_PER_PAGE = 8  # same page size as the episode file list, for consistency
        total_pages    = max(1, (len(matched) - 1) // FILES_PER_PAGE + 1)
        page            = max(0, min(page, total_pages - 1))
        start           = page * FILES_PER_PAGE
        end             = start + FILES_PER_PAGE

        # ✅ Quality row stays visible (with a ✅ on the active one) so the
        # user can switch quality directly without hitting Back first.
        btn = [build_quality_row(scope, key, uid, selected=qkey, available=get_available_qualities(pool))]
        btn.append([InlineKeyboardButton(
            f"🎚 {label} — {len(matched)} file(s)", callback_data="ident"
        )])

        for f in matched[start:end]:
            btn.append([InlineKeyboardButton(
                format_file_button_text(f),
                callback_data=f'{pre}#{f["file_id"]}'
            )])

        # Pagination nav — identical pattern to the episode file list
        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(
                    "⬅️ Prev", callback_data=f"qual#{qkey}#{scope}#{key}#{page-1}#{uid}"
                ))
            nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="ident", style=enums.ButtonStyle.PRIMARY))
            if page + 1 < total_pages:
                nav.append(InlineKeyboardButton(
                    "Next ➡️", callback_data=f"qual#{qkey}#{scope}#{key}#{page+1}#{uid}"
                ))
            btn.append(nav)

        # ✅ Back returns to the exact plain (unfiltered) screen this was
        # opened from — the episode's file list or the season's combined
        # list — not the season list.
        btn.append([
            InlineKeyboardButton("↩️ Back", callback_data=back_cb)
        ])

        try:
            await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass

        await query.answer()

    except Exception as e:
        await query.answer(f"❌ Error: {e}", show_alert=True)


# ===============================
# LANGUAGE MENU — shows only the languages actually present in scope
# ===============================
@Client.on_callback_query(filters.regex(r"^langmenu#"))
async def language_menu_cb_handler(client, query: CallbackQuery):
    try:
        _, scope, key, uid = query.data.split("#")

        if int(uid) != query.from_user.id:
            return await query.answer(
                "⚠️ This is not your search. Please search your own.",
                show_alert=True
            )

        search  = FRESH.get(key)
        chat_id = query.message.chat.id

        if not search:
            return await query.answer("⚠️ Session expired. Please search again.", show_alert=True)

        all_files = await get_cached_season_files(chat_id, key, search)
        pool, back_cb = resolve_scope_pool(scope, key, uid, all_files)

        if not pool:
            return await query.answer("🚫 No files found here.", show_alert=True)

        # ✅ Only show languages that actually exist among these files
        seen = set()
        has_untagged = False
        for f in pool:
            langs = get_file_languages(f["file_name"])
            if langs:
                seen.update(langs)
            else:
                has_untagged = True

        if not seen and not has_untagged:
            return await query.answer("🚫 No files found here.", show_alert=True)

        available = [k for k in LANGUAGE_ORDER if k in seen]
        if has_untagged:
            available.append("nolang")

        btn = _language_menu_rows(available, scope, key, uid)
        btn.append([InlineKeyboardButton("↩️ Back", callback_data=back_cb)])

        try:
            await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass

        await query.answer()

    except Exception as e:
        await query.answer(f"❌ Error: {e}", show_alert=True)


# ===============================
# LANGUAGE FILTER — files matching a chosen detected language tag
# ===============================
@Client.on_callback_query(filters.regex(r"^lang#"))
async def language_filter_cb_handler(client, query: CallbackQuery):
    try:
        _, lkey, scope, key, page, uid = query.data.split("#")
        page = int(page)

        if int(uid) != query.from_user.id:
            return await query.answer(
                "⚠️ This is not your search. Please search your own.",
                show_alert=True
            )

        search  = FRESH.get(key)
        chat_id = query.message.chat.id

        if not search:
            return await query.answer("⚠️ Session expired. Please search again.", show_alert=True)

        label = LANGUAGE_LABELS.get(lkey, lkey)

        all_files = await get_cached_season_files(chat_id, key, search)
        pool, back_cb = resolve_scope_pool(scope, key, uid, all_files)

        matched = [f for f in pool if file_matches_lang(lkey, f["file_name"])]

        if not matched:
            return await query.answer(f"🚫 No {label} files found here.", show_alert=True)

        settings = await get_settings(chat_id)
        pre = 'filep' if settings.get('file_secure', False) else 'file'

        FILES_PER_PAGE = 8
        total_pages    = max(1, (len(matched) - 1) // FILES_PER_PAGE + 1)
        page            = max(0, min(page, total_pages - 1))
        start           = page * FILES_PER_PAGE
        end             = start + FILES_PER_PAGE

        # ✅ Top of the results now shows a Quality row (to refine within
        # this language, routed through lq# so the language filter isn't
        # lost) plus a single compact "Change Language" button — instead
        # of repeating the full language menu here.
        btn = [build_language_quality_row(lkey, scope, key, uid, available=get_available_qualities(matched))]
        btn.append([InlineKeyboardButton(
            "🔁 Change Language", callback_data=f"langmenu#{scope}#{key}#{uid}", style=enums.ButtonStyle.SUCCESS
        )])
        btn.append([InlineKeyboardButton(
            f"🌐 {label} — {len(matched)} file(s)", callback_data="ident"
        )])

        for f in matched[start:end]:
            btn.append([InlineKeyboardButton(
                format_file_button_text(f),
                callback_data=f'{pre}#{f["file_id"]}'
            )])

        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(
                    "⬅️ Prev", callback_data=f"lang#{lkey}#{scope}#{key}#{page-1}#{uid}"
                ))
            nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="ident", style=enums.ButtonStyle.PRIMARY))
            if page + 1 < total_pages:
                nav.append(InlineKeyboardButton(
                    "Next ➡️", callback_data=f"lang#{lkey}#{scope}#{key}#{page+1}#{uid}"
                ))
            btn.append(nav)

        # ✅ Back returns to the exact plain screen this was opened from
        btn.append([
            InlineKeyboardButton("↩️ Back", callback_data=back_cb)
        ])

        try:
            await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass

        await query.answer()

    except Exception as e:
        await query.answer(f"❌ Error: {e}", show_alert=True)


# ===============================
# LANGUAGE + QUALITY COMBINED FILTER
# ===============================
# Reached by tapping a quality button on top of an already-language-
# filtered screen. Keeps BOTH filters active at once instead of one
# overwriting the other.
@Client.on_callback_query(filters.regex(r"^lq#"))
async def language_quality_filter_cb_handler(client, query: CallbackQuery):
    try:
        _, lkey, qkey, scope, key, page, uid = query.data.split("#")
        page = int(page)

        if int(uid) != query.from_user.id:
            return await query.answer(
                "⚠️ This is not your search. Please search your own.",
                show_alert=True
            )

        search  = FRESH.get(key)
        chat_id = query.message.chat.id

        if not search:
            return await query.answer("⚠️ Session expired. Please search again.", show_alert=True)

        lang_label = LANGUAGE_LABELS.get(lkey, lkey)
        q_label    = QUALITY_LABELS.get(qkey, qkey)
        lo, hi     = QUALITY_RANGES.get(qkey, (0, float("inf")))

        all_files = await get_cached_season_files(chat_id, key, search)
        pool, _   = resolve_scope_pool(scope, key, uid, all_files)

        matched = [
            f for f in pool
            if file_matches_lang(lkey, f["file_name"])
            and lo <= f.get("file_size", 0) < hi
        ]

        if not matched:
            return await query.answer(f"🚫 No {q_label} {lang_label} files found here.", show_alert=True)

        settings = await get_settings(chat_id)
        pre = 'filep' if settings.get('file_secure', False) else 'file'

        FILES_PER_PAGE = 8
        total_pages    = max(1, (len(matched) - 1) // FILES_PER_PAGE + 1)
        page            = max(0, min(page, total_pages - 1))
        start           = page * FILES_PER_PAGE
        end             = start + FILES_PER_PAGE

        btn = [build_language_quality_row(lkey, scope, key, uid, selected=qkey)]
        btn.append([InlineKeyboardButton(
            "🔁 Change Language", callback_data=f"langmenu#{scope}#{key}#{uid}", style=enums.ButtonStyle.SUCCESS
        )])
        btn.append([InlineKeyboardButton(
            f"🎚 {q_label} · 🌐 {lang_label} — {len(matched)} file(s)", callback_data="ident"
        )])

        for f in matched[start:end]:
            btn.append([InlineKeyboardButton(
                format_file_button_text(f),
                callback_data=f'{pre}#{f["file_id"]}'
            )])

        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(
                    "⬅️ Prev", callback_data=f"lq#{lkey}#{qkey}#{scope}#{key}#{page-1}#{uid}"
                ))
            nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="ident", style=enums.ButtonStyle.PRIMARY))
            if page + 1 < total_pages:
                nav.append(InlineKeyboardButton(
                    "Next ➡️", callback_data=f"lq#{lkey}#{qkey}#{scope}#{key}#{page+1}#{uid}"
                ))
            btn.append(nav)

        # ✅ Back returns to the language-only filtered list (drops just
        # the quality refinement, keeps the language filter)
        btn.append([
            InlineKeyboardButton("↩️ Back", callback_data=f"lang#{lkey}#{scope}#{key}#0#{uid}")
        ])

        try:
            await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass

        await query.answer()

    except Exception as e:
        await query.answer(f"❌ Error: {e}", show_alert=True)


# SESSON End Here ##############

# Fix for advantage_spell_chok in pm_filter.py
async def advantage_spell_chok(client, name, msg, reply_msg, ai_search):
    try:
        # Your existing code up to the edit_text call
        # ...
        # Before editing, check if message exists
        try:
            await reply_msg.edit_text(
                text=script.I_CUDNT.format(name),
                reply_markup=InlineKeyboardMarkup(button)
            )
        except MessageIdInvalid:
            logging.error(f"Message ID invalid for reply_msg: {reply_msg.id}")
            await msg.reply("⚠️ The original message was deleted. Please start a new search.")
            return
        except Exception as e:
            logging.error(f"Error editing message in advantage_spell_chok: {e}")
            await msg.reply("❌ An error occurred while updating the message.")
            return
        # Rest of your code
        # ...
    except Exception as e:
        logging.error(f"Error in advantage_spell_chok: {e}")
        import traceback
        logging.error(traceback.format_exc())
        await msg.reply("❌ An error occurred. Check logs.")


# END SEASON EDIT HERE


@Client.on_callback_query(filters.regex(r"^episodes#"))
async def episodes_cb_handler(client: Client, query: CallbackQuery):
    try:
        # Parse callback data
        _, seas, key = query.data.split("#")
        
        # Check user permission
        if query.message.reply_to_message:
            if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
                logger.info(f"Permission denied for user {query.from_user.id}")
                return await query.answer(
                    f"⚠️ Hello {query.from_user.first_name},\nThis is not your movie request,\nRequest yours...",
                    show_alert=True,
                )
        
        files = temp.GETALL.get(key)
        
        if not files:
            logger.error(f"No files found in temp.GETALL for key: {key}")
            await query.answer("No files found.", show_alert=True)
            return
        
        season_num = seas.split()[-1]
        season_regex = re.compile(
            r"s\s*0?{0}|season\s*0?{0}|season-?{0}\b|s-?{0}\b|s0{0}|s{0}e|season{0}e|season 0{0}|season {0}\b|s 0{0}|s {0}\b|{0}th season|{0}x|s0{0}e|season\s*{0}\s*e".format(season_num),
            re.IGNORECASE
        )
        
        filtered_files = [f for f in files if season_regex.search(f["file_name"].lower())]
        
        if not filtered_files:
            logger.error(f"No files found for season: {seas}")
            await query.answer("No episodes found for this season.", show_alert=True)
            return
        
        # Sort files by episode number
        def get_episode_num(file):
            file_name_lower = file["file_name"].lower()
            ep_patterns = [r'e\s*(\d+)', r'episode\s*(\d+)', r'ep\s*(\d+)', r'\[(\d+)\]', r'e-?(\d+)', r'ep-?(\d+)', r'x(\d+)']
            for pattern in ep_patterns:
                ep_match = re.search(pattern, file_name_lower, re.IGNORECASE)
                if ep_match:
                    return int(ep_match.group(1))
            return 999
        
        filtered_files = sorted(filtered_files, key=get_episode_num)
        
        settings = await get_settings(query.message.chat.id)
        pre = 'filep' if settings.get('file_secure', False) else 'file'
        
        btn = []
        for file in filtered_files:
            file_name_lower = file["file_name"].lower()
            episode_num = "??"
            ep_patterns = [r'e\s*(\d+)', r'episode\s*(\d+)', r'ep\s*(\d+)', r'\[(\d+)\]', r'e-?(\d+)', r'ep-?(\d+)', r'x(\d+)']
            for pattern in ep_patterns:
                ep_match = re.search(pattern, file_name_lower, re.IGNORECASE)
                if ep_match:
                    episode_num = ep_match.group(1)
                    break
            
            clean_name = file["file_name"]
            for prefix in ['[', '@', 'www.', 'http', 'https']:
                if prefix in clean_name:
                    clean_name = clean_name.split(prefix, 1)[-1].strip()
            if len(clean_name) > 30:
                clean_name = clean_name[:27] + "..."
            
            button_text = f"E{episode_num.zfill(2)} | {get_size(file['file_size'])} | {clean_name}"
            btn.append([
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"{pre}#{file['file_id']}"
                )
            ])
        
        btn.insert(0, [
            InlineKeyboardButton(f"Episodes for {seas.title()}", callback_data="ident")
        ])
        
        btn.append([InlineKeyboardButton(text="↩️ Back to Seasons", callback_data=f"seasons#{key}")])
        
        await asyncio.wait_for(
            query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn)),
            timeout=8.0
        )
        
    except Exception as e:
        logger.error(f"Error in episodes_cb_handler: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await query.answer("❌ An error occurred! Check logs.", show_alert=True)
    

@Client.on_callback_query(filters.regex(r"^fl#"))
async def filter_qualities_cb_handler(client: Client, query: CallbackQuery):
    _, qual, key = query.data.split("#")
    search = FRESH.get(key)
    try:
        search = search.replace(' ', '_')
    except:
        pass
    baal = qual in search
    if baal:
        search = search.replace(qual, "")
    else:
        search = search
    req = query.from_user.id
    chat_id = query.message.chat.id
    message = query.message
    try:
        if int(req) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ{query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇQᴜᴇꜱᴛ,\nʀᴇQᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=False,
            )
    except:
        pass
    searchagain = search
    if lang != "homepage":
        search = f"{search} {qual}" 
    BUTTONS[key] = search

    files, offset, total_results = await get_search_results(chat_id, search, offset=0, filter=True)
    # files = [file for file in files if re.search(lang, file["file_name"], re.IGNORECASE)]
    if not files:
        await query.answer("🚫 𝗡𝗼 𝗙𝗶𝗹𝗲 𝗪𝗲𝗿𝗲 𝗙𝗼𝘂𝗻𝗱 🚫", show_alert=1)
        return
    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    pre = 'filep' if settings.get('file_secure', False) else 'file'
    if settings["button"]:
        btn = [
            [
                InlineKeyboardButton(
                    text=format_file_button_text(file), callback_data=f'{pre}#{file["file_id"]}'
                ),
            ]
            for file in files
        ]
        btn.insert(0, 
            [
                #InlineKeyboardButton(f'ǫᴜᴀʟɪᴛʏ', callback_data=f"qualities#{key}"),
                #InlineKeyboardButton("ᴇᴘɪsᴏᴅᴇs", callback_data=f"episodes#{key}"),
                InlineKeyboardButton("🍃 ꜱᴇʀɪᴇꜱ ᴄʟɪᴄᴋ 🍃", callback_data=f"seasons#{key}", style=enums.ButtonStyle.PRIMARY)
            ]
        )
    else:
        btn = []
        btn.insert(0, 
            [
                #InlineKeyboardButton(f'ǫᴜᴀʟɪᴛʏ', callback_data=f"qualities#{key}"),
                #InlineKeyboardButton("ᴇᴘɪsᴏᴅᴇs", callback_data=f"episodes#{key}"),
                InlineKeyboardButton("🍃 ꜱᴇʀɪᴇꜱ ᴄʟɪᴄᴋ 🍃", callback_data=f"seasons#{key}", style=enums.ButtonStyle.PRIMARY)
            ]
        )

    if offset != "":
        try:
            if settings['max_btn']:
                btn.append(
                    [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="ɴᴇxᴛ ⇛",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
                )
    
            else:
                btn.append(
                    [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="ɴᴇxᴛ ⇛",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
                )
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append(
                [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="ɴᴇxᴛ ⇛",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
            )
    else:
        btn.append(
            [InlineKeyboardButton(text="😶 ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇꜱ ᴀᴠᴀɪʟᴀʙʟᴇ 😶",callback_data="pages")]
        )
    if lang != "homepage":
        req = query.from_user.id
        offset = 0
        btn.append([InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ↭", callback_data=f"next_{req}_{key}_{offset}")])
    
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        total_results = len(files)
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, search)
        try:
            await query.message.edit_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), disable_web_page_preview=True)
        except MessageNotModified:
            pass
    else:
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass
                
@Client.on_callback_query(group=1)
async def cb_handler(client: Client, query: CallbackQuery):
    if query.data == "close_data":
        await query.message.delete()
    elif query.data == "get_trail":
        user_id = query.from_user.id
        free_trial_status = await db.get_free_trial_status(user_id)
        if not free_trial_status:            
            await db.give_free_trail(user_id)
            new_text = "**ʏᴏᴜ ᴄᴀɴ ᴜsᴇ ꜰʀᴇᴇ ᴛʀᴀɪʟ ꜰᴏʀ 5 ᴍɪɴᴜᴛᴇs ꜰʀᴏᴍ ɴᴏᴡ 😀\n\nआप अब से 5 मिनट के लिए निःशुल्क ट्रायल का उपयोग कर सकते हैं 😀**"        
            await query.message.edit_text(text=new_text)
            return
        else:
            new_text= "**🤣 you already used free now no more free trail. please buy subscription here are our 👉 /plans**"
            await query.message.edit_text(text=new_text)
            return
            
    elif query.data == "buy_premium":
        btn = [[            
            InlineKeyboardButton("✅sᴇɴᴅ ʏᴏᴜʀ ᴘᴀʏᴍᴇɴᴛ ʀᴇᴄᴇɪᴘᴛ ʜᴇʀᴇ ✅", url = OWNER_LINK)
        ]
            for admin in ADMINS
        ]
        btn.append(
            [InlineKeyboardButton("⚠️ᴄʟᴏsᴇ / ᴅᴇʟᴇᴛᴇ⚠️", callback_data="close_data")]
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.reply_photo(
            photo=PAYMENT_QR,
            caption=PAYMENT_TEXT,
            reply_markup=reply_markup
        )
        return 
    elif query.data == "gfiltersdeleteallconfirm":
        await del_allg(query.message, 'gfilters')
        await query.answer("Done !")
        return
    elif query.data == "gfiltersdeleteallcancel": 
        await query.message.reply_to_message.delete()
        await query.message.delete()
        await query.answer("Process Cancelled !")
        return
    elif query.data == "delallconfirm":
        userid = query.from_user.id
        chat_type = query.message.chat.type

        if chat_type == enums.ChatType.PRIVATE:
            grpid = await active_connection(str(userid))
            if grpid is not None:
                grp_id = grpid
                try:
                    chat = await client.get_chat(grpid)
                    title = chat.title
                except:
                    await query.message.edit_text("Mᴀᴋᴇ sᴜʀᴇ I'ᴍ ᴘʀᴇsᴇɴᴛ ɪɴ ʏᴏᴜʀ ɢʀᴏᴜᴘ!!", quote=True)
                    return await query.answer(MSG_ALRT)
            else:
                await query.message.edit_text(
                    "I'ᴍ ɴᴏᴛ ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴛᴏ ᴀɴʏ ɢʀᴏᴜᴘs!\nCʜᴇᴄᴋ /connections ᴏʀ ᴄᴏɴɴᴇᴄᴛ ᴛᴏ ᴀɴʏ ɢʀᴏᴜᴘs",
                    quote=True
                )
                return await query.answer(MSG_ALRT)

        elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            grp_id = query.message.chat.id
            title = query.message.chat.title

        else:
            return await query.answer(MSG_ALRT)

        st = await client.get_chat_member(grp_id, userid)
        if (st.status == enums.ChatMemberStatus.OWNER) or (str(userid) in ADMINS):
            await del_all(query.message, grp_id, title)
        else:
            await query.answer("Yᴏᴜ ɴᴇᴇᴅ ᴛᴏ ʙᴇ Gʀᴏᴜᴘ Oᴡɴᴇʀ ᴏʀ ᴀɴ Aᴜᴛʜ Usᴇʀ ᴛᴏ ᴅᴏ ᴛʜᴀᴛ!", show_alert=True)
    elif query.data == "delallcancel":
        userid = query.from_user.id
        chat_type = query.message.chat.type

        if chat_type == enums.ChatType.PRIVATE:
            await query.message.reply_to_message.delete()
            await query.message.delete()

        elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            grp_id = query.message.chat.id
            st = await client.get_chat_member(grp_id, userid)
            if (st.status == enums.ChatMemberStatus.OWNER) or (str(userid) in ADMINS):
                await query.message.delete()
                try:
                    await query.message.reply_to_message.delete()
                except:
                    pass
            else:
                await query.answer("Tʜᴀᴛ's ɴᴏᴛ ғᴏʀ ʏᴏᴜ!!", show_alert=True)
    elif "groupcb" in query.data:
        await query.answer()

        group_id = query.data.split(":")[1]

        act = query.data.split(":")[2]
        hr = await client.get_chat(int(group_id))
        title = hr.title
        user_id = query.from_user.id

        if act == "":
            stat = "CONNECT"
            cb = "connectcb"
        else:
            stat = "DISCONNECT"
            cb = "disconnect"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{stat}", callback_data=f"{cb}:{group_id}"),
             InlineKeyboardButton("DELETE", callback_data=f"deletecb:{group_id}")],
            [InlineKeyboardButton("BACK", callback_data="backcb")]
        ])

        await query.message.edit_text(
            f"Gʀᴏᴜᴘ Nᴀᴍᴇ : **{title}**\nGʀᴏᴜᴘ ID : `{group_id}`",
            reply_markup=keyboard,
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return await query.answer(MSG_ALRT)
    elif "connectcb" in query.data:
        await query.answer()

        group_id = query.data.split(":")[1]

        hr = await client.get_chat(int(group_id))

        title = hr.title

        user_id = query.from_user.id

        mkact = await make_active(str(user_id), str(group_id))

        if mkact:
            await query.message.edit_text(
                f"Cᴏɴɴᴇᴄᴛᴇᴅ ᴛᴏ **{title}**",
                parse_mode=enums.ParseMode.MARKDOWN
            )
        else:
            await query.message.edit_text('Sᴏᴍᴇ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ!!', parse_mode=enums.ParseMode.MARKDOWN)
        return await query.answer(MSG_ALRT)
    elif "disconnect" in query.data:
        await query.answer()

        group_id = query.data.split(":")[1]

        hr = await client.get_chat(int(group_id))

        title = hr.title
        user_id = query.from_user.id

        mkinact = await make_inactive(str(user_id))

        if mkinact:
            await query.message.edit_text(
                f"Dɪsᴄᴏɴɴᴇᴄᴛᴇᴅ ғʀᴏᴍ **{title}**",
                parse_mode=enums.ParseMode.MARKDOWN
            )
        else:
            await query.message.edit_text(
                f"Sᴏᴍᴇ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ!!",
                parse_mode=enums.ParseMode.MARKDOWN
            )
        return await query.answer(MSG_ALRT)
    elif "deletecb" in query.data:
        await query.answer()

        user_id = query.from_user.id
        group_id = query.data.split(":")[1]

        delcon = await delete_connection(str(user_id), str(group_id))

        if delcon:
            await query.message.edit_text(
                "Sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ᴄᴏɴɴᴇᴄᴛɪᴏɴ !"
            )
        else:
            await query.message.edit_text(
                f"Sᴏᴍᴇ ᴇʀʀᴏʀ ᴏᴄᴄᴜʀʀᴇᴅ!!",
                parse_mode=enums.ParseMode.MARKDOWN
            )
        return await query.answer(MSG_ALRT)
    elif query.data == "backcb":
        await query.answer()

        userid = query.from_user.id

        groupids = await all_connections(str(userid))
        if groupids is None:
            await query.message.edit_text(
                "Tʜᴇʀᴇ ᴀʀᴇ ɴᴏ ᴀᴄᴛɪᴠᴇ ᴄᴏɴɴᴇᴄᴛɪᴏɴs!! Cᴏɴɴᴇᴄᴛ ᴛᴏ sᴏᴍᴇ ɢʀᴏᴜᴘs ғɪʀsᴛ.",
            )
            return await query.answer(MSG_ALRT)
        buttons = []
        for groupid in groupids:
            try:
                ttl = await client.get_chat(int(groupid))
                title = ttl.title
                active = await if_active(str(userid), str(groupid))
                act = " - ACTIVE" if active else ""
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"{title}{act}", callback_data=f"groupcb:{groupid}:{act}"
                        )
                    ]
                )
            except:
                pass
        if buttons:
            await query.message.edit_text(
                "Yᴏᴜʀ ᴄᴏɴɴᴇᴄᴛᴇᴅ ɢʀᴏᴜᴘ ᴅᴇᴛᴀɪʟs ;\n\n",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
    elif "gfilteralert" in query.data:
        grp_id = query.message.chat.id
        i = query.data.split(":")[1]
        keyword = query.data.split(":")[2]
        reply_text, btn, alerts, fileid = await find_gfilter('gfilters', keyword)
        if alerts is not None:
            alerts = ast.literal_eval(alerts)
            alert = alerts[int(i)]
            alert = alert.replace("\\n", "\n").replace("\\t", "\t")
            await query.answer(alert, show_alert=True)
    
    elif "alertmessage" in query.data:
        grp_id = query.message.chat.id
        i = query.data.split(":")[1]
        keyword = query.data.split(":")[2]
        reply_text, btn, alerts, fileid = await find_filter(grp_id, keyword)
        if alerts is not None:
            alerts = ast.literal_eval(alerts)
            alert = alerts[int(i)]
            alert = alert.replace("\\n", "\n").replace("\\t", "\t")
            await query.answer(alert, show_alert=True)
        
    if query.data.startswith("file"):
        clicked = query.from_user.id
        try:
            typed = query.message.reply_to_message.from_user.id
        except:
            typed = query.from_user.id
        ident, file_id = query.data.split("#")
        files_ = await get_file_details(file_id)
        if not files_:
            return await query.answer('Nᴏ sᴜᴄʜ ғɪʟᴇ ᴇxɪsᴛ.')
        files = files_
        title = files["file_name"]
        size = get_size(files["file_size"])
        f_caption = files["caption"]
        settings = await get_settings(query.message.chat.id)
        if CUSTOM_FILE_CAPTION:
            try:
                f_caption = CUSTOM_FILE_CAPTION.format(file_name='' if title is None else title,
                                                       file_size='' if size is None else size,
                                                       file_caption='' if f_caption is None else f_caption)
            except Exception as e:
                logger.exception(e)
            f_caption = f_caption
        if f_caption is None:
            f_caption = f"{files['file_name']}"

        try:
            if settings['is_shortlink'] and not await db.has_premium_access(query.from_user.id):
                if clicked == typed:
                    temp.SHORT[clicked] = query.message.chat.id
                    await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=short_{file_id}")
                    return
                else:
                    await query.answer(f"Hᴇʏ {query.from_user.first_name}, Tʜɪs Is Nᴏᴛ Yᴏᴜʀ Mᴏᴠɪᴇ Rᴇǫᴜᴇsᴛ. Rᴇǫᴜᴇsᴛ Yᴏᴜʀ's !", show_alert=True)
            elif settings['is_shortlink'] and await db.has_premium_access(query.from_user.id):
                if clicked == typed:
                    await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start={ident}_{file_id}")
                    return
                else:
                    await query.answer(f"Hᴇʏ {query.from_user.first_name}, Tʜɪs Is Nᴏᴛ Yᴏᴜʀ Mᴏᴠɪᴇ Rᴇǫᴜᴇsᴛ. Rᴇǫᴜᴇsᴛ Yᴏᴜʀ's !", show_alert=True)
                    
            else:
                if clicked == typed:
                    await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start={ident}_{file_id}")
                    return
                else:
                    await query.answer(f"Hᴇʏ {query.from_user.first_name}, Tʜɪs Is Nᴏᴛ Yᴏᴜʀ Mᴏᴠɪᴇ Rᴇǫᴜᴇsᴛ. Rᴇǫᴜᴇsᴛ Yᴏᴜʀ's !", show_alert=True)
        except UserIsBlocked:
            await query.answer('Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴍᴀʜɴ !', show_alert=True)
        except PeerIdInvalid:
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start={ident}_{file_id}")
        except QueryIdInvalid:
            logger.warning(f"Expired callback query for file_id={file_id}, user={query.from_user.id}")
        except Exception as e:
            logger.exception(e)

    elif query.data.startswith("sendfiles"):
        clicked = query.from_user.id
        ident, key = query.data.split("#")
        settings = await get_settings(query.message.chat.id)
        pre = 'allfilesp' if settings['file_secure'] else 'allfiles'
        try:
            if settings['is_shortlink'] and not await db.has_premium_access(query.from_user.id):
                await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=sendfiles1_{key}")
            elif settings['is_shortlink'] and await db.has_premium_access(query.from_user.id):
                await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start={pre}_{key}")
                return 
            else:
                await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start={pre}_{key}")
                
            
                
        except UserIsBlocked:
            await query.answer('Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴍᴀʜɴ !', show_alert=True)
        except PeerIdInvalid:
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=sendfiles3_{key}")
        except QueryIdInvalid:
            logger.warning(f"Expired callback query for key={key}, user={query.from_user.id}")
        except Exception as e:
            logger.exception(e)

    elif query.data.startswith("unmuteme"):
        ident, userid = query.data.split("#")
        user_id = query.from_user.id
        settings = await get_settings(int(query.message.chat.id))
        if userid == 0:
            await query.answer("You are anonymous admin !", show_alert=True)
            return
        try:
            btn = await pub_is_subscribed(client, query, settings['fsub'])
            if btn:
                await query.answer("Kindly Join Given Channel Then Click On Unmute Button", show_alert=True)
            else:
                await client.unban_chat_member(query.message.chat.id, user_id)
                await query.answer("Unmuted Successfully !", show_alert=True)
                try:
                    await query.message.delete()
                except:
                    return
        except:
            await query.answer("Not For Your My Dear", show_alert=True)
   
    elif query.data.startswith("del"):
        ident, file_id = query.data.split("#")
        files_ = await get_file_details(file_id)
        if not files_:
            return await query.answer('Nᴏ sᴜᴄʜ ғɪʟᴇ ᴇxɪsᴛ.')
        files = files_
        title = files['file_name']
        size = get_size(files['file_size'])
        f_caption = files['caption']
        settings = await get_settings(query.message.chat.id)
        if CUSTOM_FILE_CAPTION:
            try:
                f_caption = CUSTOM_FILE_CAPTION.format(file_name='' if title is None else title,
                                                       file_size='' if size is None else size,
                                                       file_caption='' if f_caption is None else f_caption)
            except Exception as e:
                logger.exception(e)
            f_caption = f_caption
        if f_caption is None:
            f_caption = f"{files['file_name']}"
        await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=file_{file_id}")
    
    elif query.data.startswith("checksub"):
        if AUTH_CHANNEL and not await is_subscribed(client, query):
            await query.answer("Jᴏɪɴ ᴏᴜʀ Bᴀᴄᴋ-ᴜᴘ ᴄʜᴀɴɴᴇʟ ᴍᴀʜɴ! 😒", show_alert=True)
            return
        ident, kk, file_id = query.data.split("#")
        await query.answer(url=f"https://t.me/{temp.U_NAME}?start={kk}_{file_id}")
    
    elif query.data == "pages":
        await query.answer()
    
    elif query.data.startswith("send_fsall"):
        temp_var, ident, key, offset = query.data.split("#")
        search = BUTTON0.get(key)
     #   if not search:
      #      await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name),show_alert=True)
      #      return
        files, n_offset, total = await get_search_results(query.message.chat.id, search, offset=int(offset), filter=True)
        await send_all(client, query.from_user.id, files, ident, query.message.chat.id, query.from_user.first_name, query)
        search = BUTTONS1.get(key)
        files, n_offset, total = await get_search_results(query.message.chat.id, search, offset=int(offset), filter=True)
        await send_all(client, query.from_user.id, files, ident, query.message.chat.id, query.from_user.first_name, query)
        search = BUTTONS2.get(key)
        files, n_offset, total = await get_search_results(query.message.chat.id, search, offset=int(offset), filter=True)
        await send_all(client, query.from_user.id, files, ident, query.message.chat.id, query.from_user.first_name, query)
        await query.answer(f"Hey {query.from_user.first_name}, All files on this page has been sent successfully to your PM !", show_alert=True)
        
    elif query.data.startswith("send_fall"):
        temp_var, ident, key, offset = query.data.split("#")
        search = FRESH.get(key)
     #   if not search:
       #     await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name),show_alert=True)
      #      return
        files, n_offset, total = await get_search_results(query.message.chat.id, search, offset=int(offset), filter=True)
        await send_all(client, query.from_user.id, files, ident, query.message.chat.id, query.from_user.first_name, query)
        await query.answer(f"Hey {query.from_user.first_name}, All files on this page has been sent successfully to your PM !", show_alert=True)
        
    elif query.data.startswith("killfilesdq"):
        ident, keyword = query.data.split("#")
        #await query.message.edit_text(f"<b>Fetching Files for your query {keyword} on DB... Please wait...</b>")
        files, total = await get_bad_files(keyword)
        await query.message.edit_text("<b>File deletion process will start in 5 seconds !</b>")
        await asyncio.sleep(5)
        deleted = 0
        async with lock:
            try:
                for file in files:
                    file_ids = file["file_id"]
                    file_name = file["file_name"]
                    result = col.delete_one({
                        'file_id': file_ids,
                    })
                    if not result.deleted_count:
                        result = sec_col.delete_one({
                            'file_id': file_ids,
                        })
                    if result.deleted_count:
                        logger.info(f'File Found for your query {keyword}! Successfully deleted {file_name} from database.')
                    deleted += 1
                    if deleted % 50 == 0:
                        await query.message.edit_text(f"<b>Process started for deleting files from DB. Successfully deleted {str(deleted)} files from DB for your query {keyword} !\n\nPlease wait...</b>")
            except Exception as e:
                logger.exception(e)
                await query.message.edit_text(f'Error: {e}')
            else:
                await query.message.edit_text(f"<b>Process Completed for file deletion !\n\nSuccessfully deleted {str(deleted)} files from database for your query {keyword}.</b>")
    
    elif query.data.startswith("opnsetgrp"):
        ident, grp_id = query.data.split("#")
        userid = query.from_user.id if query.from_user else None
        st = await client.get_chat_member(grp_id, userid)
        if (
                st.status != enums.ChatMemberStatus.ADMINISTRATOR
                and st.status != enums.ChatMemberStatus.OWNER
                and str(userid) not in ADMINS
        ):
            await query.answer("Yᴏᴜ Dᴏɴ'ᴛ Hᴀᴠᴇ Tʜᴇ Rɪɢʜᴛs Tᴏ Dᴏ Tʜɪs !", show_alert=True)
            return
        title = query.message.chat.title
        settings = await get_settings(grp_id)
        if settings is not None:
            buttons = [
                [
                    InlineKeyboardButton('Rᴇsᴜʟᴛ Pᴀɢᴇ',
                                         callback_data=f'setgs#button#{settings["button"]}#{str(grp_id)}'),
                    InlineKeyboardButton('Bᴜᴛᴛᴏɴ' if settings["button"] else 'Tᴇxᴛ',
                                         callback_data=f'setgs#button#{settings["button"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Pʀᴏᴛᴇᴄᴛ Cᴏɴᴛᴇɴᴛ',
                                         callback_data=f'setgs#file_secure#{settings["file_secure"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["file_secure"] else '✘ Oғғ',
                                         callback_data=f'setgs#file_secure#{settings["file_secure"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Iᴍᴅʙ', callback_data=f'setgs#imdb#{settings["imdb"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["imdb"] else '✘ Oғғ',
                                         callback_data=f'setgs#imdb#{settings["imdb"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Sᴘᴇʟʟ Cʜᴇᴄᴋ',
                                         callback_data=f'setgs#spell_check#{settings["spell_check"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["spell_check"] else '✘ Oғғ',
                                         callback_data=f'setgs#spell_check#{settings["spell_check"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Wᴇʟᴄᴏᴍᴇ Msɢ', callback_data=f'setgs#welcome#{settings["welcome"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["welcome"] else '✘ Oғғ',
                                         callback_data=f'setgs#welcome#{settings["welcome"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Aᴜᴛᴏ-Dᴇʟᴇᴛᴇ',
                                         callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{str(grp_id)}'),
                    InlineKeyboardButton('5 Mɪɴs' if settings["auto_delete"] else '✘ Oғғ',
                                         callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Aᴜᴛᴏ-Fɪʟᴛᴇʀ',
                                         callback_data=f'setgs#auto_ffilter#{settings["auto_ffilter"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["auto_ffilter"] else '✘ Oғғ',
                                         callback_data=f'setgs#auto_ffilter#{settings["auto_ffilter"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Mᴀx Bᴜᴛᴛᴏɴs',
                                         callback_data=f'setgs#max_btn#{settings["max_btn"]}#{str(grp_id)}'),
                    InlineKeyboardButton('10' if settings["max_btn"] else f'{MAX_B_TN}',
                                         callback_data=f'setgs#max_btn#{settings["max_btn"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('SʜᴏʀᴛLɪɴᴋ',
                                         callback_data=f'setgs#is_shortlink#{settings["is_shortlink"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["is_shortlink"] else '✘ Oғғ',
                                         callback_data=f'setgs#is_shortlink#{settings["is_shortlink"]}#{str(grp_id)}')
                ]
            ]
            reply_markup = InlineKeyboardMarkup(buttons)
            await query.message.edit_text(
                text=f"<b>Cʜᴀɴɢᴇ Yᴏᴜʀ Sᴇᴛᴛɪɴɢs Fᴏʀ {title} As Yᴏᴜʀ Wɪsʜ ⚙</b>",
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.HTML
            )
            await query.message.edit_reply_markup(reply_markup)
        
    elif query.data.startswith("opnsetpm"):
        ident, grp_id = query.data.split("#")
        userid = query.from_user.id if query.from_user else None
        st = await client.get_chat_member(grp_id, userid)
        if (
                st.status != enums.ChatMemberStatus.ADMINISTRATOR
                and st.status != enums.ChatMemberStatus.OWNER
                and str(userid) not in ADMINS
        ):
            await query.answer("Yᴏᴜ Dᴏɴ'ᴛ Hᴀᴠᴇ Tʜᴇ Rɪɢʜᴛs Tᴏ Dᴏ Tʜɪs !", show_alert=True)
            return
        title = query.message.chat.title
        settings = await get_settings(grp_id)
        btn2 = [[
                 InlineKeyboardButton("Cʜᴇᴄᴋ PM", url=f"telegram.me/{temp.U_NAME}")
               ]]
        reply_markup = InlineKeyboardMarkup(btn2)
        await query.message.edit_text(f"<b>Yᴏᴜʀ sᴇᴛᴛɪɴɢs ᴍᴇɴᴜ ғᴏʀ {title} ʜᴀs ʙᴇᴇɴ sᴇɴᴛ ᴛᴏ ʏᴏᴜʀ PM</b>")
        await query.message.edit_reply_markup(reply_markup)
        if settings is not None:
            buttons = [
                [
                    InlineKeyboardButton('Rᴇsᴜʟᴛ Pᴀɢᴇ',
                                         callback_data=f'setgs#button#{settings["button"]}#{str(grp_id)}'),
                    InlineKeyboardButton('Bᴜᴛᴛᴏɴ' if settings["button"] else 'Tᴇxᴛ',
                                         callback_data=f'setgs#button#{settings["button"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Pʀᴏᴛᴇᴄᴛ Cᴏɴᴛᴇɴᴛ',
                                         callback_data=f'setgs#file_secure#{settings["file_secure"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["file_secure"] else '✘ Oғғ',
                                         callback_data=f'setgs#file_secure#{settings["file_secure"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Iᴍᴅʙ', callback_data=f'setgs#imdb#{settings["imdb"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["imdb"] else '✘ Oғғ',
                                         callback_data=f'setgs#imdb#{settings["imdb"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Sᴘᴇʟʟ Cʜᴇᴄᴋ',
                                         callback_data=f'setgs#spell_check#{settings["spell_check"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["spell_check"] else '✘ Oғғ',
                                         callback_data=f'setgs#spell_check#{settings["spell_check"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Wᴇʟᴄᴏᴍᴇ Msɢ', callback_data=f'setgs#welcome#{settings["welcome"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["welcome"] else '✘ Oғғ',
                                         callback_data=f'setgs#welcome#{settings["welcome"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Aᴜᴛᴏ-Dᴇʟᴇᴛᴇ',
                                         callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{str(grp_id)}'),
                    InlineKeyboardButton('5 Mɪɴs' if settings["auto_delete"] else '✘ Oғғ',
                                         callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Aᴜᴛᴏ-Fɪʟᴛᴇʀ',
                                         callback_data=f'setgs#auto_ffilter#{settings["auto_ffilter"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["auto_ffilter"] else '✘ Oғғ',
                                         callback_data=f'setgs#auto_ffilter#{settings["auto_ffilter"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Mᴀx Bᴜᴛᴛᴏɴs',
                                         callback_data=f'setgs#max_btn#{settings["max_btn"]}#{str(grp_id)}'),
                    InlineKeyboardButton('10' if settings["max_btn"] else f'{MAX_B_TN}',
                                         callback_data=f'setgs#max_btn#{settings["max_btn"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('SʜᴏʀᴛLɪɴᴋ',
                                         callback_data=f'setgs#is_shortlink#{settings["is_shortlink"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["is_shortlink"] else '✘ Oғғ',
                                         callback_data=f'setgs#is_shortlink#{settings["is_shortlink"]}#{str(grp_id)}')
                ]
            ]
            reply_markup = InlineKeyboardMarkup(buttons)
            await client.send_message(
                chat_id=userid,
                text=f"<b>Cʜᴀɴɢᴇ Yᴏᴜʀ Sᴇᴛᴛɪɴɢs Fᴏʀ {title} As Yᴏᴜʀ Wɪsʜ ⚙</b>",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.HTML,
                reply_to_message_id=query.message.id
            )
    elif query.data.startswith("show_option"):
        ident, from_user = query.data.split("#")
        btn = [[
                InlineKeyboardButton("Uɴᴀᴠᴀɪʟᴀʙʟᴇ", callback_data=f"unavailable#{from_user}"),
                InlineKeyboardButton("Uᴘʟᴏᴀᴅᴇᴅ", callback_data=f"uploaded#{from_user}")
             ],[
                InlineKeyboardButton("Aʟʀᴇᴀᴅʏ Aᴠᴀɪʟᴀʙʟᴇ", callback_data=f"already_available#{from_user}")
              ]]
        btn2 = [[
                 InlineKeyboardButton("Vɪᴇᴡ Sᴛᴀᴛᴜs", url=f"{query.message.link}")
               ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(int(from_user))
            reply_markup = InlineKeyboardMarkup(btn)
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Hᴇʀᴇ ᴀʀᴇ ᴛʜᴇ ᴏᴘᴛɪᴏɴs !")
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("unavailable"):
        ident, from_user = query.data.split("#")
        btn = [[
                InlineKeyboardButton("⚠️ Uɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️", callback_data=f"unalert#{from_user}")
              ]]
        btn2 = [[
                 InlineKeyboardButton('Jᴏɪɴ Cʜᴀɴɴᴇʟ', url="https://t.me/+h_FlBpUHTpJkZTg1"),
                 InlineKeyboardButton("Vɪᴇᴡ Sᴛᴀᴛᴜs", url=f"{query.message.link}")
               ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(int(from_user))
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Uɴᴀᴠᴀɪʟᴀʙʟᴇ !")
            try:
                await client.send_message(chat_id=int(from_user), text=f"<b>Hᴇʏ {user.mention}, Sᴏʀʀʏ Yᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ ɪs ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ. Sᴏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs ᴄᴀɴ'ᴛ ᴜᴘʟᴏᴀᴅ ɪᴛ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
            except UserIsBlocked:
                await client.send_message(chat_id=int(SUPPORT_CHAT_ID), text=f"<b>Hᴇʏ {user.mention}, Sᴏʀʀʏ Yᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ ɪs ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ. Sᴏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs ᴄᴀɴ'ᴛ ᴜᴘʟᴏᴀᴅ ɪᴛ.\n\nNᴏᴛᴇ: Tʜɪs ᴍᴇssᴀɢᴇ ɪs sᴇɴᴛ ᴛᴏ ᴛʜɪs ɢʀᴏᴜᴘ ʙᴇᴄᴀᴜsᴇ ʏᴏᴜ'ᴠᴇ ʙʟᴏᴄᴋᴇᴅ ᴛʜᴇ ʙᴏᴛ. Tᴏ sᴇɴᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ PM, Mᴜsᴛ ᴜɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("uploaded"):
        ident, from_user = query.data.split("#")
        btn = [[
                InlineKeyboardButton("✅ Uᴘʟᴏᴀᴅᴇᴅ ✅", callback_data=f"upalert#{from_user}")
              ]]
        msg_link = query.message.link if query.message.link else "https://t.me/gofixmovie"
        btn2 = [[
                 InlineKeyboardButton('Jᴏɪɴ Cʜᴀɴɴᴇʟ', url="https://t.me/+h_FlBpUHTpJkZTg1"),
                 InlineKeyboardButton("Vɪᴇᴡ Sᴛᴀᴛᴜs", url=msg_link)
               ],[
                 InlineKeyboardButton("Rᴇᴏ̨ᴜᴇsᴛ Gʀᴏᴜᴘ Lɪɴᴋ", url="https://t.me/gofixmovie")
               ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(int(from_user))
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Uᴘʟᴏᴀᴅᴇᴅ !")
            try:
                await client.send_message(chat_id=int(from_user), text=f"<b>Hᴇʏ {user.mention}, Yᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ ʜᴀs ʙᴇᴇɴ ᴜᴘʟᴏᴀᴅᴇᴅ ʙʏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs. Kɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
            except UserIsBlocked:
                await client.send_message(chat_id=int(SUPPORT_CHAT_ID), text=f"<b>Hᴇʏ {user.mention}, Yᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ ʜᴀs ʙᴇᴇɴ ᴜᴘʟᴏᴀᴅᴇᴅ ʙʏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs. Kɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.\n\nNᴏᴛᴇ: Tʜɪs ᴍᴇssᴀɢᴇ ɪs sᴇɴᴛ ᴛᴏ ᴛʜɪs ɢʀᴏᴜᴘ ʙᴇᴄᴀᴜsᴇ ʏᴏᴜ'ᴠᴇ ʙʟᴏᴄᴋᴇᴅ ᴛʜᴇ ʙᴏᴛ. Tᴏ sᴇɴᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ PM, Mᴜsᴛ ᴜɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("already_available"):
        ident, from_user = query.data.split("#")
        btn = [[
            InlineKeyboardButton("🟢 Aʟʀᴇᴀᴅʏ Aᴠᴀɪʟᴀʙʟᴇ 🟢", callback_data=f"alalert#{from_user}")
        ]]
        btn2 = [[
            InlineKeyboardButton('Jᴏɪɴ Cʜᴀɴɴᴇʟ', url="https://t.me/+h_FlBpUHTpJkZTg1"),
            InlineKeyboardButton("Vɪᴇᴡ Sᴛᴀᴛᴜs", url=f"{query.message.link}")
        ],[
            InlineKeyboardButton("Rᴇᴏ̨ᴜᴇsᴛ Gʀᴏᴜᴘ Lɪɴᴋ", url="https://t.me/gofixmovie")
        ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(int(from_user))
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Aʟʀᴇᴀᴅʏ Aᴠᴀɪʟᴀʙʟᴇ !")
            try:
                await client.send_message(chat_id=int(from_user), text=f"<b>Hᴇʏ {user.mention}, Yᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ ɪs ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ᴏɴ ᴏᴜʀ ʙᴏᴛ's ᴅᴀᴛᴀʙᴀsᴇ. Kɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
            except UserIsBlocked:
                await client.send_message(chat_id=int(SUPPORT_CHAT_ID), text=f"<b>Hᴇʏ {user.mention}, Yᴏᴜʀ ʀᴇᴏ̨ᴜᴇsᴛ ɪs ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ᴏɴ ᴏᴜʀ ʙᴏᴛ's ᴅᴀᴛᴀʙᴀsᴇ. Kɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.\n\nNᴏᴛᴇ: Tʜɪs ᴍᴇssᴀɢᴇ ɪs sᴇɴᴛ ᴛᴏ ᴛʜɪs ɢʀᴏᴜᴘ ʙᴇᴄᴀᴜsᴇ ʏᴏᴜ'ᴠᴇ ʙʟᴏᴄᴋᴇᴅ ᴛʜᴇ ʙᴏᴛ. Tᴏ sᴇɴᴅ ᴛʜɪs ᴍᴇssᴀɢᴇ ᴛᴏ ʏᴏᴜʀ PM, Mᴜsᴛ ᴜɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ.</b>", reply_markup=InlineKeyboardMarkup(btn2))
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("alalert"):
        ident, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            user = await client.get_users(int(from_user))
            await query.answer(f"Hᴇʏ {user.first_name}, Yᴏᴜʀ Rᴇᴏ̨ᴜᴇsᴛ ɪs Aʟʀᴇᴀᴅʏ Aᴠᴀɪʟᴀʙʟᴇ !", show_alert=True)
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("upalert"):
        ident, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            user = await client.get_users(int(from_user))
            await query.answer(f"Hᴇʏ {user.first_name}, Yᴏᴜʀ Rᴇᴏ̨ᴜᴇsᴛ ɪs Uᴘʟᴏᴀᴅᴇᴅ !", show_alert=True)
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("unalert"):
        ident, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            user = await client.get_users(int(from_user))
            await query.answer(f"Hᴇʏ {user.first_name}, Yᴏᴜʀ Rᴇᴏ̨ᴜᴇsᴛ ɪs Uɴᴀᴠᴀɪʟᴀʙʟᴇ !", show_alert=True)
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)
# START HERE
    elif query.data.startswith("generate_stream_link"):
        _, file_id = query.data.split(":", 1)

        if not await is_premium_user(query.from_user.id):
            await query.answer(
                "🔒 Stream & Download link is a Premium-only feature.\n\nBuy premium with /plan to unlock it.",
                show_alert=True
            )
            return

        try:
            log_msg = await client.send_cached_media(
                chat_id=LOG_CHANNEL,
                file_id=file_id
            )
    
            file_name = quote_plus(get_name(log_msg))
            file_hash = get_hash(log_msg)
    
            stream = f"{URL}/watch/{log_msg.id}/{file_name}?hash={file_hash}"
            download = f"{URL}/download/{log_msg.id}/{file_name}?hash={file_hash}"
    
            button = [
                [
                    InlineKeyboardButton("• ᴡᴀᴛᴄʜ •", url=stream),
                    InlineKeyboardButton("• ᴅᴏᴡɴʟᴏᴀᴅ •", url=download)
                ],
                [
                    InlineKeyboardButton(
                        "• ᴡᴀᴛᴄʜ ɪɴ ᴡᴇʙ ᴀᴘᴘ •",
                        web_app=WebAppInfo(url=stream)
                    )
                ]
            ]
    
            # ✅ FIXED HERE
            await query.message.edit_reply_markup(
                reply_markup=InlineKeyboardMarkup(button)
            )
    
            await query.answer()
    
        except Exception as e:
            await query.answer(
                f"Something went wrong ❌\n\n{e}",
                show_alert=True
            )


    ######
        
    elif query.data == "reqinfo":
        await query.answer(text=script.REQINFO, show_alert=True)

    elif query.data == "select":
        await query.answer(text=script.SELECT, show_alert=True)

    elif query.data == "sinfo":
        await query.answer(text=script.SINFO, show_alert=True)

    elif query.data == "start":
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
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        await query.message.edit_text(
            text=script.START_TXT.format(query.from_user.mention, temp.U_NAME, temp.B_NAME),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer(MSG_ALRT)

    elif query.data == "clone":
        buttons = [[
            InlineKeyboardButton('⟸ Bᴀᴄᴋ', callback_data='start')
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.CLONE_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        
    elif query.data == "filters":
        buttons = [[
            InlineKeyboardButton('Mᴀɴᴜᴀʟ FIʟᴛᴇʀ', callback_data='manuelfilter'),
            InlineKeyboardButton('Aᴜᴛᴏ FIʟᴛᴇʀ', callback_data='autofilter')
        ],[
            InlineKeyboardButton('⟸ Bᴀᴄᴋ', callback_data='help'),
            InlineKeyboardButton('Gʟᴏʙᴀʟ Fɪʟᴛᴇʀs', callback_data='global_filters')
        ]]
        
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        await query.message.edit_text(
            text=script.ALL_FILTERS.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "global_filters":
        buttons = [[
            InlineKeyboardButton('⟸ Bᴀᴄᴋ', callback_data='filters')
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.GFILTER_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    
    elif query.data == "help":
        buttons = [[
             InlineKeyboardButton('⚙️ ᴀᴅᴍɪɴ ᴏɴʟʏ 🔧', callback_data='admin'),
         ], [ 
             InlineKeyboardButton('ʀᴇɴᴀᴍᴇ', callback_data='r_txt'),   
             InlineKeyboardButton('sᴛʀᴇᴀᴍ/ᴅᴏᴡɴʟᴏᴀᴅ', callback_data='s_txt') 
         ], [ 
             InlineKeyboardButton('ꜰɪʟᴇ ꜱᴛᴏʀᴇ', callback_data='store_file'),   
             InlineKeyboardButton('ᴛᴇʟᴇɢʀᴀᴘʜ', callback_data='tele') 
         ], [ 
             InlineKeyboardButton('ᴄᴏɴɴᴇᴄᴛɪᴏɴꜱ', callback_data='coct'), 
             InlineKeyboardButton('ꜰɪʟᴛᴇʀꜱ', callback_data='filters')
         ], [
             InlineKeyboardButton('ʏᴛ-ᴅʟ', callback_data='ytdl'), 
             InlineKeyboardButton('ꜱʜᴀʀᴇ ᴛᴇxᴛ', callback_data='share')
         ], [
             InlineKeyboardButton('ꜱᴏɴɢ', callback_data='song'),
             InlineKeyboardButton('ᴇᴀʀɴ ᴍᴏɴᴇʏ', callback_data='shortlink_info')
         ], [
             InlineKeyboardButton('ꜱᴛɪᴄᴋᴇʀ-ɪᴅ', callback_data='sticker'),
             InlineKeyboardButton('ᴊ-ꜱᴏɴ', callback_data='json')
         ], [             
             InlineKeyboardButton('🏠 𝙷𝙾𝙼𝙴 🏠', callback_data='start')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        await query.message.edit_text(
            text=script.HELP_TXT.format(query.from_user.mention),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "about":
        buttons = [[
            InlineKeyboardButton('Sᴜᴘᴘᴏʀᴛ Gʀᴏᴜᴘ', url=GRP_LNK),
           # InlineKeyboardButton('Sᴏᴜʀᴄᴇ Cᴏᴅᴇ', url="https://github.com/VJBots/VJ-FILTER-BOT")
        ],[
            InlineKeyboardButton('Hᴏᴍᴇ', callback_data='start'),
            InlineKeyboardButton('Cʟᴏsᴇ', callback_data='close_data')
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.ABOUT_TXT.format(temp.U_NAME, temp.B_NAME, OWNER_LNK),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "subscription":
        buttons = [[
            InlineKeyboardButton('⇚Back', callback_data='start')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        await query.message.edit_text(
            text=script.SUBSCRIPTION_TXT.format(REFERAL_PREMEIUM_TIME, temp.U_NAME, query.from_user.id, REFERAL_COUNT),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "manuelfilter":
        buttons = [[
            InlineKeyboardButton('⟸ Bᴀᴄᴋ', callback_data='filters'),
            InlineKeyboardButton('Bᴜᴛᴛᴏɴs', callback_data='button')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        await query.message.edit_text(
            text=script.MANUELFILTER_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "button":
        buttons = [[
            InlineKeyboardButton('⟸ Bᴀᴄᴋ', callback_data='manuelfilter')
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.BUTTON_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "autofilter":
        buttons = [[
            InlineKeyboardButton('⟸ Bᴀᴄᴋ', callback_data='filters')
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.AUTOFILTER_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "coct":
        buttons = [[
            InlineKeyboardButton('⟸ Bᴀᴄᴋ', callback_data='help')
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.CONNECTION_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "admin":
        buttons = [[
            InlineKeyboardButton('⟸ Bᴀᴄᴋ', callback_data='help'),
            InlineKeyboardButton('ᴇxᴛʀᴀ', callback_data='extra')
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.ADMIN_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    
    elif query.data == "store_file":
        buttons = [[
            InlineKeyboardButton('⟸ Bᴀᴄᴋ', callback_data='help')
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.FILE_STORE_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "r_txt":
        buttons = [[
            InlineKeyboardButton('⟸ Bᴀᴄᴋ', callback_data='help')
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.RENAME_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "s_txt":
        buttons = [[
            InlineKeyboardButton('⟸ Bᴀᴄᴋ', callback_data='help')
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.STREAM_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    
    elif query.data == "extra":
        buttons = [[
            InlineKeyboardButton('⟸ Bᴀᴄᴋ', callback_data='admin')
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.EXTRAMOD_TXT.format(OWNER_LNK, CHNL_LNK),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "stats":
        buttons = [[
            InlineKeyboardButton('⟸ Bᴀᴄᴋ', callback_data='help'),
            InlineKeyboardButton('⟲ Rᴇғʀᴇsʜ', callback_data='rfrsh')
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        total_users = await db.total_users_count()
        totl_chats = await db.total_chat_count()
        filesp = col.count_documents({})
        totalsec = sec_col.count_documents({})
        stats = vjdb.command('dbStats')
        used_dbSize = (stats['dataSize']/(1024*1024))+(stats['indexSize']/(1024*1024))
        free_dbSize = 512-used_dbSize
        stats2 = sec_db.command('dbStats')
        used_dbSize2 = (stats2['dataSize']/(1024*1024))+(stats2['indexSize']/(1024*1024))
        free_dbSize2 = 512-used_dbSize2
        stats3 = mydb.command('dbStats')
        used_dbSize3 = (stats3['dataSize']/(1024*1024))+(stats3['indexSize']/(1024*1024))
        free_dbSize3 = 512-used_dbSize3
        await query.message.edit_text(
            text=script.STATUS_TXT.format((int(filesp)+int(totalsec)), total_users, totl_chats, filesp, round(used_dbSize, 2), round(free_dbSize, 2), totalsec, round(used_dbSize2, 2), round(free_dbSize2, 2), round(used_dbSize3, 2), round(free_dbSize3, 2)),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "rfrsh":
        await query.answer("Fetching MongoDb DataBase")
        buttons = [[
            InlineKeyboardButton('⟸ Bᴀᴄᴋ', callback_data='help'),
            InlineKeyboardButton('⟲ Rᴇғʀᴇsʜ', callback_data='rfrsh')
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        total_users = await db.total_users_count()
        totl_chats = await db.total_chat_count()
        filesp = col.count_documents({})
        totalsec = sec_col.count_documents({})
        stats = vjdb.command('dbStats')
        used_dbSize = (stats['dataSize']/(1024*1024))+(stats['indexSize']/(1024*1024))
        free_dbSize = 512-used_dbSize
        stats2 = sec_db.command('dbStats')
        used_dbSize2 = (stats2['dataSize']/(1024*1024))+(stats2['indexSize']/(1024*1024))
        free_dbSize2 = 512-used_dbSize2
        stats3 = mydb.command('dbStats')
        used_dbSize3 = (stats3['dataSize']/(1024*1024))+(stats3['indexSize']/(1024*1024))
        free_dbSize3 = 512-used_dbSize3
        await query.message.edit_text(
            text=script.STATUS_TXT.format((int(filesp)+int(totalsec)), total_users, totl_chats, filesp, round(used_dbSize, 2), round(free_dbSize, 2), totalsec, round(used_dbSize2, 2), round(free_dbSize2, 2), round(used_dbSize3, 2), round(free_dbSize3, 2)),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "shortlink_info":
        btn = [[
            InlineKeyboardButton("👇Select Your Language 👇", callback_data="laninfo")
        ],[
            InlineKeyboardButton("Tamil", callback_data="tamil_info"),
            InlineKeyboardButton("English", callback_data="english_info"),
            InlineKeyboardButton("Hindi", callback_data="hindi_info")
        ],[
            InlineKeyboardButton("Malayalam", callback_data="malayalam_info"),
            InlineKeyboardButton("Urdu", callback_data="urdu_info"),
            InlineKeyboardButton("Bangla", callback_data="bangladesh_info")
        ],[
            InlineKeyboardButton("Telugu", callback_data="telugu_info"),
            InlineKeyboardButton("Kannada", callback_data="kannada_info"),
            InlineKeyboardButton("Gujarati", callback_data="gujarati_info")
        ],[
            InlineKeyboardButton("⟸ Bᴀᴄᴋ", callback_data="start")
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.edit_text(
            text=(script.SHORTLINK_INFO),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "tele":
        btn = [[
            InlineKeyboardButton("⟸ Bᴀᴄᴋ", callback_data="help"),
            InlineKeyboardButton("Cᴏɴᴛᴀᴄᴛ", url="https://t.me/Goflix_AdminBot")
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.edit_text(
            text=(script.TELE_TXT),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "ytdl":
        buttons = [[
            InlineKeyboardButton('⇍ ʙᴀᴄᴋ ⇏', callback_data='help')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text="● ◌ ◌"
        )
        await query.message.edit_text(
            text="● ● ◌"
        )
        await query.message.edit_text(
            text="● ● ●"
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        await query.message.edit_text(
            text=script.YTDL_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "share":
        btn = [[
            InlineKeyboardButton("⟸ Bᴀᴄᴋ", callback_data="help"),
            InlineKeyboardButton("Cᴏɴᴛᴀᴄᴛ", url="https://t.me/Goflix_AdminBot")
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.edit_text(
            text=(script.SHARE_TXT),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "song":
        btn = [[
            InlineKeyboardButton("⟸ Bᴀᴄᴋ", callback_data="help"),
            InlineKeyboardButton("Cᴏɴᴛᴀᴄᴛ", url="https://t.me/Goflix_AdminBot")
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.edit_text(
            text=(script.SONG_TXT),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "json":
        buttons = [[
            InlineKeyboardButton('⇍ ʙᴀᴄᴋ ⇏', callback_data='help')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text="● ◌ ◌"
        )
        await query.message.edit_text(
            text="● ● ◌"
        )
        await query.message.edit_text(
            text="● ● ●"
        )
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        await query.message.edit_text(
            text=script.JSON_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "sticker":
        btn = [[
            InlineKeyboardButton("⟸ Bᴀᴄᴋ", callback_data="help"),
            InlineKeyboardButton("Cᴏɴᴛᴀᴄᴛ", url="https://t.me/Goflix_AdminBot")
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.edit_text(
            text=(script.STICKER_TXT),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "tamil_info":
        btn = [[
            InlineKeyboardButton("⟸ Bᴀᴄᴋ", callback_data="start"),
            InlineKeyboardButton("Cᴏɴᴛᴀᴄᴛ", url="https://t.me/Goflix_AdminBot")
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.edit_text(
            text=(script.TAMIL_INFO),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "english_info":
        btn = [[
            InlineKeyboardButton("⟸ Bᴀᴄᴋ", callback_data="start"),
            InlineKeyboardButton("Cᴏɴᴛᴀᴄᴛ", url="https://t.me/Goflix_AdminBot")
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.edit_text(
            text=(script.ENGLISH_INFO),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "hindi_info":
        btn = [[
            InlineKeyboardButton("⟸ Bᴀᴄᴋ", callback_data="start"),
            InlineKeyboardButton("Cᴏɴᴛᴀᴄᴛ", url="https://t.me/Goflix_AdminBot")
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.edit_text(
            text=(script.HINDI_INFO),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "telugu_info":
        btn = [[
            InlineKeyboardButton("⟸ Bᴀᴄᴋ", callback_data="start"),
            InlineKeyboardButton("Cᴏɴᴛᴀᴄᴛ", url="https://t.me/Goflix_AdminBot")
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.edit_text(
            text=(script.TELUGU_INFO),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "malayalam_info":
        btn = [[
            InlineKeyboardButton("⟸ Bᴀᴄᴋ", callback_data="start"),
            InlineKeyboardButton("Cᴏɴᴛᴀᴄᴛ", url="https://t.me/Goflix_AdminBot")
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.edit_text(
            text=(script.MALAYALAM_INFO),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "urdu_info":
        btn = [[
            InlineKeyboardButton("⟸ Bᴀᴄᴋ", callback_data="start"),
            InlineKeyboardButton("Cᴏɴᴛᴀᴄᴛ", url="https://t.me/Goflix_AdminBot")
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.edit_text(
            text=(script.URDU_INFO),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "bangladesh_info":
        btn = [[
            InlineKeyboardButton("⟸ Bᴀᴄᴋ", callback_data="start"),
            InlineKeyboardButton("Cᴏɴᴛᴀᴄᴛ", url="https://t.me/Goflix_AdminBot")
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.edit_text(
            text=(script.BANGLADESH_INFO),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "kannada_info":
        btn = [[
            InlineKeyboardButton("⟸ Bᴀᴄᴋ", callback_data="start"),
            InlineKeyboardButton("Cᴏɴᴛᴀᴄᴛ", url="https://t.me/Goflix_AdminBot")
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.edit_text(
            text=(script.KANNADA_INFO),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data == "gujarati_info":
        btn = [[
            InlineKeyboardButton("⟸ Bᴀᴄᴋ", callback_data="start"),
            nlineKeyboardButton("Cᴏɴᴛᴀᴄᴛ", url="https://t.me/Goflix_AdminBot")
        ]]
        await client.edit_message_media(
            query.message.chat.id, 
            query.message.id, 
            InputMediaPhoto(random.choice(PICS))
        )
        reply_markup = InlineKeyboardMarkup(btn)
        await query.message.edit_text(
            text=(script.GUJARATI_INFO),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
    elif query.data.startswith("setgs"):
        ident, set_type, status, grp_id = query.data.split("#")
        grpid = await active_connection(str(query.from_user.id))

        if str(grp_id) != str(grpid):
            await query.message.edit("Yᴏᴜʀ Aᴄᴛɪᴠᴇ Cᴏɴɴᴇᴄᴛɪᴏɴ Hᴀs Bᴇᴇɴ Cʜᴀɴɢᴇᴅ. Gᴏ Tᴏ /connections ᴀɴᴅ ᴄʜᴀɴɢᴇ ʏᴏᴜʀ ᴀᴄᴛɪᴠᴇ ᴄᴏɴɴᴇᴄᴛɪᴏɴ.")
            return await query.answer(MSG_ALRT)

        if status == "True":
            await save_group_settings(grpid, set_type, False)
        else:
            settings = await get_settings(grpid)
            if set_type == "is_shortlink" and not settings['shortlink']:
                return await query.answer(text = "First Add Your Shortlink Url And Api By /shortlink Command, Then Turn Me On.", show_alert = True)
            await save_group_settings(grpid, set_type, True)

        settings = await get_settings(grpid)

        if settings is not None:
            buttons = [
                [
                    InlineKeyboardButton('Rᴇsᴜʟᴛ Pᴀɢᴇ',
                                         callback_data=f'setgs#button#{settings["button"]}#{str(grp_id)}'),
                    InlineKeyboardButton('Bᴜᴛᴛᴏɴ' if settings["button"] else 'Tᴇxᴛ',
                                         callback_data=f'setgs#button#{settings["button"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Pʀᴏᴛᴇᴄᴛ Cᴏɴᴛᴇɴᴛ',
                                         callback_data=f'setgs#file_secure#{settings["file_secure"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["file_secure"] else '✘ Oғғ',
                                         callback_data=f'setgs#file_secure#{settings["file_secure"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Iᴍᴅʙ', callback_data=f'setgs#imdb#{settings["imdb"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["imdb"] else '✘ Oғғ',
                                         callback_data=f'setgs#imdb#{settings["imdb"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Sᴘᴇʟʟ Cʜᴇᴄᴋ',
                                         callback_data=f'setgs#spell_check#{settings["spell_check"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["spell_check"] else '✘ Oғғ',
                                         callback_data=f'setgs#spell_check#{settings["spell_check"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Wᴇʟᴄᴏᴍᴇ Msɢ', callback_data=f'setgs#welcome#{settings["welcome"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["welcome"] else '✘ Oғғ',
                                         callback_data=f'setgs#welcome#{settings["welcome"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Aᴜᴛᴏ-Dᴇʟᴇᴛᴇ',
                                         callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{str(grp_id)}'),
                    InlineKeyboardButton('5 Mɪɴs' if settings["auto_delete"] else '✘ Oғғ',
                                         callback_data=f'setgs#auto_delete#{settings["auto_delete"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Aᴜᴛᴏ-Fɪʟᴛᴇʀ',
                                         callback_data=f'setgs#auto_ffilter#{settings["auto_ffilter"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["auto_ffilter"] else '✘ Oғғ',
                                         callback_data=f'setgs#auto_ffilter#{settings["auto_ffilter"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('Mᴀx Bᴜᴛᴛᴏɴs',
                                         callback_data=f'setgs#max_btn#{settings["max_btn"]}#{str(grp_id)}'),
                    InlineKeyboardButton('10' if settings["max_btn"] else f'{MAX_B_TN}',
                                         callback_data=f'setgs#max_btn#{settings["max_btn"]}#{str(grp_id)}')
                ],
                [
                    InlineKeyboardButton('SʜᴏʀᴛLɪɴᴋ',
                                         callback_data=f'setgs#is_shortlink#{settings["is_shortlink"]}#{str(grp_id)}'),
                    InlineKeyboardButton('✔ Oɴ' if settings["is_shortlink"] else '✘ Oғғ',
                                         callback_data=f'setgs#is_shortlink#{settings["is_shortlink"]}#{str(grp_id)}')
                ]
            ]
            reply_markup = InlineKeyboardMarkup(buttons)
            await query.message.edit_reply_markup(reply_markup)
    try:
        await query.answer(MSG_ALRT)
    except QueryIdInvalid:
        pass

async def auto_filter(client, name, msg, reply_msg, ai_search, spoll=False, from_deeplink=False):
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    _t0 = time.monotonic()  # ⏱ speed diagnostics — see the [speed] log lines
    if not spoll:
        message = msg
        # from_deeplink=True is used by the "GET FILES" channel button
        # (/start getfile-<name>), where msg.text is the /start command
        # itself, not the search text — the two guards below exist to
        # ignore stray commands/stickers typed in a live group chat and
        # don't apply here, so they're skipped in that case.
        if not from_deeplink:
            if message.text.startswith("/"): return  # ignore commands
            if re.findall("((^\/|^,|^!|^\.|^[\U0001F600-\U000E007F]).*)", message.text):
                return
        if from_deeplink or len(message.text) < 100:
            search = name
            search = search.lower()
            find = search.split(" ")
            search = ""
            removes = ["upload", "series", "full", "horror", "thriller", "mystery", "print", "file"]
            for x in find:
                if x in removes:
                    continue
                else:
                    search = search + x + " "
            search = re.sub(r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|bro|bruh|broh|helo|that|find|dubbed|link|venum|iruka|pannunga|pannungga|anuppunga|anupunga|anuppungga|anupungga|film|undo|kitti|kitty|tharu|kittumo|kittum|movie|any(one)|with\ssubtitle(s)?)", "", search, flags=re.IGNORECASE)
            search = re.sub(r"\s+", " ", search).strip()
            search = search.replace("-", " ")
            search = search.replace(":", "")
            search = search.replace(".", "")

            # ✅ Season + Episode
            search = re.sub(
                r'(?:season|seas?|s)[.\s_-]*(\d+)[.\s_-]*(?:episode|ep?|e)[.\s_-]*(\d+)',
                lambda m: f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}",
                search, flags=re.IGNORECASE
            )
            # ✅ Season only
            search = re.sub(
                r'(?:season|seas?)\s*(\d+)',
                lambda m: f"S{int(m.group(1)):02d}",
                search, flags=re.IGNORECASE
            )
            # ✅ Episode only (E3 → E03)
            search = re.sub(
                r'\bE(\d{1,2})\b',
                lambda m: f"E{int(m.group(1)):02d}",
                search, flags=re.IGNORECASE
            )

            key = f"{message.chat.id}-{message.id}"
            files, offset, total_results = await get_ranked_page(message.chat.id, key, search, offset=0, max_results=8)
            print(f"[speed] DB search+rank took {time.monotonic() - _t0:.3f}s for query '{search}'")
            settings = await get_settings(message.chat.id)
            if not files:
                if settings["spell_check"]:
                    return await advantage_spell_chok(client, name, msg, reply_msg, ai_search)
                else:
                    await reply_msg.edit_text(f"**⚠️ No File Found For Your Query - {name}**\n**Make Sure Spelling Is Correct.**")
                    await asyncio.sleep(15)
                    try:
                        await reply_msg.delete()
                    except: pass
                    return
        else:
            await asyncio.sleep(15)
            try:
                await reply_msg.delete()
            except: pass
            return
    else:
        message = msg.message.reply_to_message  # msg will be callback query
        key_from_spoll, search, files, offset, total_results = spoll
        settings = await get_settings(message.chat.id)
        await msg.message.delete()
    pre = 'filep' if settings.get('file_secure', False) else 'file'
    # spoll uses its own unique key (set in advantage_spoll_choker) so the
    # corrected-name search never collides with the cache entry the original
    # misspelled search already left behind under the plain message key.
    key = key_from_spoll if spoll else f"{message.chat.id}-{message.id}"
    req = message.from_user.id if message.from_user else 0
    FRESH[key] = search
    SEASON_OWNER[key] = req
    temp.GETALL[key] = files
    # ✅ Speed: the ❌-availability scan needs the FULL matching pool
    # (up to 50,000 files), which is what was adding 1.5-2s to every
    # single search. Don't block the results on it here — fire it off
    # in the background so SEASON_CACHE is warm by the time the user
    # taps into a season/episode/quality screen (where ❌ marks do show).
    asyncio.create_task(get_cached_season_files(message.chat.id, key, search))
    if message.from_user:
        temp.SHORT[message.from_user.id] = message.chat.id
    if settings.get("button", False):
        btn = [
            [
                InlineKeyboardButton(
                    text=format_file_button_text(file), callback_data=f'{pre}#{file["file_id"]}'
                ),
            ]
            for file in files
        ]
        btn.insert(0, [
            InlineKeyboardButton("🍃 ꜱᴇʀɪᴇꜱ ᴄʟɪᴄᴋ 🍃", callback_data=f"seasons#{key}", style=enums.ButtonStyle.PRIMARY),
            build_language_button("all", key, req)[0]
        ])
        btn.insert(1, build_quality_row("all", key, req))
    else:
        btn = [
            [
                InlineKeyboardButton("🍃 ꜱᴇʀɪᴇꜱ ᴄʟɪᴄᴋ 🍃", callback_data=f"seasons#{key}", style=enums.ButtonStyle.PRIMARY),
                build_language_button("all", key, req)[0]
            ],
            build_quality_row("all", key, req)
        ]
    if offset != "":
        try:
            if settings['max_btn']:
                btn.append(
                    [InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="𝐍𝐄𝐗𝐓 ➪",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
                )
            else:
                btn.append(
                    [InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="𝐍𝐄𝐗𝐓 ➪",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
                )
        except KeyError:
            await save_group_settings(message.chat.id, 'max_btn', True)
            btn.append(
                [InlineKeyboardButton("𝐏𝐀𝐆𝐄", callback_data="pages"), InlineKeyboardButton(text=f"1/{math.ceil(int(total_results)/10)}",callback_data="pages", style=enums.ButtonStyle.SUCCESS), InlineKeyboardButton(text="𝐍𝐄𝐗𝐓 ➪",callback_data=f"next_{req}_{key}_{offset}", style=enums.ButtonStyle.PRIMARY)]
            )
    else:
        btn.append(
            [InlineKeyboardButton(text="𝐍𝐎 𝐌𝐎𝐑𝐄 𝐏𝐀𝐆𝐄𝐒 𝐀𝐕𝐀𝐈𝐋𝐀𝐁𝐋𝐄",callback_data="pages")]
        )
    requester_is_premium = bool(message.from_user) and await is_premium_user(message.from_user.id)
    premium_badge = "👑 <u>ᴘʀᴇᴍɪᴜᴍ ᴜsᴇʀ ʀᴇǫᴜᴇsᴛ</u> 👑\n" if requester_is_premium else ""
    cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(curr_time.second+(curr_time.microsecond/1000000)))
    remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
    TEMPLATE = script.IMDB_TEMPLATE_TXT

    # ✅ SPEED: show the plain-text results IMMEDIATELY — this no longer
    # waits on get_poster() (a live IMDb web lookup that was adding the
    # 1-1.7s delay to every single search). If IMDb info is enabled for
    # this group, it's fetched in the BACKGROUND afterward and the message
    # gets upgraded in place with the poster/plot/cast once it's ready —
    # so the user sees results almost instantly either way.
    user_mention = message.from_user.mention if message.from_user else "Anonymous"
    if settings["button"]:
        cap = premium_badge + f"<b>🍃 Tʜᴇ Rᴇꜱᴜʟᴛꜱ Fᴏʀ ➤ {search}\n🍃 Rᴇǫᴜᴇsᴛᴇᴅ Bʏ ➤ {user_mention}\n🍃 ʀᴇsᴜʟᴛ sʜᴏᴡ ɪɴ ➤ {remaining_seconds} sᴇᴄᴏɴᴅs\n🍃 ᴘᴏᴡᴇʀᴇᴅ ʙʏ ➤ {message.chat.title}</b>"
    else:
        cap = premium_badge + f"<b>🍃 Tʜᴇ Rᴇꜱᴜʟᴛꜱ Fᴏʀ ➤ {search}\n🍃 Rᴇǫᴜᴇsᴛᴇᴅ Bʏ ➤ {user_mention}\n🍃 ʀᴇsᴜʟᴛ sʜᴏᴡ ɪɴ ➤ {remaining_seconds} sᴇᴄᴏɴᴅs\n🍃 ᴘᴏᴡᴇʀᴇᴅ ʙʏ ➤ {message.chat.title}</b>"
        cap += "<b><u>🍿 Your Movie Files 👇</u></b>\n"
        for file in files:
            cap += f"<b>➤ <a href='https://telegram.me/{temp.U_NAME}?start=files_{file['file_id']}'>[{get_size(file['file_size'])}] {' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@') and not x.startswith('www.'), normalize_episode_marker(file['file_name']).split()))}</a></b>\n"

    fuk = await reply_msg.edit_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), disable_web_page_preview=True)
    print(f"[speed] fast results shown after {time.monotonic() - _t0:.3f}s total (before any IMDb lookup)")

    async def _auto_delete(sent_msg):
        try:
            if settings['auto_delete']:
                await asyncio.sleep(300)
                await sent_msg.delete()
                await message.delete()
        except KeyError:
            await save_group_settings(message.chat.id, 'auto_delete', True)
            await asyncio.sleep(300)
            await sent_msg.delete()
            await message.delete()
        except Exception:
            pass

    async def _upgrade_with_imdb():
        """Runs AFTER the fast plain-text results are already on screen.
        Fetches the (slow) IMDb poster/plot/cast and, if found, replaces
        the plain-text message with the richer poster+caption version —
        exactly the same content as before, just delivered a moment later
        instead of delaying the whole search."""
        try:
            imdb = await get_poster(search, file=(files[0])['file_name'])
        except Exception as e:
            logger.exception(e)
            imdb = None
        if not imdb:
            await _auto_delete(fuk)
            return

        upgraded_cap = premium_badge + TEMPLATE.format(
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
            search=search, files=files, settings=settings,
            remaining_seconds=remaining_seconds, message=message,
        )
        if message.from_user:
            temp.IMDB_CAP[message.from_user.id] = upgraded_cap
        if not settings["button"]:
            upgraded_cap += "<b>\n\n<u>🍿 Your Movie Files 👇</u></b>\n"
            for file in files:
                upgraded_cap += f"<b>➤ <a href='https://telegram.me/{temp.U_NAME}?start=files_{file['file_id']}'>[{get_size(file['file_size'])}] {' '.join(filter(lambda x: not x.startswith('[') and not x.startswith('@') and not x.startswith('www.'), normalize_episode_marker(file['file_name']).split()))}</a></b>\n"

        if imdb.get('poster'):
            try:
                hehe = await message.reply_photo(photo=imdb.get('poster'), caption=upgraded_cap, reply_markup=InlineKeyboardMarkup(btn))
                await fuk.delete()
                await _auto_delete(hehe)
                return
            except (MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty):
                try:
                    poster = imdb.get('poster').replace('.jpg', "._V1_UX360.jpg")
                    hmm = await message.reply_photo(photo=poster, caption=upgraded_cap, reply_markup=InlineKeyboardMarkup(btn))
                    await fuk.delete()
                    await _auto_delete(hmm)
                    return
                except Exception as e:
                    logger.exception(e)
            except Exception as e:
                logger.exception(e)
        # No usable poster (or sending it failed) — just upgrade the text in place.
        try:
            fek = await fuk.edit_text(text=upgraded_cap, reply_markup=InlineKeyboardMarkup(btn))
            await _auto_delete(fek)
        except Exception as e:
            logger.exception(e)
            await _auto_delete(fuk)

    if settings["imdb"]:
        asyncio.create_task(_upgrade_with_imdb())
    else:
        asyncio.create_task(_auto_delete(fuk))


async def advantage_spell_chok(client, name, msg, reply_msg, vj_search):
    mv_id = msg.id
    mv_rqst = name
    reqstr1 = msg.from_user.id if msg.from_user else 0
    reqstr = await client.get_users(reqstr1)
    settings = await get_settings(msg.chat.id)
    query = re.sub(
        r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|br((o|u)h?)*|^h(e|a)?(l)*(o)*|mal(ayalam)?|t(h)?amil|file|that|find|und(o)*|kit(t(i|y)?)?o(w)?|thar(u)?(o)*w?|kittum(o)*|aya(k)*(um(o)*)?|full\smovie|any(one)|with\ssubtitle(s)?)",
        "", msg.text, flags=re.IGNORECASE)  # plis contribute some common words
    query = query.strip() + " movie"
    try:
        movies = await get_poster(mv_rqst, bulk=True)
    except Exception as e:
        logger.exception(e)
        reqst_gle = mv_rqst.replace(" ", "+")
        button = [[
            InlineKeyboardButton("Gᴏᴏɢʟᴇ", url=f"https://www.google.com/search?q={reqst_gle}", style=enums.ButtonStyle.DANGER)
        ]]
        if NO_RESULTS_MSG:
            await client.send_message(chat_id=LOG_CHANNEL, text=(script.NORSLTS.format(reqstr.id, reqstr.mention, mv_rqst)))
        k = await reply_msg.edit_text(text=script.I_CUDNT.format(mv_rqst), reply_markup=InlineKeyboardMarkup(button))
        await asyncio.sleep(30)
        await k.delete()
        return
    movielist = []
    if not movies:
        reqst_gle = mv_rqst.replace(" ", "+")
        button = [[
            InlineKeyboardButton("Gᴏᴏɢʟᴇ", url=f"https://www.google.com/search?q={reqst_gle}", style=enums.ButtonStyle.DANGER)
        ]]
        if NO_RESULTS_MSG:
            await client.send_message(chat_id=LOG_CHANNEL, text=(script.NORSLTS.format(reqstr.id, reqstr.mention, mv_rqst)))
        k = await reply_msg.edit_text(text=script.I_CUDNT.format(mv_rqst), reply_markup=InlineKeyboardMarkup(button))
        await asyncio.sleep(30)
        await k.delete()
        return
    movielist += [movie.get('title') for movie in movies]
    movielist += [f"{movie.get('title')} {movie.get('year')}" for movie in movies]
    SPELL_CHECK[mv_id] = movielist
    if AI_SPELL_CHECK == True and vj_search == True:
        vj_search_new = False
        vj_ai_msg = await reply_msg.edit_text("<b><i>I Am Trying To Find Your Movie With Your Wrong Spelling.</i></b>")
        movienamelist = []
        movienamelist += [movie.get('title') for movie in movies]
        for techvj in movienamelist:
            try:
                mv_rqst = mv_rqst.capitalize()
            except:
                pass
            if mv_rqst.startswith(techvj[0]):
                await auto_filter(client, techvj, msg, reply_msg, vj_search_new)
                break
        reqst_gle = mv_rqst.replace(" ", "+")
        button = [[
            InlineKeyboardButton("Gᴏᴏɢʟᴇ", url=f"https://www.google.com/search?q={reqst_gle}", style=enums.ButtonStyle.DANGER)
        ]]
        if NO_RESULTS_MSG:
            await client.send_message(chat_id=LOG_CHANNEL, text=(script.NORSLTS.format(reqstr.id, reqstr.mention, mv_rqst)))
        k = await reply_msg.edit_text(text=script.I_CUDNT.format(mv_rqst), reply_markup=InlineKeyboardMarkup(button))
        await asyncio.sleep(30)
        await k.delete()
        return
    else:
        btn = [
            [
                InlineKeyboardButton(
                    text=movie_name.strip(),
                    callback_data=f"spol#{reqstr1}#{k}",
                )
            ]
            for k, movie_name in enumerate(movielist)
        ]
async def advantage_spell_chok(client, name, msg, reply_msg, vj_search):
    mv_id = msg.id
    mv_rqst = name
    reqstr1 = msg.from_user.id if msg.from_user else 0
    if reqstr1:
        reqstr = await client.get_users(reqstr1)
        reqstr_id = reqstr.id
        reqstr_mention = reqstr.mention
    else:
        reqstr_id = 0
        reqstr_mention = "Anonymous"
    settings = await get_settings(msg.chat.id)
    query = re.sub(
        r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|br((o|u)h?)*|^h(e|a)?(l)*(o)*|mal(ayalam)?|t(h)?amil|file|that|find|und(o)*|kit(t(i|y)?)?o(w)?|thar(u)?(o)*w?|kittum(o)*|aya(k)*(um(o)*)?|full\smovie|any(one)|with\ssubtitle(s)?)",
        "", msg.text, flags=re.IGNORECASE)  # plis contribute some common words
    query = query.strip() + " movie"
    async def _build_and_show(labels):
        SPELL_CHECK[mv_id] = labels
        btn = [
            [InlineKeyboardButton(text=t.strip(), callback_data=f"spol#{reqstr1}#{k}")]
            for k, t in enumerate(labels)
        ]
        reqst_gle = urllib.parse.quote_plus(mv_rqst)
        btn.append([InlineKeyboardButton("Gᴏᴏɢʟᴇ", url=f"https://www.google.com/search?q={reqst_gle}", style=enums.ButtonStyle.DANGER)])
        btn.append([InlineKeyboardButton(text="Close", callback_data=f'spol#{reqstr1}#close_spellcheck')])
        spell_check_del = await reply_msg.edit_text(
            text=script.CUDNT_FND.format(mv_rqst),
            reply_markup=InlineKeyboardMarkup(btn)
        )
        # ✅ Suggestion messages always self-clean after 30s, regardless
        # of the group's general auto_delete setting.
        await asyncio.sleep(30)
        await spell_check_del.delete()

    # ✅ 1) Check OUR OWN library FIRST. A match from here is guaranteed
    # to be a real, clickable, in-stock movie — checking it before the
    # external API means a loosely-matched external guess (e.g. TMDB
    # returning some other title for "Durandhar" instead of the real
    # "Dhurandhar" that's actually sitting in our own DB) never shadows
    # the better, correct local match.
    similar = await get_similar_titles(mv_rqst)
    if similar:
        await _build_and_show(similar)
        return

    # 2) Nothing close in our own library — fall back to the external
    # poster/TMDB lookup. Mainly useful to confirm correct spelling even
    # for a movie that isn't uploaded yet, so /request gets the right name.
    try:
        movies = await get_poster(mv_rqst, bulk=True)
    except Exception as e:
        logger.exception(e)
        movies = None

    if movies:
        # Deduped, max 5, "Title (Year)" so each button is a distinct,
        # useful guess instead of the old list which duplicated every
        # title once plain and once with the year tacked on (unbounded).
        seen = set()
        movielist = []
        for movie in movies:
            title = (movie.get('title') or '').strip()
            year  = movie.get('year')
            if not title:
                continue
            label = f"{title} ({year})" if year else title
            if label.lower() in seen:
                continue
            seen.add(label.lower())
            movielist.append(label)
            if len(movielist) >= 5:
                break
        if movielist:
            await _build_and_show(movielist)
            return

    # 3) Truly nothing anywhere (our own DB AND the external API) —
    # Google-only fallback.
    reqst_gle = urllib.parse.quote_plus(mv_rqst)
    button = [[
        InlineKeyboardButton("Gᴏᴏɢʟᴇ", url=f"https://www.google.com/search?q={reqst_gle}", style=enums.ButtonStyle.DANGER)
    ]]
    if NO_RESULTS_MSG:
        await client.send_message(chat_id=LOG_CHANNEL, text=(script.NORSLTS.format(reqstr_id, reqstr_mention, mv_rqst)))
    k = await reply_msg.edit_text(text=script.I_CUDNT.format(mv_rqst), reply_markup=InlineKeyboardMarkup(button))
    await asyncio.sleep(30)
    await k.delete()


async def manual_filters(client, message, text=False):
    settings = await get_settings(message.chat.id)
    group_id = message.chat.id
    name = text or message.text
    reply_id = message.reply_to_message.id if message.reply_to_message else message.id
    keywords = await get_filters(group_id)
    for keyword in reversed(sorted(keywords, key=len)):
        pattern = r"( |^|[^\w])" + re.escape(keyword) + r"( |$|[^\w])"
        if re.search(pattern, name, flags=re.IGNORECASE):
            reply_text, btn, alert, fileid = await find_filter(group_id, keyword)

            if reply_text:
                reply_text = reply_text.replace("\\n", "\n").replace("\\t", "\t")

            if btn is not None:
                try:
                    if fileid == "None":
                        if btn == "[]":
                            joelkb = await client.send_message(
                                group_id, 
                                reply_text, 
                                disable_web_page_preview=True,
                                protect_content=True if settings["file_secure"] else False,
                                reply_to_message_id=reply_id
                            )
                            try:
                                if settings['auto_ffilter']:
                                    ai_search = True
                                    reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                    await auto_filter(client, message.text, message, reply_msg, ai_search)
                                    try:
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                else:
                                    try:
                                        if settings['auto_delete']:
                                            await asyncio.sleep(120)
                                            await joelkb.delete()
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            await asyncio.sleep(120)
                                            await joelkb.delete()
                            except KeyError:
                                grpid = await active_connection(str(message.from_user.id))
                                await save_group_settings(grpid, 'auto_ffilter', True)
                                settings = await get_settings(message.chat.id)
                                if settings['auto_ffilter']:
                                    ai_search = True
                                    reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                    await auto_filter(client, message.text, message, reply_msg, ai_search)

                        else:
                            button = eval(btn)
                            joelkb = await client.send_message(
                                group_id,
                                reply_text,
                                disable_web_page_preview=True,
                                reply_markup=InlineKeyboardMarkup(button),
                                protect_content=True if settings["file_secure"] else False,
                                reply_to_message_id=reply_id
                            )
                            try:
                                if settings['auto_ffilter']:
                                    ai_search = True
                                    reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                    await auto_filter(client, message.text, message, reply_msg, ai_search)
                                    try:
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                else:
                                    try:
                                        if settings['auto_delete']:
                                            await asyncio.sleep(120)
                                            await joelkb.delete()
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            await asyncio.sleep(120)
                                            await joelkb.delete()
                            except KeyError:
                                grpid = await active_connection(str(message.from_user.id))
                                await save_group_settings(grpid, 'auto_ffilter', True)
                                settings = await get_settings(message.chat.id)
                                if settings['auto_ffilter']:
                                    ai_search = True
                                    reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                    await auto_filter(client, message.text, message, reply_msg, ai_search)
                    elif btn == "[]":
                        joelkb = await client.send_cached_media(
                            group_id,
                            fileid,
                            caption=reply_text or "",
                            protect_content=True if settings["file_secure"] else False,
                            reply_to_message_id=reply_id
                        )
                        try:
                            if settings['auto_ffilter']:
                                ai_search = True
                                reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                await auto_filter(client, message.text, message, reply_msg, ai_search)
                                try:
                                    if settings['auto_delete']:
                                        await joelkb.delete()
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_delete', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_delete']:
                                        await joelkb.delete()
                            else:
                                try:
                                    if settings['auto_delete']:
                                        await asyncio.sleep(120)
                                        await joelkb.delete()
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_delete', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_delete']:
                                        await asyncio.sleep(120)
                                        await joelkb.delete()
                        except KeyError:
                            grpid = await active_connection(str(message.from_user.id))
                            await save_group_settings(grpid, 'auto_ffilter', True)
                            settings = await get_settings(message.chat.id)
                            if settings['auto_ffilter']:
                                ai_search = True
                                reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                await auto_filter(client, message.text, message, reply_msg, ai_search)
                    else:
                        button = eval(btn)
                        joelkb = await message.reply_cached_media(
                            fileid,
                            caption=reply_text or "",
                            reply_markup=InlineKeyboardMarkup(button),
                            reply_to_message_id=reply_id
                        )
                        try:
                            if settings['auto_ffilter']:
                                ai_search = True
                                reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                await auto_filter(client, message.text, message, reply_msg, ai_search)
                                try:
                                    if settings['auto_delete']:
                                        await joelkb.delete()
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_delete', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_delete']:
                                        await joelkb.delete()
                            else:
                                try:
                                    if settings['auto_delete']:
                                        await asyncio.sleep(120)
                                        await joelkb.delete()
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_delete', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_delete']:
                                        await asyncio.sleep(120)
                                        await joelkb.delete()
                        except KeyError:
                            grpid = await active_connection(str(message.from_user.id))
                            await save_group_settings(grpid, 'auto_ffilter', True)
                            settings = await get_settings(message.chat.id)
                            if settings['auto_ffilter']:
                                ai_search = True
                                reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                await auto_filter(client, message.text, message, reply_msg, ai_search)

                except Exception as e:
                    logger.exception(e)
                break
    else:
        return False

async def global_filters(client, message, text=False):
    settings = await get_settings(message.chat.id)
    group_id = message.chat.id
    name = text or message.text
    reply_id = message.reply_to_message.id if message.reply_to_message else message.id
    keywords = await get_gfilters('gfilters')
    for keyword in reversed(sorted(keywords, key=len)):
        pattern = r"( |^|[^\w])" + re.escape(keyword) + r"( |$|[^\w])"
        if re.search(pattern, name, flags=re.IGNORECASE):
            reply_text, btn, alert, fileid = await find_gfilter('gfilters', keyword)

            if reply_text:
                reply_text = reply_text.replace("\\n", "\n").replace("\\t", "\t")

            if btn is not None:
                try:
                    if fileid == "None":
                        if btn == "[]":
                            joelkb = await client.send_message(
                                group_id, 
                                reply_text, 
                                disable_web_page_preview=True,
                                reply_to_message_id=reply_id
                            )
                            manual = await manual_filters(client, message)
                            if manual == False:
                                settings = await get_settings(message.chat.id)
                                try:
                                    if settings['auto_ffilter']:
                                        ai_search = True
                                        reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                        await auto_filter(client, message.text, message, reply_msg, ai_search)
                                        try:
                                            if settings['auto_delete']:
                                                await joelkb.delete()
                                        except KeyError:
                                            grpid = await active_connection(str(message.from_user.id))
                                            await save_group_settings(grpid, 'auto_delete', True)
                                            settings = await get_settings(message.chat.id)
                                            if settings['auto_delete']:
                                                await joelkb.delete()
                                    else:
                                        try:
                                            if settings['auto_delete']:
                                                await asyncio.sleep(120)
                                                await joelkb.delete()
                                        except KeyError:
                                            grpid = await active_connection(str(message.from_user.id))
                                            await save_group_settings(grpid, 'auto_delete', True)
                                            settings = await get_settings(message.chat.id)
                                            if settings['auto_delete']:
                                                await asyncio.sleep(120)
                                                await joelkb.delete()
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_ffilter', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_ffilter']:
                                        ai_search = True
                                        reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                        await auto_filter(client, message.text, message, reply_msg, ai_search) 
                            else:
                                try:
                                    if settings['auto_delete']:
                                        await joelkb.delete()
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_delete', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_delete']:
                                        await joelkb.delete()
                            
                        else:
                            button = eval(btn)
                            joelkb = await client.send_message(
                                group_id,
                                reply_text,
                                disable_web_page_preview=True,
                                reply_markup=InlineKeyboardMarkup(button),
                                reply_to_message_id=reply_id
                            )
                            manual = await manual_filters(client, message)
                            if manual == False:
                                settings = await get_settings(message.chat.id)
                                try:
                                    if settings['auto_ffilter']:
                                        ai_search = True
                                        reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                        await auto_filter(client, message.text, message, reply_msg, ai_search)
                                        try:
                                            if settings['auto_delete']:
                                                await joelkb.delete()
                                        except KeyError:
                                            grpid = await active_connection(str(message.from_user.id))
                                            await save_group_settings(grpid, 'auto_delete', True)
                                            settings = await get_settings(message.chat.id)
                                            if settings['auto_delete']:
                                                await joelkb.delete()
                                    else:
                                        try:
                                            if settings['auto_delete']:
                                                await asyncio.sleep(120)
                                                await joelkb.delete()
                                        except KeyError:
                                            grpid = await active_connection(str(message.from_user.id))
                                            await save_group_settings(grpid, 'auto_delete', True)
                                            settings = await get_settings(message.chat.id)
                                            if settings['auto_delete']:
                                                await asyncio.sleep(120)
                                                await joelkb.delete()
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_ffilter', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_ffilter']:
                                        ai_search = True
                                        reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                        await auto_filter(client, message.text, message, reply_msg, ai_search)
                            else:
                                try:
                                    if settings['auto_delete']:
                                        await joelkb.delete()
                                except KeyError:
                                    grpid = await active_connection(str(message.from_user.id))
                                    await save_group_settings(grpid, 'auto_delete', True)
                                    settings = await get_settings(message.chat.id)
                                    if settings['auto_delete']:
                                        await joelkb.delete()

                    elif btn == "[]":
                        joelkb = await client.send_cached_media(
                            group_id,
                            fileid,
                            caption=reply_text or "",
                            reply_to_message_id=reply_id
                        )
                        manual = await manual_filters(client, message)
                        if manual == False:
                            settings = await get_settings(message.chat.id)
                            try:
                                if settings['auto_ffilter']:
                                    ai_search = True
                                    reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                    await auto_filter(client, message.text, message, reply_msg, ai_search)
                                    try:
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                else:
                                    try:
                                        if settings['auto_delete']:
                                            await asyncio.sleep(120)
                                            await joelkb.delete()
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            await asyncio.sleep(120)
                                            await joelkb.delete()
                            except KeyError:
                                grpid = await active_connection(str(message.from_user.id))
                                await save_group_settings(grpid, 'auto_ffilter', True)
                                settings = await get_settings(message.chat.id)
                                if settings['auto_ffilter']:
                                    ai_search = True
                                    reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                    await auto_filter(client, message.text, message, reply_msg, ai_search) 
                        else:
                            try:
                                if settings['auto_delete']:
                                    await joelkb.delete()
                            except KeyError:
                                grpid = await active_connection(str(message.from_user.id))
                                await save_group_settings(grpid, 'auto_delete', True)
                                settings = await get_settings(message.chat.id)
                                if settings['auto_delete']:
                                    await joelkb.delete()

                    else:
                        button = eval(btn)
                        joelkb = await message.reply_cached_media(
                            fileid,
                            caption=reply_text or "",
                            reply_markup=InlineKeyboardMarkup(button),
                            reply_to_message_id=reply_id
                        )
                        manual = await manual_filters(client, message)
                        if manual == False:
                            settings = await get_settings(message.chat.id)
                            try:
                                if settings['auto_ffilter']:
                                    ai_search = True
                                    reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                    await auto_filter(client, message.text, message, reply_msg, ai_search)
                                    try:
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            await joelkb.delete()
                                else:
                                    try:
                                        if settings['auto_delete']:
                                            await asyncio.sleep(60)
                                            await joelkb.delete()
                                    except KeyError:
                                        grpid = await active_connection(str(message.from_user.id))
                                        await save_group_settings(grpid, 'auto_delete', True)
                                        settings = await get_settings(message.chat.id)
                                        if settings['auto_delete']:
                                            await asyncio.sleep(120)
                                            await joelkb.delete()
                            except KeyError:
                                grpid = await active_connection(str(message.from_user.id))
                                await save_group_settings(grpid, 'auto_ffilter', True)
                                settings = await get_settings(message.chat.id)
                                if settings['auto_ffilter']:
                                    ai_search = True
                                    reply_msg = await message.reply_text(f"<b><i>Searching For {message.text} 🔍</i></b>")
                                    await auto_filter(client, message.text, message, reply_msg, ai_search)
                        else:
                            try:
                                if settings['auto_delete']:
                                    await joelkb.delete()
                            except KeyError:
                                grpid = await active_connection(str(message.from_user.id))
                                await save_group_settings(grpid, 'auto_delete', True)
                                settings = await get_settings(message.chat.id)
                                if settings['auto_delete']:
                                    await joelkb.delete()

                                
                except Exception as e:
                    logger.exception(e)
                break
    else:
        return False
