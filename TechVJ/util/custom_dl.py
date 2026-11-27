import math
import time
import heapq
import asyncio
import itertools
import logging
from collections import deque, OrderedDict
from info import *
from typing import Dict, Optional, Tuple, Union
from TechVJ.bot import work_loads
from pyrogram import Client, utils, raw
from TechVJ.util.file_properties import get_file_ids, get_media_from_message
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid, FloodWait, RPCError, InternalServerError, ServiceUnavailable
from TechVJ.server.exceptions import FIleNotFound
from pyrogram.file_id import FileId, FileType, ThumbnailSource

# How many 1 MB chunks are requested from Telegram at the same time for ONE
# stream. Before, every chunk was fetched only after the previous one had been
# written to the player, so the stream could never be faster than
# (1 MB / Telegram round-trip) and any slow round-trip froze the video.
# Keep this small: all the requests share ONE connection, so on a slow link the
# last of them waits behind the others - 3 keeps that wait well inside the timeout.
PREFETCH_PARTS = 8

# One Telegram connection only carries a few MB/s (it is limited per connection, and by the distance to the
# data centre), which is what makes a video "buffer" even on a fast phone connection. So the file is read
# over several connections to the same data centre at once - the first one exists from the start, the extra
# ones are opened in the background while the first chunks are already flowing.
SESSIONS_PER_DC = 3

# At most this many GetFile requests are in flight per connection (all streams and the warm-up together), so
# they never queue up behind each other long enough to time out.
MAX_PER_SESSION = 4

# Downloaded chunks are kept in memory (least recently used go first). A chunk is fetched ONCE, even when
# several requests want it, and a download keeps going when the player drops the request - so a player
# that restarts loses nothing, and the start / end of a file (what a player asks for first) is ready
# before it asks.
CHUNK_CACHE_BYTES = 64 * 1024 * 1024

# Pyrogram's default wait for a reply is 15 s. With several 1 MB chunks queued on
# one connection that is too short on a slow link: the timeouts then triggered a
# session rebuild that killed every other chunk in flight ("Connection lost").
GETFILE_TIMEOUT = 60

# FileId objects (with their file_reference) are cached for this long.
FILE_ID_TTL = 20 * 60


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


class _PrioGate:
    """A counting semaphore whose waiters are served by PRIORITY instead of first-come-first-served:
    0 = a player is waiting for this chunk right now, 1 = read-ahead. After a seek, the chunk the player
    waits for therefore never queues behind read-ahead of the part of the file it just left."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.used = 0
        self._waiters = []                 # heap of [priority, seq, future, key]
        self._seq = itertools.count()

    async def acquire(self, key, prio: int) -> None:
        if self.used < self.capacity and not self._waiters:
            self.used += 1
            return
        fut = asyncio.get_event_loop().create_future()
        heapq.heappush(self._waiters, [prio, next(self._seq), fut, key])
        self._wake()
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self.release()             # the slot was handed to us just as we were cancelled
            raise

    def release(self) -> None:
        self.used -= 1
        self._wake()

    def _wake(self) -> None:
        while self._waiters and self.used < self.capacity:
            fut = heapq.heappop(self._waiters)[2]
            if fut.cancelled():
                continue
            self.used += 1
            fut.set_result(None)

    def set_capacity(self, n: int) -> None:
        self.capacity = n
        self._wake()

    def bump(self, key, prio: int) -> None:
        for e in self._waiters:
            if e[3] == key and prio < e[0]:
                e[0] = prio
                heapq.heapify(self._waiters)
                return


class ByteStreamer:
    def __init__(self, client: Client):
        """A custom class that holds the cache of a specific client and class functions.
        attributes:
            client: the client that the cache is for.
            cached_file_ids: a dict of cached file IDs (per client: a FileId's access_hash and
                file_reference only work for the client that fetched it).

        functions:
            generate_file_properties: returns the properties for a media of a specific message contained in Tuple.
            generate_media_session: returns the media session for the DC that contains the media file.
            yield_file: yield a file from telegram servers for streaming.

        This is a modified version of the <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/telegram/utils/custom_download.py>
        Thanks to Eyaadh <https://github.com/eyaadh>
        """
        self.client: Client = client
        self.cached_file_ids: Dict[int, Tuple[float, FileId]] = {}
        # Browsers open 2-3 range requests at once when a video starts. Without
        # this lock each of them built its own media session for the same DC and
        # all but one leaked.
        self._session_lock = asyncio.Lock()
        self._chunks: "OrderedDict[tuple, asyncio.Future]" = OrderedDict()   # (file, chunk_size, index) -> Future[bytes]
        self._chunk_len: Dict[tuple, int] = {}
        self._chunk_bytes = 0
        self._extra_sessions: Dict[int, list] = {}      # dc_id -> extra media connections (besides client.media_sessions[dc])
        self._growing: Dict[int, float] = {}            # dc_id -> 0 while idle / start time while opening extras / retry-after
        self._session_load: Dict[int, int] = {}
        self._gate = _PrioGate(MAX_PER_SESSION)         # grows with the number of connections
        self._want: Dict[tuple, int] = {}               # chunk -> how many streams still want it (warm-up chunks are pinned)
        self._started: set = set()                      # chunks whose GetFile is already on its way to Telegram
        self._prio: Dict[tuple, int] = {}

    async def get_file_properties(self, id: int, refresh: bool = False) -> FileId:
        """
        Returns the properties of a media of a specific message in a FIleId class.
        if the properties are cached, then it'll return the cached results.
        or it'll generate the properties from the Message ID and cache them.
        """
        hit = self.cached_file_ids.get(id)
        if hit and not refresh and time.monotonic() - hit[0] < FILE_ID_TTL:
            return hit[1]
        return await self.generate_file_properties(id)

    def remember(self, message_id: int, file_id: FileId) -> None:
        """Cache a FileId this client already holds (e.g. from the message it just forwarded)."""
        now = time.monotonic()
        if len(self.cached_file_ids) > 500:
            for key in [k for k, (ts, _) in self.cached_file_ids.items() if now - ts > FILE_ID_TTL]:
                self.cached_file_ids.pop(key, None)
        self.cached_file_ids[message_id] = (now, file_id)

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
        self.remember(id, file_id)
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
        extras = self._extra_sessions.get(file_id.dc_id, [])
        if broken in extras:
            extras.remove(broken)
            try:
                await broken.stop()
            except Exception:
                pass
            self._gate.set_capacity(MAX_PER_SESSION * (1 + len(extras)))
            self._growing[file_id.dc_id] = time.monotonic() + 30     # open a replacement in 30 s
            logging.warning(f"Dropped broken extra media connection for DC {file_id.dc_id}")
            return await self.generate_media_session(client, file_id)
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
        timeouts = 0
        for attempt in range(4):
            session = state["session"]
            try:
                return await session.send(
                    raw.functions.upload.GetFile(location=location, offset=off, limit=limit),
                    timeout=GETFILE_TIMEOUT,
                )
            except FloodWait as e:
                last_exc = e
                logging.warning(f"GetFile FloodWait {e.value}s at offset={off}, client {index}")
                await asyncio.sleep(min(int(e.value), 5))
            except (InternalServerError, ServiceUnavailable) as e:
                last_exc = e
                logging.warning(f"GetFile server error at offset={off} (attempt {attempt + 1}/4), client {index}: {type(e).__name__}")
                await asyncio.sleep(0.5 * (attempt + 1))
            except RPCError as e:
                # A real refusal from Telegram (expired file reference, bad offset ...):
                # retrying cannot help, and the name is exactly what is needed to diagnose it.
                logging.error(f"GetFile refused by Telegram at offset={off}, client {index}: {type(e).__name__}: {e}")
                raise
            except (TimeoutError, asyncio.TimeoutError) as e:
                last_exc = e
                timeouts += 1
                logging.warning(f"GetFile timed out after {GETFILE_TIMEOUT}s at offset={off} (attempt {attempt + 1}/4), client {index}")
                if timeouts >= 2:
                    # Two long waits in a row: the connection itself is most likely dead.
                    try:
                        state["session"] = await self._rebuild_media_session(self.client, file_id, session)
                    except Exception as rebuild_err:
                        logging.warning(f"media session rebuild failed: {rebuild_err!r}")
                await asyncio.sleep(0.4)
            except (ConnectionError, OSError) as e:
                last_exc = e
                logging.warning(f"GetFile connection error at offset={off} (attempt {attempt + 1}/4), client {index}: {e!r}")
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
        logging.error(f"GetFile permanently failed at offset={off} after 4 attempts, client {index}: {last_exc!r}")
        raise last_exc

    # ---------------- chunk cache ----------------
    @staticmethod
    def _key(file_id: FileId, idx: int, chunk_size: int) -> tuple:
        return (file_id.unique_id, chunk_size, idx)

    def _chunk_future(self, file_id: FileId, idx: int, chunk_size: int, index: int, prio: int = 1) -> "asyncio.Future":
        """The (shared) download of chunk `idx`. It runs as its own task, not as part of any HTTP request."""
        key = self._key(file_id, idx, chunk_size)
        fut = self._chunks.get(key)
        if fut is not None:
            self._chunks.move_to_end(key)
            if prio < self._prio.get(key, 1) and key not in self._started and not fut.done():
                self._prio[key] = prio
                self._gate.bump(key, prio)
            return fut
        self._prio[key] = prio
        fut = asyncio.ensure_future(self._fetch_chunk(file_id, idx, chunk_size, index, key))
        self._chunks[key] = fut
        fut.add_done_callback(lambda f, k=key: self._chunk_done(k, f))
        return fut

    def _release_want(self, key: tuple) -> None:
        """A stream no longer needs this chunk (it was read, or the player left / seeked away). If nobody
        else wants it and it has not been requested from Telegram yet, drop it - otherwise the read-ahead
        of the old position would keep the connections busy while the player waits for the new one."""
        n = self._want.get(key, 0) - 1
        if n > 0:
            self._want[key] = n
            return
        self._want.pop(key, None)
        fut = self._chunks.get(key)
        if fut is not None and not fut.done() and key not in self._started:
            fut.cancel()
            self._chunks.pop(key, None)

    def _chunk_done(self, key, fut) -> None:
        self._started.discard(key)
        self._prio.pop(key, None)
        if fut.cancelled() or fut.exception() is not None:   # exception() also marks it as retrieved
            self._chunks.pop(key, None)                      # never keep a failure: the next request tries again
            return
        n = len(fut.result())
        self._chunk_len[key] = n
        self._chunk_bytes += n
        if self._chunk_bytes > CHUNK_CACHE_BYTES:
            for k in list(self._chunks):
                if self._chunk_bytes <= CHUNK_CACHE_BYTES:
                    break
                if self._chunks[k].done():
                    self._chunks.pop(k, None)
                    self._want.pop(k, None)
                    self._chunk_bytes -= self._chunk_len.pop(k, 0)

    async def _new_media_session(self, dc_id: int) -> Session:
        """An extra media connection to a DC (same steps as generate_media_session, but not stored as THE session)."""
        client = self.client
        if dc_id != await client.storage.dc_id():
            session = Session(
                client, dc_id,
                await Auth(client, dc_id, await client.storage.test_mode()).create(),
                await client.storage.test_mode(), is_media=True,
            )
            await session.start()
            for _ in range(6):
                exported_auth = await client.invoke(raw.functions.auth.ExportAuthorization(dc_id=dc_id))
                try:
                    await session.send(raw.functions.auth.ImportAuthorization(id=exported_auth.id, bytes=exported_auth.bytes))
                    break
                except AuthBytesInvalid:
                    continue
            else:
                await session.stop()
                raise AuthBytesInvalid
        else:
            session = Session(client, dc_id, await client.storage.auth_key(), await client.storage.test_mode(), is_media=True)
            await session.start()
        return session

    async def _grow_sessions(self, dc_id: int) -> None:
        """Open the extra connections in the background. A failure only means fewer connections (and a retry later)."""
        try:
            extras = self._extra_sessions.setdefault(dc_id, [])
            while len(extras) < SESSIONS_PER_DC - 1:
                extras.append(await self._new_media_session(dc_id))
                self._gate.set_capacity(MAX_PER_SESSION * (1 + len(extras)))
                logging.info(f"Opened extra media connection {len(extras) + 1}/{SESSIONS_PER_DC} to DC {dc_id}")
            self._growing[dc_id] = float("inf")           # done: never try again
        except Exception as e:
            logging.warning(f"extra media connection to DC {dc_id} failed ({type(e).__name__}: {e}); using fewer, retry in 60s")
            self._growing[dc_id] = time.monotonic() + 60

    async def _pick_session(self, file_id: FileId) -> Session:
        """The least busy connection to the file's DC; also starts opening the extra ones the first time."""
        primary = await self.generate_media_session(self.client, file_id)
        dc = file_id.dc_id
        extras = self._extra_sessions.get(dc, [])
        if len(extras) < SESSIONS_PER_DC - 1:
            state = self._growing.get(dc, 0)
            if state == 0 or (state != float("inf") and time.monotonic() > state and state > 1):
                self._growing[dc] = 1                     # "busy opening"
                asyncio.ensure_future(self._grow_sessions(dc))
        return min([primary] + extras, key=lambda x: self._session_load.get(id(x), 0))

    async def _fetch_chunk(self, file_id: FileId, idx: int, chunk_size: int, index: int, key: tuple) -> bytes:
        await self._gate.acquire(key, self._prio.get(key, 1))
        self._started.add(key)
        try:
            session = await self._pick_session(file_id)
            sid = id(session)
            self._session_load[sid] = self._session_load.get(sid, 0) + 1
            try:
                location = await self.get_location(file_id)
                r = await self._get_chunk({"session": session}, file_id, location, idx * chunk_size, chunk_size, index)
            finally:
                self._session_load[sid] -= 1
        finally:
            self._gate.release()
        if not isinstance(r, raw.types.upload.File):
            raise ConnectionError("unexpected GetFile reply")
        return r.bytes

    def warm(self, file_id: FileId, head: int = 3, tail: int = 3, chunk_size: int = 1024 * 1024, index: int = 0):
        """Start downloading the first `head` and last `tail` chunks of a file in the background and return the
        futures of the first ones. A player asks for the beginning first, and (MP4 'moov' / MKV index) for the
        end of the file next - both are then answered from memory. These chunks stay until evicted."""
        total = max(1, math.ceil((getattr(file_id, "file_size", 0) or 0) / chunk_size))
        n_head = min(head, total)
        wanted = list(range(n_head)) + list(range(max(n_head, total - tail), total))
        futs = []
        for i in wanted:
            futs.append(self._chunk_future(file_id, i, chunk_size, index, 0))
            key = self._key(file_id, i, chunk_size)
            self._want[key] = self._want.get(key, 0) + 1          # pinned: never released
        return futs[:n_head]

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

        Chunks come from the shared chunk cache; up to PREFETCH_PARTS are requested ahead of the one being
        sent, so Telegram latency overlaps with the time spent writing to the player. `offset` is always
        a multiple of `chunk_size`.
        """
        work_loads[index] += 1
        wanted = deque()            # (key, future) in the order the player needs them
        current = None
        try:
            first_idx = offset // chunk_size
            next_part = 0

            def schedule():
                nonlocal next_part
                while next_part < part_count and len(wanted) < PREFETCH_PARTS:
                    i = first_idx + next_part
                    # the first two chunks of a request are what the player is waiting for: urgent
                    fut = self._chunk_future(file_id, i, chunk_size, index, 0 if next_part < 2 else 1)
                    key = self._key(file_id, i, chunk_size)
                    self._want[key] = self._want.get(key, 0) + 1
                    wanted.append((key, fut))
                    next_part += 1

            schedule()
            current_part = 1
            while wanted:
                current = wanted.popleft()
                if not current[1].done():
                    self._gate.bump(current[0], 0)       # the player is waiting for it now
                # shield: if the player drops this request, the download itself keeps going for the next one
                chunk = await asyncio.shield(current[1])
                self._release_want(current[0])
                current = None
                schedule()  # keep the pipeline full while this chunk is written out
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
        except (TimeoutError, asyncio.TimeoutError, ConnectionError, OSError, AttributeError, RPCError) as e:
            # _get_chunk already logged the details; the caller notices the short
            # body and closes the connection so the player retries immediately.
            logging.warning(f"yield_file aborted early for client {index}: {type(e).__name__}: {e}")
        finally:
            if current is not None:
                self._release_want(current[0])
            for key, _ in wanted:
                self._release_want(key)      # read-ahead nobody needs any more (seek / closed) is dropped
            work_loads[index] -= 1
