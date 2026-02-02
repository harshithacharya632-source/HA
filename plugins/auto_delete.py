# Auto delete user messages after 3 minutes (NON-BLOCKING)

import asyncio
from pyrogram import Client, filters

@Client.on_message(filters.private & filters.incoming & ~filters.bot)
async def auto_delete_user_messages(client, message):

    async def delete_later(msg):
        await asyncio.sleep(180)  # 3 minutes
        try:
            await msg.delete()
        except:
            pass

    # run in background so other handlers still work
    asyncio.create_task(delete_later(message))
