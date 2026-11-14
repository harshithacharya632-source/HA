"""
Copy the bot's user/premium/payment data from the FULL cluster to another
cluster that still has free space.

Reads work on a full Atlas cluster, so this works even while writes are blocked.
It is safe to re-run: records that already exist in the destination are skipped
(matched by user id / payment key, not just by Mongo _id, so you never get
duplicate users even if the bot already wrote to the new cluster).

TWO MODES
---------
1) --premium-only   (recommended when you just want premium to work again)
   Copies ONLY the premium + payment collections:
       uersz, payment_requests, payment_signatures, pending_screenshots, support_links
   Then set  PREMIUM_DB_URI = NEW_URI  in your host's environment and restart.
   DATABASE_URI stays the same (users, groups, settings stay where they are).

2) (no flag)        Copy everything to a new cluster, then set DATABASE_URI = NEW_URI.

Usage:
    export OLD_URI="mongodb+srv://...full cluster..."
    export NEW_URI="mongodb+srv://...cluster with free space..."
    export DATABASE_NAME="harshithacharya8"      # same as info.py
    python tools/migrate_users_db.py --premium-only

Run it BEFORE you set PREMIUM_DB_URI (otherwise premium users look "not premium"
until it has finished). Run it again afterwards if you like - it only adds what
is missing.

Files (the 'Goflix' collection) are NOT copied - they are handled by
F_DB_URI / S_DB_URI.
"""
import os
import sys
import argparse
from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError

PREMIUM_COLLECTIONS = [
    "uersz",                # premium users, free trial, daily verification, offer claims
    "payment_requests",
    "payment_signatures",
    "pending_screenshots",
    "support_links",
]
OTHER_COLLECTIONS = [
    "users",
    "groups",
    "clone_bots",
    "movie_updates",
]

# How a record is recognised in the destination (so re-runs / late runs never duplicate it).
# Anything not listed here is matched by its Mongo _id.
NATURAL_KEY = {
    "uersz": ("id",),
    "payment_signatures": ("key",),
    "pending_screenshots": ("user_id",),
    "support_links": ("admin_id", "message_id"),
    "users": ("id",),
    "groups": ("id",),
}
BATCH = 500


def build_op(coll, doc):
    """UpdateOne that inserts `doc` only if its natural key is not there yet."""
    fields = NATURAL_KEY.get(coll)
    if fields and all(f in doc for f in fields):
        flt = {f: doc[f] for f in fields}
        return UpdateOne(flt, {"$setOnInsert": doc}, upsert=True)     # keeps the original _id
    flt = {"_id": doc["_id"]}
    body = {k: v for k, v in doc.items() if k != "_id"}               # _id comes from the filter
    if not body:
        return UpdateOne(flt, {"$setOnInsert": {"_migrated": True}}, upsert=True)
    return UpdateOne(flt, {"$setOnInsert": body}, upsert=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--premium-only", action="store_true",
                    help="copy only the premium/payment collections (use with PREMIUM_DB_URI)")
    args = ap.parse_args()

    old_uri, new_uri = os.environ.get("OLD_URI"), os.environ.get("NEW_URI")
    name = os.environ.get("DATABASE_NAME", "harshithacharya8")
    if not old_uri or not new_uri:
        sys.exit("Set OLD_URI and NEW_URI.")
    if old_uri == new_uri:
        sys.exit("OLD_URI and NEW_URI are the same cluster - nothing to do.")

    src = MongoClient(old_uri, serverSelectionTimeoutMS=15000)[name]
    dst = MongoClient(new_uri, serverSelectionTimeoutMS=15000)[name]

    collections = PREMIUM_COLLECTIONS if args.premium_only else PREMIUM_COLLECTIONS + OTHER_COLLECTIONS
    print(f"Mode: {'premium only' if args.premium_only else 'everything'}  |  database '{name}'\n")

    for coll in collections:
        total = src[coll].estimated_document_count()
        copied = skipped = 0
        batch = []

        def flush():
            nonlocal copied, skipped, batch
            if not batch:
                return
            n = len(batch)
            try:
                res = dst[coll].bulk_write(batch, ordered=False)
                new = res.upserted_count
            except BulkWriteError as e:
                errs = e.details.get("writeErrors", [])
                # 11000 = duplicate key (e.g. payment_signatures unique index): already there, fine.
                if any(x.get("code") != 11000 for x in errs):
                    raise
                new = e.details.get("nUpserted", 0)
            copied += new
            skipped += n - new
            batch = []

        for doc in src[coll].find():
            batch.append(build_op(coll, doc))
            if len(batch) >= BATCH:
                flush()
        flush()
        print(f"{coll:22} source={total:<8} copied={copied:<8} already_there={skipped}")

    if args.premium_only:
        print("\nDone. Now set PREMIUM_DB_URI to the NEW cluster in your host's environment and restart the bot.")
    else:
        print("\nDone. Now set DATABASE_URI to the NEW cluster and restart the bot.")


if __name__ == "__main__":
    main()
