# # Don't Remove Credit @VJ_Botz
# # Subscribe YouTube Channel For Amazing Bot @Tech_VJ
# # Ask Doubt on telegram @KingVJ01

# import re, math, logging, secrets, mimetypes, time
# from info import *
# from aiohttp import web
# from aiohttp.http_exceptions import BadStatusLine
# from TechVJ.bot import multi_clients, work_loads, TechVJBot
# from TechVJ.server.exceptions import FIleNotFound, InvalidHash
# from TechVJ import StartTime, __version__
# from TechVJ.util.custom_dl import ByteStreamer
# from TechVJ.util.time_format import get_readable_time
# from TechVJ.util.render_template import render_page

# routes = web.RouteTableDef()

# @routes.get("/", allow_head=True)
# async def root_route_handler(request):
#     return web.json_response("BenFilterBot")

# @routes.get(r"/watch/{path:\S+}", allow_head=True)
# async def stream_handler(request: web.Request):
#     try:
#         path = request.match_info["path"]
#         match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path)
#         if match:
#             secure_hash = match.group(1)
#             id = int(match.group(2))
#         else:
#             id = int(re.search(r"(\d+)(?:\/\S+)?", path).group(1))
#             secure_hash = request.rel_url.query.get("hash")
#         return web.Response(text=await render_page(id, secure_hash), content_type='text/html')
#     except InvalidHash as e:
#         raise web.HTTPForbidden(text=e.message)
#     except FIleNotFound as e:
#         raise web.HTTPNotFound(text=e.message)
#     except (AttributeError, BadStatusLine, ConnectionResetError):
#         pass
#     except Exception as e:
#         logging.critical(e.with_traceback(None))
#         raise web.HTTPInternalServerError(text=str(e))

# @routes.get(r"/{path:\S+}", allow_head=True)
# async def stream_handler(request: web.Request):
#     try:
#         path = request.match_info["path"]
#         match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path)
#         if match:
#             secure_hash = match.group(1)
#             id = int(match.group(2))
#         else:
#             id = int(re.search(r"(\d+)(?:\/\S+)?", path).group(1))
#             secure_hash = request.rel_url.query.get("hash")
#         return await media_streamer(request, id, secure_hash)
#     except InvalidHash as e:
#         raise web.HTTPForbidden(text=e.message)
#     except FIleNotFound as e:
#         raise web.HTTPNotFound(text=e.message)
#     except (AttributeError, BadStatusLine, ConnectionResetError):
#         pass
#     except Exception as e:
#         logging.critical(e.with_traceback(None))
#         raise web.HTTPInternalServerError(text=str(e))

# class_cache = {}

# async def media_streamer(request: web.Request, id: int, secure_hash: str):
#     range_header = request.headers.get("Range", 0)
    
#     index = min(work_loads, key=work_loads.get)
#     faster_client = multi_clients[index]
    
#     if MULTI_CLIENT:
#         logging.info(f"Client {index} is now serving {request.remote}")

#     if faster_client in class_cache:
#         tg_connect = class_cache[faster_client]
#         logging.debug(f"Using cached ByteStreamer object for client {index}")
#     else:
#         logging.debug(f"Creating new ByteStreamer object for client {index}")
#         tg_connect = ByteStreamer(faster_client)
#         class_cache[faster_client] = tg_connect
#     logging.debug("before calling get_file_properties")
#     file_id = await tg_connect.get_file_properties(id)
#     logging.debug("after calling get_file_properties")
    
#     if file_id.unique_id[:6] != secure_hash:
#         logging.debug(f"Invalid hash for message with ID {id}")
#         raise InvalidHash
    
#     file_size = file_id.file_size

#     if range_header:
#         from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
#         from_bytes = int(from_bytes)
#         until_bytes = int(until_bytes) if until_bytes else file_size - 1
#     else:
#         from_bytes = request.http_range.start or 0
#         until_bytes = (request.http_range.stop or file_size) - 1

#     if (until_bytes > file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
#         return web.Response(
#             status=416,
#             body="416: Range not satisfiable",
#             headers={"Content-Range": f"bytes */{file_size}"},
#         )

#     chunk_size = 1024 * 1024
#     until_bytes = min(until_bytes, file_size - 1)

#     offset = from_bytes - (from_bytes % chunk_size)
#     first_part_cut = from_bytes - offset
#     last_part_cut = until_bytes % chunk_size + 1

#     req_length = until_bytes - from_bytes + 1
#     part_count = math.ceil(until_bytes / chunk_size) - math.floor(offset / chunk_size)
#     body = tg_connect.yield_file(
#         file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size
#     )

#     mime_type = file_id.mime_type
#     file_name = file_id.file_name
#     disposition = "attachment"

#     if mime_type:
#         if not file_name:
#             try:
#                 file_name = f"{secrets.token_hex(2)}.{mime_type.split('/')[1]}"
#             except (IndexError, AttributeError):
#                 file_name = f"{secrets.token_hex(2)}.unknown"
#     else:
#         if file_name:
#             mime_type = mimetypes.guess_type(file_id.file_name)
#         else:
#             mime_type = "application/octet-stream"
#             file_name = f"{secrets.token_hex(2)}.unknown"

#     return web.Response(
#         status=206 if range_header else 200,
#         body=body,
#         headers={
#             "Content-Type": f"{mime_type}",
#             "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
#             "Content-Length": str(req_length),
#             "Content-Disposition": f'{disposition}; filename="{file_name}"',
#             "Accept-Ranges": "bytes",
#         },
#     )



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


