"""
Copy the bot's user/premium/payment data from the FULL cluster to a new
(empty) cluster, so you can point DATABASE_URI at the new one.

Reads work on a full Atlas cluster, so this works even while writes are blocked.
It is safe to re-run: documents that already exist in the new DB are skipped.

Usage:
    export OLD_URI="mongodb+srv://...full cluster..."
    export NEW_URI="mongodb+srv://...new empty cluster..."
    export DATABASE_NAME="harshithacharya8"      # same as info.py
    python tools/migrate_users_db.py

Then, in your host's environment variables, set DATABASE_URI = NEW_URI
and restart the bot.

Files (the 'Goflix' collection) are NOT copied - they are handled by
F_DB_URI / S_DB_URI.
"""
import os
import sys
from pymongo import MongoClient
from pymongo.errors import BulkWriteError

COLLECTIONS = [
    "uersz",              # premium users  <- most important
    "users",
    "groups",
    "clone_bots",
    "payment_requests",
    "payment_signatures",
    "pending_screenshots",
    "support_links",
    "movie_updates",
]
BATCH = 500


def main():
    old_uri, new_uri = os.environ.get("OLD_URI"), os.environ.get("NEW_URI")
    name = os.environ.get("DATABASE_NAME", "harshithacharya8")
    if not old_uri or not new_uri:
        sys.exit("Set OLD_URI and NEW_URI.")
    if old_uri == new_uri:
        sys.exit("OLD_URI and NEW_URI are the same cluster - nothing to do.")

    src = MongoClient(old_uri, serverSelectionTimeoutMS=15000)[name]
    dst = MongoClient(new_uri, serverSelectionTimeoutMS=15000)[name]

    for coll in COLLECTIONS:
        total = src[coll].estimated_document_count()
        copied = skipped = 0
        batch = []

        def flush():
            nonlocal copied, skipped, batch
            if not batch:
                return
            try:
                dst[coll].insert_many(batch, ordered=False)
                copied += len(batch)
            except BulkWriteError as e:
                dups = sum(1 for x in e.details.get("writeErrors", []) if x.get("code") == 11000)
                other = [x for x in e.details.get("writeErrors", []) if x.get("code") != 11000]
                if other:
                    raise
                skipped += dups
                copied += len(batch) - dups
            batch = []

        for doc in src[coll].find():
            batch.append(doc)
            if len(batch) >= BATCH:
                flush()
        flush()
        print(f"{coll:22} source={total:<8} copied={copied:<8} already_there={skipped}")

    print("\nDone. Now set DATABASE_URI to the NEW cluster and restart the bot.")


if __name__ == "__main__":
    main()
