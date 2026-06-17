
import traceback
from asyncio import get_running_loop
from io import BytesIO
from deep_translator import GoogleTranslator  # ✅ replaced googletrans
from gtts import gTTS
from pyrogram import Client, filters
from pyrogram.types import Message

def convert(text):
    audio = BytesIO()
    # ✅ detect language using deep_translator
    translated = GoogleTranslator(source='auto', target='en').translate(text)
    lang = GoogleTranslator(source='auto', target='en')._source  # get source lang
    if not lang or lang == 'auto':
        lang = 'en'  # fallback to english
    tts = gTTS(text, lang=lang)
    audio.name = lang + ".mp3"
    tts.write_to_fp(audio)
    return audio

@Client.on_message(filters.command("tts"))
async def text_to_speech(bot, message: Message):
    vj = await bot.ask(chat_id=message.from_user.id, text="Now send me your text.")
    if vj.text:
        m = await vj.reply_text("Processing")
        text = vj.text
        try:
            loop = get_running_loop()
            audio = await loop.run_in_executor(None, convert, text)
            await vj.reply_audio(audio)
            await m.delete()
            audio.close()
        except Exception as e:
            await m.edit(str(e))
            e = traceback.format_exc()
            print(e)
    else:
        await vj.reply_text("Send me only text Buddy.")
