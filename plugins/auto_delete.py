# Auto delete user messages after 3 minutes
import asyncio
from pyrogram import Client, filters

@Client.on_message(
    filters.private & ~filters.bot
)
async def auto_delete_user_messages(client, message):
    await asyncio.sleep(180)  # 3 minutes
    try:
        await message.delete()
    except:
        pass
