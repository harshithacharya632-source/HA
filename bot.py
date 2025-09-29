# Don't Remove Credit @VJ_Botz
# Subscribe YouTube Channel For Amazing Bot @Tech_VJ
# Ask Doubt on telegram @KingVJ01

# Clone Code Credit: YT - @Tech_VJ / TG - @VJ_Bots / GitHub - @VJBots

import sys, glob, importlib, logging, logging.config, pytz, asyncio
from pathlib import Path
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

# Configure logging
logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("cinemagoer").setLevel(logging.ERROR)

# Load plugin files
ppath = "plugins/*.py"
files = glob.glob(ppath)
TechVJBot.start()
loop = asyncio.get_event_loop()

async def start():
    print('\n')
    print('Initializing Your Bot')
    try:
        bot_info = await TechVJBot.get_me()
        logging.info(f"Bot Started: {bot_info.username} (ID: {bot_info.id})")
        await initialize_clients()
        
        # Load plugins
        for name in files:
            try:
                with open(name) as a:
                    patt = Path(a.name)
                    plugin_name = patt.stem.replace(".py", "")
                    plugins_dir = Path(f"plugins/{plugin_name}.py")
                    import_path = "plugins.{}".format(plugin_name)
                    spec = importlib.util.spec_from_file_location(import_path, plugins_dir)
                    load = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(load)
                    sys.modules["plugins." + plugin_name] = load
                    logging.info(f"Imported Plugin: {plugin_name}")
            except Exception as e:
                logging.error(f"Failed to load plugin {name}: {e}")
        
        # Heroku keepalive
        if ON_HEROKU:
            asyncio.create_task(ping_server())
        
        # Load banned users/chats
        b_users, b_chats = await db.get_banned()
        temp.BANNED_USERS = b_users
        temp.BANNED_CHATS = b_chats
        
        # Set bot info
        me = await TechVJBot.get_me()
        temp.BOT = TechVJBot
        temp.ME = me.id
        temp.U_NAME = me.username
        temp.B_NAME = me.first_name
        logging.info(script.LOGO)
        
        # Log restart to LOG_CHANNEL
        tz = pytz.timezone('Asia/Kolkata')
        today = date.today()
        now = datetime.now(tz)
        time = now.strftime("%H:%M:%S %p")
        try:
            await TechVJBot.send_message(
                chat_id=LOG_CHANNEL,
                text=script.RESTART_TXT.format(today, time),
                parse_mode="Markdown"
            )
            logging.info(f"Sent restart message to LOG_CHANNEL ({LOG_CHANNEL})")
        except Exception as e:
            logging.error(f"Failed to send restart message to LOG_CHANNEL: {e}")
            print("Make Your Bot Admin In Log Channel With Full Rights")
        
        # Test file channels
        for ch in CHANNELS:
            try:
                k = await TechVJBot.send_message(chat_id=ch, text="**Bot Restarted**")
                await k.delete()
                logging.info(f"Verified access to channel {ch}")
            except Exception as e:
                logging.error(f"Failed to access channel {ch}: {e}")
                print("Make Your Bot Admin In File Channels With Full Rights")
        
        # Test force-sub channel
        try:
            k = await TechVJBot.send_message(chat_id=AUTH_CHANNEL, text="**Bot Restarted**")
            await k.delete()
            logging.info(f"Verified access to AUTH_CHANNEL {AUTH_CHANNEL}")
        except Exception as e:
            logging.error(f"Failed to access AUTH_CHANNEL: {e}")
            print("Make Your Bot Admin In Force Subscribe Channel With Full Rights")
        
        # Restart clone bots if enabled
        if CLONE_MODE:
            print("Restarting All Clone Bots...")
            try:
                await restart_bots()
                print("Restarted All Clone Bots.")
            except Exception as e:
                logging.error(f"Failed to restart clone bots: {e}")
        
        # Start web server
        app = web.AppRunner(await web_server())
        await app.setup()
        bind_address = "0.0.0.0"
        await web.TCPSite(app, bind_address, PORT).start()
        logging.info(f"Web server started on {bind_address}:{PORT}")
        
        await idle()
    except Exception as e:
        logging.error(f"Startup failed: {e}")
        raise

if __name__ == '__main__':
    try:
        loop.run_until_complete(start())
    except KeyboardInterrupt:
        logging.info('Service Stopped Bye 👋')
    except Exception as e:
        logging.error(f"Main loop error: {e}")
