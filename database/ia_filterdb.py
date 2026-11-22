import re, base64, json, asyncio, time
from concurrent.futures import ThreadPoolExecutor
from struct import pack
from pyrogram.file_id import FileId
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, ExecutionTimeout, NetworkTimeout, AutoReconnect
from info import FILE_DB_URI, SEC_FILE_DB_URI, DATABASE_NAME, COLLECTION_NAME, MULTIPLE_DATABASE, USE_CAPTION_FILTER, MAX_B_TN, P_DB_URI
from database.extra_dbs import extra_db_uris, is_full_error, is_down_error, host_of, WritePool

# Dedicated thread pool for Mongo calls. asyncio.to_thread() uses the loop's
# DEFAULT executor, which is tiny on small hosts (min(32, cpu+4) -> ~5 threads
# on a 1 vCPU Koyeb box). A few 50,000-file pool fetches (season/quality
# buttons) would fill it, and then EVERY light call queued behind them
# (get_settings on each button click, etc.) -> all buttons "hang" 5-10s.
DB_EXECUTOR = ThreadPoolExecutor(max_workers=32, thread_name_prefix="mongo")

# First Database For File Saving 
client = MongoClient(FILE_DB_URI, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
db = client[DATABASE_NAME]
col = db[COLLECTION_NAME]

# Second Database For File Saving
sec_client = MongoClient(SEC_FILE_DB_URI, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
sec_db = sec_client[DATABASE_NAME]
sec_col = sec_db[COLLECTION_NAME]

# ══════════════════════════════════════════════════════════════════════════════
#  MORE FILE DATABASES: DB2_URI, DB3_URI, DB4_URI ... (+ PREMIUM_DB_URI / EXTRA_DB_URIS)
#  They are the SAME extra clusters the users database uses (see database/extra_dbs.py).
#
#  * NEW files go to the FIRST file database (in this order) that still has space, so they
#    fill up one after another: 1st, then 2nd, then 3rd ...
#  * A database that answers "over your space quota" (or is unreachable) is skipped for a few
#    minutes and the next one is used - no restart needed.
#  * Searching / opening / deleting look in ALL of them, so nothing already indexed is lost.
#  * When every one is full: add the next DB<n>_URI in Koyeb and redeploy. Nothing else.
#  With no extra variable set, this behaves exactly like the old two-database setup.
# ══════════════════════════════════════════════════════════════════════════════
_original_uris = [FILE_DB_URI] + ([SEC_FILE_DB_URI] if MULTIPLE_DATABASE else [])
extra_uris = [u for u in extra_db_uris(P_DB_URI) if u not in _original_uris]
extra_clients = [MongoClient(u, serverSelectionTimeoutMS=15000, connectTimeoutMS=15000) for u in extra_uris]
extra_cols = [c[DATABASE_NAME][COLLECTION_NAME] for c in extra_clients]
_EXTRA_IDS = {id(c) for c in extra_cols}

# Every file collection, oldest first: col, sec_col (only when MULTIPLE_DATABASE), DB2, DB3 ...
file_cols = [col] + ([sec_col] if MULTIPLE_DATABASE else []) + extra_cols
file_hosts = [host_of(u) for u in _original_uris + extra_uris]
file_pool = WritePool(file_hosts)
if extra_cols:
    print("[files] " + str(len(file_cols)) + " file databases: "
          + ", ".join(f"#{i + 1} {h}" for i, h in enumerate(file_hosts))
          + " - new files go to the first one (in this order) that still has space")


def _is_extra(collection):
    return id(collection) in _EXTRA_IDS


class _NoDelete:
    deleted_count = 0


def create_search_index():
    """One-time setup: create a text index on file_name so get_search_results()
    can use fast, indexed $text search instead of a full collection scan.
    Safe to call anytime - creating an index that already exists is a no-op."""
    for collection in file_cols:
        try:
            collection.create_index([('file_name', 'text')], name='file_name_text')
            print(f"Text index ready on {collection.full_name}")
        except Exception as e:
            print(f"Could not create text index on {collection.full_name}: {e}")


# ✅ Auto-create the text index at import time (bot startup), instead of
# depending on someone remembering to run build_search_index.py by hand.
# Without this index, EVERY search silently falls back to a full
# collection scan — which is almost certainly the real "no speed change"
# bottleneck, independent of anything in the search/ranking code itself.
# Creating an index that already exists is a cheap no-op, so this is safe
# to run on every startup.
try:
    create_search_index()
except Exception as e:
    print(f"[startup] create_search_index() failed: {e}")


def warn_if_name_index_is_unique():
    """Same-name files of different sizes can only be stored if the DB has no
    UNIQUE index on file_name alone (an index left over from an older setup
    would make every second version fail with DuplicateKeyError). This only
    reports it at startup - it never changes or drops anything."""
    for collection in file_cols:
        try:
            for name, spec in collection.index_information().items():
                keys = [k for k, _ in spec.get('key', [])]
                if name != '_id_' and spec.get('unique') and 'file_name' in keys and 'file_size' not in keys:
                    print(f"[startup] WARNING: unique index '{name}' on {collection.full_name} blocks same-name "
                          f"files of different sizes. Drop it with: db.{collection.name}.dropIndex('{name}')")
        except Exception as e:
            print(f"[startup] could not check indexes on {collection.full_name}: {e}")


try:
    warn_if_name_index_is_unique()
except Exception as e:
    print(f"[startup] warn_if_name_index_is_unique() failed: {e}")


async def save_file(media):
    """Save file in the database."""
    
    file_id = unpack_new_file_id(media.file_id)
    file_name = clean_file_name(media.file_name)
    
    file = {
        'file_id': file_id,
        'file_name': file_name,
        'file_size': media.file_size,
        'caption': media.caption.html if media.caption else None
    }

    if is_file_already_saved(file_id, file_name, media.file_size):
        return False, 0

    # First file database with space first (1st, 2nd, 3rd ...); a full / unreachable one is skipped.
    for i in file_pool.write_order():
        try:
            file_cols[i].insert_one(dict(file))
            file_pool.ok(i)
            print(f"{file_name} is successfully saved.")
            return True, 1
        except DuplicateKeyError:
            print(f"{file_name} is already saved.")
            return False, 0
        except Exception as e:
            if not (is_full_error(e) or is_down_error(e)):
                print(f"[files] could not save {file_name} on database #{i + 1} ({file_hosts[i]}): {e}")
            file_pool.mark_bad(i, e, what="files")
            continue

    # Every file database refused the write. (False, 2) = "error" for index.py's counter.
    print(f"[files] NOT SAVED: {file_name} - every file database is full or refusing writes. "
          f"Add the next DB<n>_URI in Koyeb (Settings > Environment variables) and redeploy.")
    return False, 2

def clean_file_name(file_name):
    """Clean and format the file name."""
    file_name = re.sub(r"(_|\-|\.|\+)", " ", str(file_name)) 
    unwanted_chars = ['[', ']', '(', ')', '{', '}']
    
    for char in unwanted_chars:
        file_name = file_name.replace(char, '')
        
    return ' '.join(filter(lambda x: not x.startswith('@') and not x.startswith('http') and not x.startswith('www.') and not x.startswith('t.me'), file_name.split()))

def is_file_already_saved(file_id, file_name, file_size=None):
    """Check if the file is already saved in either collection.

    A file is a duplicate only if it is the SAME Telegram file (same
    file_id) or has the same name AND the same size. Files that share a
    name but differ in size (e.g. a 400 MB, a 1 GB and a 3 GB version of
    one movie) are different files, so every size is kept. If the size is
    unknown the versions can't be told apart, so it falls back to the old
    name-only check.
    """
    by_id = {'file_id': file_id}
    by_name = {'file_name': file_name}
    if file_size is not None:
        by_name['file_size'] = file_size

    for collection in file_cols:
        try:
            found = collection.find_one({'$or': [by_name, by_id]})
        except Exception as e:
            if not _is_extra(collection):
                raise
            print(f"[files] duplicate check skipped on {collection.full_name}: {e}")
            continue
        if found:
            print(f"{file_name} ({file_size} bytes) is already saved.")
            return True

    return False

# ══════════════════════════════════════════════════════════════════════════════
#  SEARCH SPEED SETTINGS
# ══════════════════════════════════════════════════════════════════════════════
# Longest ONE database query may run on the server. When it is reached MongoDB aborts the
# query itself, which frees the worker thread too (before, a slow query kept a thread busy
# until the 30s network timeout and a few of them blocked every other search).
SEARCH_MAX_TIME_MS = 6000

# The old "retry with The / A / An in front" fallback ran THREE extra full searches on every
# miss. A file called "The Mentalist" already matches the search "Mentalist", so it found
# nothing new in practice and made every "not available" answer several times slower.
# Set True to bring it back.
ARTICLE_FALLBACK = False

_SLOW_DB_ERRORS = (ExecutionTimeout, NetworkTimeout, AutoReconnect)

# Self-tuning for VERY COMMON words ("hindi", "1080p", "kannada" ...). Rare words and misses
# are what the text index is great at. A word that matches a huge share of the library is the
# opposite: the old scan stops as soon as it has found a page of results (a few ms), while the
# index would have to sort every file containing the word. So when a text search for some
# words returns a FULL page and took longer than DENSE_SEARCH_MS, those words are remembered
# for DENSE_TTL seconds and searched with the early-stopping scan instead.
DENSE_SEARCH_MS = 120
DENSE_TTL = 3600
_DENSE = {}

# A file database that is unreachable or not answering is skipped for DB_DOWN_SKIP_S seconds,
# so one dead cluster does not make EVERY search wait for it. (A merely slow QUERY does not
# count - only network errors / no answer.) It is tried again afterwards, automatically.
DB_DOWN_SKIP_S = 45
_DB_DOWN_UNTIL = {}


def added_ts(doc):
    """When a file was added to the bot, in seconds. A file's _id is a MongoDB ObjectId made at
    the moment it was saved, and its first 4 bytes are that time. Old documents whose _id is
    plain text return 0 (= oldest)."""
    try:
        return int.from_bytes(doc.get('_id').binary[:4], 'big')
    except AttributeError:
        return 0


def _live_cols():
    now = time.time()
    live = [c for c in file_cols if _DB_DOWN_UNTIL.get(id(c), 0) <= now]
    return live or list(file_cols)         # all marked down -> just try them all


def _is_dense(key):
    ts = _DENSE.get(key)
    return ts is not None and (time.time() - ts) < DENSE_TTL


def _mark_dense(key):
    if len(_DENSE) > 500:
        _DENSE.clear()
    _DENSE[key] = time.time()

# Words MongoDB's (english) text index ignores. Measured on a real MongoDB, not guessed.
# They are never REQUIRED through the index - a title like "It", "Up" or "The Mentalist"
# is still found, the regex below checks those words.
_TEXT_STOPWORDS = frozenset("""
a about above after again against all am an and any are as at be because been before being
below between both but by cannot could did do does doing down during each few for from further
had has have having he her here hers herself him himself his how i if in into is it its itself
me more most my myself no nor not of off on once only or other our ours ourselves out over own
same she should so some such than that the their them themselves then there these they this
those through to too under until up very was we were what when where which while who whom why
with would you your yours yourself yourselves
""".split())

_WORD_SPLIT_RE = re.compile(r"[\s.+\-_]+")


def _text_term_sets(query):
    """Which words may be required through the text index, most strict first.

    The text index answers in milliseconds, but it only knows WHOLE words, while the regex
    that follows accepts a half-typed LAST word ("avengers end" -> Endgame). So the strict
    set uses every usable word; if that finds nothing and the query has more than one word,
    a second set without the last word is tried. Words with symbols or combining marks (Indic scripts),
    one-letter words and stop words are left to the regex.  [] = use the regex scan."""
    words = [w for w in _WORD_SPLIT_RE.split(query.lower()) if w]
    usable = []
    for w in words:
        if w.isalnum() and len(w) >= 2 and w not in _TEXT_STOPWORDS and w not in usable:
            usable.append(w)
    if not usable:
        return []
    sets = [usable]
    if len(words) > 1 and len(usable) > 1 and words[-1] == usable[-1]:
        sets.append(usable[:-1])
    return sets


async def get_search_results(chat_id, query, file_type=None, max_results=10, offset=0, filter=False, need_count=True, projection=None, article_fallback=True):
    """For given query return (results, next_offset, total_results).

    HOW IT IS FAST: MongoDB can NOT use the text index together with the old
    sort('$natural') ("text expression not allowed with $natural sort order"). That error
    was swallowed and EVERY search silently became a full scan of every file name in every
    database - the real reason searches were slow and misses were the slowest of all.
    Now the whole words of the search go through the text index (sorted newest-first by
    _id), and the same regex as before is applied on top, so what matches is unchanged -
    only the number of files MongoDB has to look at drops from "all" to "a handful".

    article_fallback=False skips the "The/A/An" retry (see ARTICLE_FALLBACK).

    need_count=False skips the count_documents() call entirely when the
    caller is never going to use the exact total anyway (e.g. the display
    pool, which computes its own total from the capped local list) —
    count_documents() has to tally EVERY matching document in the whole
    collection, not just the `max_results` page, so on a large/growing
    library it can easily be the single slowest part of a search."""
    if not query:
        return [], "", 0
    query = query.strip()
    if not query:
        return [], "", 0

    async def _search(q):
        """Run one search pass for query string `q` and return (files, total)."""
        if not q:
            raw_pattern = '.'
        elif ' ' not in q:
            # re.escape: a title with ( ) ? * + [ etc. used to break/alter the
            # pattern (and a failed compile fell back to an exact-match string
            # that never matches anything).
            raw_pattern = r'(\b|[\.\+\-_])' + re.escape(q) + r'(\b|[\.\+\-_])'
        else:
            raw_pattern = r'.*[\s\.\+\-_]'.join(re.escape(w) for w in q.split())
        try:
            regex = re.compile(raw_pattern, flags=re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(q), flags=re.IGNORECASE)
        regex_filter = {'file_name': regex}
        term_sets = _text_term_sets(q) if q else []
        if offset:
            term_sets = term_sets[:1]       # page 2+ must stay on the set page 1 used

        def _run(collection, mongo_filter, sort_key):
            cur = (collection.find(mongo_filter, projection).sort(sort_key, -1)
                   .skip(offset).limit(max_results).max_time_ms(SEARCH_MAX_TIME_MS))
            found = list(cur)
            # A short first page already IS the whole result set (and an empty
            # one is a definite miss) - a second count_documents() round trip
            # to Mongo would only repeat the answer we already have.
            if not need_count or (offset == 0 and len(found) < max_results):
                count = len(found)
            else:
                count = collection.count_documents(mongo_filter, maxTimeMS=SEARCH_MAX_TIME_MS)
            return found, count

        def _run_one_blocking(collection, terms):
            # All the actual pymongo work (synchronous/blocking network calls)
            # for ONE collection happens here, so it can be handed to a worker
            # thread as a single unit below.
            dense_key = frozenset(terms) if terms else None
            if terms and not _is_dense(dense_key):
                text_filter = {
                    '$text': {'$search': ' '.join(f'"{t}"' for t in terms)},
                    'file_name': regex,
                }
                started = time.perf_counter()
                try:
                    found, count = _run(collection, text_filter, '_id')
                    if len(found) >= max_results and (time.perf_counter() - started) * 1000 > DENSE_SEARCH_MS:
                        _mark_dense(dense_key)  # very common words: use the early-stopping scan next time
                    return found, count
                except _SLOW_DB_ERRORS:
                    raise                      # slow / unreachable: a regex rescan would only be slower
                except Exception:
                    pass                       # no text index on this collection -> regex scan below
            return _run(collection, regex_filter, '$natural')

        loop = asyncio.get_running_loop()

        async def _ask_all(terms):
            # Every database is asked at the same time (in worker threads, so the bot's event
            # loop never freezes). One database failing, hanging or being slow no longer
            # throws away the others' answers: whatever came back in time is used. Only when
            # ALL of them fail does this raise (-> the "search failed" message).
            cols = _live_cols()
            futs = [loop.run_in_executor(DB_EXECUTOR, _run_one_blocking, c, terms) for c in cols]
            _, pending = await asyncio.wait(futs, timeout=SEARCH_MAX_TIME_MS / 1000 + 1)
            files_, total_, errors = [], 0, []
            for c, fut in zip(cols, futs):
                try:
                    if fut in pending:
                        fut.cancel()
                        raise asyncio.TimeoutError("database did not answer in time")
                    found, count = fut.result()
                except Exception as e:
                    errors.append(e)
                    if isinstance(e, (AutoReconnect, asyncio.TimeoutError)):
                        _DB_DOWN_UNTIL[id(c)] = time.time() + DB_DOWN_SKIP_S
                    print(f"[files] search skipped on {c.full_name}: {e!r}")
                    continue
                _DB_DOWN_UNTIL.pop(id(c), None)
                files_.extend(found)
                total_ += count
            if errors and len(errors) == len(cols):
                raise errors[0]
            if len(cols) > 1:
                # Each database answers newest-first, but they were simply joined one after the
                # other (1st database, then 2nd ...), so a file added to the 2nd/3rd database
                # always came AFTER every file of the 1st. Merge them by add time instead.
                files_.sort(key=added_ts, reverse=True)
            return files_, total_

        if not term_sets:
            return await _ask_all(None)
        for terms in term_sets:
            files_, total_ = await _ask_all(terms)
            if total_:
                return files_, total_
        return [], 0

    files, total_results = await _search(query)

    # 🔤 Article-prefix fallback (off by default, see ARTICLE_FALLBACK): titles like
    # "The Mentalist" are stored with the leading article; retry with "The"/"A"/"An" in
    # front before giving up. Only on the initial search (offset 0) and never when the
    # query already starts with one of these words.
    if ARTICLE_FALLBACK and article_fallback and total_results == 0 and offset == 0:
        first_word = query.split()[0].lower() if query.split() else ""
        if first_word not in ("the", "a", "an"):
            tries = await asyncio.gather(*[_search(f"{prefix} {query}") for prefix in ("The", "A", "An")])
            for prefixed_files, prefixed_total in tries:
                if prefixed_total > 0:
                    files, total_results = prefixed_files, prefixed_total
                    break

    next_offset = "" if (offset + max_results) >= total_results else (offset + max_results)
    return files, next_offset, total_results


async def get_bad_files(query, file_type=None, use_filter=False):
    """For given query return (results, next_offset)"""
    query = query.strip()
    
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = rf'(\b|[.+-_]){query}(\b|[.+-_])'
    else:
        raw_pattern = query.replace(' ', r'.*[s.+-_]')
    
    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except re.error:
        return [], 0

    filter_criteria = {'file_name': regex}
    if USE_CAPTION_FILTER:
        filter_criteria = {'$or': [filter_criteria, {'caption': regex}]}

    def count_documents(collection):
        return collection.count_documents(filter_criteria)

    total_results = sum(count_documents(c) for c in file_cols)

    def find_documents(collection):
        return list(collection.find(filter_criteria))

    files = []
    for c in file_cols:
        files += find_documents(c)

    return files, total_results

async def get_file_details(query):
    def _lookup():
        for collection in file_cols:
            try:
                doc = collection.find_one({'file_id': query})
            except Exception as e:
                if not _is_extra(collection):
                    raise
                print(f"[files] lookup skipped on {collection.full_name}: {e}")
                continue
            if doc:
                return doc
        return None
    return await asyncio.get_running_loop().run_in_executor(DB_EXECUTOR, _lookup)


def delete_file_docs(flt, many=False):
    """Delete from the first file database (oldest first) that has a match - the same rule the
    old `col`, then `sec_col` code followed, now over every database. Returns an object with
    .deleted_count. Deleting also works on a FULL Atlas cluster."""
    for collection in file_cols:
        try:
            res = collection.delete_many(flt) if many else collection.delete_one(flt)
        except Exception as e:
            if not _is_extra(collection):
                raise
            print(f"[files] delete skipped on {collection.full_name}: {e}")
            continue
        if res.deleted_count:
            return res
    return _NoDelete()


def drop_all_files():
    """/deleteall: remove every indexed file from every file database."""
    for collection in file_cols:
        try:
            collection.drop()
        except Exception as e:
            if not _is_extra(collection):
                raise
            print(f"[files] drop skipped on {collection.full_name}: {e}")


def count_extra_files():
    """Files held by the extra databases (DB2, DB3 ...), for the bot's total-files figure."""
    total = 0
    for collection in extra_cols:
        try:
            total += collection.count_documents({})
        except Exception:
            pass
    return total

def encode_file_id(s: bytes) -> str:
    r = b""
    n = 0
    for i in s + bytes([22]) + bytes([4]):
        if i == 0:
            n += 1
        else:
            if n:
                r += b"\x00" + bytes([n])
                n = 0
            r += bytes([i])
    return base64.urlsafe_b64encode(r).decode().rstrip("=")
    
def unpack_new_file_id(new_file_id):
    """Return file_id"""
    decoded = FileId.decode(new_file_id)
    file_id = encode_file_id(
        pack(
            "<iiqq",
            int(decoded.file_type),
            decoded.dc_id,
            decoded.media_id,
            decoded.access_hash
        )
    )
    return file_id
