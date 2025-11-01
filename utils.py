# utils.py
# ===============================
# Utilities for your bot
# ===============================

# Don't Remove Credit @VJ_Botz
# Subscribe YouTube Channel For Amazing Bot @Tech_VJ
# Ask Doubt on telegram @KingVJ01

"""
Updated util module
- Added ShrinkMe shortener support (uses provided token by user).
- Kept backward compatibility with Bitly, Shortzy, Shareus.
- Kept verification helpers and token handling.
- Minor robustness and logging improvements.
"""

import os
import logging
import asyncio
import re
import random
import pytz
import aiohttp
import string
import json
import requests
from info import *
from imdb import Cinemagoer
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram import enums
from pyrogram.errors import *
from typing import Union, List
from Script import script
from datetime import datetime, date
from database.users_chats_db import db
from database.join_reqs import JoinReqs
from bs4 import BeautifulSoup
from shortzy import Shortzy

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
join_db = JoinReqs
BTN_URL_REGEX = re.compile(r"(\[([^\[]+?)\]\((buttonurl|buttonalert):(?:/{0,2})(.+?)(:same)?\))")

imdb = Cinemagoer()
TOKENS = {}
VERIFIED = {}
BANNED = {}
SECOND_SHORTENER = {}
SMART_OPEN = '“'
SMART_CLOSE = '”'
START_CHAR = ('\'', '"', SMART_OPEN)

# BITLY config (read from env)
BITLY_API_URL = "https://api-ssl.bitly.com/v4/shorten"
BITLY_TOKEN = os.environ.get("BITLY_TOKEN")  # Put your token in env (recommended)

# ShrinkMe config
SHRINKME_TOKEN = os.environ.get("SHRINKME_TOKEN") or "175a9da92b79af75bf3120f9feee208af8905620"

# temp db for banned
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

# -------------------------------
# ShrinkMe GET-based shortener
# -------------------------------
async def create_shrinkme_shortlink(long_url: str, alias: str = None) -> str:
    """
    Shorten URL using ShrinkMe API (GET request, recommended by ShrinkMe docs).
    Returns shortened URL as plain text.
    """
    if not SHRINKME_TOKEN:
        logger.debug("[ShrinkMe] SHRINKME_TOKEN not set - skipping ShrinkMe.")
        return long_url

    api_url = f"https://shrinkme.io/api?api={SHRINKME_TOKEN}&url={long_url}"
    if alias:
        api_url += f"&alias={alias}"
    api_url += "&format=text"  # returns plain short URL

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=10) as resp:
                text = await resp.text()
                text = text.strip()
                if resp.status == 200 and text.startswith("http"):
                    return text
                else:
                    logger.error(f"[ShrinkMe] Unexpected response: {text} status={resp.status}")
                    return long_url
    except Exception as e:
        logger.exception(f"[ShrinkMe Exception] {e}")
        return long_url

# -------------------------------
# Bitly Shortener
# -------------------------------
async def create_bitly_shortlink(long_url: str) -> str:
    if not BITLY_TOKEN:
        logger.debug("[Bitly] BITLY_TOKEN not set - skipping Bitly.")
        return long_url
    headers = {"Authorization": f"Bearer {BITLY_TOKEN}", "Content-Type": "application/json"}
    payload = {"long_url": long_url}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(BITLY_API_URL, headers=headers, json=payload, timeout=10) as resp:
                try:
                    data = await resp.json()
                except Exception:
                    text = await resp.text()
                    logger.error(f"[Bitly] non-json response: {text}")
                    return long_url
                if resp.status == 200 and "link" in data:
                    return data["link"]
                else:
                    logger.error(f"[Bitly Error] status={resp.status} data={data}")
                    return long_url
    except Exception as e:
        logger.exception(f"[Bitly Exception] {e}")
        return long_url

# -------------------------------
# Shareus Shortener
# -------------------------------
async def get_clone_shortlink(link, url, api):
    shortzy = Shortzy(api_key=api, base_site=url)
    return await shortzy.convert(link)

# -------------------------------
# Unified get_shortlink function
# -------------------------------
async def get_shortlink(chat_id, link):
    """
    Generate a shortlink for the given link.
    Supports Bitly, ShrinkMe, Shareus, and Shortzy-based services.
    """
    settings = await get_settings(chat_id)
    URL = settings.get("shortlink", SHORTLINK_URL) if settings else SHORTLINK_URL
    API = settings.get("shortlink_api", SHORTLINK_API) if settings else SHORTLINK_API

    if URL and any(URL.startswith(x) for x in ["shorturllink", "terabox.in", "urlshorten.in"]):
        URL = SHORTLINK_URL
        API = SHORTLINK_API

    use_bitly = (URL and "bitly" in URL.lower()) or (BITLY_TOKEN and "bitly" in str(SHORTLINK_URL).lower())
    use_shrinkme = (URL and "shrinkme" in str(URL).lower()) or (SHRINKME_TOKEN and "shrinkme" in str(SHORTLINK_URL).lower())

    if use_bitly:
        return await create_bitly_shortlink(link)
    if use_shrinkme:
        return await create_shrinkme_shortlink(link)
    if URL == "api.shareus.io":
        api_url = f'https://{URL}/easy_api'
        params = {"key": API, "link": link}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, params=params, raise_for_status=True, ssl=False) as response:
                    return await response.text()
        except Exception as e:
            logger.error(e)
            return link
    try:
        shortzy = Shortzy(api_key=API, base_site=URL)
        return await shortzy.convert(link)
    except Exception as e:
        logger.error(f"[Shortzy Error] {e}")
        return link

# -------------------------------
# Verification Shortlink helpers
# -------------------------------
async def get_verify_shorted_link(link, url, api):
    if url and "shrinkme" in str(url).lower():
        return await create_shrinkme_shortlink(link)
    if url == "api.shareus.io":
        api_url = f'https://{url}/easy_api'
        params = {"key": api, "link": link}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, params=params, raise_for_status=True, ssl=False) as response:
                    return await response.text()
        except Exception as e:
            logger.error(e)
            return link
    try:
        shortzy = Shortzy(api_key=api, base_site=url)
        return await shortzy.convert(link)
    except Exception as e:
        logger.error(e)
        return link

async def get_token(bot, userid, link):
    user = await bot.get_users(userid)
    if not await db.is_user_exist(user.id):
        await db.add_user(user.id, user.first_name)
        await bot.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(user.id, user.mention))
    token = ''.join(random.choices(string.ascii_letters + string.digits, k=7))
    TOKENS[user.id] = {token: False}
    link = f"{link}verify-{user.id}-{token}"
    shortened_verify_url = await get_verify_shorted_link(link, VERIFY_SHORTLINK_URL, VERIFY_SHORTLINK_API)
    if VERIFY_SECOND_SHORTNER:
        return await get_verify_shorted_link(shortened_verify_url, VERIFY_SND_SHORTLINK_URL, VERIFY_SND_SHORTLINK_API)
    return shortened_verify_url

async def verify_user(bot, userid, token):
    TOKENS[userid] = {token: True}
    VERIFIED[userid] = str(date.today())

async def check_verification(bot, userid):
    today = date.today()
    if userid in VERIFIED:
        y, m, d = map(int, VERIFIED[userid].split('-'))
        return not (date(y, m, d) < today)
    return False

# -------------------------------
# Utility functions
# -------------------------------
def get_size(size):
    units = ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB"]
    size = float(size)
    i = 0
    while size >= 1024.0 and i < len(units):
        i += 1
        size /= 1024.0
    return "%.2f %s" % (size, units[i])

def humanbytes(size):
    power = 2**10
    n = 0
    Dic_powerN = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + Dic_powerN[n] + 'B'

# -------------------------------
# Force shortener helper (diagnostic)
# -------------------------------
async def force_shorten(link, provider='shrinkme', api_key=None, base_url=None):
    provider = provider.lower()
    if provider == 'bitly':
        return await create_bitly_shortlink(link)
    if provider == 'shrinkme':
        return await create_shrinkme_shortlink(link)
    if provider == 'shareus' and base_url and api_key:
        api_url = f'https://{base_url}/easy_api'
        params = {"key": api_key, "link": link}
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, params=params, ssl=False) as response:
                return await response.text()
    try:
        shortzy = Shortzy(api_key=api_key or SHORTLINK_API, base_site=base_url or SHORTLINK_URL)
        return await shortzy.convert(link)
    except Exception as e:
        logger.error(e)
        return link

async def check_shortener_health():
    res = {}
    test_url = "https://example.com/"
    try:
        if BITLY_TOKEN:
            res['bitly'] = await create_bitly_shortlink(test_url)
    except Exception as e:
        res['bitly'] = str(e)
    try:
        if SHRINKME_TOKEN:
            res['shrinkme'] = await create_shrinkme_shortlink(test_url)
    except Exception as e:
        res['shrinkme'] = str(e)
    try:
        if SHORTLINK_API and SHORTLINK_URL:
            shortzy = Shortzy(api_key=SHORTLINK_API, base_site=SHORTLINK_URL)
            res['shortzy'] = await shortzy.convert(test_url)
    except Exception as e:
        res['shortzy'] = str(e)
    return res
