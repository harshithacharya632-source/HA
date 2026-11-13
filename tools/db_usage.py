"""
Show what is using space in your Mongo cluster, and (optionally) clean the
safe-to-delete stuff.

Usage (run from the repo root, on your PC or server):
    export DATABASE_URI="mongodb+srv://..."        # the FULL cluster
    export DATABASE_NAME="harshithacharya8"        # optional, same as info.py
    python tools/db_usage.py                       # report only
    python tools/db_usage.py --clean --days 30     # DRY RUN of the cleanup
    python tools/db_usage.py --clean --days 30 --apply   # really delete

What --clean touches (nothing else is ever deleted):
  * movie_updates       : posts whose last_post_date is older than --days
                          (if that title gets a new file later, the bot just
                          makes a fresh post for it)
  * pending_screenshots : stashed payment screenshots older than --days
  * payment_requests    : only REJECTED requests older than --days
Never touched: users, groups, uersz (premium), approved payments,
payment_signatures, clone_bots, files.

Reads still work on a full Atlas cluster, so this script works even now.
"""
import os
import sys
import argparse
import datetime
from pymongo import MongoClient

MB = 1024 * 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default=os.environ.get("DATABASE_URI", ""))
    ap.add_argument("--db", default=os.environ.get("DATABASE_NAME", "harshithacharya8"))
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--apply", action="store_true", help="actually delete (default is dry run)")
    args = ap.parse_args()

    if not args.uri:
        sys.exit("Set DATABASE_URI (or pass --uri).")

    client = MongoClient(args.uri, serverSelectionTimeoutMS=15000)
    db = client[args.db]

    stats = db.command("dbstats")
    print(f"\nDatabase '{args.db}': data {stats['dataSize']/MB:.1f} MB, "
          f"storage {stats['storageSize']/MB:.1f} MB, indexes {stats['indexSize']/MB:.1f} MB")
    print("(Atlas free tier limit is 512 MB)\n")

    rows = []
    for name in db.list_collection_names():
        try:
            s = db.command("collstats", name)
            rows.append((name, s.get("count", 0), s.get("size", 0), s.get("storageSize", 0), s.get("totalIndexSize", 0)))
        except Exception as e:
            rows.append((name, -1, 0, 0, 0))
            print(f"  could not stat {name}: {e}")
    rows.sort(key=lambda r: r[3] + r[4], reverse=True)

    print(f"{'collection':28} {'docs':>10} {'data MB':>9} {'storage MB':>11} {'index MB':>9}")
    for name, count, size, storage, idx in rows:
        print(f"{name:28} {count:>10} {size/MB:>9.1f} {storage/MB:>11.1f} {idx/MB:>9.1f}")

    if not args.clean:
        print("\nReport only. Add --clean to see what could be removed.")
        return

    cutoff = datetime.datetime.now() - datetime.timedelta(days=args.days)
    cutoff_date = cutoff.date().isoformat()
    mode = "DELETING" if args.apply else "DRY RUN (nothing deleted)"
    print(f"\n--- Cleanup: older than {args.days} days -> {mode} ---")

    jobs = [
        ("movie_updates", {"last_post_date": {"$lt": cutoff_date}}),
        ("pending_screenshots", {"created_at": {"$lt": cutoff}}),
        ("payment_requests", {"status": "rejected", "handled_at": {"$lt": cutoff}}),
    ]
    for coll, query in jobs:
        c = db[coll]
        n = c.count_documents(query)
        print(f"{coll:24} matches {n}")
        if args.apply and n:
            res = c.delete_many(query)
            print(f"{'':24} deleted {res.deleted_count}")

    if args.apply:
        s2 = db.command("dbstats")
        print(f"\nNow: data {s2['dataSize']/MB:.1f} MB (was {stats['dataSize']/MB:.1f} MB)")
    else:
        print("\nRe-run with --apply to delete.")


if __name__ == "__main__":
    main()
