"""Stops the same payment screenshot from being credited twice — by ANYONE.

Put this file in the BOT repo root (next to plan_pricing.py and upi_qr.py).

One real payment may unlock premium ONCE. It does not matter who sends the screenshot: the person
who paid, a friend they forwarded it to, or someone who re-uploaded / re-screenshotted / cropped it.

Why the old check let a friend through: a different user's copy usually has DIFFERENT file bytes
(Telegram re-compresses a re-uploaded photo), so the exact-file hash missed; the transaction ID is
often not read by OCR; and the "same amount + same payment time" check was limited to the SAME user.

find_duplicate() now checks an already-approved payment, for ANY user, in this order:
   a) the same transaction ID                         (when OCR read it)
   b) the byte-identical screenshot FILE (sha256)     (a straight forward of the same photo)
   c) the same AMOUNT at the same payment MINUTE      (a re-upload / re-screenshot / crop, by anyone)
   If both screenshots show a transaction ID and the two IDs DIFFER, they are different payments,
   so c) is skipped (it can never block a genuinely different payment that has its own ID).

claim_signatures() is the atomic safety net: when a payment is about to be approved, its signatures
are written to a collection with a UNIQUE index. Only one request can ever own a signature, so even
if two people send the same screenshot at the same instant, exactly one wins.

user_lock() — one screenshot check at a time per user (a double-send by the same person).

Trade-off (deliberate, asked for by the owner): two DIFFERENT customers who each pay the same amount
in the SAME minute with no readable transaction ID look identical to c). The second one is rejected
and can use "Appeal to admin" (an admin approving it manually is never blocked).
"""
import asyncio
import datetime
import hashlib
import logging
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

APPROVED = {"$in": ["approved", "auto_approved"]}

_locks = {}
_index_ready = False


def user_lock(user_id) -> asyncio.Lock:
    """The same Lock object for the same user, so a user's screenshots are checked one after another."""
    lock = _locks.get(user_id)
    if lock is None:
        lock = _locks[user_id] = asyncio.Lock()
    return lock


def image_fingerprint(photo_bytes: bytes) -> str:
    return hashlib.sha256(photo_bytes).hexdigest()


def norm_amount(amount):
    """'15', '15.0', '₹15.00', 15 -> '15'.  '40.50' -> '40.5'.  None/garbage -> None."""
    if amount is None:
        return None
    try:
        d = Decimal(str(amount).replace("₹", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if d <= 0:
        return None
    s = format(d.normalize(), "f")
    return s


def amount_variants(amount):
    """Every way OCR may have stored the same amount: 15 -> ['15', '15.0', '15.00']."""
    n = norm_amount(amount)
    if n is None:
        return []
    out = {n}
    if "." not in n:
        out.update({n + ".0", n + ".00"})
    else:
        out.add(n + "0")
    return sorted(out)


def _minute(parsed_date):
    if not isinstance(parsed_date, datetime.datetime):
        return None
    return parsed_date.replace(second=0, microsecond=0)


def signature_keys(*, txn_id=None, image_sha256=None, amount=None, parsed_date=None):
    """The signatures one real payment is known by. Any one of them repeating = same payment."""
    keys = []
    if txn_id:
        keys.append(f"txn:{str(txn_id).strip().upper()}")
    if image_sha256:
        keys.append(f"sha:{image_sha256}")
    n, m = norm_amount(amount), _minute(parsed_date)
    if n and m:
        keys.append(f"pay:{n}|{m.strftime('%Y-%m-%d %H:%M')}")
    return keys


async def find_duplicate(db, *, txn_id=None, image_sha256=None, user_id=None, amount=None, parsed_date=None):
    """Returns (earlier_request, kind) for an already-approved payment that this screenshot repeats,
    or (None, None). `db` is the bot's database object (uses db.payment_requests)."""
    col = db.payment_requests
    if txn_id:
        doc = await col.find_one({"extracted.txn_id": txn_id, "status": APPROVED})
        if doc:
            return doc, "same transaction ID"
    if image_sha256:
        doc = await col.find_one({"extracted.image_sha256": image_sha256, "status": APPROVED})
        if doc:
            return doc, "identical screenshot file"

    variants, minute = amount_variants(amount), _minute(parsed_date)
    if variants and minute:
        cursor = col.find({
            "extracted.amount": {"$in": variants},
            "extracted.parsed_date": {"$gte": minute, "$lt": minute + datetime.timedelta(minutes=1)},
            "status": APPROVED,
        }).limit(10)
        async for doc in cursor:
            earlier_txn = (doc.get("extracted") or {}).get("txn_id")
            if txn_id and earlier_txn and str(txn_id).upper() != str(earlier_txn).upper():
                continue   # both show a transaction ID and they differ -> a different, genuine payment
            same_user = user_id is not None and int(doc.get("user_id", -1)) == int(user_id)
            return doc, ("same user, amount and payment time" if same_user
                         else "another user already used this payment (same amount and payment time)")
    return None, None


async def _signatures(db):
    """The unique-indexed claims collection (index created once, lazily)."""
    global _index_ready
    col = db.payment_signatures
    if not _index_ready:
        await col.create_index("key", unique=True)
        _index_ready = True
    return col


async def claim_signatures(db, request_id, keys):
    """Atomically claim every signature for this request.
    Returns (True, None) when this request now owns them all, or (False, owner_request_id) when
    another request already owns one — in which case anything this call claimed is released again."""
    if not keys:
        return True, None
    col = await _signatures(db)
    rid = str(request_id)
    claimed = []
    for key in keys:
        try:
            await col.insert_one({"key": key, "request_id": rid, "claimed_at": datetime.datetime.now()})
            claimed.append(key)
        except Exception as e:
            if "duplicate key" not in str(e).lower() and "E11000" not in str(e):
                raise
            owner = await col.find_one({"key": key})
            if owner and owner.get("request_id") == rid:
                continue   # our own claim (e.g. a retry) — fine
            if claimed:
                await col.delete_many({"key": {"$in": claimed}, "request_id": rid})
            return False, (owner or {}).get("request_id")
    return True, None


async def release_claims(db, request_id):
    """Give the signatures back (used if the premium grant itself failed, so a retry isn't blocked)."""
    try:
        col = await _signatures(db)
        await col.delete_many({"request_id": str(request_id)})
    except Exception as e:
        logger.warning(f"Couldn't release payment signatures for request {request_id}: {e}")
