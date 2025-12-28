

import re
import math
import logging

from aiohttp import web
from info import *

from TechVJ.bot import multi_clients, work_loads
from TechVJ.server.exceptions import FIleNotFound, InvalidHash
from TechVJ.util.custom_dl import ByteStreamer
from TechVJ.util.render_template import render_page

routes = web.RouteTableDef()
class_cache = {}

# ---------------- ROOT (Health Check) ----------------
@routes.get("/", allow_head=True)
async def root(request):
    return web.Response(text="OK", status=200)


# ---------------- WATCH PAGE (HTML PLAYER) ----------------
@routes.get(r"/watch/{path:\S+}", allow_head=True)
async def watch_page(request: web.Request):
    try:
        path = request.match_info["path"]
        id = int(re.search(r"(\d+)", path).group(1))
        secure_hash = request.rel_url.query.get("hash")

        return web.Response(
            text=await render_page(id, secure_hash),
            content_type="text/html"
        )
    except Exception:
        raise web.HTTPNotFound()


# ---------------- DIRECT STREAM (ROOT PATH – REQUIRED FOR VLC/MX) ----------------
@routes.get(r"/{path:\S+}", allow_head=True)
async def direct_stream_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        id = int(re.search(r"(\d+)", path).group(1))
        secure_hash = request.rel_url.query.get("hash")
        return await media_streamer(request, id, secure_hash, inline=True)
    except InvalidHash:
        raise web.HTTPForbidden()
    except FIleNotFound:
        raise web.HTTPNotFound()
    except Exception as e:
        logging.exception(e)
        raise web.HTTPInternalServerError()


# ---------------- DOWNLOAD ----------------
@routes.get(r"/download/{path:\S+}", allow_head=True)
async def download_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        id = int(re.search(r"(\d+)", path).group(1))
        secure_hash = request.rel_url.query.get("hash")
        return await media_streamer(request, id, secure_hash, inline=False)
    except InvalidHash:
        raise web.HTTPForbidden()
    except FIleNotFound:
        raise web.HTTPNotFound()
    except Exception as e:
        logging.exception(e)
        raise web.HTTPInternalServerError()


# ---------------- CORE STREAMER ----------------
async def media_streamer(
    request: web.Request,
    id: int,
    secure_hash: str,
    inline: bool
):
    range_header = request.headers.get("Range", None)

    # pick least-loaded client
    index = min(work_loads, key=work_loads.get)
    client = multi_clients[index]

    tg = class_cache.get(client)
    if not tg:
        tg = ByteStreamer(client)
        class_cache[client] = tg

    file_id = await tg.get_file_properties(id)

    if file_id.unique_id[:6] != secure_hash:
        raise InvalidHash

    file_size = file_id.file_size
    file_name = file_id.file_name or "file.bin"

    # ---------------- RANGE PARSING (SAFE) ----------------
    if range_header:
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            status = 206
        else:
            start = 0
            end = file_size - 1
            status = 200
    else:
        start = 0
        end = file_size - 1
        status = 200

    if start < 0 or end >= file_size or start > end:
        return web.Response(
            status=416,
            headers={"Content-Range": f"bytes */{file_size}"}
        )

    # ---------------- STREAM SETUP ----------------
    chunk_size = 1024 * 1024
    offset = start - (start % chunk_size)
    first_cut = start - offset
    last_cut = end % chunk_size + 1
    part_count = math.ceil((end + 1) / chunk_size) - math.floor(offset / chunk_size)
    length = end - start + 1

    # ---------------- MIME FIX (CRITICAL FOR VLC/MX) ----------------
    mime = file_id.mime_type
    if not mime or mime == "application/octet-stream":
        if file_name.lower().endswith(".mp4"):
            mime = "video/mp4"
        elif file_name.lower().endswith(".mkv"):
            mime = "video/x-matroska"
        elif file_name.lower().endswith(".avi"):
            mime = "video/x-msvideo"
        else:
            mime = "application/octet-stream"

    response = web.StreamResponse(
        status=status,
        headers={
            "Content-Type": mime,
            "Content-Length": str(length),
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Disposition": (
                f'inline; filename="{file_name}"'
                if inline else
                f'attachment; filename="{file_name}"'
            ),
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
        }
    )

    await response.prepare(request)

    async for chunk in tg.yield_file(
        file_id,
        index,
        offset,
        first_cut,
        last_cut,
        part_count,
        chunk_size
    ):
        await response.write(chunk)

    await response.write_eof()
    return response


