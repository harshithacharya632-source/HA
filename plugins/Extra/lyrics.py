
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton 
from info import CHNL_LNK
import requests 
import os

API = "https://apis.xditya.me/lyrics?song="

@Client.on_message(filters.text & filters.command(["lyrics"]))
async def sng(bot, message):
    vj = await bot.ask(chat_id=message.from_user.id, text="Now send me your song name.")
    if vj.text:
        mee = await vj.reply_text("`Searching 🔎`")
        song = vj.text
        chat_id = message.from_user.id
        rpl = lyrics(song)
        await mee.delete()
        if rpl:
            try:
                await bot.send_message(chat_id, text=rpl, reply_to_message_id=message.id, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ᴜᴘᴅᴀᴛᴇs", url=CHNL_LNK)]]))
            except Exception as e:
                await vj.reply_text(f"I Can't Find A Song With `{song}`", quote=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ᴜᴘᴅᴀᴛᴇs", url=CHNL_LNK)]]))
        else:
            await vj.reply_text(f"I Can't Find Lyrics For `{song}` 😔", quote=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ᴜᴘᴅᴀᴛᴇs", url=CHNL_LNK)]]))
    else:
        await vj.reply_text("Send me only text Buddy.")

def search(song):
    try:
        r = requests.get(API + song, timeout=10)
        return r.json()
    except Exception:
        return {}

def lyrics(song):
    fin = search(song)
    # handle missing lyrics key gracefully
    if not fin or "lyrics" not in fin:
        return None
    text = f'**🎶 Sᴜᴄᴄᴇꜱꜰᴜʟʟy Exᴛʀᴀᴄᴛᴇᴅ Lyɪʀɪᴄꜱ Oꜰ {song}**\n\n'
    text += f'`{fin["lyrics"]}`'
    text += '\n\n\n**Made By Artificial Intelligence**'
    return text
