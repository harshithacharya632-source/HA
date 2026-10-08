"""Dynamic UPI payment QR codes + one-tap "open in PhonePe / GPay / Paytm / Navi".

A fresh QR is generated for the plan the user tapped, with THAT user's current
price as the amount, so scanning it opens the UPI app with the payment request
already filled in (e.g. Rs 15 for the 1-week plan). Because the amount comes
from the live plan rates every time, changing a plan to Rs 7 with /plan_rate
immediately gives a Rs 7 QR - nothing is saved or cached.

The "pay with your UPI app" buttons: Telegram only allows https:// links on
buttons (upi:// and phonepe:// are rejected), so each button opens a tiny page
on this bot's own web server (plugins/route.py -> /pay) which then hands the
payment over to the chosen app. The link is signed, so nobody can edit the
amount or point it at another UPI ID.

Needs UPI_ID (and optionally UPI_PAYEE_NAME) in the environment / info.py and
the `segno` package. If either is missing, upi_enabled() is False and the bots
fall back to the old static PAYMENT_QR picture, so nothing breaks.
"""
import asyncio
import hashlib
import hmac
import html
import inspect
import io
import json
import logging
import re
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode

from info import UPI_ID, UPI_PAYEE_NAME, URL, BOT_TOKEN

try:
    import segno
except Exception:          # package not installed yet -> feature stays off
    segno = None

_VPA_RE = re.compile(r"^[A-Za-z0-9.\-_]{2,256}@[A-Za-z][A-Za-z0-9]{1,64}$")
_MAX_AMOUNT = Decimal("100000")      # UPI's usual per-transaction ceiling (Rs 1 lakh)

# Android: Chrome "intent://" link + the app's package name opens exactly that app.
# iOS: each app's own URL scheme. (Package names / schemes as published in the
# Juspay and Razorpay UPI-intent docs.)
UPI_APPS = {
    "phonepe": {"name": "PhonePe",    "short": "PhonePe", "package": "com.phonepe.app",                         "ios": "phonepe://pay"},
    "gpay":    {"name": "Google Pay", "short": "GPay",    "package": "com.google.android.apps.nbu.paisa.user", "ios": "tez://upi/pay"},
    "paytm":   {"name": "Paytm",      "short": "Paytm",   "package": "net.one97.paytm",                         "ios": "paytmmp://upi/pay"},
    "navi":    {"name": "Navi",       "short": "Navi",    "package": "com.naviapp",                             "ios": "navipay://pay"},
}


# Apps that get their OWN button under the QR. The 4th button is "Other UPI apps": it opens the web
# page with no app chosen, which lists every app (Navi included) plus Android's "all UPI apps" chooser.
BUTTON_APPS = ("phonepe", "gpay", "paytm")


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


def build_upi_query(amount, note: str = "", upi_id: str = None, payee: str = None) -> str:
    """pa=<id>&pn=<name>&am=<amount>&cu=INR&tn=<note>   (the part after upi://pay?)"""
    pa = (upi_id or UPI_ID).strip()
    pn = (payee or UPI_PAYEE_NAME or "Goflix").strip()
    params = [("pa", pa), ("pn", pn), ("am", format_amount(amount)), ("cu", "INR")]
    if note:
        params.append(("tn", note[:50]))
    return "&".join(f"{k}={quote(v, safe='@.-_')}" for k, v in params)


def build_upi_link(amount, note: str = "", upi_id: str = None, payee: str = None) -> str:
    """upi://pay?pa=<id>&pn=<name>&am=<amount>&cu=INR&tn=<note>"""
    return "upi://pay?" + build_upi_query(amount, note, upi_id, payee)


def make_qr_png(link: str) -> io.BytesIO:
    """Plain black-on-white PNG (scans best), returned ready to upload."""
    qr = segno.make(link, error="m", micro=False)
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=10, border=4, dark="#000000", light="#ffffff")
    buf.seek(0)
    buf.name = "goflix_upi_qr.png"
    return buf


# ───────────────────────── signed "open in app" links ─────────────────────────
def _secret() -> bytes:
    return hashlib.sha256(("goflix-upi-pay:" + (BOT_TOKEN or "")).encode()).digest()


def sign_pay(amount, note: str) -> str:
    msg = f"{format_amount(amount)}|{note}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:24]


def verify_pay_sig(amount, note: str, sig: str) -> bool:
    try:
        return hmac.compare_digest(sign_pay(amount, note), sig or "")
    except ValueError:
        return False


def pay_page_url(amount, note: str, app: str = None):
    """https link to this server's /pay page, or None when URL isn't an https address."""
    base = (URL or "").strip().rstrip("/")
    if not base.startswith("https://"):
        return None
    params = {"am": format_amount(amount), "tn": note, "sig": sign_pay(amount, note)}
    if app:
        params["app"] = app
    return f"{base}/pay?{urlencode(params)}"


def open_page_url(app: str = None):
    """https link to this server's /open page: it only OPENS the UPI app (nothing is passed to it).
    No app => the page that lists every app. None when URL isn't an https address."""
    base = (URL or "").strip().rstrip("/")
    if not base.startswith("https://"):
        return None
    return f"{base}/open/{app}" if app else f"{base}/open"


def app_button_rows(amount=None, note: str = "") -> list:
    """2x2 grid: PhonePe / GPay / Paytm + "Other UPI apps". Each only OPENS that app ([] if URL isn't https)."""
    from pyrogram.types import InlineKeyboardButton
    buttons = []
    for key in BUTTON_APPS:
        url = open_page_url(key)
        if not url:
            return []
        buttons.append(InlineKeyboardButton(f"📱 {UPI_APPS[key]['short']}", url=url))
    buttons.append(InlineKeyboardButton("➕ Other UPI apps", url=open_page_url()))
    return [buttons[i:i + 2] for i in range(0, len(buttons), 2)]


def detect_platform(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    if "android" in ua:
        return "android"
    if any(k in ua for k in ("iphone", "ipad", "ipod")):
        return "ios"
    return "desktop"


def app_url(app_key: str, platform: str, query: str) -> str:
    meta = UPI_APPS[app_key]
    if platform == "android":
        return f"intent://pay?{query}#Intent;scheme=upi;package={meta['package']};end"
    if platform == "ios":
        return f"{meta['ios']}?{query}"
    return f"upi://pay?{query}"


def render_pay_page(amount, note: str, app_key: str = None, user_agent: str = "") -> str:
    """The small page the 'open in <app>' buttons land on. Raises ValueError for a bad amount."""
    amt = format_amount(amount)
    query = build_upi_query(amt, note)
    platform = detect_platform(user_agent)
    keys = list(UPI_APPS)
    if app_key in UPI_APPS:
        keys.remove(app_key)
        keys.insert(0, app_key)
    esc = lambda s: html.escape(s, quote=True)

    buttons = []
    for i, k in enumerate(keys):
        cls = "btn main" if (app_key in UPI_APPS and i == 0) else "btn"
        buttons.append(f'<a class="{cls}" href="{esc(app_url(k, platform, query))}">Open {esc(UPI_APPS[k]["name"])}</a>')
    buttons.append(f'<a class="btn alt" href="{esc("upi://pay?" + query)}">Any other UPI app</a>')

    if platform == "desktop":
        hint = "This page is meant for your phone. On a computer, scan the QR code shown in Telegram instead."
    else:
        hint = ("Nothing opened? Open this page in Chrome (⋮ → Open in browser) and tap the button again. "
                "The app must be installed on this phone.")
    auto = ""
    if app_key in UPI_APPS and platform in ("android", "ios"):
        target = json.dumps(app_url(app_key, platform, query)).replace("</", "<\\/")
        auto = f"<script>setTimeout(function(){{window.location.href={target};}},250);</script>"

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Pay ₹{esc(amt)} — Goflix</title><style>"
        "body{margin:0;background:#0f1115;color:#f2f4f8;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;"
        "display:flex;justify-content:center}main{width:100%;max-width:420px;padding:28px 18px;text-align:center}"
        "h1{font-size:32px;margin:6px 0}.sub{color:#9aa3b2;margin:0 0 22px}"
        ".btn{display:block;margin:10px 0;padding:15px;border-radius:12px;background:#1d2330;color:#fff;"
        "text-decoration:none;font-weight:600;font-size:17px;border:1px solid #2c3446}"
        ".btn.main{background:#2f6bff;border-color:#2f6bff}.btn.alt{background:transparent;color:#9aa3b2}"
        ".hint{color:#9aa3b2;font-size:13px;line-height:1.5;margin:18px 0 0}"
        f'</style></head><body><main><h1>Pay ₹{esc(amt)}</h1><p class="sub">{esc(note)}</p>'
        + "".join(buttons)
        + f'<p class="hint">{esc(hint)}</p>'
        '<p class="hint">After paying, go back to Telegram, tap “I\'ve paid” and send the payment screenshot.</p>'
        + auto + "</main></body></html>"
    )


# ───────────────────────────── Telegram message ─────────────────────────────
def build_caption(plan_label: str, amount, standard_amount=None, offer: bool = False, has_app_buttons: bool = False) -> str:
    if offer and standard_amount is not None and str(standard_amount) != str(amount):
        price = f"<s>₹{standard_amount}</s> <b>₹{amount}</b> 🎁 <i>one-time offer</i>"
    else:
        price = f"<b>₹{amount}</b>"
    lines = [f"<b>💳 Pay {price} — {plan_label} Premium</b>\n"]
    if has_app_buttons:
        lines += [
            "1️⃣ Tap <b>💾 Save QR code</b> below",
            f"2️⃣ Open your UPI app (buttons below) → <b>Scan QR</b> → choose the saved QR from your gallery — the amount ₹{amount} is already filled in",
            "3️⃣ After paying, tap “I've paid” and send the payment screenshot",
            "\n<i>On another phone? Just scan the QR above.</i>",
        ]
    else:
        lines += [
            f"1️⃣ Scan this QR with any UPI app — the amount ₹{amount} is already filled in",
            "💾 Paying from this phone? Tap <b>Save QR code</b> below, then in your UPI app choose scan → gallery",
            "2️⃣ After paying, tap “I've paid” and send the payment screenshot",
        ]
    # Some UPI apps show their own warning for payments opened from a link/QR (PhonePe's "QR via gallery"
    # note, Paytm's "UPI Risk Policy" alert). The bot can't remove those, so give a manual way that always works.
    if upi_enabled():
        lines.append(
            f"\n💡 <i>UPI app shows a warning or fails? Close it, open your UPI app → Pay to UPI ID, "
            f"and pay ₹{amount} to (tap to copy):</i>\n<code>{html.escape(UPI_ID.strip())}</code>"
        )
    return "\n".join(lines)


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


# ───────────────────────────── "Save QR code" button ─────────────────────────────
# Telegram can't save a picture to the phone's gallery by itself, so the button sends the same QR again as
# a FILE (not compressed, with a Save/Download option). The handler is attached to whichever bot sent the QR
# (main bot or admin bot) the first time that bot sends one.
SAVE_QR_DATA = "upi_saveqr"
_QR_CACHE = {}                 # (chat_id, message_id) -> (png_bytes, amount, time stored)
_QR_CACHE_TTL = 900            # the QR message itself is deleted after 10 minutes
_save_handler_clients = set()
_log = logging.getLogger(__name__)


def _cache_put(chat_id, message_id, png: bytes, amount):
    now = time.time()
    for k in [k for k, v in _QR_CACHE.items() if now - v[2] > _QR_CACHE_TTL]:
        _QR_CACHE.pop(k, None)
    _QR_CACHE[(chat_id, message_id)] = (png, amount, now)


async def _ensure_save_handler(client):
    """Registers the Save-QR button handler on this bot, once. Runs before the normal button handlers
    (group -7) and stops them, so no other callback handler ever sees this button's data."""
    key = id(client)
    if key in _save_handler_clients:
        return
    from pyrogram import filters
    from pyrogram.handlers import CallbackQueryHandler
    _save_handler_clients.add(key)
    try:
        res = client.add_handler(CallbackQueryHandler(_on_save_qr, filters.regex(rf"^{SAVE_QR_DATA}$")), group=-7)
        if inspect.isawaitable(res):
            await res
    except Exception:
        _save_handler_clients.discard(key)
        raise


async def _on_save_qr(client, query):
    from pyrogram import StopPropagation, enums
    answered = False
    chat_id = None
    try:
        msg = query.message
        chat_id = msg.chat.id if msg else None
        cached = _QR_CACHE.get((msg.chat.id, msg.id)) if msg else None
        if cached:
            png, amount = cached[0], cached[1]
        elif msg is not None and msg.photo:                       # bot restarted: take the picture from the message
            buf = await client.download_media(msg.photo.file_id, in_memory=True)
            png, amount = bytes(buf.getbuffer()), None
        else:
            answered = True
            await query.answer("This QR has expired — please open /plan again.", show_alert=True)
            png = None
        if png:
            answered = True
            await query.answer("Sending the QR file…")
            f = io.BytesIO(png)
            f.name = "Goflix-UPI-QR.png"
            amt = f" ₹{amount}" if amount else ""
            sent = await client.send_document(
                msg.chat.id, document=f, force_document=True, parse_mode=enums.ParseMode.HTML,
                caption=(f"💾 <b>Goflix UPI QR{amt}</b>\n"
                         "Open this file and save it to your gallery "
                         "(Android: ⋮ → <b>Save to Gallery</b> · iPhone: Share → <b>Save Image</b>).\n"
                         "Then open your UPI app → <b>Scan QR</b> → choose it from the gallery."),
            )
            _spawn(_delete_later(sent, 600))
    except Exception:
        _log.exception("Save QR button failed")
        notice = "Couldn't send the QR file. Long-press the QR picture above and choose Save instead."
        try:
            if not answered:
                await query.answer(notice, show_alert=True)
            elif chat_id is not None:               # a button tap can only be answered once, so tell them in the chat
                _spawn(_delete_later(await client.send_message(chat_id, "⚠️ " + notice), 60))
        except Exception:
            pass
    raise StopPropagation


async def send_plan_qr(client, chat_id, plan: str, labels: dict, pricing: dict, rows_after: list = None, delete_after: int = 600):
    """Generate + send the QR for `plan` at this user's price. Returns the sent message.
    Under the QR: PhonePe/GPay/Paytm/Other apps (only open the app), a "Save QR code" button, then `rows_after`."""
    from pyrogram import enums
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    amount = pricing["prices"][plan]
    offer = bool(pricing["is_offer"].get(plan))
    note = f"Goflix {labels[plan]}"
    link = build_upi_link(amount, note=note)
    png = await asyncio.to_thread(make_qr_png, link)
    png_bytes = png.getvalue()
    await _ensure_save_handler(client)
    app_rows = app_button_rows()
    save_row = [[InlineKeyboardButton("💾 Save QR code", callback_data=SAVE_QR_DATA)]]
    sent = await client.send_photo(
        chat_id=chat_id,
        photo=png,
        caption=build_caption(labels[plan], amount, pricing["standard"].get(plan), offer, has_app_buttons=bool(app_rows)),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(app_rows + save_row + list(rows_after or [])),
    )
    _cache_put(chat_id, sent.id, png_bytes, amount)
    if delete_after:
        _spawn(_delete_later(sent, delete_after))    # old QRs don't linger after a price change
    return sent
