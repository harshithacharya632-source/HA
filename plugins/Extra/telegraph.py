
import os
import requests
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def upload_image_requests(image_path):
    # Try Telegraph first (best for Telegram bots)
    try:
        with open(image_path, 'rb') as file:
            response = requests.post(
                "https://telegra.ph/upload",
                files={"file": file},
                timeout=30
            )
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and "src" in data[0]:
                    return "https://telegra.ph" + data[0]["src"]
    except Exception as e:
        print(f"Telegraph failed: {e}")

    # Fallback: envs.sh
    try:
        with open(image_path, 'rb') as file:
            response = requests.post(
                "https://envs.sh",
                files={"file": file},
                timeout=30
            )
            if response.status_code == 200:
                return response.text.strip()
    except Exception as e:
        print(f"envs.sh failed: {e}")

    # Fallback: 0x0.st
    try:
        with open(image_path, 'rb') as file:
            response = requests.post(
                "https://0x0.st",
                files={"file": file},
                timeout=30
            )
            if response.status_code == 200:
                return response.text.strip()
    except Exception as e:
        print(f"0x0.st failed: {e}")

    return None


@Client.on_message(filters.command("telegraph") & filters.private)
async def telegraph_upload(bot, update):
    t_msg = await bot.ask(
        chat_id=update.from_user.id,
        text="Now Send Me Your Photo Or Video Under 5MB To Get Media Link."
    )
    if not t_msg.media:
        return await update.reply_text("**Only Media Supported.**")

    uploading_message = await update.reply_text("<b>ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ...</b>")
    path = await t_msg.download()

    await uploading_message.edit_text("<b>ᴜᴘʟᴏᴀᴅɪɴɢ...</b>")
    try:
        image_url = upload_image_requests(path)
    except Exception as error:
        await uploading_message.edit_text(f"**Upload failed:** `{error}`")
        return
    finally:
        # Always delete the temp file
        if os.path.exists(path):
            os.remove(path)

    if not image_url:
        await uploading_message.edit_text("❌ **All upload servers failed. Try again later.**")
        return

    await uploading_message.edit_text(
        text=f"<b>✅ Link:</b>\n\n<code>{image_url}</code>",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(text="Open Link", url=image_url),
            InlineKeyboardButton(text="Share Link", url=f"https://telegram.me/share/url?url={image_url}")
        ], [
            InlineKeyboardButton(text="✗ Close ✗", callback_data="close")
        ]])
    )
