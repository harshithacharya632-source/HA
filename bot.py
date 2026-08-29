import sys, glob, importlib, logging, logging.config, pytz, asyncio
from pathlib import Path

# Get logging configurations
logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("cinemagoer").setLevel(logging.ERROR)

from pyrogram import Client, idle
from database.users_chats_db import db
from info import *
from utils import temp
from typing import Union, Optional, AsyncGenerator
from Script import script 
from datetime import date, datetime 
from aiohttp import web
from plugins import web_server
from plugins.clone import restart_bots

from TechVJ.bot import TechVJBot
from TechVJ.util.keepalive import ping_server
from TechVJ.bot.clients import initialize_clients

# ── Second bot: @Goflix_AdminBot ────────────────────────────────────────
# Handles payment screenshots + admin approve/reject buttons (see
# admin_plugins/payment_approval.py). Uses Pyrogram's own built-in plugin
# loader (plugins=dict(root=...)), which — unlike the manual glob loader
# below for TechVJBot — walks subfolders too, and only loads what's in
# admin_plugins/, so nothing here gets double-registered on the main bot.
# If ADMIN_BOT_TOKEN isn't set, this bot just doesn't start; everything
# else keeps working as before.
AdminBot = Client(
    name="GoflixAdminBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=ADMIN_BOT_TOKEN,
    plugins=dict(root="admin_plugins"),
) if ADMIN_BOT_TOKEN else None

ppath = "plugins/*.py"
files = glob.glob(ppath)
TechVJBot.start()
if AdminBot:
    AdminBot.start()
loop = asyncio.get_event_loop()


async def start():
    print('\n')
    print('Initalizing Your Bot')
    bot_info = await TechVJBot.get_me()
    await initialize_clients()
    if AdminBot:
        admin_bot_info = await AdminBot.get_me()
        print(f"Goflix_AdminBot started as @{admin_bot_info.username}")
    for name in files:
        with open(name) as a:
            patt = Path(a.name)
            plugin_name = patt.stem.replace(".py", "")
            plugins_dir = Path(f"plugins/{plugin_name}.py")
            import_path = "plugins.{}".format(plugin_name)
            spec = importlib.util.spec_from_file_location(import_path, plugins_dir)
            load = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(load)
            sys.modules["plugins." + plugin_name] = load
            print("Tech VJ Imported => " + plugin_name)
    #if ON_HEROKU:
        #asyncio.create_task(ping_server())
    b_users, b_chats = await db.get_banned()
    temp.BANNED_USERS = b_users
    temp.BANNED_CHATS = b_chats
    me = await TechVJBot.get_me()
    temp.BOT = TechVJBot
    temp.ME = me.id
    temp.U_NAME = me.username
    temp.B_NAME = me.first_name
    temp.B_LINK = f"https://t.me/{me.username}"
    logging.info(script.LOGO)
    tz = pytz.timezone('Asia/Kolkata')
    today = date.today()
    now = datetime.now(tz)
    time = now.strftime("%H:%M:%S %p")
    try:
        await TechVJBot.send_message(chat_id=LOG_CHANNEL, text=script.RESTART_TXT.format(today, time))
    except:
        print("Make Your Bot Admin In Log Channel With Full Rights")
    for ch in CHANNELS:
        try:
            k = await TechVJBot.send_message(chat_id=ch, text="**Bot Restarted**")
            await k.delete()
        except:
            print("Make Your Bot Admin In File Channels With Full Rights")
    try:
        k = await TechVJBot.send_message(chat_id=AUTH_CHANNEL, text="**Bot Restarted**")
        await k.delete()
    except:
        print("Make Your Bot Admin In Force Subscribe Channel With Full Rights")
    if CLONE_MODE == True:
        print("Restarting All Clone Bots.......")
        await restart_bots()
        print("Restarted All Clone Bots.")

    # Background task: hourly check for premium users expiring within 24h
    # (sends a one-time reminder) and users whose premium expired within
    # the last 24h (sends a one-time thank-you + "buy again" message).
    from plugins.commands import premium_expiry_notifier
    asyncio.create_task(premium_expiry_notifier(TechVJBot))
    print("Started premium expiry notifier background task.")

    app = web.AppRunner(await web_server())
    await app.setup()
    bind_address = "0.0.0.0"
    await web.TCPSite(app, bind_address, PORT).start()
    await idle()


if __name__ == '__main__':
    try:
        loop.run_until_complete(start())
    except KeyboardInterrupt:
        logging.info('Service Stopped Bye 👋')
