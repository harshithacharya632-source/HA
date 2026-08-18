import jinja2
from info import *
from TechVJ.bot import TechVJBot
from TechVJ.util.human_readable import humanbytes
from TechVJ.util.file_properties import get_file_ids
from TechVJ.server.exceptions import InvalidHash
import urllib.parse
import logging
import asyncio


async def _get_message_and_file_data(id):
    """Both TechVJBot calls below can hit the exact same class of transient
    MTProto hiccup that custom_dl.py's yield_file already retries around —
    but here a single failure used to propagate straight up as an unhandled
    exception. route.py's watch_page catches any generic Exception and
    turns it into a plain 404, so a flaky moment looked identical to "this
    file doesn't exist" with zero trace of what actually happened. Retry a
    few times and log clearly before actually giving up."""
    last_exc = None
    for attempt in range(3):
        try:
            file = await TechVJBot.get_messages(int(LOG_CHANNEL), int(id))
            file_data = await get_file_ids(TechVJBot, int(LOG_CHANNEL), int(id))
            return file, file_data
        except Exception as e:
            last_exc = e
            logging.warning(f"render_page: message/file lookup failed for id={id} (attempt {attempt + 1}/3): {e!r}")
            await asyncio.sleep(0.4 * (attempt + 1))
    logging.exception(f"render_page: message/file lookup permanently failed for id={id}: {last_exc!r}")
    raise last_exc


def _resolve_mime_tag(file_data):
    """file_data.mime_type is frequently None or missing for files Telegram
    didn't tag cleanly — file_data.mime_type.split("/") on None raised an
    AttributeError, which (same as above) silently became a 404 on a file
    that was actually fine. Fall back to the filename's extension, and default
    to "video" rather than a type that sends a real video to the download
    page instead of the streaming player."""
    mime = getattr(file_data, "mime_type", None)
    if mime and "/" in mime:
        return mime.split("/")[0].strip()
    name = (getattr(file_data, "file_name", "") or "").lower()
    audio_ext = (".mp3", ".m4a", ".flac", ".ogg", ".wav", ".aac")
    video_ext = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".ts", ".flv", ".3gp")
    if name.endswith(audio_ext):
        return "audio"
    if name.endswith(video_ext):
        return "video"
    return "video"


async def render_page(id, secure_hash, src=None):
    file, file_data = await _get_message_and_file_data(id)
    if file_data.unique_id[:6] != secure_hash:
        logging.debug(f"link hash: {secure_hash} - {file_data.unique_id[:6]}")
        logging.debug(f"Invalid hash for message with - ID {id}")
        raise InvalidHash

    src = urllib.parse.urljoin(
        URL,
        f"{id}/{urllib.parse.quote_plus(file_data.file_name)}?hash={secure_hash}",
    )

    tag = _resolve_mime_tag(file_data)
    # Use the size Telegram already gave us instead of making a second live
    # HTTP request back to our own streaming endpoint just to read a
    # Content-Length header — that round-trip was a second, entirely
    # avoidable place for the exact same transient failure to turn into a
    # 404, and it depended on the streaming endpoint being healthy just to
    # render a page that describes it.
    file_size = humanbytes(file_data.file_size)
    template_file = "TechVJ/template/req.html" if tag in ("video", "audio") else "TechVJ/template/dl.html"

    with open(template_file) as f:
        template = jinja2.Template(f.read())

    file_name = file_data.file_name.replace("_", " ")

    return template.render(
        file_name=file_name,
        file_url=src,
        file_size=file_size,
        file_unique_id=file_data.unique_id,
    )
