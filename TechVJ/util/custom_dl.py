import math
import time
import asyncio
import logging
from collections import deque
from info import *
from typing import Dict, Optional, Tuple, Union
from TechVJ.bot import work_loads
from pyrogram import Client, utils, raw
from TechVJ.util.file_properties import get_file_ids, get_media_from_message
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid, FloodWait
from TechVJ.server.exceptions import FIleNotFound
from pyrogram.file_id import FileId, FileType, ThumbnailSource

# How many 1 MB chunks are requested from Telegram at the same time for ONE
# stream. Before, every chunk was fetched only after the previous one had been
# written to the player, so the stream could never be faster than
# (1 MB / Telegram round-trip) and any slow round-trip froze the video.
PREFETCH_PARTS = 6

# FileId objects (with their file_reference) are cached for this long.
FILE_ID_TTL = 20 * 60

# Shared by every ByteStreamer (one per Telegram client): a message that was
# looked up / warmed by one client is instantly known to all of them.
_FILE_ID_CACHE: Dict[int, Tuple[float, FileId]] = {}


def file_id_from_message(message) -> Optional[FileId]:
    """Build the same FileId get_file_ids() builds, from a message we already
    hold (e.g. the one just forwarded to LOG_CHANNEL) - no extra get_messages."""
    media = get_media_from_message(message)
    if not media:
        return None
    file_id = FileId.decode(media.file_id)
    setattr(file_id, "file_size", getattr(media, "file_size", 0))
    setattr(file_id, "mime_type", getattr(media, "mime_type", ""))
    setattr(file_id, "file_name", getattr(media, "file_name", ""))
    setattr(file_id, "unique_id", media.file_unique_id)
    return file_id


def remember_file_id(message_id: int, file_id: FileId) -> None:
    now = time.monotonic()
    if len(_FILE_ID_CACHE) > 500:
        for key in [k for k, (ts, _) in _FILE_ID_CACHE.items() if now - ts > FILE_ID_TTL]:
            _FILE_ID_CACHE.pop(key, None)
    _FILE_ID_CACHE[message_id] = (now, file_id)


class ByteStreamer:
    def __init__(self, client: Client):
        """A custom class that holds the cache of a specific client and class functions.
        attributes:
            client: the client that the cache is for.
            cached_file_ids: a dict of cached file IDs (shared between clients).

        functions:
            generate_file_properties: returns the properties for a media of a specific message contained in Tuple.
            generate_media_session: returns the media session for the DC that contains the media file.
            yield_file: yield a file from telegram servers for streaming.

        This is a modified version of the <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/telegram/utils/custom_download.py>
        Thanks to Eyaadh <https://github.com/eyaadh>
        """
        self.client: Client = client
        self.cached_file_ids = _FILE_ID_CACHE
        # Browsers open 2-3 range requests at once when a video starts. Without
        # this lock each of them built its own media session for the same DC and
        # all but one leaked.
        self._session_lock = asyncio.Lock()

    async def get_file_properties(self, id: int, refresh: bool = False) -> FileId:
        """
        Returns the properties of a media of a specific message in a FIleId class.
        if the properties are cached, then it'll return the cached results.
        or it'll generate the properties from the Message ID and cache them.
        """
        hit = _FILE_ID_CACHE.get(id)
        if hit and not refresh and time.monotonic() - hit[0] < FILE_ID_TTL:
            return hit[1]
        return await self.generate_file_properties(id)

    async def generate_file_properties(self, id: int) -> FileId:
        """
        Generates the properties of a media file on a specific message.
        returns ths properties in a FIleId class.
        """
        # A single transient failure here (session hiccup, momentary Telegram
        # timeout) must not look like "file does not exist" - retry a few times.
        last_exc = None
        for attempt in range(3):
            try:
                file_id = await get_file_ids(self.client, LOG_CHANNEL, id)
                break
            except FIleNotFound:
                raise
            except Exception as e:
                last_exc = e
                logging.warning(
                    f"get_file_ids failed for id={id} (attempt {attempt + 1}/3): {e!r}"
                )
                await asyncio.sleep(0.4 * (attempt + 1))
        else:
            logging.error(f"get_file_ids permanently failed for id={id}: {last_exc!r}")
            raise FIleNotFound

        if not file_id:
            logging.debug(f"Message with ID {id} not found")
            raise FIleNotFound
        remember_file_id(id, file_id)
        return file_id

    async def generate_media_session(self, client: Client, file_id: FileId) -> Session:
        """
        Generates the media session for the DC that contains the media file.
        This is required for getting the bytes from Telegram servers.
        """
        media_session = client.media_sessions.get(file_id.dc_id, None)
        if media_session is not None:
            return media_session

        async with self._session_lock:
            media_session = client.media_sessions.get(file_id.dc_id, None)
            if media_session is not None:
                return media_session

            if file_id.dc_id != await client.storage.dc_id():
                media_session = Session(
                    client,
                    file_id.dc_id,
                    await Auth(
                        client, file_id.dc_id, await client.storage.test_mode()
                    ).create(),
                    await client.storage.test_mode(),
                    is_media=True,
                )
                await media_session.start()

                for _ in range(6):
                    exported_auth = await client.invoke(
                        raw.functions.auth.ExportAuthorization(dc_id=file_id.dc_id)
                    )

                    try:
                        await media_session.send(
                            raw.functions.auth.ImportAuthorization(
                                id=exported_auth.id, bytes=exported_auth.bytes
                            )
                        )
                        break
                    except AuthBytesInvalid:
                        logging.debug(
                            f"Invalid authorization bytes for DC {file_id.dc_id}"
                        )
                        continue
                else:
                    await media_session.stop()
                    raise AuthBytesInvalid
            else:
                media_session = Session(
                    client,
                    file_id.dc_id,
                    await client.storage.auth_key(),
                    await client.storage.test_mode(),
                    is_media=True,
                )
                await media_session.start()
            logging.debug(f"Created media session for DC {file_id.dc_id}")
            client.media_sessions[file_id.dc_id] = media_session
            return media_session

    async def _rebuild_media_session(self, client: Client, file_id: FileId, broken: Session) -> Session:
        """A media session whose connection died stays in client.media_sessions
        and every later GetFile on it fails until the whole bot restarts - which
        is what a video that 'buffers forever' after a network blip really was.
        Drop the dead one so the next call builds a fresh session."""
        async with self._session_lock:
            if client.media_sessions.get(file_id.dc_id) is broken:
                client.media_sessions.pop(file_id.dc_id, None)
                try:
                    await broken.stop()
                except Exception:
                    pass
                logging.warning(f"Dropped broken media session for DC {file_id.dc_id}")
        return await self.generate_media_session(client, file_id)

    @staticmethod
    async def get_location(file_id: FileId) -> Union[raw.types.InputPhotoFileLocation,
                                                     raw.types.InputDocumentFileLocation,
                                                     raw.types.InputPeerPhotoFileLocation,]:
        """
        Returns the file location for the media file.
        """
        file_type = file_id.file_type

        if file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id, access_hash=file_id.chat_access_hash
                )
            else:
                if file_id.chat_access_hash == 0:
                    peer = raw.types.InputPeerChat(chat_id=-file_id.chat_id)
                else:
                    peer = raw.types.InputPeerChannel(
                        channel_id=utils.get_channel_id(file_id.chat_id),
                        access_hash=file_id.chat_access_hash,
                    )

            location = raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                volume_id=file_id.volume_id,
                local_id=file_id.local_id,
                big=file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG,
            )
        elif file_type == FileType.PHOTO:
            location = raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        else:
            location = raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        return location

    async def _get_chunk(self, state: dict, file_id: FileId, location, off: int, limit: int, index: int):
        """One GetFile with retries. `state['session']` is shared by all the
        in-flight chunk requests of a stream, so a rebuilt session is picked up
        by every one of them."""
        last_exc = None
        for attempt in range(3):
            session = state["session"]
            try:
                return await session.send(
                    raw.functions.upload.GetFile(location=location, offset=off, limit=limit),
                )
            except FloodWait as e:
                last_exc = e
                logging.warning(f"GetFile FloodWait {e.value}s at offset={off}, client {index}")
                await asyncio.sleep(min(int(e.value), 5))
            except (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError) as e:
                last_exc = e
                logging.warning(
                    f"GetFile failed at offset={off} (attempt {attempt + 1}/3), client {index}: {e!r}"
                )
                if attempt >= 1:
                    # Failed twice in a row: the session itself is most likely dead.
                    try:
                        state["session"] = await self._rebuild_media_session(self.client, file_id, session)
                    except Exception as rebuild_err:
                        logging.warning(f"media session rebuild failed: {rebuild_err!r}")
                await asyncio.sleep(0.4 * (attempt + 1))
            except AttributeError as e:
                # Not a network hiccup - an unexpected None/missing attribute is
                # almost always a real bug, so surface it immediately.
                logging.exception(
                    f"GetFile hit an AttributeError (likely a bug, not a transient failure) "
                    f"at offset={off}, client {index}: {e!r}"
                )
                raise
        logging.error(f"GetFile permanently failed at offset={off} after 3 attempts, client {index}: {last_exc!r}")
        raise last_exc

    async def yield_file(
        self,
        file_id: FileId,
        index: int,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int,
    ) -> Union[str, None]:
        """
        Custom generator that yields the bytes of the media file.
        Modded from <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/telegram/utils/custom_download.py#L20>
        Thanks to Eyaadh <https://github.com/eyaadh>

        Up to PREFETCH_PARTS chunks are requested ahead of the one being sent,
        so Telegram latency overlaps with the time spent writing to the player.
        """
        client = self.client
        work_loads[index] += 1
        pending = deque()
        try:
            media_session = await self.generate_media_session(client, file_id)
            location = await self.get_location(file_id)
            state = {"session": media_session}
            next_offset = offset
            scheduled = 0

            def schedule():
                nonlocal next_offset, scheduled
                while scheduled < part_count and len(pending) < PREFETCH_PARTS:
                    pending.append(asyncio.ensure_future(
                        self._get_chunk(state, file_id, location, next_offset, chunk_size, index)
                    ))
                    next_offset += chunk_size
                    scheduled += 1

            schedule()
            current_part = 1
            while pending:
                r = await pending.popleft()
                schedule()  # keep the pipeline full while this chunk is written out
                if not isinstance(r, raw.types.upload.File):
                    break
                chunk = r.bytes
                if not chunk:
                    break
                if part_count == 1:
                    yield chunk[first_part_cut:last_part_cut]
                elif current_part == 1:
                    yield chunk[first_part_cut:]
                elif current_part == part_count:
                    yield chunk[:last_part_cut]
                else:
                    yield chunk
                current_part += 1
                if current_part > part_count:
                    break
        except (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError, AttributeError) as e:
            # _get_chunk already logged the details; the caller notices the short
            # body and closes the connection so the player retries immediately.
            logging.warning(f"yield_file aborted early for client {index}: {e!r}")
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            work_loads[index] -= 1
