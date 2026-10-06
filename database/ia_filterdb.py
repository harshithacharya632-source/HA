import re, base64, json, asyncio
from concurrent.futures import ThreadPoolExecutor
from struct import pack
from pyrogram.file_id import FileId
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from info import FILE_DB_URI, SEC_FILE_DB_URI, DATABASE_NAME, COLLECTION_NAME, MULTIPLE_DATABASE, USE_CAPTION_FILTER, MAX_B_TN

# Dedicated thread pool for Mongo calls. asyncio.to_thread() uses the loop's
# DEFAULT executor, which is tiny on small hosts (min(32, cpu+4) -> ~5 threads
# on a 1 vCPU Koyeb box). A few 50,000-file pool fetches (season/quality
# buttons) would fill it, and then EVERY light call queued behind them
# (get_settings on each button click, etc.) -> all buttons "hang" 5-10s.
DB_EXECUTOR = ThreadPoolExecutor(max_workers=32, thread_name_prefix="mongo")

# First Database For File Saving 
client = MongoClient(FILE_DB_URI)
db = client[DATABASE_NAME]
col = db[COLLECTION_NAME]

# Second Database For File Saving
sec_client = MongoClient(SEC_FILE_DB_URI)
sec_db = sec_client[DATABASE_NAME]
sec_col = sec_db[COLLECTION_NAME]


def create_search_index():
    """One-time setup: create a text index on file_name so get_search_results()
    can use fast, indexed $text search instead of a full collection scan.
    Safe to call anytime - creating an index that already exists is a no-op."""
    for collection in ([col, sec_col] if MULTIPLE_DATABASE else [col]):
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

    if is_file_already_saved(file_id, file_name):
        return False, 0

    try:
        col.insert_one(file)
        print(f"{file_name} is successfully saved.")
        return True, 1
    except DuplicateKeyError:
        print(f"{file_name} is already saved.")
        return False, 0
    except:
        if MULTIPLE_DATABASE:
            try:
                sec_col.insert_one(file)
                print(f"{file_name} is successfully saved.")
                return True, 1
            except DuplicateKeyError:
                print(f"{file_name} is already saved.")
                return False, 0
        else:
            print("Your Current File Database Is Full, Turn On Multiple Database Feature And Add Second File Mongodb To Save File.")

def clean_file_name(file_name):
    """Clean and format the file name."""
    file_name = re.sub(r"(_|\-|\.|\+)", " ", str(file_name)) 
    unwanted_chars = ['[', ']', '(', ')', '{', '}']
    
    for char in unwanted_chars:
        file_name = file_name.replace(char, '')
        
    return ' '.join(filter(lambda x: not x.startswith('@') and not x.startswith('http') and not x.startswith('www.') and not x.startswith('t.me'), file_name.split()))

def is_file_already_saved(file_id, file_name):
    """Check if the file is already saved in either collection."""
    found1 = {'file_name': file_name}
    found = {'file_id': file_id}

    for collection in [col, sec_col]:
        if collection.find_one(found1) or collection.find_one(found):
            print(f"{file_name} is already saved.")
            return True
            
    return False

async def get_search_results(chat_id, query, file_type=None, max_results=10, offset=0, filter=False, need_count=True, projection=None, article_fallback=True):
    """For given query return (results, next_offset, total_results).

    article_fallback=False skips the "The/A/An" retry on an empty result (used by
    quick existence probes that only want to know if THIS spelling has files).

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
        # Phrase search on the text index (fast + gives an exact, indexed count).
        # Quoting the whole query makes Mongo require the words as a phrase,
        # matching the old regex's "words in order" behaviour. A stray " or \
        # inside the text would break out of the phrase, so drop them.
        q_text = q.replace('"', ' ').replace('\\', ' ').strip()
        text_filter = {'$text': {'$search': f'"{q_text}"'}}

        def _run(collection, mongo_filter):
            cur = collection.find(mongo_filter, projection).sort('$natural', -1).skip(offset).limit(max_results)
            found = list(cur)
            # A short first page already IS the whole result set (and an empty
            # one is a definite miss) - a second count_documents() round trip
            # to Mongo would only repeat the answer we already have.
            if not need_count or (offset == 0 and len(found) < max_results):
                count = len(found)
            else:
                count = collection.count_documents(mongo_filter)
            return found, count

        def _run_one_blocking(collection):
            # All the actual pymongo work (synchronous/blocking network calls)
            # for ONE collection happens here, so it can be handed to a worker
            # thread as a single unit below.
            try:
                # Requires a text index on file_name - see create_search_index()
                # below / the one-time setup note. Falls back to the regex scan
                # automatically if that index doesn't exist yet, so search never
                # breaks - it's just slower until the index is created.
                return _run(collection, text_filter)
            except Exception:
                return _run(collection, regex_filter)

        # Run the blocking pymongo calls in worker threads instead of directly
        # in this async function. Without this, every search froze the ENTIRE
        # bot's event loop for the duration of the DB round trip - meaning
        # other users' searches AND the file-send callbacks all queued up behind
        # it. Both collections are queried at the same time (they used to be
        # queried one after the other, doubling the wait when
        # MULTIPLE_DATABASE is on). gather() keeps the original order.
        loop = asyncio.get_running_loop()
        collections = [col, sec_col] if MULTIPLE_DATABASE else [col]
        results = await asyncio.gather(*[
            loop.run_in_executor(DB_EXECUTOR, _run_one_blocking, c) for c in collections
        ])
        files_ = []
        total_ = 0
        for found, count in results:
            files_.extend(found)
            total_ += count
        return files_, total_

    files, total_results = await _search(query)

    # 🔤 Article-prefix fallback: titles like "The Mentalist" are stored
    # with the leading article, but users often search the plain title
    # ("Mentalist"). If the plain query comes up completely empty on the
    # first page, retry with "The"/"A"/"An" in front before giving up.
    # Only runs on the initial search (offset 0) and only when the query
    # doesn't already start with one of these words, so it never fires on
    # pagination ("next page") calls or double-prefixes a query.
    if article_fallback and total_results == 0 and offset == 0:
        first_word = query.split()[0].lower() if query.split() else ""
        if first_word not in ("the", "a", "an"):
            # Was 3 sequential round trips on EVERY miss - the main reason a
            # "not found" took so long to appear. Now all three run at once;
            # the first prefix in this order that has results still wins.
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

    total_results = (count_documents(col) + count_documents(sec_col) if MULTIPLE_DATABASE else count_documents(col))

    def find_documents(collection):
        return list(collection.find(filter_criteria))

    files = (find_documents(col) + find_documents(sec_col) if MULTIPLE_DATABASE else find_documents(col))

    return files, total_results

async def get_file_details(query):
    def _lookup():
        return col.find_one({'file_id': query}) or sec_col.find_one({'file_id': query})
    return await asyncio.get_running_loop().run_in_executor(DB_EXECUTOR, _lookup)

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
