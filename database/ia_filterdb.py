import re, base64, json, asyncio
from struct import pack
from pyrogram.file_id import FileId
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from info import FILE_DB_URI, SEC_FILE_DB_URI, DATABASE_NAME, COLLECTION_NAME, MULTIPLE_DATABASE, USE_CAPTION_FILTER, MAX_B_TN

# First Database For File Saving 
client = MongoClient(FILE_DB_URI)
db = client[DATABASE_NAME]
col = db[COLLECTION_NAME]

# Second Database For File Saving
sec_client = MongoClient(SEC_FILE_DB_URI)
sec_db = sec_client[DATABASE_NAME]
sec_col = sec_db[COLLECTION_NAME]


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

def _build_regex(query):
    """Build a regex that matches all words of the query anywhere in the
    file_name, in ANY order/position (not just back-to-back in the order the
    user typed them). Using lookaheads instead of a single 'word1...word2'
    chain means a query like 'War Master' still matches 'Master.of.War.2023'
    and nothing gets dropped just because the words are separated or
    reordered in the real filename."""
    words = query.split()
    if not words:
        return re.compile('.', flags=re.IGNORECASE)
    parts = [r'(?=.*(?:\b|[\.\+\-_])' + re.escape(w) + r'(?:\b|[\.\+\-_]))' for w in words]
    raw_pattern = ''.join(parts) + '.'
    return re.compile(raw_pattern, flags=re.IGNORECASE)


def _relevance_key(file_name, query):
    """Lower score = should rank first. Prefers the file whose title actually
    starts with / equals the query (e.g. 'Master') over one that merely
    contains a loosely related word (e.g. 'Mast...') buried inside it."""
    name = (file_name or '').lower()
    q = query.lower().strip()
    words = q.split()

    if name == q:
        return 0
    if name.startswith(q):
        return 1
    # query words appear together, in order, as a contiguous phrase
    if re.search(r'\b' + r'[\s\.\+\-_]+'.join(re.escape(w) for w in words) + r'\b', name):
        return 2
    # each word appears as its own token, but not necessarily contiguous/ordered
    if all(re.search(r'(?:\b|[\.\+\-_])' + re.escape(w) + r'(?:\b|[\.\+\-_])', name) for w in words):
        return 3
    return 4


async def get_search_results(chat_id, query, file_type=None, max_results=10, offset=0, filter=False):
    """For given query return (results, next_offset, total_results)."""
    if not query:
        return [], "", 0
    query = query.strip()
    if not query:
        regex = re.compile('.', flags=re.IGNORECASE)
    else:
        regex = _build_regex(query)
    regex_filter = {'file_name': regex}
    # Unquoted (no phrase-quotes) text search: Mongo's $text tokenizes the
    # query into individual words and matches documents containing them
    # regardless of order, instead of requiring one exact phrase - that
    # phrase requirement was the earlier cause of "missing" results whenever
    # a movie/series name's words weren't stored back-to-back in that order.
    text_filter = {'$text': {'$search': query}}

    def _run(collection, mongo_filter):
        cur = collection.find(mongo_filter).sort('$natural', -1).skip(offset).limit(max_results)
        return list(cur), collection.count_documents(mongo_filter)

    def _run_all_blocking():
        # All the actual pymongo work (synchronous/blocking network calls)
        # happens here, inside one function, so it can be handed to a worker
        # thread as a single unit below.
        files_ = []
        total_ = 0
        for collection in ([col, sec_col] if MULTIPLE_DATABASE else [col]):
            try:
                # Requires a text index on file_name - see create_search_index()
                # below / the one-time setup note. Falls back to the regex scan
                # automatically if that index doesn't exist yet, so search never
                # breaks - it's just slower until the index is created.
                found, count = _run(collection, text_filter)
            except Exception:
                found, count = _run(collection, regex_filter)
            files_.extend(found)
            total_ += count
        return files_, total_

    # Run all the blocking pymongo calls in a worker thread instead of
    # directly in this async function. Without this, every search froze the
    # ENTIRE bot's event loop for the duration of the DB round trip - meaning
    # other users' searches AND the file-send callbacks all queued up behind
    # it. That's what was causing the multi-second lag on both search and
    # file delivery, not the DB query itself being slow.
    files, total_results = await asyncio.to_thread(_run_all_blocking)

    # Re-rank so an exact/prefix title match (e.g. the real "Master" movie)
    # is always shown before loosely-matched files, instead of relying on
    # insertion order ($natural) alone.
    files.sort(key=lambda f: _relevance_key(f.get('file_name', ''), query))

    next_offset = "" if (offset + max_results) >= total_results else (offset + max_results)
    return files, next_offset, total_results


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
    return await asyncio.to_thread(_lookup)

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
