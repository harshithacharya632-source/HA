"""Stops the same payment screenshot from being credited twice.

Put this file in the BOT repo root (next to plan_pricing.py and upi_qr.py).

The old check only compared the transaction ID that OCR managed to read. When OCR missed the ID
(very common), nothing was compared at all and the same screenshot got premium again. This adds:

1. find_duplicate()  - checks an already-approved request with, in this order:
       a) the same transaction ID                      (OCR-read, as before)
       b) the byte-identical screenshot FILE (sha256)   (works even if OCR reads nothing)
       c) same user + same amount + same payment time   (catches a re-screenshot / crop by the same user)
   a) and b) can never hit a different genuine payment. c) is limited to the SAME user.
2. user_lock()       - one screenshot check at a time per user. Without it, sending the picture twice
                       quickly makes both checks run before the first is approved, so both pass.

Only requests approved AFTER this update carry the image fingerprint, so b) protects new payments.
"""
import asyncio
import hashlib

APPROVED = {"$in": ["approved", "auto_approved"]}

_locks = {}


def user_lock(user_id) -> asyncio.Lock:
    """The same Lock object for the same user, so a user's screenshots are checked one after another."""
    lock = _locks.get(user_id)
    if lock is None:
        lock = _locks[user_id] = asyncio.Lock()
    return lock


def image_fingerprint(photo_bytes: bytes) -> str:
    return hashlib.sha256(photo_bytes).hexdigest()


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
    if user_id is not None and amount is not None and parsed_date is not None:
        doc = await col.find_one({
            "user_id": int(user_id),
            "extracted.amount": amount,
            "extracted.parsed_date": parsed_date,
            "status": APPROVED,
        })
        if doc:
            return doc, "same user, amount and payment time"
    return None, None
