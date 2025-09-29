import re
import math
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from pyrogram.errors import FloodWait, RPCError
from info import LOG_CHANNEL
from TechVJ.bot import TechVJBot
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

@Client.on_message(filters.text & filters.private)
async def search_handler(bot: Client, message: Message):
    query = message.text.strip()
    user = message.from_user

    # Validate query
    if not query:
        await message.reply_text("⚠️ Please provide a valid search query.")
        return

    # Sanitize query to prevent Markdown issues
    sanitized_query = re.sub(r'[_*[\]()~`>#+-=|{}.!]', r'\\\g<0>', query)

    # --- Perform your file search logic here ---
    # Example: files = await db.search_files(query)
    files = ["Movie1.mkv", "Movie2.mp4"]  # Mock results for testing

    # Send results back to user
    if files:
        btn = [[InlineKeyboardButton("✅ Download", url="https://example.com")]]  # Replace with dynamic URL in production
        await message.reply_text(
            f"🔎 Results for: **{sanitized_query}**\n\n" + "\n".join(files),
            reply_markup=InlineKeyboardMarkup(btn),
            disable_web_page_preview=True
        )
    else:
        await message.reply_text(f"❌ No results found for: **{sanitized_query}**")
        # Log failed searches
        logging.info(f"No results found for query '{query}' by user {user.id}")

    # --- LOG USER SEARCH ---
    if LOG_CHANNEL:
        try:
            await bot.send_message(
                chat_id=LOG_CHANNEL,
                text=(
                    f"📩 **User Search Log**\n"
                    f"🕒 Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"👤 User: {user.mention} (`{user.id}`)\n"
                    f"🔎 Query: `{sanitized_query}`\n"
                    f"📈 Results: {len(files)} file(s) found"
                ),
                disable_notification=True
            )
            logging.info(f"Logged search for '{query}' by user {user.id}")
        except FloodWait as fw:
            logging.warning(f"FloodWait error: Waiting for {fw.value} seconds")
            time.sleep(fw.value)
        except RPCError as e:
            logging.error(f"Failed to log search to channel: {e}")
        except Exception as e:
            logging.error(f"Unexpected error while logging search: {e}")
