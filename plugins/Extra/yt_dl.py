
from __future__ import unicode_literals

import os, requests, asyncio, math, time, wget, json
from pyrogram import filters, Client
from pyrogram.types import Message
from info import CHNL_LNK
from youtube_search import YoutubeSearch
from youtubesearchpython import SearchVideos
from yt_dlp import YoutubeDL
import logging

logger = logging.getLogger(__name__)

# YouTube cookies to bypass bot detection
# This uses the web client instead of mobile client
YOUTUBE_COOKIES = [
    {
        "name": "CONSENT",
        "value": "YES+1",
        "domain": ".youtube.com",
        "path": "/",
    }
]

def get_ydl_opts(with_audio=True):
    """Get optimized yt-dlp options for YouTube downloads"""
    opts = {
        "noplaylist": True,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "source_address": "0.0.0.0",
        "quiet": False,
        "no_warnings": False,
        "socket_timeout": 30,
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "android"],
                "extract_flat": ["in_playlist"],
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        }
    }
    
    if with_audio:
        opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    else:
        opts["format"] = "best[ext=mp4]/best"
    
    return opts


@Client.on_message(filters.command(['song', 'mp3']) & filters.private)
async def song(client, message):
    user_id = message.from_user.id 
    user_name = message.from_user.first_name 
    rpk = "["+user_name+"](tg://user?id="+str(user_id)+")"
    query = ''
    for i in message.command[1:]:
        query += ' ' + str(i)
    print(query)
    m = await message.reply(f"**🔍 Searching for your song...!\n {query}**")
    
    try:
        results = YoutubeSearch(query, max_results=1).to_dict()
        
        # Error handling: Check if results list is empty
        if not results or len(results) == 0:
            return await m.edit("❌ **No results found for:** `" + query + "`\n\n**Try:** `/song vaa vaathi song`")
        
        link = f"https://youtube.com{results[0]['url_suffix']}"
        title = results[0]["title"][:40]       
        thumbnail = results[0]["thumbnails"][0]
        thumb_name = f'thumb{title}.jpg'
        
        try:
            thumb = requests.get(thumbnail, allow_redirects=True, timeout=10)
            open(thumb_name, 'wb').write(thumb.content)
        except Exception as e:
            print(f"Thumbnail error: {e}")
            thumb_name = None
            
        performer = f"[NETWORKS™]" 
        duration = results[0]["duration"]
        
    except Exception as e:
        print(f"[ERROR] Search failed: {str(e)}")
        return await m.edit("❌ **Search failed!**\n\n**Try:** `/song vaa vaathi song`")
                
    await m.edit("**⬇️ Downloading your song...!**")
    
    ydl_opts = get_ydl_opts(with_audio=True)
    audio_file = None
    
    try:
        with YoutubeDL(ydl_opts) as ydl:
            print(f"[INFO] Downloading: {link}")
            info_dict = ydl.extract_info(link, download=False)
            audio_file = ydl.prepare_filename(info_dict)
            print(f"[INFO] Prepared file: {audio_file}")
            ydl.process_info(info_dict)

        cap = f"**BY›› [UPDATE]({CHNL_LNK})**"
        secmul, dur, dur_arr = 1, 0, duration.split(':')
        for i in range(len(dur_arr)-1, -1, -1):
            dur += (int(dur_arr[i]) * secmul)
            secmul *= 60
        
        if audio_file and os.path.exists(audio_file):
            await message.reply_audio(
                audio_file,
                caption=cap,            
                quote=False,
                title=title,
                duration=dur,
                performer=performer,
                thumb=thumb_name if thumb_name and os.path.exists(thumb_name) else None
            )            
            await m.delete()
        else:
            await m.edit("❌ **File not found after download!**")
            
    except Exception as e:
        error_msg = str(e)
        print(f"[ERROR] Download failed: {error_msg}")
        
        if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
            await m.edit("⚠️ **YouTube blocked the request (bot detection)**\n\nTry a different song or wait a few minutes")
        elif "age-restricted" in error_msg.lower():
            await m.edit("⚠️ **This video is age-restricted**\n\nTry a different song")
        elif "not available" in error_msg.lower():
            await m.edit("❌ **Video not available in your region**\n\nTry a different song")
        else:
            await m.edit(f"❌ **Download failed!**\n\n**Try:** `/song different song name`")
    
    finally:
        # Cleanup
        try:
            if audio_file and os.path.exists(audio_file):
                os.remove(audio_file)
            if thumb_name and os.path.exists(thumb_name):
                os.remove(thumb_name)
        except Exception as e:
            print(f"[CLEANUP ERROR] {e}")


def get_text(message: Message) -> [None,str]:
    text_to_return = message.text
    if message.text is None:
        return None
    if " " not in text_to_return:
        return None
    try:
        return message.text.split(None, 1)[1]
    except IndexError:
        return None


@Client.on_message(filters.command(["video", "mp4"]))
async def vsong(client, message: Message):
    urlissed = get_text(message)
    pablo = await client.send_message(message.chat.id, f"**🔍 Finding your video** `{urlissed}`")
    if not urlissed:
        return await pablo.edit("**Usage:** `/video https://youtube.com/watch?v=...`")     
    
    try:
        search = SearchVideos(f"{urlissed}", offset=1, mode="dict", max_results=1)
        mi = search.result()
        mio = mi["search_result"]
        
        # Error handling for empty search results
        if not mio or len(mio) == 0:
            return await pablo.edit("❌ **Video not found!**\n\nPlease try with a valid YouTube URL")
        
        mo = mio[0]["link"]
        thum = mio[0]["title"]
        fridayz = mio[0]["id"]
        kekme = f"https://img.youtube.com/vi/{fridayz}/hqdefault.jpg"
        
        await asyncio.sleep(0.6)
        
        sedlyf = None
        try:
            sedlyf = wget.download(kekme)
        except Exception as e:
            print(f"Thumbnail download error: {e}")
        
        opts = get_ydl_opts(with_audio=False)
        opts["postprocessors"] = [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]
        opts["outtmpl"] = "%(id)s.mp4"
        opts["logtostderr"] = False
        
        try:
            with YoutubeDL(opts) as ytdl:
                print(f"[INFO] Downloading video: {mo}")
                ytdl_data = ytdl.extract_info(mo, download=True)
        except Exception as e:
            error_msg = str(e)
            print(f"[ERROR] Video download failed: {error_msg}")
            
            if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
                return await pablo.edit_text("⚠️ **YouTube blocked the request (bot detection)**\n\nTry again later or use a different video")
            elif "age-restricted" in error_msg.lower():
                return await pablo.edit_text("⚠️ **This video is age-restricted**")
            else:
                return await pablo.edit_text(f"❌ **Download failed!**\n\nPlease try again later")
        
        file_stark = f"{ytdl_data['id']}.mp4"
        capy = f"""**𝚃𝙸𝚃𝙻𝙴 :** [{thum}]({mo})\n**𝚁𝙴𝚀𝚄𝙴𝚂𝚃𝙴𝙳 𝙱𝚈 :** {message.from_user.mention}"""

        if os.path.exists(file_stark):
            await client.send_video(
                message.chat.id,
                video=open(file_stark, "rb"),
                duration=int(ytdl_data["duration"]),
                file_name=str(ytdl_data["title"]),
                thumb=sedlyf if sedlyf and os.path.exists(sedlyf) else None,
                caption=capy,
                supports_streaming=True,        
                reply_to_message_id=message.id 
            )
        else:
            await pablo.edit_text("❌ **File not found after download!**")
            
        await pablo.delete()
        
    except Exception as e:
        print(f"[ERROR] Video command error: {str(e)}")
        await pablo.edit_text(f"❌ **Error:** Please try again")
    
    finally:
        # Cleanup
        try:
            if sedlyf and os.path.exists(sedlyf):
                os.remove(sedlyf)
            for f in os.listdir('.'):
                if f.endswith('.mp4'):
                    os.remove(f)
        except Exception as e:
            print(f"[CLEANUP ERROR] {e}")
