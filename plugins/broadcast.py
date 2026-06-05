# Don't Remove Credit @VJ_Botz
# Subscribe YouTube Channel For Amazing Bot @Tech_VJ
# Ask Doubt on telegram @KingVJ01
import datetime, time, asyncio
from pyrogram import Client, filters
from database.users_chats_db import db
from info import ADMINS
from utils import broadcast_messages, broadcast_messages_group

# State tracking
_waiting_broadcast = {}  # user_id -> "user" or "group"

@Client.on_message(filters.command("broadcast") & filters.user(ADMINS))
async def pm_broadcast(bot, message):
    _waiting_broadcast[message.from_user.id] = "user"
    await message.reply("Now Send Me Your Broadcast Message")

@Client.on_message(filters.command("grp_broadcast") & filters.user(ADMINS))
async def broadcast_group(bot, message):
    _waiting_broadcast[message.from_user.id] = "group"
    await message.reply("Now Send Me Your Broadcast Message")

@Client.on_message(filters.private & filters.user(ADMINS), group=2)
async def handle_broadcast_input(bot, message):
    user_id = message.from_user.id
    if user_id not in _waiting_broadcast:
        return
    if message.text and message.text.startswith("/"):
        return  # ignore commands

    mode = _waiting_broadcast.pop(user_id)
    b_msg = message

    if mode == "user":
        try:
            users = await db.get_all_users()
            sts = await message.reply_text('Broadcasting your messages...')
            start_time = time.time()
            total_users = await db.total_users_count()
            done = 0
            blocked = 0
            deleted = 0
            failed = 0
            success = 0
            async for user in users:
                if 'id' in user:
                    pti, sh = await broadcast_messages(int(user['id']), b_msg)
                    if pti:
                        success += 1
                    elif pti == False:
                        if sh == "Blocked":
                            blocked += 1
                        elif sh == "Deleted":
                            deleted += 1
                        elif sh == "Error":
                            failed += 1
                    done += 1
                    if not done % 20:
                        await sts.edit(f"Broadcast in progress:\n\nTotal Users {total_users}\nCompleted: {done} / {total_users}\nSuccess: {success}\nBlocked: {blocked}\nDeleted: {deleted}")
                else:
                    done += 1
                    failed += 1
                    if not done % 20:
                        await sts.edit(f"Broadcast in progress:\n\nTotal Users {total_users}\nCompleted: {done} / {total_users}\nSuccess: {success}\nBlocked: {blocked}\nDeleted: {deleted}")

            time_taken = datetime.timedelta(seconds=int(time.time() - start_time))
            await sts.edit(f"Broadcast Completed:\nCompleted in {time_taken} seconds.\n\nTotal Users: {total_users}\nCompleted: {done} / {total_users}\nSuccess: {success}\nBlocked: {blocked}\nDeleted: {deleted}")
        except Exception as e:
            print(f"error: {e}")

    elif mode == "group":
        groups = await db.get_all_chats()
        sts = await message.reply_text('Broadcasting your messages To Groups...')
        start_time = time.time()
        total_groups = await db.total_chat_count()
        done = 0
        failed = 0
        success = 0
        async for group in groups:
            pti, sh = await broadcast_messages_group(int(group['id']), b_msg)
            if pti:
                success += 1
            elif sh == "Error":
                failed += 1
            done += 1
            if not done % 20:
                await sts.edit(f"Broadcast in progress:\n\nTotal Groups {total_groups}\nCompleted: {done} / {total_groups}\nSuccess: {success}")
        time_taken = datetime.timedelta(seconds=int(time.time() - start_time))
        await sts.edit(f"Broadcast Completed:\nCompleted in {time_taken} seconds.\n\nTotal Groups {total_groups}\nCompleted: {done} / {total_groups}\nSuccess: {success}")
# Don't Remove Credit Tg - @VJ_Botz
# Subscribe YouTube Channel For Amazing Bot https://youtube.com/@Tech_VJ
# Ask Doubt on telegram @KingVJ01
