"""
One-time cleanup: shrink the user documents by removing the OLD next-episode data.

The bot no longer stores these on every user (see plugins/next_episode.py):
  * next_uses     - free "Next" counter            -> now kept in memory only
  * last_watched  - stem/season/episode/size/fid/card (5-6 fields)
                                                   -> now just ONE short field `lw` (the file id)

This script converts every existing user the same way:
  last_watched.fid  ->  lw   (so /last keeps working),  then  last_watched + next_uses are removed.
Nothing else on the user is touched. Safe to re-run (it only touches users that still have old fields).

Usage (run from the repo root, on your PC or server):
    export DATABASE_URI="mongodb+srv://..."        # the USERS cluster (the one that is full)
    export DATABASE_NAME="harshithacharya8"        # optional, same as info.py
    python tools/slim_next_episode.py              # report only: how many users / how much space
    python tools/slim_next_episode.py --apply      # really do it

If the cluster is completely full, Atlas can refuse updates too. Then first free a little space
with deletes (python tools/db_usage.py --clean --days 30 --apply), and run this again.
"""
import os
import sys
import argparse
from pymongo import MongoClient
from pymongo.errors import PyMongoError

MB = 1024 * 1024

FILTER = {"$or": [{"last_watched": {"$exists": True}}, {"next_uses": {"$exists": True}}]}

# lw := the old last_watched.fid (only if there is a string fid and no lw yet), then drop the old fields.
# A user with no fid gets NO lw field (not even null).
PIPELINE = [
    {"$set": {"lw": {"$cond": [
        {"$and": [{"$ne": [{"$type": "$lw"}, "string"]},
                  {"$eq": [{"$type": "$last_watched.fid"}, "string"]}]},
        "$last_watched.fid",
        "$lw",
    ]}}},
    {"$unset": ["last_watched", "next_uses"]},
]


def estimate_bytes(users):
    """Approximate bytes held by the old fields (needs MongoDB 5.0+; returns None otherwise)."""
    try:
        stripped = {"$unsetField": {"field": "next_uses",
                                    "input": {"$unsetField": {"field": "last_watched", "input": "$$ROOT"}}}}
        res = list(users.aggregate([
            {"$match": FILTER},
            {"$group": {"_id": None,
                        "before": {"$sum": {"$bsonSize": "$$ROOT"}},
                        "after": {"$sum": {"$bsonSize": stripped}}}},
        ]))
        return (res[0]["before"] - res[0]["after"]) if res else 0
    except PyMongoError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default=os.environ.get("DATABASE_URI", ""))
    ap.add_argument("--db", default=os.environ.get("DATABASE_NAME", "harshithacharya8"))
    ap.add_argument("--apply", action="store_true", help="really change the documents (default is a report only)")
    ap.add_argument("--chunk", type=int, default=2000, help="users per update (keeps each operation small)")
    args = ap.parse_args()

    if not args.uri:
        sys.exit("Set DATABASE_URI (or pass --uri).")

    users = MongoClient(args.uri, serverSelectionTimeoutMS=15000)[args.db]["users"]
    total = users.count_documents({})
    todo = users.count_documents(FILTER)
    print(f"\nusers collection: {total} users, {todo} still have old next-episode fields")

    if not todo:
        print("Nothing to do.")
        return

    est = estimate_bytes(users)
    if est is not None:
        print(f"old fields hold about {est / MB:.2f} MB (the short `lw` field adds a little back for users who have one)")

    if not args.apply:
        print("\nReport only. Re-run with --apply to clean them.")
        return

    done = 0
    try:
        while True:
            ids = [d["_id"] for d in users.find(FILTER, {"_id": 1}).limit(args.chunk)]
            if not ids:
                break
            res = users.update_many({"_id": {"$in": ids}}, PIPELINE)
            if res.modified_count == 0:
                print("No progress, stopping.")
                break
            done += res.modified_count
            print(f"  cleaned {done}/{todo}")
    except PyMongoError as e:
        print(f"\nStopped after {done} users: {str(e)[:200]}")
        print("If the cluster is full, free some space with deletes first "
              "(python tools/db_usage.py --clean --days 30 --apply) and run this again.")
        sys.exit(1)

    print(f"\nDone: {done} users cleaned. Run  python tools/db_usage.py  to see the new size "
          "(Atlas can take a few minutes to update the number).")


if __name__ == "__main__":
    main()
