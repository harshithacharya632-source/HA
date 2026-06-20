
import os
import json
import requests
from pyrogram import Client, filters

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/88.0.4324.104 Safari/537.36",
    "content-type": "application/json",
}

async def p_paste(message, extension=None):
    siteurl = "https://pasty.lus.pm/api/v1/pastes"
    data = {"content": message}
    try:
        response = requests.post(url=siteurl, data=json.dumps(data), headers=headers)
    except Exception as e:
        return {"error": str(e)}
    if response.ok:
        response = response.json()
        purl = (
            f"https://pasty.lus.pm/{response['id']}.{extension}"
            if extension
            else f"https://pasty.lus.pm/{response['id']}.txt"
        )
        return {
            "url": purl,
            "raw": f"https://pasty.lus.pm/{response['id']}/raw",
            "bin": "Pasty",
        }
    return {"error": "Unable to reach pasty.lus.pm"}

@Client.on_message(filters.command(["tgpaste", "pasty", "paste"]))
async def pasty(client, message):
    pablo = await message.reply_text("`Please wait...`")
    message_s = None

    if len(message.command) > 1:
        # text after command: /paste some text here
        message_s = message.text.split(" ", 1)[1]
    elif message.reply_to_message:
        if message.reply_to_message.text:
            message_s = message.reply_to_message.text
        else:
            # it's a file
            try:
                file = await message.reply_to_message.download()
                message_s = open(file, "r").read()
                os.remove(file)
            except Exception:
                await pablo.edit("❌ Could not read the file. Only text files are supported.")
                return
    
    if not message_s:
        await pablo.edit("❌ Please provide text or reply to a message!\n\n**Usage:** `/paste your text` or reply to a message with `/paste`")
        return

    x = await p_paste(message_s, "py")
    
    if "error" in x:
        await pablo.edit(f"❌ Failed to paste: `{x['error']}`")
        return

    p_link = x["url"]
    p_raw = x["raw"]
    pasted = f"**✅ Successfully Pasted!**\n\n**Link:** [Click here]({p_link})\n**Raw:** [Click here]({p_raw})"
    await pablo.edit(pasted, disable_web_page_preview=True)
