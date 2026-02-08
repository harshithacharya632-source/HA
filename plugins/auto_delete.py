# Auto delete user messages after 3 minutes (PRIVATE + GROUP)

import asyncio
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus

@Client.on_message(
    filters.incoming & ~filters.bot,
    group=100
)
async def auto_delete_user_messages(client, message):

    # ❌ Skip admins in groups (Telegram rule)
    if message.chat.type in ["group", "supergroup"]:
        try:
            member = await client.get_chat_member(
                message.chat.id,
                message.from_user.id
            )
            if member.status in (
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER
            ):
                return
        except:
            return

    async def delete_later(msg):
        await asyncio.sleep(200)  # 3 minutes
        try:
            await msg.delete()
        except:
            pass

    asyncio.create_task(delete_later(message))
