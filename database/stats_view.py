"""
Text of the /stats panel (the /stats command AND the Stats / Refresh buttons) for ANY number of
databases. Before, the panel had exactly two "FILE ... DB" slots, so DB2_URI, DB3_URI ... never
showed up. Now every file database gets its own block:

    FILE FIRST DB, FILE SECOND DB, FILE THIRD DB (= DB2_URI), FILE FOURTH DB (= DB3_URI) ...

Free storage is shown against the 512 MB Atlas free-tier limit, exactly like before. Cluster
host names are NOT printed (the Stats button can be pressed by any user).
"""
import asyncio
import html

from Script import script
from database.ia_filterdb import file_cols, DB_EXECUTOR
from database.users_chats_db import db
from database.connections_mdb import mydb

MB = 1024 * 1024
LIMIT_MB = 512          # Atlas free-tier size, same figure the old panel used

_ORDINALS = ["FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH", "EIGHTH", "NINTH", "TENTH"]


def _label(i):
    return f"FILE {_ORDINALS[i]} DB" if i < len(_ORDINALS) else f"FILE DB {i + 1}"


def _used_mb(database):
    s = database.command("dbStats")
    return (s["dataSize"] / MB) + (s["indexSize"] / MB)


def _file_db(collection):
    """(number of files, used MB) of one file database - blocking, runs in a worker thread."""
    return collection.count_documents({}), _used_mb(collection.database)


def _other_db():
    return _used_mb(mydb)


async def _in_thread(fn, *args):
    # One unreachable database must not take the whole panel down: return the error instead.
    try:
        return await asyncio.get_running_loop().run_in_executor(DB_EXECUTOR, fn, *args)
    except Exception as e:
        return e


def _free(used):
    free = LIMIT_MB - used
    if free <= 0:
        return "<code>0 MB</code> ⚠️ FULL"
    return f"<code>{round(free, 2)} MB</code>"


def _storage_lines(used):
    return [f"★ Usᴇᴅ Sᴛᴏʀᴀɢᴇ: <code>{round(used, 2)} MB</code>",
            f"★ Fʀᴇᴇ Sᴛᴏʀᴀɢᴇ: {_free(used)}"]


def _error_line(err):
    return f"★ Sᴛᴀᴛᴜs: <code>unreachable - {html.escape(str(err)[:60])}</code>"


async def build_stats_text():
    total_users = await db.total_users_count()
    total_chats = await db.total_chat_count()

    results = await asyncio.gather(*[_in_thread(_file_db, c) for c in file_cols], _in_thread(_other_db))
    file_res, other_res = list(results[:-1]), results[-1]

    # Only ONE file database (MULTIPLE_DATABASE off, no DB2_URI): the short panel, as before.
    if len(file_cols) == 1:
        if isinstance(file_res[0], Exception):
            raise file_res[0]
        files, used = file_res[0]
        return script.SEC_STATUS_TXT.format(total_users, total_chats, files, round(used, 2), round(LIMIT_MB - used, 2))

    total_files = sum(r[0] for r in file_res if not isinstance(r, Exception))
    parts = [
        f"<b>Total Files From All DBs: <code>{total_files}</code>",
        "USERS DB :-\n"
        f"★ Tᴏᴛᴀʟ Usᴇʀs: <code>{total_users}</code>\n"
        f"★ Tᴏᴛᴀʟ Cʜᴀᴛs: <code>{total_chats}</code>",
    ]
    for i, r in enumerate(file_res):
        lines = [f"{_label(i)} :-"]
        if isinstance(r, Exception):
            lines.append(_error_line(r))
        else:
            files, used = r
            lines.append(f"★ Tᴏᴛᴀʟ Fɪʟᴇs: <code>{files}</code>")
            lines += _storage_lines(used)
        parts.append("\n".join(lines))

    lines = ["OTHER DB :-"]
    if isinstance(other_res, Exception):
        lines.append(_error_line(other_res))
    else:
        lines += _storage_lines(other_res)
    parts.append("\n".join(lines))
    return "\n\n".join(parts) + "</b>"
