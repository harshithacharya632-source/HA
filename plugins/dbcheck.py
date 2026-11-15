# /dbcheck  (admins only)
# Shows every database the bot uses, how full it is, which one NEW data is written to right now,
# and whether the DB2_URI / DB3_URI ... variables reached this running bot.
# Also prints one "[db-check]" line in the Koyeb log at startup.

import os
import html
import asyncio
import datetime
from pyrogram import Client, filters
from info import ADMINS, OTHER_DB_URI
import database.users_chats_db as U

db = U.db
MB = 1024 * 1024
ENV_SHOW = ["DB2_URI", "DB3_URI", "DB4_URI", "PREMIUM_DB_URI", "EXTRA_DB_URIS"]


def _host(uri):
    """Only the host part of a Mongo URI (never the user name / password)."""
    try:
        rest = (uri or "").split("://", 1)[-1].split("@", 1)[-1]
        return rest.split("/", 1)[0].split("?", 1)[0] or "(empty)"
    except Exception:
        return "?"


def _env_line():
    return ", ".join(f"{k}: {'set' if os.environ.get(k, '').strip() else 'MISSING'}" for k in ENV_SHOW)


def _startup_print():
    hosts = getattr(db, "cluster_hosts", None)
    if hosts is None:
        print("[db-check] WARNING: the OLD users_chats_db.py is deployed - it has no multi-database support")
    else:
        print(f"[db-check] {len(hosts)} database(s) in use: " + ", ".join(hosts) + " | variables seen by the bot -> " + _env_line())


_startup_print()


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
    dbs = getattr(db, "_dbs", None)
    hosts = getattr(db, "cluster_hosts", None)
    if dbs is None or hosts is None:
        return ("<b>🔎 Database check</b>\n\n❌ The deployed <code>database/users_chats_db.py</code> is the OLD one "
                "(no multi-database support). Push the new file to GitHub and redeploy.")

    n = len(dbs)
    lines = ["<b>🔎 Database check</b>", "",
             f"Databases in use: <b>{n}</b> (users, premium, payments, admin chat, channel posts)",
             f"Variables seen by the bot: <code>{html.escape(_env_line())}</code>"]
    target = None
    results = []
    for i, d in enumerate(dbs):
        ok, err = await _test_write(d)
        results.append(ok)
        if ok:
            target = i                      # the newest database that accepts writes gets the new data
    for i, d in enumerate(dbs):
        mark = "OK ✅" if results[i] else "FULL / refused ❌"
        here = "  ⬅️ <b>new data goes here</b>" if i == target else ""
        lines.append("")
        lines.append(f"<b>#{i + 1}</b> <code>{html.escape(hosts[i])}</code>")
        lines.append(f"   {await _size(d)}")
        lines.append(f"   write: {mark}{here}")

    dup = sorted({h for h in hosts if hosts.count(h) > 1})
    if dup:
        lines.append("")
        lines.append("⚠️ Same cluster used more than once: <code>" + html.escape(", ".join(dup)) + "</code> "
                     "(a second DB must be a DIFFERENT Atlas cluster)")

    # filters / connections / referrals live on O_DB_URI (not part of the chain above)
    mydb = getattr(U, "mydb", None)
    if mydb is not None:
        o_ok, _ = await asyncio.to_thread(_test_write_sync, mydb["_dbcheck"])
        o_host = _host(OTHER_DB_URI)
        lines.append("")
        lines.append(f"Other data (filters, connections, referrals): <code>{html.escape(o_host)}</code> "
                     f"write {'OK ✅' if o_ok else 'FAILED ❌'}")
        if o_host in hosts:
            lines.append("   (same cluster as database #" + str(hosts.index(o_host) + 1) + ")")

    lines.append("")
    nxt = f"DB{n + 1}_URI"
    if n == 1 and not os.environ.get("DB2_URI", "").strip():
        lines.append("➡️ <b>DB2_URI is MISSING</b> in this running bot, so nothing can move to a second database. "
                     "Koyeb → your service → Settings → Environment variables → add <code>DB2_URI</code> "
                     "(name exactly like this) → Deploy.")
    elif n == 1:
        lines.append("➡️ DB2_URI is set but not used: it is identical to DATABASE_URI or empty. "
                     "It must be the connection string of a DIFFERENT cluster.")
    elif target is None:
        lines.append(f"➡️ EVERY database is full. Add a new variable <code>{nxt}</code> = connection string "
                     "of a NEW Atlas cluster, then Deploy.")
    else:
        lines.append(f"➡️ All good. New data goes to #{target + 1}. When it is full, add "
                     f"<code>{nxt}</code> in Koyeb and Deploy - nothing else to change.")
    return "\n".join(lines)


@Client.on_message(filters.command("dbcheck") & filters.user(ADMINS))
async def dbcheck_cmd(client, message):
    wait = await message.reply_text("Checking databases…")
    try:
        await wait.edit_text(await build_report())
    except Exception as e:
        await wait.edit_text(f"dbcheck failed: <code>{html.escape(str(e)[:200])}</code>")
