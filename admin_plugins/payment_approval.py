"""
Goflix_AdminBot — UPI screenshot payment approval + user support relay.

This is a SEPARATE bot from the main Goflix file-store bot (its own
BOT_TOKEN, its own Client, started alongside the main bot in bot.py).
Users are sent here (via the "Send screenshot" link under /plan on the
main bot, which is just OWNER_LNK) to submit their UPI payment proof —
and can also just message this bot directly with questions, which get
relayed to the admins (see "Support Q&A relay" below).

/plan and /myplan also work directly on this bot (not just the main
bot) — same info, shown here.

Payment flow:
  1. User taps /start (or says hi), OR sends the screenshot photo cold
     with no plan picked yet (see unsolicited_screenshot_cb — the photo
     is stashed and they're asked which plan it's for).
  2. Bot shows a "Submit Payment Screenshot" button -> asks which plan.
  3. Bot asks for the screenshot photo (unless one was already stashed
     from step 1, in which case that's used instead).
  4. OCR (pytesseract) pulls out an amount, a date/time, and whether the
     payee name on the screenshot looks like ours (see PAYEE_NAME_HINT).
  5a. Exact match — amount equals the claimed plan's rate, the payment
      timestamp is within the last hour, and the payee name matches —
      premium is granted immediately, no admin needed. A record-only
      copy (no buttons) goes to LOG_CHANNEL so approvals stay auditable.
  5b. Clear mismatch — an amount WAS read off the screenshot and it does
      not equal the claimed plan's rate — auto-rejected immediately, no
      admin needed either (this is a wrong/mismatched payment, not an
      ambiguous one).
  5c. Anything else (amount not readable, date stale, payee not
      detected, etc.) — sent to LOG_CHANNEL with the screenshot and
      Approve/Reject buttons for an admin to decide.

  Extending: if the user already has time remaining on an existing
  plan, a new approval (auto or manual) is added on top of what's left
  rather than overwriting it.

Support Q&A relay:
  Any other message a user sends (not /start, /plan, /myplan, or a
  screenshot) is forwarded to every admin's PM with this bot. An admin
  replies by using Telegram's native reply-to on that forwarded copy,
  and the reply is relayed straight back to the user.

Requires: pytesseract + tesseract-ocr/tesseract-ocr-eng system packages
(see Dockerfile) and Pillow (already in requirements.txt).
"""

import io
import re
import asyncio
import datetime
import logging

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.users_chats_db import db
from info import (
    ADMINS, LOG_CHANNEL, PREMIUM_AND_REFERAL_MODE, STAR_PLAN_LABELS,
    STAR_PLAN_SECONDS, OWNER_LNK, BOT_TOKEN, PAYMENT_TEXT, PAYMENT_QR,
)
from plugins.commands import (
    load_plan_rates, format_plan_rates, format_remaining_time, format_expiry_time,
)
# The main Goflix bot's own Client instance, so the "premium unlocked"
# message can be sent from THAT bot too (in addition to this AdminBot),
# since that's the bot the user is actually using day-to-day.
from TechVJ.bot import TechVJBot

logger = logging.getLogger(__name__)

try:
    import pytesseract
    from PIL import Image, ImageOps
    # Importing the pytesseract *library* only proves the Python package
    # is installed — it says nothing about whether the actual `tesseract`
    # binary is on PATH inside the container (see Dockerfile). Those two
    # failed independently before: pytesseract wasn't even in
    # requirements.txt, then even once it is, a missing system binary
    # would raise TesseractNotFoundError on every single screenshot and
    # get silently swallowed by ocr_screenshot()'s broad except — so
    # every screenshot would show OCR amount/date "not detected" with no
    # obvious clue why. Checking the binary here, once, at import time,
    # turns that into one clear log line instead of a mystery.
    pytesseract.get_tesseract_version()
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    logger.warning("pytesseract/Pillow not available — screenshot OCR is disabled, admins will see raw screenshots only.")
except pytesseract.TesseractNotFoundError:
    OCR_AVAILABLE = False
    logger.warning(
        "pytesseract is installed but the 'tesseract' binary isn't on PATH — "
        "screenshot OCR is disabled, admins will see raw screenshots only. "
        "Install the tesseract-ocr system package (see Dockerfile)."
    )

# Same plan keys/durations as the Stars flow on the main bot — a plan's
# length doesn't depend on how it was paid for.
PLAN_LABELS = STAR_PLAN_LABELS
PLAN_SECONDS = STAR_PLAN_SECONDS

# Plan rates are stored in MongoDB keyed by the OWNING bot's Telegram id
# (so /plan_rate on the main bot writes under the main bot's id). This
# bot is a different bot with a different id, so it must explicitly read
# the main bot's rates rather than its own (which would just be empty
# defaults). A bot's Telegram user id is the numeric part before the
# ':' in its token — no extra config needed.
MAIN_BOT_ID = int(BOT_TOKEN.split(":")[0]) if BOT_TOKEN and ":" in BOT_TOKEN else None

# How old a screenshot's payment date/time is allowed to be before it no
# longer counts as "exact" for auto-approval (someone reusing an old
# screenshot, or a scheduled/pending payment). Tightened to 1 hour —
# auto-approval is meant for "I just paid this second", anything older
# goes to manual review instead of being rejected outright.
MAX_SCREENSHOT_AGE_HOURS = 1

# Text that must appear (case-insensitively) in the OCR'd screenshot for
# the payment to count as "paid to us" — i.e. the payee/receiver name
# UPI apps print on a successful payment (matches the UPI ID's account
# name, e.g. "harshithacharya632-3@oksbi"). Adjust this if the UPI
# display name ever changes.
PAYEE_NAME_HINT = "harshith"


async def _delayed_delete(message, delay: int):
    """Fire-and-forget helper: deletes a message after `delay` seconds
    without blocking whatever handler scheduled it. Used for status/
    confirmation messages that are only useful for a minute or two."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass


# ── OCR extraction ──────────────────────────────────────────────────────

_AMOUNT_PATTERNS = [
    # Best case: OCR read the currency symbol/prefix correctly.
    re.compile(r'(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.\d{1,2})?)', re.IGNORECASE),
    # Fallback: Tesseract very often drops or mangles the ₹ glyph
    # entirely (confirmed against real screenshots — "₹15.00" OCRs as
    # bare "15.00" once the image is upscaled, see ocr_screenshot()).
    # A standalone amount-shaped number (exactly 2 decimals, its own
    # line) is how GPay/PhonePe/Paytm always print the amount, so this
    # is safe to use as a fallback without a currency symbol at all.
    re.compile(r'^\s*([0-9][0-9,]*\.\d{2})\s*$', re.MULTILINE),
]

# Common UPI-app receipt date formats. Not exhaustive — different apps
# (GPay/PhonePe/Paytm) format this differently, so this is intentionally
# forgiving. If nothing matches, the screenshot is just treated as
# low-confidence rather than failing.
_DATE_PATTERNS = [
    # "19 March 2026, 11:03 pm" (GPay) / "9 Mar 2026, 12:14 PM" (Navi)
    re.compile(r'\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4},?\s+\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?)\b'),
    re.compile(r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4},?\s+\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?)\b'),
]

# PhonePe prints it the other way round — "10:19 pm on 22 Aug 2026" —
# time first, then the date, joined by "on". Handled separately since
# the two pieces need to be rejoined into "date, time" order before the
# same strptime formats below can parse it.
_TIME_ON_DATE_PATTERN = re.compile(
    r'\b(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))\s+on\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b'
)

_DATE_TRY_FORMATS = [
    "%d %b %Y, %I:%M %p", "%d %B %Y, %I:%M %p",
    "%d %b %Y %I:%M %p", "%d %B %Y %I:%M %p",
    "%d/%m/%Y, %I:%M %p", "%d/%m/%Y %I:%M %p",
    "%d-%m-%Y, %I:%M %p", "%d-%m-%Y %I:%M %p",
    "%d/%m/%y, %I:%M %p", "%d/%m/%y %I:%M %p",
]


def _extract_amount(text: str):
    for pattern in _AMOUNT_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1).replace(",", "")
    return None


def _amounts_equal(a, b) -> bool:
    """Compares screenshot amounts numerically, not as strings — OCR
    often reads '₹15.00' where the stored plan rate is just '15', and a
    plain string compare would wrongly call that a mismatch."""
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


def _extract_datetime(text: str):
    """Returns (raw_string, parsed_datetime_or_None)."""
    for pattern in _DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            raw = m.group(1)
            for fmt in _DATE_TRY_FORMATS:
                try:
                    return raw, datetime.datetime.strptime(raw, fmt)
                except ValueError:
                    continue
            return raw, None

    m = _TIME_ON_DATE_PATTERN.search(text)
    if m:
        time_part, date_part = m.group(1), m.group(2)
        raw = f"{date_part}, {time_part}"
        for fmt in _DATE_TRY_FORMATS:
            try:
                return raw, datetime.datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return raw, None

    return None, None


def _payee_name_matches(text: str) -> bool:
    """True if the screenshot's OCR text mentions our UPI account name —
    i.e. the payment actually went to us, not to someone else's ID (or
    this isn't a payment screenshot at all)."""
    return PAYEE_NAME_HINT.lower() in text.lower()


def _ocr_variants(image):
    """Yields preprocessing variants of the screenshot to try OCR on, in
    order — cheapest/most reliable first. Deliberately does NOT try the
    image at native resolution: confirmed on real screenshots that at
    native size Tesseract fuses the ₹ glyph into the digits themselves
    (e.g. "₹15.00" reads as "215.00", a corrupted but still valid-
    looking number) rather than dropping it, which the amount-without-
    a-currency-symbol fallback pattern would wrongly accept as genuine.
    Upscaling first reliably avoids that fusion (₹ gets dropped cleanly
    instead), so it's always tried before anything else.

    A single OCR pass can still fail on screenshots that have been
    re-compressed (e.g. forwarded through Telegram, which re-encodes
    every photo it stores) even when the same screenshot at slightly
    different compression reads perfectly — so if the 2x pass doesn't
    find an amount, progressively more aggressive variants are tried."""
    upscaled = image.resize((image.width * 2, image.height * 2), Image.LANCZOS)
    yield upscaled
    # Grayscale + autocontrast + a larger upscale — helps on low-contrast
    # / heavily compressed screenshots where a plain 2x upscale isn't
    # enough on its own.
    yield ImageOps.autocontrast(
        image.resize((image.width * 3, image.height * 3), Image.LANCZOS).convert("L")
    )
    # Dark-theme receipts (white text on black) often read better
    # inverted — try that last since it's the most likely to mangle
    # OTHER text (date/payee) even when it helps the amount.
    yield ImageOps.invert(upscaled)


async def ocr_screenshot(photo_bytes: bytes) -> dict:
    """Runs OCR — trying a few preprocessing variants (see
    _ocr_variants), stopping as soon as one successfully reads an amount
    — and returns a dict with whatever was extracted, plus a
    'confidence' verdict. Never raises — OCR failing just means low
    confidence, not a crash."""
    result = {
        "amount": None, "raw_date": None, "parsed_date": None,
        "confidence": "low", "ocr_text": "", "payee_ok": False,
        "ocr_read_ok": False,
    }
    if not OCR_AVAILABLE:
        return result
    try:
        image = Image.open(io.BytesIO(photo_bytes)).convert("RGB")

        best_text = ""
        for variant in _ocr_variants(image):
            variant_text = pytesseract.image_to_string(variant)
            if len(variant_text.strip()) > len(best_text.strip()):
                best_text = variant_text
            if _extract_amount(variant_text):
                # This variant found a usable amount — stop here. Further,
                # more aggressive variants can sometimes read WORSE on the
                # surrounding text (date/payee) once heavily processed.
                best_text = variant_text
                break
        text = best_text

        result["ocr_text"] = text
        result["ocr_read_ok"] = len(text.strip()) >= 20
        result["amount"] = _extract_amount(text)
        raw_date, parsed_date = _extract_datetime(text)
        result["raw_date"] = raw_date
        result["parsed_date"] = parsed_date
        result["payee_ok"] = _payee_name_matches(text)

        if result["amount"] and parsed_date:
            age = datetime.datetime.now() - parsed_date
            if datetime.timedelta(0) <= age <= datetime.timedelta(hours=MAX_SCREENSHOT_AGE_HOURS):
                result["confidence"] = "high"
    except Exception as e:
        logger.warning(f"OCR failed on a payment screenshot: {e}")
    return result


# ── Entry points: /start and a plain "hi" ────────────────────────────────

def _welcome_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🧾 Submit Payment Screenshot", callback_data="submit_upi_screenshot")]])


@Client.on_message(filters.private & filters.command("start"))
async def admin_bot_start(client, message):
    await message.reply_text(
        "<b>👋 Welcome to Goflix Payments</b>\n\n"
        "Paid for premium via UPI? Tap below to submit your screenshot and an admin will verify it shortly.",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_welcome_markup()
    )


@Client.on_message(filters.private & filters.text & filters.regex(r"(?i)^(hi|hii|hai|hello|hey)$"), group=-1)
async def admin_bot_greeting(client, message):
    """A bare hi/hello isn't a real question, so it's handled fully here
    (no payment button, and it never reaches the support relay below —
    admins' time is precious, this doesn't need to bother them) and
    cleans itself up quickly."""
    sent = await message.reply_text("Hey! Use /plan to see plans, or just send your payment screenshot.")
    asyncio.create_task(_delayed_delete(sent, 30))


# ── /plan and /myplan also work directly on this bot ────────────────────
# Same info as the main bot's versions, just shown here too since users
# often end up talking to this bot anyway.

@Client.on_message(filters.private & filters.command("plan"))
async def admin_bot_plan_cmd(client, message):
    if PREMIUM_AND_REFERAL_MODE == False:
        return
    rates = await load_plan_rates(MAIN_BOT_ID)
    caption_text = PAYMENT_TEXT.format(plan_rates=format_plan_rates(rates["upi"]))
    sent = await message.reply_photo(
        photo=PAYMENT_QR,
        caption=caption_text,
        parse_mode=enums.ParseMode.HTML,
        has_spoiler=True,
        reply_markup=_welcome_markup(),
    )
    asyncio.create_task(_delayed_delete(sent, 180))


@Client.on_message(filters.private & filters.command("myplan"))
async def admin_bot_myplan_cmd(client, message):
    if PREMIUM_AND_REFERAL_MODE == False:
        return
    user_id = message.from_user.id
    if await db.has_premium_access(user_id):
        remaining_time = await db.check_remaining_uasge(user_id)
        expiry_time = remaining_time + datetime.datetime.now()
        sent = await message.reply_text(
            "✨ <b>Your Plan Details</b> ✨\n\n"
            f"⏳ <b>Remaining Time :</b> {format_remaining_time(remaining_time)}\n"
            f"📅 <b>Expires On :</b> {format_expiry_time(expiry_time)}\n\n"
            "🔄 Extend your plan : /plan\n\n"
            "Have a great day! 😊",
            parse_mode=enums.ParseMode.HTML,
        )
        asyncio.create_task(_delayed_delete(sent, 180))
    else:
        await message.reply_text(
            "😢 You don't have any premium subscription yet.\n\nCheck out our plans: /plan",
            reply_markup=_welcome_markup()
        )


@Client.on_callback_query(filters.regex("^submit_upi_screenshot$"))
async def start_screenshot_flow_cb(client, query):
    if PREMIUM_AND_REFERAL_MODE == False:
        return await query.answer()
    await query.answer()
    if MAIN_BOT_ID is None:
        return await client.send_message(query.from_user.id, "⚠️ Bot misconfigured (BOT_TOKEN missing) — contact an admin.")

    rates = await load_plan_rates(MAIN_BOT_ID)
    upi = rates["upi"]
    btn = [
        [InlineKeyboardButton(f"{PLAN_LABELS[p]} — {upi[p]}Rs", callback_data=f"claim_upi_plan_{p}")]
        for p in ("week", "month", "3months", "6months")
    ]
    await client.send_message(
        chat_id=query.from_user.id,
        text="<b>Which plan did you pay for?</b>\n\nPick the one matching what you just paid.",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(btn)
    )


@Client.on_callback_query(filters.regex(r"^claim_upi_plan_(\w+)$"))
async def claim_plan_then_ask_screenshot_cb(client, query):
    plan = query.matches[0].group(1)
    if plan not in PLAN_LABELS:
        return await query.answer("Invalid plan.", show_alert=True)
    await query.answer()

    # Did they already send a screenshot before picking a plan (caught by
    # unsolicited_screenshot_cb below)? If so, use that instead of asking
    # them to send it again.
    stashed_file_id = await db.pop_pending_screenshot(query.from_user.id)
    if stashed_file_id:
        await client.send_message(
            chat_id=query.from_user.id,
            text=f"<b>Got it — {PLAN_LABELS[plan]}.</b> Checking the screenshot you already sent…",
            parse_mode=enums.ParseMode.HTML
        )
        return await _handle_screenshot(client, query.from_user, query.from_user.id, stashed_file_id, plan)

    prompt = await client.send_message(
        chat_id=query.from_user.id,
        text=(
            f"<b>Got it — {PLAN_LABELS[plan]}.</b>\n\n"
            "Now send the payment screenshot as a photo (not a file/document).\n\n"
            "Make sure the amount and date/time in the screenshot are clearly visible.\n\n"
            "Send /cancel to abort."
        ),
        parse_mode=enums.ParseMode.HTML
    )

    try:
        reply = await client.ask(
            query.from_user.id, "Waiting for your screenshot…", timeout=600,
            filters=filters.photo | (filters.text & filters.regex("^/cancel$"))
        )
    except TimeoutError:
        return await prompt.reply_text("⌛ Timed out waiting for the screenshot. Tap /start again to retry.")

    if reply.text and reply.text.strip() == "/cancel":
        return await reply.reply_text("❌ Cancelled.")
    if not reply.photo:
        return await reply.reply_text("⚠️ That wasn't a photo. Tap /start again to retry.")

    await _handle_screenshot(client, reply.from_user, reply.chat.id, reply.photo.file_id, plan)


# ── Screenshot sent cold, with no plan picked yet ─────────────────────────

@Client.on_message(filters.private & filters.photo & ~filters.user(ADMINS))
async def unsolicited_screenshot_cb(client, message):
    """Catches a screenshot sent straight into the chat with no /start
    and no plan chosen (e.g. the user just pastes/forwards the photo).
    If an active client.ask() is already waiting on a photo from this
    user (the normal flow above), the ask_patch resolver (group=-1)
    consumes it first and this handler never runs — so this only fires
    for a genuinely cold screenshot.

    Runs OCR first to check this actually looks like a payment
    screenshot (an amount and/or our payee name shows up) before asking
    which plan it's for — a random unrelated photo instead gets
    forwarded to admins like any other message, not funneled into the
    payment flow."""
    if PREMIUM_AND_REFERAL_MODE == False:
        return

    photo_bytes = await client.download_media(message.photo.file_id, in_memory=True)
    extracted = await ocr_screenshot(bytes(photo_bytes.getbuffer()))

    if extracted["ocr_read_ok"] and extracted["amount"] is None and not extracted["payee_ok"]:
        # OCR read the image fine but found nothing payment-shaped in it
        # — genuinely not a payment screenshot. (If OCR failed outright,
        # ocr_read_ok is False and we don't use that as a signal either
        # way — better to assume it might be a payment and let the
        # normal flow/admin review sort it out than to silently drop a
        # real payment into the support inbox because OCR choked on it.)
        return await relay_user_question_to_admins_cb(client, message)

    await db.set_pending_screenshot(message.from_user.id, message.photo.file_id)
    rates = await load_plan_rates(MAIN_BOT_ID)
    upi = rates["upi"]
    btn = [
        [InlineKeyboardButton(f"{PLAN_LABELS[p]} — {upi[p]}Rs", callback_data=f"claim_upi_plan_{p}")]
        for p in ("week", "month", "3months", "6months")
    ]
    await message.reply_text(
        "<b>Got your screenshot — which plan did you pay for?</b>\n\nPick the one matching what you just paid.",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(btn)
    )


async def _handle_screenshot(client, user, chat_id, file_id, claimed_plan: str):
    status_msg = await client.send_message(chat_id, "🔍 Reading your screenshot…")

    photo_bytes = await client.download_media(file_id, in_memory=True)
    extracted = await ocr_screenshot(bytes(photo_bytes.getbuffer()))

    rates = await load_plan_rates(MAIN_BOT_ID)
    upi = rates["upi"]
    claimed_amount = upi.get(claimed_plan)
    amount_read = extracted["amount"]
    amount_matches = _amounts_equal(amount_read, claimed_amount)
    extracted["matched_plan"] = claimed_plan if amount_matches else None

    # Four-way decision:
    #   exact  -> payee is ours AND the amount matches the claimed plan
    #             AND the payment is within the last hour -> auto-approve,
    #             no admin needed.
    #   reject (payee)      -> OCR successfully read the screenshot but
    #             the payee name it found is NOT ours -> a confirmed
    #             wrong/fake screenshot -> auto-reject, no admin needed.
    #   reject (stale date) -> OCR successfully READ a date off this
    #             screenshot (so we're not guessing) and that date is
    #             NOT recent (older than MAX_SCREENSHOT_AGE_HOURS, or in
    #             the future) -> this is an old/reused screenshot, not
    #             an ambiguous case -> auto-reject, no admin needed. A
    #             stale date is only trusted as grounds for rejection
    #             when OCR actually parsed a date; if OCR couldn't read
    #             any date at all, that's ambiguous (below), not stale.
    #   manual -> OCR simply couldn't read the screenshot clearly enough
    #             to be sure either way (amount unreadable, or no date
    #             found at all — dark-theme screenshots sometimes still
    #             come out unreadable even after the invert retry) ->
    #             could well be a genuine payment -> falls back to admin
    #             review rather than being auto-rejected on an OCR
    #             failure that isn't the user's fault.
    date_recent = (
        extracted["parsed_date"] is not None
        and datetime.timedelta(0) <= (datetime.datetime.now() - extracted["parsed_date"]) <= datetime.timedelta(hours=MAX_SCREENSHOT_AGE_HOURS)
    )
    date_known_stale = extracted["parsed_date"] is not None and not date_recent
    reject_reason = None
    if extracted["payee_ok"] and amount_matches and date_recent:
        decision = "exact"
        extracted["confidence"] = "high"
    elif extracted["ocr_read_ok"] and not extracted["payee_ok"]:
        decision = "reject"
        reject_reason = "payee"
        extracted["confidence"] = "not_verified"
    elif date_known_stale:
        decision = "reject"
        reject_reason = "stale_date"
        extracted["confidence"] = "stale_date"
    else:
        decision = "manual"
        # The amount matched but something else didn't quite clear the
        # bar for auto-approval — the plan is still known for certain,
        # so give the admin a single confident Approve button instead of
        # a per-plan picker (that's only for when the amount itself
        # couldn't be read at all, or doesn't match today's rate — e.g.
        # rates changed since, or OCR just couldn't read this one).
        extracted["confidence"] = "high" if amount_matches else "low"

    request_id = await db.add_payment_request(
        user_id=user.id, username=user.username or user.first_name,
        screenshot_file_id=file_id,
        claimed_plan=claimed_plan, extracted=extracted,
    )

    if decision == "exact":
        sent = await status_msg.edit_text("✅ All clear! Thank you for purchasing GoFlix Premium 🎉")
        asyncio.create_task(_delayed_delete(sent, 60))
        await _grant_premium(client, request_id, user.id, claimed_plan, auto=True)
        await _log_auto_approval(client, request_id, user, claimed_plan, extracted, file_id)
        return

    if decision == "reject":
        appeal_btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📮 Appeal to admin", callback_data=f"pay_appeal_{request_id}")]
        ])
        if reject_reason == "stale_date":
            reject_text = (
                "❌ This screenshot's payment date/time isn't recent — rejected.\n\n"
                "Please send a screenshot of a payment you just made. If this IS a fresh "
                "payment and the date was misread, tap below to send it to an admin."
            )
        else:
            reject_text = (
                "❌ This screenshot isn't verified as a payment to us — rejected.\n\n"
                "If you think this is a mistake, tap below to send it to an admin for a manual check."
            )
        # Not auto-deleted like the other status messages — it carries
        # the Appeal button, which needs to stay clickable whenever the
        # user gets around to it, not just for the next minute.
        await status_msg.edit_text(reject_text, reply_markup=appeal_btn)
        await db.set_payment_request_status(request_id, "auto_rejected", None)
        await _log_auto_rejection(client, request_id, user, claimed_plan, extracted, file_id, reject_reason)
        return

    sent = await status_msg.edit_text(
        "✅ Got it! Your screenshot is with the admins for a quick check — "
        "you'll get a message the moment it's approved."
    )
    asyncio.create_task(_delayed_delete(sent, 60))
    await _notify_admins(client, request_id, user, claimed_plan, extracted, file_id)


async def _grant_premium(client, request_id, user_id, plan: str, auto: bool, admin_id=None):
    """Shared by the auto-approve path above and the manual Approve
    button below — same premium-granting logic either way, only the
    payment_request's recorded status differs.

    Extends on top of any time the user already has left, instead of
    overwriting it — a user with 6 days left on a 1-week plan who then
    buys a 1-month plan ends up with 1 month + 6 days, not just 1 month.
    """
    seconds = PLAN_SECONDS[plan]
    now = datetime.datetime.now()
    existing = await db.get_user(user_id)
    current_expiry = existing.get("expiry_time") if existing else None
    base_time = current_expiry if isinstance(current_expiry, datetime.datetime) and current_expiry > now else now
    expiry_time = base_time + datetime.timedelta(seconds=seconds)

    await db.update_user({
        "id": user_id, "expiry_time": expiry_time,
        "expiry_reminder_sent": False, "expired_notified": False,
    })
    await db.set_payment_request_status(request_id, "auto_approved" if auto else "approved", admin_id)

    unlock_text = (
        "<b>👑 ᴄᴏɴɢʀᴀᴛꜱ 👑</b>\n\n"
        f"💎 <b>ᴘʀᴇᴍɪᴜᴍ ᴜɴʟᴏᴄᴋᴇᴅ ꜰᴏʀ {PLAN_LABELS[plan]}</b>\n"
        "🌟 ᴀʟʟ ᴘʀᴇᴍɪᴜᴍ ꜰᴇᴀᴛᴜʀᴇꜱ ᴀʀᴇ ɴᴏᴡ ᴀᴄᴄᴇꜱꜱɪʙʟᴇ\n\n"
        "🚀 <b>ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ ɢᴏꜰʟɪx!</b>"
    )
    # Sent from BOTH bots — the AdminBot (where the screenshot was sent)
    # and the main Goflix bot (where the user actually spends their
    # time), so the unlock is visible wherever they check next.
    try:
        await client.send_message(chat_id=user_id, text=unlock_text, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"Couldn't DM user {user_id} after granting premium (AdminBot): {e}")
    try:
        await TechVJBot.send_message(chat_id=user_id, text=unlock_text, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"Couldn't DM user {user_id} after granting premium (main bot): {e}")


async def _log_auto_approval(client, request_id, user, plan: str, extracted, file_id):
    """Posts a record-only copy to LOG_CHANNEL for auto-approved payments
    — no buttons, nothing for an admin to action, just an audit trail
    ('the premium list') of who got premium and why."""
    caption = (
        f"<b>✅ All clear — thank you for purchasing GoFlix Premium!</b>\n\n"
        f"👤 User: {user.mention} (<code>{user.id}</code>)\n"
        f"📦 Plan: <b>{PLAN_LABELS[plan]}</b>\n"
        f"🔎 OCR amount: {extracted['amount']}Rs\n"
        f"🕐 OCR date: {extracted['raw_date'] or 'not detected'}\n"
        f"🟢 Payee matched + amount matched + within {MAX_SCREENSHOT_AGE_HOURS}h — "
        f"granted automatically, no admin action needed.\n\n"
        f"Request ID: <code>{request_id}</code>"
    )
    try:
        if not LOG_CHANNEL:
            raise ValueError("LOG_CHANNEL not set")
        await client.send_photo(chat_id=LOG_CHANNEL, photo=file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"Couldn't post auto-approval log to LOG_CHANNEL ({e}), DMing admins instead.")
        for admin_id in ADMINS:
            try:
                await client.send_photo(chat_id=admin_id, photo=file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
            except Exception as e2:
                logger.warning(f"Couldn't DM admin {admin_id} either: {e2}")


async def _log_auto_rejection(client, request_id, user, claimed_plan, extracted, file_id, reject_reason=None):
    """Posts a record-only copy to LOG_CHANNEL for auto-rejected payments
    — either the payee name didn't match ours (confirmed wrong/fake
    screenshot) or a date WAS read and it's stale/reused — either way a
    confirmed reason, not just an OCR failure — no buttons, just a
    record of what happened in case of a dispute."""
    if reject_reason == "stale_date":
        reason_line = (
            f"🔴 Date/time was read as {extracted['raw_date'] or '(unknown)'} — not recent "
            f"(older than {MAX_SCREENSHOT_AGE_HOURS}h or in the future). Looks like an old/"
            f"reused screenshot. Rejected automatically, no admin action needed."
        )
    else:
        reason_line = (
            f"🔴 Screenshot was readable but the payee name didn't match ours — "
            f"doesn't look like a real payment to us. Rejected automatically, "
            f"no admin action needed."
        )
    caption = (
        f"<b>❌ Auto-rejected — not verified</b>\n\n"
        f"👤 User: {user.mention} (<code>{user.id}</code>)\n"
        f"📦 Claimed: <b>{PLAN_LABELS[claimed_plan]}</b>\n"
        f"🔎 OCR amount: {extracted['amount'] or 'not detected'}Rs\n"
        f"🕐 OCR date: {extracted['raw_date'] or 'not detected'}\n"
        f"{reason_line}\n\n"
        f"Request ID: <code>{request_id}</code>"
    )
    try:
        if not LOG_CHANNEL:
            raise ValueError("LOG_CHANNEL not set")
        await client.send_photo(chat_id=LOG_CHANNEL, photo=file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        logger.warning(f"Couldn't post auto-rejection log to LOG_CHANNEL ({e}), DMing admins instead.")
        for admin_id in ADMINS:
            try:
                await client.send_photo(chat_id=admin_id, photo=file_id, caption=caption, parse_mode=enums.ParseMode.HTML)
            except Exception as e2:
                logger.warning(f"Couldn't DM admin {admin_id} either: {e2}")


async def _notify_admins(client, request_id, user, claimed_plan, extracted, file_id):
    date_line = extracted["raw_date"] or "not detected"
    amount_line = f"{extracted['amount']}Rs" if extracted["amount"] else "not detected"
    if extracted["confidence"] == "high":
        confidence_line = "🟢 Amount matched — just tap Approve (date/payee couldn't be auto-confirmed)"
    elif not extracted["ocr_read_ok"]:
        confidence_line = "⚪ OCR couldn't read this screenshot clearly — please check it manually"
    else:
        confidence_line = "🟡 Amount unclear — pick the correct plan below"

    caption = (
        f"<b>💳 New UPI payment claim</b>\n\n"
        f"👤 User: {user.mention} (<code>{user.id}</code>)\n"
        f"📦 Claims: <b>{PLAN_LABELS[claimed_plan]}</b>\n"
        f"🔎 OCR amount: {amount_line}\n"
        f"🕐 OCR date: {date_line}\n"
        f"{confidence_line}\n\n"
        f"Request ID: <code>{request_id}</code>"
    )

    btn_rows = []
    if extracted["confidence"] == "high":
        btn_rows.append([InlineKeyboardButton(f"✅ Approve — {PLAN_LABELS[claimed_plan]}", callback_data=f"pay_approve_{request_id}_{claimed_plan}")])
    else:
        # Ambiguous: let the admin pick the correct plan explicitly.
        btn_rows.append([
            InlineKeyboardButton(f"✅ {PLAN_LABELS[p]}", callback_data=f"pay_approve_{request_id}_{p}")
            for p in ("week", "month")
        ])
        btn_rows.append([
            InlineKeyboardButton(f"✅ {PLAN_LABELS[p]}", callback_data=f"pay_approve_{request_id}_{p}")
            for p in ("3months", "6months")
        ])
    btn_rows.append([InlineKeyboardButton("❌ Reject", callback_data=f"pay_reject_{request_id}")])

    # This bot must be an admin in LOG_CHANNEL to post here. If that
    # fails for any reason, fall back to DMing every admin individually
    # so a request never silently disappears.
    try:
        if not LOG_CHANNEL:
            raise ValueError("LOG_CHANNEL not set")
        await client.send_photo(
            chat_id=LOG_CHANNEL, photo=file_id, caption=caption,
            parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(btn_rows)
        )
    except Exception as e:
        logger.warning(f"Couldn't post payment request to LOG_CHANNEL ({e}), DMing admins instead.")
        for admin_id in ADMINS:
            try:
                await client.send_photo(
                    chat_id=admin_id, photo=file_id, caption=caption,
                    parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(btn_rows)
                )
            except Exception as e2:
                logger.warning(f"Couldn't DM admin {admin_id} either: {e2}")


# ── Admin taps Approve / Reject ──────────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^pay_approve_([0-9a-fA-F]{24})_(\w+)$"))
async def approve_payment_cb(client, query):
    if query.from_user.id not in ADMINS:
        return await query.answer("Admins only.", show_alert=True)

    request_id, plan = query.matches[0].group(1), query.matches[0].group(2)
    req = await db.get_payment_request(request_id)
    if not req:
        return await query.answer("Request not found (maybe already handled).", show_alert=True)
    if req["status"] != "pending":
        return await query.answer(f"Already handled ({req['status']}).", show_alert=True)

    if plan not in PLAN_SECONDS:
        return await query.answer("Invalid plan.", show_alert=True)

    user_id = req["user_id"]
    await _grant_premium(client, request_id, user_id, plan, auto=False, admin_id=query.from_user.id)

    await query.answer("Approved — premium granted.")
    await query.message.edit_caption(
        query.message.caption + f"\n\n✅ <b>Approved by {query.from_user.mention} — {PLAN_LABELS[plan]} granted.</b>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=None
    )


@Client.on_callback_query(filters.regex(r"^pay_reject_([0-9a-fA-F]{24})$"))
async def reject_payment_cb(client, query):
    if query.from_user.id not in ADMINS:
        return await query.answer("Admins only.", show_alert=True)

    request_id = query.matches[0].group(1)
    req = await db.get_payment_request(request_id)
    if not req:
        return await query.answer("Request not found.", show_alert=True)
    if req["status"] != "pending":
        return await query.answer(f"Already handled ({req['status']}).", show_alert=True)

    await db.set_payment_request_status(request_id, "rejected", query.from_user.id)
    await query.answer("Rejected.")
    await query.message.edit_caption(
        query.message.caption + f"\n\n❌ <b>Rejected by {query.from_user.mention}.</b>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=None
    )
    try:
        await client.send_message(
            chat_id=req["user_id"],
            text=(
                "<b>⚠️ Your payment screenshot couldn't be verified.</b>\n\n"
                f"Please double-check your payment and try again, or contact an admin: {OWNER_LNK}"
            ),
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"Couldn't DM user {req['user_id']} after rejection: {e}")


# ── User appeals an auto-rejection ───────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^pay_appeal_([0-9a-fA-F]{24})$"))
async def appeal_rejected_payment_cb(client, query):
    request_id = query.matches[0].group(1)
    req = await db.get_payment_request(request_id)
    if not req:
        return await query.answer("Request not found.", show_alert=True)
    if req["user_id"] != query.from_user.id:
        return await query.answer("This isn't your request.", show_alert=True)
    if req["status"] != "auto_rejected":
        return await query.answer(f"Already handled ({req['status']}).", show_alert=True)

    # Send it back into the normal manual-review queue — same
    # LOG_CHANNEL post with Approve/Reject buttons an admin decides on,
    # same as any other case OCR couldn't be fully sure about.
    await db.set_payment_request_status(request_id, "pending", None)
    await _notify_admins(
        client, request_id, query.from_user, req["claimed_plan"], req["extracted"], req["screenshot_file_id"]
    )

    await query.answer()
    await query.message.edit_text(
        "📨 I will send it to the admin — please wait, the admin will be checking soon.",
        reply_markup=None
    )


# ── Admin: see the backlog of unreviewed screenshots ────────────────────

@Client.on_message(filters.command("pending_payments") & filters.user(ADMINS))
async def pending_payments_cmd(client, message):
    pending = await db.get_pending_payment_requests()
    if not pending:
        return await message.reply_text("✅ No pending payment screenshots.")

    lines = [f"<b>💳 {len(pending)} pending payment request(s)</b>\n"]
    for req in pending:
        age = datetime.datetime.now() - req["submitted_at"]
        lines.append(
            f"• <code>{req['_id']}</code> — @{req.get('username') or req['user_id']} — "
            f"claims {PLAN_LABELS.get(req['claimed_plan'], req['claimed_plan'])} — "
            f"{int(age.total_seconds() // 60)} min ago"
        )
    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)


# ── Support Q&A relay ─────────────────────────────────────────────────────
# Lets this same bot double as a help desk: any user message that isn't
# /start, the greeting, a payment screenshot, or an admin command falls
# through every handler above (photos are consumed by ask()/the
# unsolicited_screenshot_cb handler; /start and "hi" are consumed at
# their own groups) and lands here at group=5 — the lowest priority, so
# it only ever sees what nothing else claimed.

@Client.on_message(filters.private & filters.incoming & ~filters.user(ADMINS) & ~filters.command(["start", "plan", "myplan"]), group=5)
async def relay_user_question_to_admins_cb(client, message):
    if not ADMINS:
        return await message.reply_text("⚠️ No admin is configured to receive messages right now.")

    delivered = False
    for admin_id in ADMINS:
        try:
            fwd = await client.forward_messages(admin_id, message.chat.id, message.id)
            note = await client.send_message(
                chat_id=admin_id,
                text=(
                    f"👆 Message from {message.from_user.mention} (<code>{message.from_user.id}</code>).\n"
                    f"Reply to THIS message to answer them."
                ),
                parse_mode=enums.ParseMode.HTML,
                reply_to_message_id=fwd.id,
            )
            await db.add_support_link(admin_id, note.id, message.from_user.id)
            delivered = True
        except Exception as e:
            logger.warning(f"Couldn't relay question to admin {admin_id}: {e}")

    if delivered:
        sent = await message.reply_text("📨 Got your message — an admin will reply here shortly.")
        asyncio.create_task(_delayed_delete(sent, 60))
    else:
        await message.reply_text("⚠️ Couldn't reach an admin right now — please try again later.")


@Client.on_message(filters.private & filters.user(ADMINS) & filters.reply, group=-1)
async def admin_reply_to_user_cb(client, message):
    """An admin replying (Telegram's reply-swipe) to one of the forwarded
    copies above gets that reply relayed straight back to the user. If
    the reply isn't to a relayed message, this quietly does nothing and
    lets the message fall through to any other admin-side handler."""
    if not message.reply_to_message:
        return
    target_user_id = await db.get_support_link(message.from_user.id, message.reply_to_message.id)
    if not target_user_id:
        return

    try:
        await client.copy_message(target_user_id, message.chat.id, message.id)
        await message.reply_text("✅ Sent to the user.")
    except Exception as e:
        await message.reply_text(f"⚠️ Couldn't deliver your reply: {e}")
