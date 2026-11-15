# /dbcheck  (admins only)
# Shows every database the bot uses, how full it is, which one NEW data is written to right now,
# and whether a test write works on each. Use it whenever something says "space quota" / "Premium NOT added".

import html
import asyncio
import datetime
from pyrogram import Client, filters
from info import ADMINS, OTHER_DB_URI
from database.users_chats_db import db, mydb, _host_of

MB = 1024 * 1024


async def _size(database):
    try:
        s = await database.command("dbstats")
        return f"{s['dataSize'] / MB:.0f} MB data / {s['storageSize'] / MB:.0f} MB storage"
    except Exception as e:
        return f"size unknown ({html.escape(str(e)[:50])})"


async def _test_write(database):
    """Insert + delete one temporary document. Returns (ok, error text)."""
    try:
        col = database["_dbcheck"]
        res = await col.insert_one({"t": datetime.datetime.now()})
        await col.delete_one({"_id": res.inserted_id})
        return True, ""
    except Exception as e:
        return False, str(e)


def _test_write_sync(collection):
    try:
        res = collection.insert_one({"t": datetime.datetime.now()})
        collection.delete_one({"_id": res.inserted_id})
        return True, ""
    except Exception as e:
        return False, str(e)


async def build_report():
    n = len(db._dbs)
    lines = ["<b>🔎 Database check</b>", "", f"Databases configured: <b>{n}</b> (users, premium, payments, admin chat, channel posts)"]
    target = None
    results = []
    for i, d in enumerate(db._dbs):
        ok, err = await _test_write(d)
        results.append(ok)
        if ok:
            target = i                      # the newest database that accepts writes gets the new data
    for i, d in enumerate(db._dbs):
        mark = "OK ✅" if results[i] else "FULL / refused ❌"
        here = "  ⬅️ <b>new data goes here</b>" if i == target else ""
        lines.append(f"<b>#{i + 1}</b> <code>{html.escape(db.cluster_hosts[i])}</code>")
        lines.append(f"   {await _size(d)}")
        lines.append(f"   write: {mark}{here}")

    hosts = db.cluster_hosts
    dup = sorted({h for h in hosts if hosts.count(h) > 1})
    if dup:
        lines.append("")
        lines.append("⚠️ Same cluster used more than once: <code>" + html.escape(", ".join(dup)) + "</code> "
                     "(a second DB must be a DIFFERENT Atlas cluster)")

    # filters / connections / referrals live on O_DB_URI (not part of the chain above)
    o_ok, o_err = await asyncio.to_thread(_test_write_sync, mydb["_dbcheck"])
    o_host = _host_of(OTHER_DB_URI)
    lines.append("")
    lines.append(f"Other data (filters, connections, referrals): <code>{html.escape(o_host)}</code> "
                 f"write {'OK ✅' if o_ok else 'FAILED ❌'}")
    if o_host in hosts:
        lines.append("   (same cluster as database #" + str(hosts.index(o_host) + 1) + ")")

    lines.append("")
    nxt = f"DB{n + 1}_URI"
    if target is None:
        lines.append(f"➡️ EVERY database is full. In Koyeb add a new variable <code>{nxt}</code> = connection string "
                     "of a NEW Atlas cluster, then redeploy.")
    elif n == 1:
        lines.append("➡️ Only DATABASE_URI is set. When it gets full add <code>DB2_URI</code> in Koyeb and redeploy.")
    else:
        lines.append(f"➡️ All good. New data goes to #{target + 1}. When it is full, add "
                     f"<code>{nxt}</code> in Koyeb and redeploy - nothing else to change.")
    return "\n".join(lines)


@Client.on_message(filters.command("dbcheck") & filters.user(ADMINS))
async def dbcheck_cmd(client, message):
    wait = await message.reply_text("Checking databases…")
    try:
        await wait.edit_text(await build_report())
    except Exception as e:
        await wait.edit_text(f"dbcheck failed: <code>{html.escape(str(e)[:200])}</code>")
