# /dbcheck  (admins only)
# Shows which MongoDB cluster the bot is REALLY writing premium data to, and whether a
# test write works there. Use it when /add_premium still says "Premium NOT added".

import html
import datetime
from pyrogram import Client, filters
from info import ADMINS, USER_DB_URI, PREMIUM_DB_URI, P_DB_URI
from database.users_chats_db import db, _DualCollection

MB = 1024 * 1024


def _host(uri):
    """Only the host part of a Mongo URI (never the user name / password)."""
    try:
        rest = (uri or "").split("://", 1)[-1].split("@", 1)[-1]
        return rest.split("/", 1)[0].split("?", 1)[0] or "(empty)"
    except Exception:
        return "?"


async def _size(database):
    try:
        s = await database.command("dbstats")
        return f"data {s['dataSize'] / MB:.0f} MB, storage {s['storageSize'] / MB:.0f} MB"
    except Exception as e:
        return f"could not read ({html.escape(str(e)[:60])})"


async def build_report():
    split = isinstance(db.users, _DualCollection)
    first_host, second_host = _host(USER_DB_URI), _host(PREMIUM_DB_URI)

    # Test write on whichever cluster premium data is written to (temporary doc, deleted at once).
    test_col = db.premium_db["_dbcheck"]
    try:
        res = await test_col.insert_one({"t": datetime.datetime.now()})
        await test_col.delete_one({"_id": res.inserted_id})
        write_ok, write_err = True, ""
    except Exception as e:
        write_ok, write_err = False, str(e)

    lines = [
        "<b>🔎 Premium database check</b>",
        "",
        f"PREMIUM_DB_URI set in this running bot: <b>{'YES' if P_DB_URI else 'NO'}</b>",
        f"First cluster (DATABASE_URI): <code>{html.escape(first_host)}</code>",
        f"  {await _size(db.db)}",
        f"Premium cluster (PREMIUM_DB_URI): <code>{html.escape(second_host)}</code>",
        f"  {await _size(db.premium_db)}",
        f"Two-cluster mode active: <b>{'YES' if split else 'NO'}</b>",
        f"Test write on the premium cluster: <b>{'OK ✅' if write_ok else 'FAILED ❌'}</b>",
    ]
    if not write_ok:
        lines.append(f"<code>{html.escape(write_err[:180])}</code>")

    lines.append("")
    if not P_DB_URI:
        lines.append("➡️ PREMIUM_DB_URI is NOT set in Koyeb (or the service was not redeployed). "
                     "Add it in Koyeb → Environment variables, then redeploy.")
    elif first_host == second_host:
        lines.append("➡️ PREMIUM_DB_URI points to the SAME cluster as DATABASE_URI, so it is still full. "
                     "Use the connection string of a DIFFERENT cluster that has free space.")
    elif not split:
        lines.append("➡️ Two-cluster mode is off. Make sure the updated users_chats_db.py is the one deployed.")
    elif not write_ok:
        lines.append("➡️ The premium cluster refused the write. Check its free space and its Network Access "
                     "(allow 0.0.0.0/0), then try again.")
    else:
        lines.append("➡️ All good. New premium users are saved on the premium cluster. "
                     "Try /add_premium again.")
    return "\n".join(lines)


@Client.on_message(filters.command("dbcheck") & filters.user(ADMINS))
async def dbcheck_cmd(client, message):
    wait = await message.reply_text("Checking databases…")
    try:
        await wait.edit_text(await build_report())
    except Exception as e:
        await wait.edit_text(f"dbcheck failed: <code>{html.escape(str(e)[:200])}</code>")
