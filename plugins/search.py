# plugins/search.py
# Don't Remove Credit @VJ_Botz
# Subscribe YouTube Channel For Amazing Bot @Tech_VJ
# Ask Doubt on telegram @KingVJ01

from pyrogram import Client, filters, enums
from info import LOG_CHANNEL, CHANNELS
from datetime import datetime
import logging

# Configure logging
logging.getLogger().setLevel(logging.INFO)

async def log_search(client, user, query, results=None):
    """Log search queries to LOG_CHANNEL."""
    if not LOG_CHANNEL:
        logging.error("LOG_CHANNEL not set in info.py")
        return

    # Handle user mention safely
    user_mention = f"[{user.first_name}](tg://user?id={user.id})"
    results_count = len(results) if results else 0

    log_msg = (
        f"🔍 **Search Query Logged**\n\n"
        f"👤 User: {user_mention} (`{user.id}`)\n"
        f"🔎 Query: `{query}`\n"
        f"📊 Results Found: {results_count}\n"
        f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    # If results exist, log filenames (max 10 for safety)
    if results:
        file_list = "\n".join(
            [f"• {doc.file_name}" for doc in results[:10] if doc.file_name]
        )
        log_msg += f"\n📂 Files:\n{file_list}"

    try:
        await client.send_message(LOG_CHANNEL, log_msg, parse_mode=enums.ParseMode.MARKDOWN)
        logging.info(f"Logged search query '{query}' by {user.id} to LOG_CHANNEL ({LOG_CHANNEL})")
    except Exception as e:
        logging.error(f"Failed to log search to LOG_CHANNEL ({LOG_CHANNEL}): {e}")

@Client.on_message(filters.command("search") & filters.private)
async def search_handler(client, message):
    try:
        # Extract query
        query = message.text.split(" ", 1)[1] if len(message.text.split()) > 1 else None
        if not query:
            await message.reply("Usage: /search <query>")
            await log_search(client, message.from_user, "Empty query", [])
            return

        # Search files in configured CHANNELS
        results = []
        for channel in CHANNELS:
            try:
                async for msg in client.search_messages(channel, query, limit=50):
                    if msg.document:
                        results.append(msg.document)
            except Exception as e:
                logging.error(f"Failed to search channel {channel}: {e}")

        logging.info(f"User {message.from_user.id} searched for: {query}")

        if not results:
            await message.reply("❌ No files found for your query.")
            await log_search(client, message.from_user, query, [])
            return

        # Send files to user
        for file in results:
            caption = file.file_name or "File"
            await message.reply_document(file.file_id, caption=caption)

        # Log search with results
        await log_search(client, message.from_user, query, results)

    except Exception as e:
        logging.error(f"Search handler error: {e}")
        await message.reply("⚠️ An error occurred while processing your search.")
