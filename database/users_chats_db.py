import re
import os
import asyncio
import logging
from pymongo.errors import (DuplicateKeyError, ServerSelectionTimeoutError, PyMongoError,
                            OperationFailure, AutoReconnect, ConnectionFailure)
import motor.motor_asyncio
from pymongo import MongoClient
from database.extra_dbs import extra_db_uris
from info import DATABASE_NAME, USER_DB_URI, OTHER_DB_URI, PREMIUM_DB_URI, CUSTOM_FILE_CAPTION, IMDB, IMDB_TEMPLATE, MELCOW_NEW_USERS, BUTTON_MODE, SPELL_CHECK_REPLY, PROTECT_CONTENT, AUTO_DELETE, MAX_BTN, AUTO_FFILTER, SHORTLINK_API, SHORTLINK_URL, SHORTLINK_MODE, TUTORIAL, IS_TUTORIAL
import time
import datetime

logger = logging.getLogger(__name__)

my_client = MongoClient(
    OTHER_DB_URI,
    serverSelectionTimeoutMS=15000,   # fail in 15s instead of the 30s default
    connectTimeoutMS=15000,
    retryReads=True,
    retryWrites=True,
)
mydb = my_client["referal_user"]

# NOTE: my_client above is a plain SYNCHRONOUS pymongo client (unlike `db`
# below, which uses motor's async driver). Every call on it below is
# wrapped in asyncio.to_thread() so a slow/laggy response from this
# cluster only blocks the one request waiting on it - not the bot's whole
# event loop (which would otherwise freeze every user's buttons at once
# for however long that one Mongo call takes).

async def referal_add_user(user_id, ref_user_id):
    user_db = mydb[str(user_id)]
    user = {'_id': ref_user_id}
    try:
        await asyncio.to_thread(user_db.insert_one, user)
        return True
    except DuplicateKeyError:
        return False
    

async def get_referal_all_users(user_id):
    user_db = mydb[str(user_id)]
    # Materialize to a list inside the thread - iterating a pymongo
    # cursor is itself blocking network I/O, so doing that on the main
    # event loop later would reintroduce the exact same freeze.
    return await asyncio.to_thread(lambda: list(user_db.find()))
    
async def get_referal_users_count(user_id):
    user_db = mydb[str(user_id)]
    count = await asyncio.to_thread(user_db.count_documents, {})
    return count
    

async def delete_all_referal_users(user_id):
    user_db = mydb[str(user_id)]
    await asyncio.to_thread(user_db.delete_many, {})

default_setgs = {
    'button': BUTTON_MODE,
    'file_secure': PROTECT_CONTENT,
    'imdb': IMDB,
    'spell_check': SPELL_CHECK_REPLY,
    'welcome': MELCOW_NEW_USERS,
    'auto_delete': AUTO_DELETE,
    'auto_ffilter': AUTO_FFILTER,
    'max_btn': MAX_BTN,
    'template': IMDB_TEMPLATE,
    'caption': CUSTOM_FILE_CAPTION,
    'shortlink': SHORTLINK_URL,
    'shortlink_api': SHORTLINK_API,
    'is_shortlink': SHORTLINK_MODE,
    'fsub': None,
    'tutorial': TUTORIAL,
    'is_tutorial': IS_TUTORIAL
}


# ══════════════════════════════════════════════════════════════════════════════
#  MANY DATABASES, ONE AFTER ANOTHER  (DB1 -> DB2 -> DB3 -> ...)
#
#  Clusters are listed in this order:
#      1) DATABASE_URI
#      2) PREMIUM_DB_URI (old name, still works), then DB2_URI, DB3_URI, DB4_URI ... DB20_URI
#         (and optionally EXTRA_DB_URIS = several URIs separated by spaces)
#
#  * NEW DATA / WRITES go to the FIRST cluster (in the order above) that still accepts writes,
#    so the clusters fill up one after another: 1st, then 2nd, then 3rd ...
#  * When a cluster answers "over your space quota" (or is unreachable) it is skipped
#    automatically (re-tried every 10 minutes) and the next one is used - no restart needed.
#  * READS look in every cluster, newest first. A newer copy of a record always wins.
#  * An old record is NEVER lost: reading works on a full Atlas cluster. The first time such
#    a record has to be changed it is moved to a cluster that has space (same _id), the
#    change is applied there and the old copy is deleted (deleting is allowed when full).
#  * When all of them are full: add DB<next>_URI in Koyeb and redeploy. Nothing else.
#
#  With only DATABASE_URI set none of this is used and the bot behaves exactly as before.
# ══════════════════════════════════════════════════════════════════════════════
_NO_KEY = object()


def _is_full_error(e):
    """Atlas / MongoDB 'writes are blocked' errors."""
    if isinstance(e, OperationFailure) and getattr(e, "code", None) == 8000:
        return True
    t = str(e).lower()
    return "space quota" in t or "writes are blocked" in t or "no space left" in t or "disk full" in t


def _is_down_error(e):
    return isinstance(e, (ServerSelectionTimeoutError, AutoReconnect, ConnectionFailure))


def _host_of(uri):
    """Only the host of a Mongo URI - never the user name / password."""
    try:
        rest = (uri or "").split("://", 1)[-1].split("@", 1)[-1]
        return rest.split("/", 1)[0].split("?", 1)[0] or "(empty)"
    except Exception:
        return "?"


def _cluster_uris(first_uri, premium_uri=None):
    """DATABASE_URI first, then every extra database in order, without duplicates.
    (The list of extra databases is shared with the file index - see database/extra_dbs.py)"""
    uris = [first_uri]
    for u in extra_db_uris(premium_uri):
        if u not in uris:
            uris.append(u)
    return uris


# key_field    : field that identifies a record -> lets old clusters be skipped without a query
# unique_field : must stay unique across ALL clusters
# exact_count  : count_documents() de-duplicates across clusters (small collections only)
_COLLECTION_SPECS = {
    "users": dict(key_field="id"),
    "groups": dict(key_field="id"),
    "clone_bots": dict(),
    "movie_updates": dict(key_field="_id", unique_field="_id"),
    "uersz": dict(key_field="id", exact_count=True),
    "payment_requests": dict(key_field="_id", exact_count=True),
    "pending_screenshots": dict(key_field="user_id", exact_count=True),
    "payment_signatures": dict(key_field="key", unique_field="key", exact_count=True),
    "support_links": dict(exact_count=True),
}


class _Result:
    """Minimal stand-in for pymongo's UpdateResult / DeleteResult."""

    def __init__(self, matched_count=0, modified_count=0, upserted_id=None, deleted_count=0):
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.upserted_id = upserted_id
        self.deleted_count = deleted_count
        self.acknowledged = True


def _apply_projection(doc, projection):
    """Tiny stand-in for a Mongo projection (documents are fetched whole, then trimmed here)."""
    if not projection:
        return doc
    keep_id = bool(projection.get("_id", 1))
    wanted = {k.split(".")[0] for k, v in projection.items() if k != "_id" and v}
    if wanted:
        out = {k: doc[k] for k in wanted if k in doc}
    else:
        dropped = {k for k, v in projection.items() if k != "_id" and not v}
        out = {k: v for k, v in doc.items() if k not in dropped}
    if keep_id and "_id" in doc:
        out["_id"] = doc["_id"]
    else:
        out.pop("_id", None)
    return out


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class _ClusterPool:
    """Which clusters can take writes right now."""
    FULL_RETRY = 600     # seconds a FULL cluster is skipped before it is tried again
    DOWN_RETRY = 90      # seconds an unreachable cluster is skipped

    def __init__(self, hosts):
        self.hosts = list(hosts)
        self.n = len(self.hosts)
        self.skip_until = [0.0] * self.n

    def usable(self, i):
        return time.monotonic() >= self.skip_until[i]

    def write_order(self):
        """First usable cluster first (1st, 2nd, 3rd ...). Clusters that were marked full/unreachable
        come last, so space you free up (or a cluster that came back) is found at once if nothing
        else works."""
        in_order = list(range(self.n))
        return [i for i in in_order if self.usable(i)] + [i for i in in_order if not self.usable(i)]

    def ok(self, i):
        self.skip_until[i] = 0.0

    def mark_bad(self, i, err):
        full = _is_full_error(err)
        was_usable = self.usable(i)
        self.skip_until[i] = time.monotonic() + (self.FULL_RETRY if full else self.DOWN_RETRY)
        if was_usable:
            nxt = [j for j in range(self.n) if j != i and self.usable(j)]
            where = f"cluster #{nxt[0] + 1}" if nxt else "NOWHERE - every cluster is full, add a new DB (DB<next>_URI)"
            print(f"[db] cluster #{i + 1} ({self.hosts[i]}) is {'FULL' if full else 'unreachable'} -> new data goes to {where}")


class _MergedCursor:
    """What .find() returns: supports .sort(), .limit(), `async for` and .to_list()."""

    def __init__(self, coll, flt):
        self._coll = coll
        self._flt = flt or {}
        self._sort = []
        self._limit = 0

    def sort(self, key_or_list, direction=1):
        self._sort = [(key_or_list, direction)] if isinstance(key_or_list, str) else list(key_or_list)
        return self

    def limit(self, n):
        self._limit = int(n or 0)
        return self

    async def _fetch(self):
        docs = [d async for d in self._coll._stream(self._flt)]
        for key, direction in reversed(self._sort):
            docs.sort(key=lambda d, k=key: (d.get(k) is None, d.get(k)), reverse=(direction == -1))
        if self._limit > 0:
            docs = docs[:self._limit]
        return docs

    async def to_list(self, length=None):
        docs = await self._fetch()
        return docs[:length] if length else docs

    def __aiter__(self):
        if not self._sort and not self._limit:
            return self._coll._stream(self._flt)      # streams: big collections are not loaded into memory
        return self._iterate()

    async def _iterate(self):
        for doc in await self._fetch():
            yield doc


class _MultiCollection:
    """One logical collection spread over several clusters (see the block comment above)."""

    def __init__(self, name, cols, pool, key_field=None, unique_field=None, exact_count=False):
        self.name = name
        self.cols = cols
        self.pool = pool
        self.n = len(cols)
        self.top = self.n - 1
        self.key_field = key_field
        self.unique_field = unique_field
        self.exact_count = exact_count
        self._keys = [None] * self.n          # per OLD cluster: set of key values it holds
        self._keys_try = [0.0] * self.n
        self._lock = asyncio.Lock()

    def __getattr__(self, item):
        if item.startswith("_") or item in ("name", "cols", "pool", "n", "top", "key_field",
                                            "unique_field", "exact_count"):
            raise AttributeError(item)
        return getattr(self.cols[self.top], item)

    # ── key shortcut: old clusters are skipped without a query when they cannot hold a key ──
    def _simple_key(self, flt):
        if self.key_field and isinstance(flt, dict) and len(flt) == 1 and self.key_field in flt:
            v = flt[self.key_field]
            if not isinstance(v, (dict, list)):
                return v
        return _NO_KEY

    async def _keyset(self, i):
        if self.key_field is None or i == self.top:
            return None                        # the newest cluster is always asked directly
        if self._keys[i] is not None:
            return self._keys[i]
        async with self._lock:
            if self._keys[i] is not None:
                return self._keys[i]
            if time.monotonic() - self._keys_try[i] < 60:
                return None
            self._keys_try[i] = time.monotonic()
            try:
                proj = {self.key_field: 1}
                if self.key_field != "_id":
                    proj["_id"] = 0
                keys = set()
                async for d in self.cols[i].find({}, proj):
                    v = d.get(self.key_field)
                    if v is not None:
                        try:
                            keys.add(v)
                        except TypeError:
                            pass
                self._keys[i] = keys
                print(f"[db] {self.name}: {len(keys)} records on cluster #{i + 1} (older cluster, read as fallback)")
            except Exception as e:
                logger.warning(f"[db] {self.name}: could not list records of cluster #{i + 1}: {e}")
                return None
        return self._keys[i]

    async def _may_have(self, i, flt):
        k = self._simple_key(flt)
        if k is _NO_KEY:
            return True
        ks = await self._keyset(i)
        if ks is None:
            return True
        try:
            return k in ks
        except TypeError:
            return True

    def _invalidate(self, i):
        """Something was written into an OLD cluster -> forget its cached key list."""
        if i != self.top and self._keys[i] is not None:
            self._keys[i] = None
            self._keys_try[i] = 0.0

    # ── locating records ────────────────────────────────────────────────────
    async def _in_newer(self, doc, i):
        """True when a NEWER cluster already holds this record (that copy is the real one)."""
        if i >= self.top:
            return False
        kv = doc.get(self.key_field) if self.key_field else None
        for j in range(self.top, i, -1):
            if kv is not None and j != self.top:
                ks = await self._keyset(j)
                if ks is not None:
                    try:
                        if kv not in ks:
                            continue
                    except TypeError:
                        pass
            conds = [{"_id": doc["_id"]}]
            if kv is not None and self.key_field != "_id":
                conds.append({self.key_field: kv})
            try:
                if await self.cols[j].find_one({"$or": conds}, {"_id": 1}) is not None:
                    return True
            except Exception as e:
                logger.warning(f"[db] {self.name}: read on cluster #{j + 1} failed: {e}")
        return False

    async def _locate(self, flt):
        """(cluster index, whole document) of the real record matching flt, else (None, None)."""
        last_err, answered = None, False
        for i in range(self.top, -1, -1):
            if not await self._may_have(i, flt):
                answered = True
                continue
            try:
                doc = await self.cols[i].find_one(flt)
            except Exception as e:
                last_err = e
                logger.warning(f"[db] {self.name}: read on cluster #{i + 1} failed: {e}")
                continue
            answered = True
            if doc is None:
                continue
            if await self._in_newer(doc, i):
                return None, None              # newer copy exists (and does not match) -> no match
            return i, doc
        if not answered and last_err is not None:
            raise last_err
        return None, None

    # ── reading ─────────────────────────────────────────────────────────────
    async def find_one(self, flt=None, projection=None, *args, **kwargs):
        _, doc = await self._locate(flt or {})
        if doc is None:
            return None
        return _apply_projection(doc, projection) if projection is not None else doc

    def find(self, flt=None, *args, **kwargs):
        return _MergedCursor(self, flt)

    async def _uncovered(self, docs, i):
        """Documents of OLD cluster i that no newer cluster holds a copy of."""
        ids = [d["_id"] for d in docs]
        use_key = bool(self.key_field) and self.key_field != "_id"
        kvals = [d[self.key_field] for d in docs if use_key and d.get(self.key_field) is not None]
        covered_ids, covered_keys = set(), set()
        for j in range(self.top, i, -1):
            conds = [{"_id": {"$in": ids}}]
            proj = {"_id": 1}
            if kvals:
                conds.append({self.key_field: {"$in": kvals}})
                proj[self.key_field] = 1
            try:
                async for d in self.cols[j].find({"$or": conds}, proj):
                    covered_ids.add(d["_id"])
                    if use_key and d.get(self.key_field) is not None:
                        covered_keys.add(d[self.key_field])
            except Exception as e:
                logger.warning(f"[db] {self.name}: read on cluster #{j + 1} failed: {e}")
        out = []
        for d in docs:
            if d["_id"] in covered_ids:
                continue
            if use_key:
                try:
                    if d.get(self.key_field) in covered_keys:
                        continue
                except TypeError:
                    pass
            out.append(d)
        return out

    async def _stream(self, flt):
        """Every matching record once, newest cluster first; a newer copy hides older ones."""
        flt = flt or {}
        done, last_err = 0, None
        for i in range(self.top, -1, -1):
            try:
                batch = []
                async for d in self.cols[i].find(flt):
                    if i == self.top:
                        yield d
                        continue
                    batch.append(d)
                    if len(batch) >= 500:
                        for x in await self._uncovered(batch, i):
                            yield x
                        batch = []
                if batch:
                    for x in await self._uncovered(batch, i):
                        yield x
                done += 1
            except Exception as e:
                last_err = e
                logger.warning(f"[db] {self.name}: listing cluster #{i + 1} failed: {e}")
        if done == 0 and last_err is not None:
            raise last_err

    async def count_documents(self, flt=None, *args, **kwargs):
        flt = flt or {}
        if self.exact_count:
            return sum([1 async for _ in self._stream(flt)])
        total = 0
        for i in range(self.n):
            try:
                total += await self.cols[i].count_documents(flt)
            except Exception as e:
                logger.warning(f"[db] {self.name}: count on cluster #{i + 1} failed: {e}")
        return total

    # ── writing ─────────────────────────────────────────────────────────────
    async def _check_unique(self, value):
        flt = {self.unique_field: value}
        for i in range(self.top, -1, -1):
            if self.key_field == self.unique_field and not await self._may_have(i, flt):
                continue
            try:
                if await self.cols[i].find_one(flt, {"_id": 1}) is not None:
                    raise DuplicateKeyError(f"E11000 duplicate key error ({self.name}.{self.unique_field}, cluster #{i + 1})")
            except DuplicateKeyError:
                raise
            except Exception as e:
                logger.warning(f"[db] {self.name}: uniqueness check on cluster #{i + 1} failed: {e}")

    async def insert_one(self, doc, *args, **kwargs):
        if self.unique_field and doc.get(self.unique_field) is not None:
            await self._check_unique(doc[self.unique_field])
        last = None
        for t in self.pool.write_order():
            try:
                res = await self.cols[t].insert_one(doc, *args, **kwargs)
                self.pool.ok(t)
                self._invalidate(t)
                return res
            except Exception as e:
                if _is_full_error(e) or _is_down_error(e):
                    self.pool.mark_bad(t, e)
                    last = e
                    continue
                raise
        raise last or OperationFailure("space quota: every database is full - add a new DB", 8000)

    async def _upsert_new(self, flt, update, *args, **kwargs):
        last = None
        for t in self.pool.write_order():
            try:
                res = await self.cols[t].update_one(flt, update, upsert=True, *args, **kwargs)
                self.pool.ok(t)
                if res.upserted_id is not None:
                    self._invalidate(t)
                return res
            except Exception as e:
                if _is_full_error(e) or _is_down_error(e):
                    self.pool.mark_bad(t, e)
                    last = e
                    continue
                raise
        raise last or OperationFailure("space quota: every database is full - add a new DB", 8000)

    async def _move_and_update(self, holder, doc, flt, update, *args, **kwargs):
        """The cluster that holds the record refuses writes: copy the record to a cluster that
        accepts them (same _id), apply the update there and delete the old copy."""
        last = None
        for t in self.pool.write_order():
            if t == holder:
                continue
            try:
                try:
                    await self.cols[t].insert_one(dict(doc))
                except DuplicateKeyError:
                    pass
                self._invalidate(t)
                if t < holder:
                    # moving to an OLDER cluster: the newer copy would hide the updated one -> remove it first
                    await self.cols[holder].delete_one({"_id": doc["_id"]})
                res = await self.cols[t].update_one({"$and": [{"_id": doc["_id"]}, flt]}, update, *args, **kwargs)
                self.pool.ok(t)
                if t > holder:
                    try:
                        await self.cols[holder].delete_one({"_id": doc["_id"]})
                    except Exception as e:
                        logger.warning(f"[db] {self.name}: could not delete the old copy on cluster #{holder + 1}: {e}")
                return res
            except Exception as e:
                if _is_full_error(e) or _is_down_error(e):
                    self.pool.mark_bad(t, e)
                    last = e
                    continue
                raise
        raise last or OperationFailure("space quota: every database is full - add a new DB", 8000)

    async def update_one(self, flt, update, upsert=False, *args, **kwargs):
        flt = flt or {}
        holder, doc = await self._locate(flt)
        if holder is None:
            if not upsert:
                return _Result()
            return await self._upsert_new(flt, update, *args, **kwargs)
        if self.pool.usable(holder):
            try:
                return await self.cols[holder].update_one(flt, update, *args, **kwargs)
            except Exception as e:
                if not (_is_full_error(e) or _is_down_error(e)):
                    raise
                self.pool.mark_bad(holder, e)
        return await self._move_and_update(holder, doc, flt, update, *args, **kwargs)

    async def update_many(self, flt, update, *args, **kwargs):
        matched = modified = 0
        for i in range(self.n):
            try:
                r = await self.cols[i].update_many(flt, update, *args, **kwargs)
                matched += r.matched_count
                modified += r.modified_count
            except Exception as e:
                logger.warning(f"[db] {self.name}: update_many skipped on cluster #{i + 1}: {e}")
        return _Result(matched_count=matched, modified_count=modified)

    async def find_one_and_delete(self, flt, *args, **kwargs):
        for i in range(self.top, -1, -1):
            if not await self._may_have(i, flt):
                continue
            try:
                doc = await self.cols[i].find_one_and_delete(flt, *args, **kwargs)
            except Exception as e:
                logger.warning(f"[db] {self.name}: delete on cluster #{i + 1} failed: {e}")
                continue
            if doc is not None:
                return doc
        return None

    async def delete_one(self, flt, *args, **kwargs):
        for i in range(self.top, -1, -1):
            try:
                r = await self.cols[i].delete_one(flt, *args, **kwargs)
                if r.deleted_count:
                    return r
            except Exception as e:
                logger.warning(f"[db] {self.name}: delete on cluster #{i + 1} failed: {e}")
        return _Result()

    async def delete_many(self, flt, *args, **kwargs):
        total = 0
        for i in range(self.n):
            try:
                total += (await self.cols[i].delete_many(flt, *args, **kwargs)).deleted_count
            except Exception as e:
                logger.warning(f"[db] {self.name}: delete on cluster #{i + 1} failed: {e}")
        return _Result(deleted_count=total)

    async def create_index(self, *args, **kwargs):
        last = None
        for t in self.pool.write_order():
            try:
                res = await self.cols[t].create_index(*args, **kwargs)
                self.pool.ok(t)
                return res
            except Exception as e:
                if _is_full_error(e) or _is_down_error(e):
                    self.pool.mark_bad(t, e)
                    last = e
                    continue
                raise
        raise last or OperationFailure("space quota: every database is full - add a new DB", 8000)


class _MultiDatabase:
    """Stands in for `db.db`: every collection name gives a _MultiCollection over all clusters."""

    def __init__(self, dbs, pool, specs):
        self._dbs = dbs
        self._pool = pool
        self._specs = specs
        self._cache = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def __getitem__(self, name):
        if name not in self._cache:
            self._cache[name] = _MultiCollection(name, [d[name] for d in self._dbs], self._pool,
                                                 **self._specs.get(name, {}))
        return self._cache[name]

    async def command(self, cmd, *args, **kwargs):
        if cmd == "dbstats":                     # total size over all clusters
            total = {}
            for d in self._dbs:
                try:
                    st = await d.command("dbstats")
                except Exception:
                    continue
                for k, v in st.items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        total[k] = total.get(k, 0) + v
            return total
        return await self._dbs[-1].command(cmd, *args, **kwargs)


class Database:
    
    def __init__(self, uri, database_name, premium_uri=None):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(
            uri,
            serverSelectionTimeoutMS=15000,   # fail in 15s instead of the 30s default
            connectTimeoutMS=15000,
            retryReads=True,
            retryWrites=True,
            maxPoolSize=50,
        )
        # Every database cluster, in order: DATABASE_URI, then PREMIUM_DB_URI / DB2_URI / DB3_URI ...
        self.cluster_uris = _cluster_uris(uri, premium_uri)
        self.cluster_hosts = [_host_of(u) for u in self.cluster_uris]
        self._clients = [self._client] + [
            motor.motor_asyncio.AsyncIOMotorClient(
                u,
                serverSelectionTimeoutMS=15000,
                connectTimeoutMS=15000,
                retryReads=True,
                retryWrites=True,
                maxPoolSize=20,
            ) for u in self.cluster_uris[1:]
        ]
        self._dbs = [c[database_name] for c in self._clients]
        self.premium_db = self._dbs[-1]
        if len(self._dbs) == 1:
            # one database only: plain collections, exactly like before
            self.pool = None
            self.db = self._dbs[0]
        else:
            # several databases: writes go to the first one (in order) that has space, reads look in all
            self.pool = _ClusterPool(self.cluster_hosts)
            self.db = _MultiDatabase(self._dbs, self.pool, _COLLECTION_SPECS)
            print("[db] " + str(len(self._dbs)) + " databases configured: "
                  + ", ".join(f"#{i + 1} {h}" for i, h in enumerate(self.cluster_hosts))
                  + " - new data goes to the first one (in this order) that still has space")
        self.col = self.db.users
        self.grp = self.db.groups
        self.bot = self.db.clone_bots

        # Premium + payment data (same databases, same automatic fail-over as everything else).
        self.users = self.db.uersz
        self.payment_requests = self.db.payment_requests
        self.pending_screenshots = self.db.pending_screenshots
        self.payment_signatures = self.db.payment_signatures   # unique-key claims: one real payment = one premium
        self.support_links = self.db.support_links

        # Safety net for the daily expiry notifier: if marking a user as "notified" ever fails
        # (database full / down), remember it here so the same "premium ended" message is not
        # re-sent to the same people every 5 minutes. Cleared again when premium is renewed.
        self._reminder_sent_mem = set()
        self._expired_notified_mem = set()

    async def _safe_find_one(self, collection, filter_query, projection=None):
        """find_one wrapped so a transient replica-set election (no primary
        momentarily available) returns None instead of raising and crashing
        the caller. Every call site below already treats a falsy/None
        result as "not found", so this is a safe drop-in."""
        try:
            if projection is not None:
                return await collection.find_one(filter_query, projection)
            return await collection.find_one(filter_query)
        except ServerSelectionTimeoutError:
            return None


    def new_user(self, id, name):
        return dict(
            id = id,
            name = name,
            file_id=None,
            caption=None,
            message_command=None,
            save=False,
            ban_status=dict(
                is_banned=False,
                ban_reason="",
            ),
        )


    def new_group(self, id, title):
        return dict(
            id = id,
            title = title,
            chat_status=dict(
                is_disabled=False,
                reason="",
            ),
            settings=default_setgs
        )
    
    async def add_user(self, id, name):
        user = self.new_user(id, name)
        await self.col.insert_one(user)
    
    async def is_user_exist(self, id):
        user = await self._safe_find_one(self.col, {'id':int(id)})
        return bool(user)
    
    async def total_users_count(self):
        count = await self.col.count_documents({})
        return count

    async def add_clone_bot(self, bot_id, user_id, bot_token):
        settings = {
            'bot_id': bot_id,
            'bot_token': bot_token,
            'user_id': user_id,
            'url': None,
            'api': None,
            'tutorial': None,
            'update_channel_link': None
        }
        await self.bot.insert_one(settings)

    async def is_clone_exist(self, user_id):
        clone = await self._safe_find_one(self.bot, {'user_id': int(user_id)})
        return bool(clone)

    async def delete_clone(self, user_id):
        await self.bot.delete_many({'user_id': int(user_id)})

    async def get_clone(self, user_id):
        clone_data = await self._safe_find_one(self.bot, {"user_id": user_id})
        return clone_data
            
    async def update_clone(self, user_id, user_data):
        await self.bot.update_one({"user_id": user_id}, {"$set": user_data}, upsert=True)

    async def get_bot(self, bot_id):
        bot_data = await self._safe_find_one(self.bot, {"bot_id": bot_id})
        return bot_data
            
    async def update_bot(self, bot_id, bot_data):
        await self.bot.update_one({"bot_id": bot_id}, {"$set": bot_data}, upsert=True)
    
    async def get_all_bots(self):
        return self.bot.find({})
        
    async def remove_ban(self, id):
        ban_status = dict(
            is_banned=False,
            ban_reason=''
        )
        await self.col.update_one({'id': id}, {'$set': {'ban_status': ban_status}})
    
    async def ban_user(self, user_id, ban_reason="No Reason"):
        ban_status = dict(
            is_banned=True,
            ban_reason=ban_reason
        )
        await self.col.update_one({'id': user_id}, {'$set': {'ban_status': ban_status}})

    async def get_ban_status(self, id):
        default = dict(
            is_banned=False,
            ban_reason=''
        )
        user = await self._safe_find_one(self.col, {'id':int(id)})
        if not user:
            return default
        return user.get('ban_status', default)

    async def get_all_users(self):
        return self.col.find({})
    

    async def delete_user(self, user_id):
        await self.col.delete_many({'id': int(user_id)})


    async def get_banned(self):
        users = self.col.find({'ban_status.is_banned': True})
        chats = self.grp.find({'chat_status.is_disabled': True})
        b_chats = [chat['id'] async for chat in chats]
        b_users = [user['id'] async for user in users]
        return b_users, b_chats
    


    async def add_chat(self, chat, title):
        chat = self.new_group(chat, title)
        await self.grp.insert_one(chat)
    

    async def get_chat(self, chat):
        chat = await self._safe_find_one(self.grp, {'id':int(chat)})
        return False if not chat else chat.get('chat_status')
    

    async def re_enable_chat(self, id):
        chat_status=dict(
            is_disabled=False,
            reason="",
            )
        await self.grp.update_one({'id': int(id)}, {'$set': {'chat_status': chat_status}})
        
    async def update_settings(self, id, settings):
        await self.grp.update_one({'id': int(id)}, {'$set': {'settings': settings}})
        
    
    async def get_settings(self, id):
        chat = await self._safe_find_one(self.grp, {'id':int(id)})
        if chat:
            return chat.get('settings', default_setgs)
        return default_setgs
    

    async def disable_chat(self, chat, reason="No Reason"):
        chat_status=dict(
            is_disabled=True,
            reason=reason,
            )
        await self.grp.update_one({'id': int(chat)}, {'$set': {'chat_status': chat_status}})
    

    async def total_chat_count(self):
        count = await self.grp.count_documents({})
        return count
    

    async def get_all_chats(self):
        return self.grp.find({})


    async def get_db_size(self):
        return (await self.db.command("dbstats"))['dataSize']

    async def get_user(self, user_id):
        user_data = await self._safe_find_one(self.users, {"id": user_id})
        return user_data
            
    async def update_user(self, user_data):
        await self.users.update_one({"id": user_data["id"]}, {"$set": user_data}, upsert=True)
        # Renewed premium resets the flags -> allow the reminder / expired notice again later.
        if user_data.get("expiry_reminder_sent") is False:
            self._reminder_sent_mem.discard(user_data["id"])
        if user_data.get("expired_notified") is False:
            self._expired_notified_mem.discard(user_data["id"])

    async def has_premium_access(self, user_id):
        user_data = await self.get_user(user_id)
        if user_data:
            expiry_time = user_data.get("expiry_time")
            if expiry_time is None:
                # User previously used the free trial, but it has ended.
                return False
            elif isinstance(expiry_time, datetime.datetime) and datetime.datetime.now() <= expiry_time:
                return True
            else:
                try:
                    await self.users.update_one({"id": user_id}, {"$set": {"expiry_time": None}})
                except PyMongoError as e:
                    # Only bookkeeping (marks the old plan as ended). The answer is still "no premium".
                    logger.warning(f"[premium] could not mark premium ended for {user_id}: {e}")
        return False
    
    async def check_remaining_uasge(self, userid):
        user_id = userid
        user_data = await self.get_user(user_id)        
        expiry_time = user_data.get("expiry_time")
        # Calculate remaining time
        remaining_time = expiry_time - datetime.datetime.now()
        return remaining_time

    async def get_free_trial_status(self, user_id):
        user_data = await self.get_user(user_id)
        if user_data:
            return user_data.get("has_free_trial", False)
        return False

    async def give_free_trail(self, userid):        
        user_id = userid
        seconds = 5*60         
        expiry_time = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
        user_data = {"id": user_id, "expiry_time": expiry_time, "has_free_trial": True}
        await self.users.update_one({"id": user_id}, {"$set": user_data}, upsert=True)
    
    
    async def all_premium_users(self):
        count = await self.users.count_documents({
        "expiry_time": {"$gt": datetime.datetime.now()}
        })
        return count

    async def extend_all_premium_users(self, days):
        """Extend expiry_time by `days` for every user with CURRENTLY ACTIVE
        premium (used for e.g. compensating users after server downtime).
        Returns the list of user ids that were extended."""
        now = datetime.datetime.now()
        extended_ids = []
        cursor = self.users.find({"expiry_time": {"$gt": now}})
        async for user_data in cursor:
            uid = user_data["id"]
            old_expiry = user_data.get("expiry_time")
            if not isinstance(old_expiry, datetime.datetime):
                continue
            new_expiry = old_expiry + datetime.timedelta(days=days)
            await self.users.update_one({"id": uid}, {"$set": {"expiry_time": new_expiry}})
            extended_ids.append(uid)
        return extended_ids

    async def get_all_premium_users(self):
        """All users with currently-active premium, soonest-expiring first.
        Used by /premium_list."""
        now = datetime.datetime.now()
        cursor = self.users.find({"expiry_time": {"$gt": now}}).sort("expiry_time", 1)
        return [user_data async for user_data in cursor]

    async def get_users_expiring_within(self, seconds):
        """Active-premium users whose expiry falls within the next `seconds`
        who have NOT already been sent the expiry reminder. Used by the
        daily expiry-notifier loop to send a one-time 'ends tomorrow' notice."""
        now = datetime.datetime.now()
        soon = now + datetime.timedelta(seconds=seconds)
        cursor = self.users.find({
            "expiry_time": {"$gt": now, "$lte": soon},
            "expiry_reminder_sent": {"$ne": True}
        })
        return [u async for u in cursor if u.get("id") not in self._reminder_sent_mem]

    async def mark_expiry_reminder_sent(self, user_id):
        self._reminder_sent_mem.add(user_id)      # remembered even if the DB write below fails
        await self.users.update_one({"id": user_id}, {"$set": {"expiry_reminder_sent": True}})

    async def get_recently_expired_users(self, seconds):
        """Users whose premium expired within the last `seconds` who have
        NOT already been sent the 'premium ended' notice. Used by the daily
        expiry-notifier loop to send a one-time thank-you/upsell message."""
        now = datetime.datetime.now()
        cutoff = now - datetime.timedelta(seconds=seconds)
        cursor = self.users.find({
            "expiry_time": {"$lte": now, "$gt": cutoff},
            "expired_notified": {"$ne": True}
        })
        return [u async for u in cursor if u.get("id") not in self._expired_notified_mem]

    async def mark_expired_notified(self, user_id):
        self._expired_notified_mem.add(user_id)   # remembered even if the DB write below fails
        await self.users.update_one({"id": user_id}, {"$set": {"expired_notified": True}})

    # ── One-time offer tracking ────────────────────────────────────────
    # `offer_claimed` holds the id of the last offer this user used (see
    # plan_pricing.py). Same id as the running offer = already used, so they
    # pay the standard price; a brand new offer has a new id = usable again.
    async def get_offer_claimed(self, user_id):
        user_data = await self._safe_find_one(self.users, {"id": int(user_id)}, {"offer_claimed": 1, "_id": 0})
        return user_data.get("offer_claimed") if user_data else None

    async def claim_offer(self, user_id, offer_id) -> bool:
        """Atomically marks `offer_id` as used by this user. True = this call
        claimed it; False = it had already been claimed before (so two
        payments racing each other can never both count as the 'first')."""
        uid = int(user_id)
        # Make sure the user's document exists first (a no-op for existing users),
        # so the claim below is ONE atomic conditional update in every case -
        # Mongo lets exactly one of several simultaneous callers match the
        # "not already claimed" filter.
        await self.users.update_one({"id": uid}, {"$setOnInsert": {"id": uid}}, upsert=True)
        res = await self.users.update_one(
            {"id": uid, "offer_claimed": {"$ne": offer_id}},
            {"$set": {"offer_claimed": offer_id, "offer_claimed_at": datetime.datetime.now()}},
        )
        return res.matched_count == 1

    # ================== [PERSISTENT DAILY VERIFICATION] ==================
    # These mirror the free-trial/premium pattern above but store the
    # "verified until" date in Mongo instead of an in-memory dict, so
    # verification status survives bot restarts/redeploys.
    async def set_verified(self, user_id, expiry_date_str):
        await self.users.update_one(
            {"id": user_id},
            {"$set": {"verified_until": expiry_date_str}},
            upsert=True
        )

    async def is_verified_today(self, user_id):
        user_data = await self.get_user(user_id)
        if not user_data:
            return False
        expiry_date_str = user_data.get("verified_until")
        if not expiry_date_str:
            return False
        try:
            years, month, day = expiry_date_str.split('-')
            comp = datetime.date(int(years), int(month), int(day))
        except Exception:
            return False
        return comp >= datetime.date.today()

    async def get_all_verified_users(self):
        """All users whose persisted daily verification is still valid
        today (mirrors the same comp >= today logic as is_verified_today).
        Used by the owner-only /verification command."""
        today = datetime.date.today()
        cursor = self.users.find({"verified_until": {"$exists": True, "$ne": None}})
        verified = []
        async for user_data in cursor:
            expiry_date_str = user_data.get("verified_until")
            try:
                years, month, day = expiry_date_str.split('-')
                comp = datetime.date(int(years), int(month), int(day))
            except Exception:
                continue
            if comp >= today:
                verified.append(user_data)
        return verified

    async def set_thumbnail(self, id, file_id):
        await self.col.update_one({'id': int(id)}, {'$set': {'file_id': file_id}})

    async def get_thumbnail(self, id):
        user = await self._safe_find_one(self.col, {'id': int(id)})
        return user.get('file_id', None) if user else None

    async def set_caption(self, id, caption):
        await self.col.update_one({'id': int(id)}, {'$set': {'caption': caption}})

    async def get_caption(self, id):
        user = await self._safe_find_one(self.col, {'id': int(id)})
        return user.get('caption', None) if user else None

    async def set_msg_command(self, id, com):
        await self.col.update_one({'id': int(id)}, {'$set': {'message_command': com}})

    async def get_msg_command(self, id):
        user = await self._safe_find_one(self.col, {'id': int(id)})
        return user.get('message_command', None) if user else None

    async def set_save(self, id, save):
        await self.col.update_one({'id': int(id)}, {'$set': {'save': save}})

    async def get_save(self, id):
        user = await self._safe_find_one(self.col, {'id': int(id)})
        return user.get('save', False) if user else False

    async def get_bot_setting(self, bot_id, setting_key, default_value):
        bot = await self._safe_find_one(self.col, {'id': int(bot_id)}, {setting_key: 1, '_id': 0})
        return bot[setting_key] if bot and setting_key in bot else default_value

    async def update_bot_setting(self, bot_id, setting_key, value):
        await self.col.update_one(
            {'id': int(bot_id)},
            {'$set': {setting_key: value}},
            upsert=True
        )

    async def movie_update_status(self, bot_id):
        from info import MOVIE_UPDATE_NOTIFICATION
        return await self.get_bot_setting(bot_id, 'MOVIE_UPDATE_NOTIFICATION', MOVIE_UPDATE_NOTIFICATION)

    async def update_movie_update_status(self, bot_id, enable):
        await self.update_bot_setting(bot_id, 'MOVIE_UPDATE_NOTIFICATION', enable)

    # ── UPI screenshot payment-approval queue ──────────────────────────
    # Used by plugins/goflix_admin/payment_approval.py. A "request" is
    # created the moment a user submits a screenshot, and stays "pending"
    # until an admin taps Approve/Reject (or, in future, an automated
    # check marks it "auto_approved").
    async def add_payment_request(self, user_id, username, screenshot_file_id, claimed_plan, extracted):
        """extracted: dict, e.g. {"amount": "110", "raw_date": "29 Aug 2026, 6:41 PM",
        "matched_plan": "3months" or None, "confidence": "high"/"low"}"""
        doc = {
            "user_id": int(user_id),
            "username": username,
            "screenshot_file_id": screenshot_file_id,
            "claimed_plan": claimed_plan,
            "extracted": extracted,
            "status": "pending",
            "submitted_at": datetime.datetime.now(),
            "handled_by": None,
            "handled_at": None,
        }
        result = await self.payment_requests.insert_one(doc)
        return result.inserted_id

    async def get_payment_request(self, request_id):
        from bson import ObjectId
        return await self.payment_requests.find_one({"_id": ObjectId(request_id)})

    async def set_payment_request_status(self, request_id, status, admin_id):
        from bson import ObjectId
        await self.payment_requests.update_one(
            {"_id": ObjectId(request_id)},
            {"$set": {"status": status, "handled_by": admin_id, "handled_at": datetime.datetime.now()}}
        )

    async def set_payment_request_log_message(self, request_id, message_id):
        """Remembers which LOG_CHANNEL message (the one with the pinned
        Approve/Reject buttons) belongs to this request, so it can be
        unpinned again the moment an admin resolves it (see
        approve_payment_cb/reject_payment_cb in payment_approval.py) —
        without this, a resolved request's pin would sit there forever,
        and the channel's single pinned-message slot would jam up after
        the very next pending request."""
        from bson import ObjectId
        await self.payment_requests.update_one(
            {"_id": ObjectId(request_id)},
            {"$set": {"log_message_id": message_id}}
        )

    async def find_approved_request_by_txn_id(self, txn_id):
        """Looks for an earlier request with this same OCR'd UPI/bank
        transaction ID that was already approved (auto or manual) —
        used to catch someone submitting an already-used screenshot a
        second time (see _handle_screenshot in payment_approval.py). A
        transaction ID is unique per real payment, so a repeat hit here
        means this exact payment already got credited once."""
        return await self.payment_requests.find_one({
            "extracted.txn_id": txn_id,
            "status": {"$in": ["approved", "auto_approved"]},
        })

    async def get_pending_payment_requests(self):
        """Oldest-first, used by /pending_payments so admins can see the
        backlog (the 'premium list' of unreviewed screenshots)."""
        cursor = self.payment_requests.find({"status": "pending"}).sort("submitted_at", 1)
        return [r async for r in cursor]

    # ── Screenshot sent before a plan was picked ────────────────────────
    # A user can send the payment screenshot straight into the AdminBot
    # chat with no /start and no plan chosen yet. We stash the file_id
    # here (keyed by user, upserted so a second stray screenshot just
    # replaces the first) and pop it once they tap a plan button, instead
    # of asking them to resend the photo.
    #
    # extracted is the OCR result computed on the FIRST read (see
    # unsolicited_screenshot_cb in payment_approval.py). Stashing it here
    # and handing it back on pop_pending_screenshot means the SECOND
    # read (once they pick a plan, see _handle_screenshot) can reuse it
    # instead of re-running OCR on the same photo from scratch — that
    # used to mean two full OCR passes (and, once Vision is configured,
    # two billed Vision API calls) per screenshot in this flow, for no
    # reason: the image never changes between "got a screenshot" and
    # "which plan is this for".
    async def set_pending_screenshot(self, user_id, file_id, extracted=None):
        await self.pending_screenshots.update_one(
            {"user_id": int(user_id)},
            {"$set": {
                "user_id": int(user_id), "file_id": file_id,
                "extracted": extracted, "created_at": datetime.datetime.now(),
            }},
            upsert=True
        )

    async def pop_pending_screenshot(self, user_id):
        """Fetches and deletes in one step so the same stashed screenshot
        can never be claimed twice. Returns (file_id, extracted) — extracted
        is None for anything stashed before this field existed, so the
        caller can safely re-run OCR in that case."""
        doc = await self.pending_screenshots.find_one_and_delete({"user_id": int(user_id)})
        if not doc:
            return None, None
        return doc["file_id"], doc.get("extracted")

    # ── Support Q&A relay (AdminBot doubles as a help desk) ─────────────
    # Any message a user sends that isn't part of the screenshot flow
    # gets forwarded to each admin's PM. We record (admin_id, message_id)
    # -> user_id so that when an admin replies (Telegram's native
    # reply-to) to that forwarded copy, the bot knows who to relay the
    # answer back to.
    async def add_support_link(self, admin_id, message_id, user_id):
        await self.support_links.update_one(
            {"admin_id": int(admin_id), "message_id": message_id},
            {"$set": {"user_id": int(user_id), "created_at": datetime.datetime.now()}},
            upsert=True
        )

    async def get_support_link(self, admin_id, message_id):
        doc = await self.support_links.find_one({"admin_id": int(admin_id), "message_id": message_id})
        return doc["user_id"] if doc else None


db = Database(USER_DB_URI, DATABASE_NAME, premium_uri=PREMIUM_DB_URI)
