
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

# Alternative Invidious instances to try
INVIDIOUS_INSTANCES = [
    "https://invidious.nerdvpn.de",
    "https://invidious.kavin.rocks",
    "https://invidious.snopyta.org",
    "https://y.com.sb",
]

def convert_youtube_to_invidious(youtube_url, instance=None):
    """Convert YouTube URL to Invidious URL"""
    if not instance:
        instance = INVIDIOUS_INSTANCES[0]
    
    if "youtube.com/watch?v=" in youtube_url:
        video_id = youtube_url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in youtube_url:
        video_id = youtube_url.split("youtu.be/")[1].split("?")[0]
    else:
        return None
    
    return f"{instance}/watch?v={video_id}"


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
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "tv_embedded"],
                "extract_flat": ["in_playlist"],
            }
        },
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
    query = ''
    for i in message.command[1:]:
        query += ' ' + str(i)
    print(query)
    m = await message.reply(f"**🔍 Searching for your song...!\n {query}**")
    
    try:
        results = YoutubeSearch(query, max_results=1).to_dict()
        
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
    download_success = False
    
    # Try YouTube first
    try:
        with YoutubeDL(ydl_opts) as ydl:
            print(f"[INFO] Attempting YouTube download: {link}")
            info_dict = ydl.extract_info(link, download=False)
            audio_file = ydl.prepare_filename(info_dict)
            print(f"[INFO] Prepared file: {audio_file}")
            ydl.process_info(info_dict)
            download_success = True
            
    except Exception as e:
        error_msg = str(e)
        print(f"[ERROR] YouTube download failed: {error_msg}")
        
        if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
            print("[INFO] YouTube bot detection triggered. Trying Invidious fallback...")
            await m.edit("**⚙️ Switching to alternative source...**")
            
            # Try Invidious instances
            for instance in INVIDIOUS_INSTANCES:
                try:
                    invidious_url = convert_youtube_to_invidious(link, instance)
                    if not invidious_url:
                        continue
                    
                    print(f"[INFO] Trying Invidious: {invidious_url}")
                    
                    with YoutubeDL(ydl_opts) as ydl:
                        info_dict = ydl.extract_info(invidious_url, download=False)
                        audio_file = ydl.prepare_filename(info_dict)
                        ydl.process_info(info_dict)
                        download_success = True
                        print(f"[SUCCESS] Downloaded via {instance}")
                        break
                        
                except Exception as inv_error:
                    print(f"[ERROR] Invidious {instance} failed: {str(inv_error)}")
                    continue
    
    # Upload if successful
    if download_success and audio_file:
        try:
            cap = f"**BY›› [UPDATE]({CHNL_LNK})**"
            secmul, dur, dur_arr = 1, 0, duration.split(':')
            for i in range(len(dur_arr)-1, -1, -1):
                dur += (int(dur_arr[i]) * secmul)
                secmul *= 60
            
            if os.path.exists(audio_file):
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
            print(f"[ERROR] Upload failed: {str(e)}")
            await m.edit("❌ **Failed to upload audio!**")
    else:
        await m.edit("⚠️ **YouTube is blocking requests (bot detection)**\n\n✅ **Solutions:**\n1. Try a different song name\n2. Wait 5-10 minutes and try again\n3. Contact bot owner for YouTube API key")
    
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
        
        video_success = False
        
        # Try YouTube first
        try:
            with YoutubeDL(opts) as ytdl:
                print(f"[INFO] Downloading video: {mo}")
                ytdl_data = ytdl.extract_info(mo, download=True)
                video_success = True
                
        except Exception as e:
            error_msg = str(e)
            print(f"[ERROR] YouTube video download failed: {error_msg}")
            
            if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
                print("[INFO] Trying Invidious for video...")
                await pablo.edit("**⚙️ Switching to alternative source...**")
                
                for instance in INVIDIOUS_INSTANCES:
                    try:
                        invidious_url = convert_youtube_to_invidious(mo, instance)
                        if not invidious_url:
                            continue
                        
                        with YoutubeDL(opts) as ytdl:
                            print(f"[INFO] Trying Invidious: {invidious_url}")
                            ytdl_data = ytdl.extract_info(invidious_url, download=True)
                            video_success = True
                            print(f"[SUCCESS] Video downloaded via {instance}")
                            break
                            
                    except Exception as inv_error:
                        print(f"[ERROR] Invidious video {instance} failed: {str(inv_error)}")
                        continue
        
        if video_success and 'ytdl_data' in locals():
            try:
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
                    
            except Exception as e:
                print(f"[ERROR] Video upload failed: {str(e)}")
                await pablo.edit_text("❌ **Failed to upload video!**")
        else:
            await pablo.edit_text("⚠️ **YouTube is blocking requests**\n\n✅ **Solutions:**\n1. Try a different video\n2. Wait 5-10 minutes\n3. Contact bot owner")
            
        await pablo.delete()
        
    except Exception as e:
        print(f"[ERROR] Video command error: {str(e)}")
        await pablo.edit_text(f"❌ **Error:** Please try again")
    
    finally:
        # Cleanup
        try:
            if 'sedlyf' in locals() and sedlyf and os.path.exists(sedlyf):
                os.remove(sedlyf)
            for f in os.listdir('.'):
                if f.endswith('.mp4'):
                    os.remove(f)
        except Exception as e:
            print(f"[CLEANUP ERROR] {e}")
