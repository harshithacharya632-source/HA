"""Dynamic UPI payment QR codes.

A fresh QR is generated for the plan the user tapped, with THAT user's current
price as the amount, so scanning it opens the UPI app with the payment request
already filled in (e.g. Rs 15 for the 1-week plan). Because the amount comes
from the live plan rates every time, changing a plan to Rs 7 with /plan_rate
immediately gives a Rs 7 QR - nothing is saved or cached.

Needs UPI_ID (and optionally UPI_PAYEE_NAME) in the environment / info.py and
the `segno` package. If either is missing, upi_enabled() is False and the bots
fall back to the old static PAYMENT_QR picture, so nothing breaks.
"""
import asyncio
import io
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from info import UPI_ID, UPI_PAYEE_NAME

try:
    import segno
except Exception:          # package not installed yet -> feature stays off
    segno = None

_VPA_RE = re.compile(r"^[A-Za-z0-9.\-_]{2,256}@[A-Za-z][A-Za-z0-9]{1,64}$")
_MAX_AMOUNT = Decimal("100000")      # UPI's usual per-transaction ceiling (Rs 1 lakh)


def upi_enabled() -> bool:
    return bool(segno and UPI_ID and _VPA_RE.match(UPI_ID.strip()))


def format_amount(amount) -> str:
    """'15' -> '15.00'. Raises ValueError for junk, zero, negative or huge values."""
    try:
        d = Decimal(str(amount).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"invalid amount: {amount!r}")
    if not d.is_finite() or d <= 0 or d > _MAX_AMOUNT:
        raise ValueError(f"amount out of range: {amount!r}")
    return str(d.quantize(Decimal("0.01")))


def build_upi_link(amount, note: str = "", upi_id: str = None, payee: str = None) -> str:
    """upi://pay?pa=<id>&pn=<name>&am=<amount>&cu=INR&tn=<note>"""
    pa = (upi_id or UPI_ID).strip()
    pn = (payee or UPI_PAYEE_NAME or "Goflix").strip()
    params = [("pa", pa), ("pn", pn), ("am", format_amount(amount)), ("cu", "INR")]
    if note:
        params.append(("tn", note[:50]))
    return "upi://pay?" + "&".join(f"{k}={quote(v, safe='@.-_')}" for k, v in params)


def make_qr_png(link: str) -> io.BytesIO:
    """Plain black-on-white PNG (scans best), returned ready to upload."""
    qr = segno.make(link, error="m", micro=False)
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=10, border=4, dark="#000000", light="#ffffff")
    buf.seek(0)
    buf.name = "goflix_upi_qr.png"
    return buf


def build_caption(plan_label: str, amount, standard_amount=None, offer: bool = False, upi_id: str = None) -> str:
    if offer and standard_amount is not None and str(standard_amount) != str(amount):
        price = f"<s>₹{standard_amount}</s> <b>₹{amount}</b> 🎁 <i>one-time offer</i>"
    else:
        price = f"<b>₹{amount}</b>"
    return (
        f"<b>💳 Pay {price} — {plan_label} Premium</b>\n\n"
        "1️⃣ Scan this QR with any UPI app (GPay, PhonePe, Paytm…)\n"
        f"2️⃣ The amount ₹{amount} is already filled in — just confirm and pay\n"
        "3️⃣ Come back and send the payment screenshot\n\n"
        "<i>On the same phone? Save this image, then in your UPI app choose "
        "“scan from gallery / upload QR”.</i>\n\n"
        f"UPI ID: <code>{(upi_id or UPI_ID).strip()}</code>"
    )


_bg_tasks = set()


def _spawn(coro):
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def _delete_later(message, delay):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass


def plan_qr_buttons(pricing: dict, labels: dict) -> list:
    """One button per plan, callback 'upiqr_<plan>', showing THIS user's price."""
    from pyrogram.types import InlineKeyboardButton
    from plan_pricing import PLAN_ORDER
    rows = []
    for p in PLAN_ORDER:
        if p in labels and p in pricing["prices"]:
            star = " 🎁" if pricing["is_offer"].get(p) else ""
            rows.append([InlineKeyboardButton(f"📲 {labels[p]} — ₹{pricing['prices'][p]}{star}", callback_data=f"upiqr_{p}")])
    return rows


async def send_plan_qr(client, chat_id, plan: str, labels: dict, pricing: dict, reply_markup=None, delete_after: int = 600):
    """Generate + send the QR for `plan` at this user's price. Returns the sent message."""
    from pyrogram import enums
    amount = pricing["prices"][plan]
    offer = bool(pricing["is_offer"].get(plan))
    link = build_upi_link(amount, note=f"Goflix {labels[plan]}")
    png = await asyncio.to_thread(make_qr_png, link)
    sent = await client.send_photo(
        chat_id=chat_id,
        photo=png,
        caption=build_caption(labels[plan], amount, pricing["standard"].get(plan), offer),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=reply_markup,
    )
    if delete_after:
        _spawn(_delete_later(sent, delete_after))    # old QRs don't linger after a price change
    return sent
