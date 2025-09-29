import re
import math
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from info import LOG_CHANNEL
from TechVJ.bot import TechVJBot

# Example search handler (your actual code may vary)
@Client.on_message(filters.text & filters.private)
async def search_handler(bot: Client, message: Message):
    query = message.text.strip()
    user = message.from_user

    if not query:
        return

    # --- Perform your file search logic here ---
    # Example: files = await db.search_files(query)
    files = ["Movie1.mkv", "Movie2.mp4"]  # mock results

    # Send results back to user
    if files:
        btn = [[InlineKeyboardButton("✅ Download", url="https://example.com")]]
        await message.reply_text(
            f"🔎 Results for: **{query}**\n\n" + "\n".join(files),
            reply_markup=InlineKeyboardMarkup(btn)
        )
    else:
        await message.reply_text("❌ No results found")

    # --- LOG USER SEARCH ---
    try:
        if LOG_CHANNEL:
            await TechVJBot.send_message(
                chat_id=LOG_CHANNEL,
                text=(
                    f"📩 **User Search Log**\n"
                    f"👤 User: {user.mention} (`{user.id}`)\n"
                    f"🔎 Query: `{query}`"
                )
            )
            logging.info(f"Logged search for {query} by {user.id}")
    except Exception as e:
        logging.error(f"Failed to log search: {e}")
