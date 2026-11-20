"""
Shared helpers for "many databases, one after another" (DB1 -> DB2 -> DB3 -> ...).

Used by BOTH:
  * database/users_chats_db.py   (users, groups, premium, payments ...)
  * database/ia_filterdb.py      (the indexed files)

so a single new variable in Koyeb (DB3_URI, DB4_URI ...) gives every kind of data more room.

Extra databases are read from the environment, in this order:
    PREMIUM_DB_URI (old name, still works), DB2_URI, DB3_URI ... DB20_URI,
    then EXTRA_DB_URIS (several URIs separated by spaces or commas).
Duplicates and empty values are ignored.
"""
import os
import re
import time

from pymongo.errors import (OperationFailure, ServerSelectionTimeoutError, AutoReconnect,
                            ConnectionFailure)

MAX_EXTRA = 20          # DB2_URI ... DB20_URI


def extra_db_uris(premium_uri=None):
    """Every extra database URI from the environment, in order, without empties / duplicates."""
    cands = [premium_uri or ""]
    cands += [os.environ.get(f"DB{n}_URI", "") for n in range(2, MAX_EXTRA + 1)]
    cands += re.split(r"[\s,]+", os.environ.get("EXTRA_DB_URIS", ""))
    out = []
    for u in cands:
        u = (u or "").strip()
        if u and u not in out:
            out.append(u)
    return out


def is_full_error(e):
    """Atlas / MongoDB 'writes are blocked, over your space quota' errors."""
    if isinstance(e, OperationFailure) and getattr(e, "code", None) == 8000:
        return True
    t = str(e).lower()
    return "space quota" in t or "writes are blocked" in t or "no space left" in t or "disk full" in t


def is_down_error(e):
    return isinstance(e, (ServerSelectionTimeoutError, AutoReconnect, ConnectionFailure))


def host_of(uri):
    """Only the host of a Mongo URI - never the user name / password."""
    try:
        rest = (uri or "").split("://", 1)[-1].split("@", 1)[-1]
        return rest.split("/", 1)[0].split("?", 1)[0] or "(empty)"
    except Exception:
        return "?"


class WritePool:
    """Which of N clusters can take writes right now. They are filled IN ORDER: 1st, then 2nd,
    then 3rd ... a full one is skipped and the next one is used."""
    FULL_RETRY = 600     # seconds a FULL cluster is skipped before it is tried again
    DOWN_RETRY = 90      # seconds an unreachable cluster is skipped

    def __init__(self, hosts):
        self.hosts = list(hosts)
        self.n = len(self.hosts)
        self.skip_until = [0.0] * self.n

    def usable(self, i):
        return time.monotonic() >= self.skip_until[i]

    def write_order(self):
        """First usable cluster first (1st, 2nd, 3rd ...). Clusters marked full / unreachable come
        last, so space you free up (or a cluster that came back) is found at once if nothing
        else works."""
        in_order = list(range(self.n))
        return [i for i in in_order if self.usable(i)] + [i for i in in_order if not self.usable(i)]

    def ok(self, i):
        self.skip_until[i] = 0.0

    def mark_bad(self, i, err, what="data"):
        full = is_full_error(err)
        was_usable = self.usable(i)
        self.skip_until[i] = time.monotonic() + (self.FULL_RETRY if full else self.DOWN_RETRY)
        if was_usable:
            nxt = [j for j in range(self.n) if j != i and self.usable(j)]
            where = (f"cluster #{nxt[0] + 1}" if nxt
                     else "NOWHERE - every cluster is full, add a new DB (DB<next>_URI)")
            print(f"[db] {what}: cluster #{i + 1} ({self.hosts[i]}) is "
                  f"{'FULL' if full else 'unreachable'} -> new {what} goes to {where}")
