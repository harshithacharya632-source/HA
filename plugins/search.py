# plugins/search.py
# Don't Remove Credit @VJ_Botz
# Subscribe YouTube Channel For Amazing Bot @Tech_VJ
# Ask Doubt on telegram @KingVJ01

from pyrogram import Client, filters, enums
from info import LOG_CHANNEL
from datetime import datetime
import logging

# Configure logging
logging.getLogger().setLevel(logging.INFO)

async def log_search(client, user_id, username, query, num_results=0):
    if not LOG_CHANNEL:
        logging.error("LOG_CHANNEL not set in info.py")
        return
    user_mention = f"[{username}](tg://user?id={user_id})" if username else f"User ID: {user_id}"
    log_msg = (
        f"🔍 **Search Query Logged**\n\n"
        f"👤 User: {user_mention}\n"
        f"🔎 Query: `{query}`\n"
        f"📊 Results Found: {num_results}\n"
        f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    try:
        await client.send_message(LOG_CHANNEL, log_msg, parse_mode=enums.ParseMode.MARKDOWN)
        logging.info(f"Logged search query '{query}' by {user_mention}")
    except Exception as e:
        logging.error(f"Failed to log search to LOG_CHANNEL ({LOG_CHANNEL}): {e}")

@Client.on_message(filters.command("search") & filters.private)
async def search_handler(client, message):
    try:
        query = message.text.split(" ", 1)[1] if len(message.text.split()) > 1 else None
        if not query:
            await message.reply("Usage: /search <query>")
            await log_search(client, message.from_user.id, message.from_user.username, "Empty query", 0)
            return
        
        # Replace with your actual search logic (e.g., database or channel search)
        results = []  # Example: await db.search_files(query)
        logging.info(f"User {message.from_user.id} searched for: {query}")
        
        if not results:
            await message.reply("No files found for your query.")
            await log_search(client, message.from_user.id, message.from_user.username, query, 0)
            return
        
        for file in results:
            await message.reply_document(file.file_id, caption=file.file_name)
        
        await log_search(client, message.from_user.id, message.from_user.username, query, len(results))
    except Exception as e:
        logging.error(f"Search handler error: {e}")
        await message.reply("An error occurred while processing your search.")
