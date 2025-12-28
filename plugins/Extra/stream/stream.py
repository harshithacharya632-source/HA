from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from info import STREAM_MODE, URL, LOG_CHANNEL
from urllib.parse import quote_plus
from TechVJ.util.file_properties import get_name, get_hash, get_media_file_size
from TechVJ.util.human_readable import humanbytes
import humanize

@Client.on_message(filters.private & filters.command("stream"))
async def stream_start(client, message):
    if not STREAM_MODE:
        return

    msg = await client.ask(
        message.chat.id,
        "**Now send me your file/video to get stream and download link**"
    )

    if msg.media not in [
        enums.MessageMediaType.VIDEO,
        enums.MessageMediaType.DOCUMENT
    ]:
        return await message.reply("**Please send me supported media.**")

    file = getattr(msg, msg.media.value)
    file_id = file.file_id
    user_id = message.from_user.id
    username = message.from_user.mention

    # Send media to log channel
    log_msg = await client.send_cached_media(
        chat_id=LOG_CHANNEL,
        file_id=file_id,
    )

    file_name = get_name(log_msg)
    quoted_name = quote_plus(file_name)
    file_hash = get_hash(log_msg)
    msg_id = log_msg.id

    # ✅ NEW STREAMING SERVER URLS
    stream = f"{URL}/watch/{msg_id}/{quoted_name}?hash={file_hash}"
    download = f"{URL}/download/{msg_id}/{quoted_name}?hash={file_hash}"

    # Log message
    await log_msg.reply_text(
        text=(
            f"•• ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ\n"
            f"•• ᴜꜱᴇʀ : {username} ({user_id})\n\n"
            f"•• ꜰɪʟᴇ : {file_name}"
        ),
        quote=True,
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("🚀 Fast Download 🚀", url=download),
                InlineKeyboardButton("🖥️ Watch Online 🖥️", url=stream)
            ]]
        )
    )

    # User buttons
    reply_markup = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🖥 Stream", url=stream),
            InlineKeyboardButton("📥 Download", url=download)
        ]]
    )

    msg_text = (
        "<i><u>𝗬𝗼𝘂𝗿 𝗟𝗶𝗻𝗸 𝗚𝗲𝗻𝗲𝗿𝗮𝘁𝗲𝗱 !</u></i>\n\n"
        "<b>📂 Fɪʟᴇ ɴᴀᴍᴇ :</b> <i>{}</i>\n\n"
        "<b>📦 Fɪʟᴇ ꜱɪᴢᴇ :</b> <i>{}</i>\n\n"
        "<b>📥 Dᴏᴡɴʟᴏᴀᴅ :</b> <i>{}</i>\n\n"
        "<b>🖥 Wᴀᴛᴄʜ :</b> <i>{}</i>\n\n"
        "<b>🚸 Nᴏᴛᴇ :</b> <i>Link won't expire until deleted</i>"
    )

    await message.reply_text(
        text=msg_text.format(
            file_name,
            humanbytes(get_media_file_size(msg)),
            download,
            stream
        ),
        quote=True,
        disable_web_page_preview=True,
        reply_markup=reply_markup
    )
