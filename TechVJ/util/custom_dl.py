import math
import asyncio
import logging
from info import *
from typing import Dict, Union
from TechVJ.bot import work_loads
from pyrogram import Client, utils, raw
from TechVJ.util.file_properties import get_file_ids
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid
from TechVJ.server.exceptions import FIleNotFound
from pyrogram.file_id import FileId, FileType, ThumbnailSource


class ByteStreamer:
    def __init__(self, client: Client):
        """A custom class that holds the cache of a specific client and class functions.
        attributes:
            client: the client that the cache is for.
            cached_file_ids: a dict of cached file IDs.
            cached_file_properties: a dict of cached file properties.
        
        functions:
            generate_file_properties: returns the properties for a media of a specific message contained in Tuple.
            generate_media_session: returns the media session for the DC that contains the media file.
            yield_file: yield a file from telegram servers for streaming.
            
        This is a modified version of the <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/telegram/utils/custom_download.py>
        Thanks to Eyaadh <https://github.com/eyaadh>
        """
        self.clean_timer = 30 * 60
        self.client: Client = client
        self.cached_file_ids: Dict[int, FileId] = {}
        asyncio.create_task(self.clean_cache())

    async def get_file_properties(self, id: int) -> FileId:
        """
        Returns the properties of a media of a specific message in a FIleId class.
        if the properties are cached, then it'll return the cached results.
        or it'll generate the properties from the Message ID and cache them.
        """
        if id not in self.cached_file_ids:
            await self.generate_file_properties(id)
            logging.debug(f"Cached file properties for message with ID {id}")
        return self.cached_file_ids[id]
    
    async def generate_file_properties(self, id: int) -> FileId:
        """
        Generates the properties of a media file on a specific message.
        returns ths properties in a FIleId class.
        """
        # Same class of bug as the byte-fetching side used to have: a single
        # transient failure here (session hiccup, momentary Telegram
        # timeout) used to immediately raise FIleNotFound — indistinguishable
        # from the file genuinely not existing, and the caller (route.py)
        # turns that straight into a 404 with no way to tell the two apart.
        # Retry a couple of times before actually giving up.
        last_exc = None
        for attempt in range(3):
            try:
                file_id = await get_file_ids(self.client, LOG_CHANNEL, id)
                break
            except Exception as e:
                last_exc = e
                logging.warning(
                    f"get_file_ids failed for id={id} (attempt {attempt + 1}/3): {e!r}"
                )
                await asyncio.sleep(0.4 * (attempt + 1))
        else:
            logging.exception(f"get_file_ids permanently failed for id={id}: {last_exc!r}")
            raise FIleNotFound

        logging.debug(f"Generated file ID and Unique ID for message with ID {id}")
        if not file_id:
            logging.debug(f"Message with ID {id} not found")
            raise FIleNotFound
        self.cached_file_ids[id] = file_id
        logging.debug(f"Cached media message with ID {id}")
        return self.cached_file_ids[id]

    async def generate_media_session(self, client: Client, file_id: FileId) -> Session:
        """
        Generates the media session for the DC that contains the media file.
        This is required for getting the bytes from Telegram servers.
        """

        media_session = client.media_sessions.get(file_id.dc_id, None)

        if media_session is None:
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
        else:
            logging.debug(f"Using cached media session for DC {file_id.dc_id}")
        return media_session


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
        """
        client = self.client
        work_loads[index] += 1
        logging.debug(f"Starting to yielding file with client {index}.")
        media_session = await self.generate_media_session(client, file_id)

        current_part = 1
        location = await self.get_location(file_id)

        # A transient MTProto hiccup (dropped connection, a session that
        # needs re-auth, a momentary Telegram-side timeout) used to just
        # silently end the generator here with no log line at all — the
        # HTTP response would stop short of its promised Content-Length and
        # the browser/player would sit there "loading" forever with zero
        # trace of why. Retry each chunk fetch a few times with a short
        # backoff before actually giving up, and always log the final
        # failure so a stuck stream is diagnosable instead of a silent
        # black hole.
        async def _get_file(off):
            last_exc = None
            for attempt in range(3):
                try:
                    return await media_session.send(
                        raw.functions.upload.GetFile(
                            location=location, offset=off, limit=chunk_size
                        ),
                    )
                except (TimeoutError, ConnectionError, OSError) as e:
                    last_exc = e
                    logging.warning(
                        f"GetFile failed for id-ish offset={off} (attempt {attempt + 1}/3), "
                        f"client {index}: {e!r}"
                    )
                    await asyncio.sleep(0.5 * (attempt + 1))
                except AttributeError as e:
                    # Not a network hiccup — an unexpected None/missing attribute
                    # is almost always a real bug (bad session state, malformed
                    # response, etc). Retrying it 3 times would just hide that
                    # behind the same "transient" log line as a dropped
                    # connection. Surface it immediately with its own message.
                    logging.exception(
                        f"GetFile hit an AttributeError (likely a bug, not a "
                        f"transient failure) at offset={off}, client {index}: {e!r}"
                    )
                    raise
            logging.exception(
                f"GetFile permanently failed at offset={off} after 3 attempts, client {index}: {last_exc!r}"
            )
            raise last_exc

        try:
            r = await _get_file(offset)
            if isinstance(r, raw.types.upload.File):
                while True:
                    chunk = r.bytes
                    if not chunk:
                        break
                    elif part_count == 1:
                        yield chunk[first_part_cut:last_part_cut]
                    elif current_part == 1:
                        yield chunk[first_part_cut:]
                    elif current_part == part_count:
                        yield chunk[:last_part_cut]
                    else:
                        yield chunk

                    current_part += 1
                    offset += chunk_size

                    if current_part > part_count:
                        break

                    r = await _get_file(offset)
        except (TimeoutError, ConnectionError, OSError, AttributeError) as e:
            # _get_file already logged the full traceback for this failure
            # before re-raising it — just record that the stream stopped
            # because of it, without dumping a second identical traceback.
            logging.warning(f"yield_file aborted early for client {index}: {e!r}")
        finally:
            logging.debug("Finished yielding file with {current_part} parts.")
            work_loads[index] -= 1

    
    async def clean_cache(self) -> None:
        """
        function to clean the cache to reduce memory usage
        """
        while True:
            await asyncio.sleep(self.clean_timer)
            self.cached_file_ids.clear()
            logging.debug("Cleaned the cache")
