"""
Goflix_AdminBot — UPI screenshot payment approval + user support relay.

This is a SEPARATE bot from the main Goflix file-store bot (its own
BOT_TOKEN, its own Client, started alongside the main bot in bot.py).
Users are sent here (via the "Send screenshot" link under /plan on the
main bot, which is just OWNER_LNK) to submit their UPI payment proof —
and can also just message this bot directly with questions, which get
relayed to the admins (see "Support Q&A relay" below).

Payment flow:
  1. User taps /start (or says hi), OR sends the screenshot photo cold
     with no plan picked yet (see unsolicited_screenshot_cb — the photo
     is stashed and they're asked which plan it's for).
  2. Bot shows a "Submit Payment Screenshot" button -> asks which plan.
  3. Bot asks for the screenshot photo (unless one was already stashed
     from step 1, in which case that's used instead).
  4. OCR (pytesseract) pulls out an amount + date/time and checks: does
     the amount match the claimed plan, and is the date recent (not an
     old/reused screenshot)?
  5a. High confidence (amount matched + date recent): premium is granted
      immediately, no admin needed. The screenshot is still posted to
      LOG_CHANNEL as a record only (no buttons) so approvals stay
      auditable.
  5b. Anything less than that: sent to LOG_CHANNEL (this bot must be an
      admin there) with the screenshot, extracted info, and buttons. An
      admin taps something before premium is granted. Ambiguous cases
      show a button per plan.

Support Q&A relay:
  Any other message a user sends (not /start, not a screenshot) is
  forwarded to every admin's PM with this bot. An admin replies by using
  Telegram's native reply-to on that forwarded copy, and the reply is
  relayed straight back to the user.

Requires: pytesseract + tesseract-ocr/tesseract-ocr-eng system packages
(see Dockerfile) and Pillow (already in requirements.txt).
"""

import io
import re
import datetime
import logging

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.users_chats_db import db
from info import ADMINS, LOG_CHANNEL, PREMIUM_AND_REFERAL_MODE, STAR_PLAN_LABELS, STAR_PLAN_SECONDS, OWNER_LNK, BOT_TOKEN
from plugins.commands import load_plan_rates

logger = logging.getLogger(__name__)

try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    logger.warning("pytesseract/Pillow not available — screenshot OCR is disabled, admins will see raw screenshots only.")

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

# How old a screenshot's payment date/time is allowed to be before we
# stop trusting it automatically and flag it as low-confidence (someone
# reusing an old screenshot, or a scheduled/pending payment).
MAX_SCREENSHOT_AGE_HOURS = 48


# ── OCR extraction ──────────────────────────────────────────────────────

_AMOUNT_PATTERNS = [
    re.compile(r'(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.\d{1,2})?)', re.IGNORECASE),
]

# Common UPI-app receipt date formats. Not exhaustive — different apps
# (GPay/PhonePe/Paytm) format this differently, so this is intentionally
# forgiving. If nothing matches, the screenshot is just treated as
# low-confidence rather than failing.
_DATE_PATTERNS = [
    re.compile(r'\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4},?\s+\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?)\b'),
    re.compile(r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4},?\s+\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?)\b'),
]

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
    return None, None


async def ocr_screenshot(photo_bytes: bytes) -> dict:
    """Runs OCR and returns a dict with whatever was extracted, plus a
    'confidence' verdict. Never raises — OCR failing just means low
    confidence, not a crash."""
    result = {"amount": None, "raw_date": None, "parsed_date": None, "confidence": "low", "ocr_text": ""}
    if not OCR_AVAILABLE:
        return result
    try:
        image = Image.open(io.BytesIO(photo_bytes))
        text = pytesseract.image_to_string(image)
        result["ocr_text"] = text
        result["amount"] = _extract_amount(text)
        raw_date, parsed_date = _extract_datetime(text)
        result["raw_date"] = raw_date
        result["parsed_date"] = parsed_date

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
    await message.reply_text(
        "Hello! 👋 Tap below to submit a payment screenshot.",
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

@Client.on_message(filters.private & filters.photo)
async def unsolicited_screenshot_cb(client, message):
    """Catches a screenshot sent straight into the chat with no /start
    and no plan chosen (e.g. the user just pastes/forwards the photo).
    If an active client.ask() is already waiting on a photo from this
    user (the normal flow above), the ask_patch resolver (group=-1)
    consumes it first and this handler never runs — so this only fires
    for a genuinely cold screenshot."""
    if PREMIUM_AND_REFERAL_MODE == False:
        return

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
    claimed_amount = str(upi.get(claimed_plan))
    amount_matches = extracted["amount"] is not None and extracted["amount"] == claimed_amount
    extracted["matched_plan"] = claimed_plan if amount_matches else None
    if not amount_matches:
        extracted["confidence"] = "low"

    request_id = await db.add_payment_request(
        user_id=user.id, username=user.username or user.first_name,
        screenshot_file_id=file_id,
        claimed_plan=claimed_plan, extracted=extracted,
    )

    if extracted["confidence"] == "high":
        # Amount matched the claimed plan AND the screenshot's date/time
        # is recent enough to trust — skip manual review entirely.
        await status_msg.edit_text("✅ Screenshot verified automatically — premium is active now! 🎉")
        await _grant_premium(client, request_id, user.id, claimed_plan, auto=True)
        await _log_auto_approval(client, request_id, user, claimed_plan, extracted, file_id)
        return

    await status_msg.edit_text(
        "✅ Got it! Your screenshot is with the admins for a quick check — "
        "you'll get a message the moment it's approved."
    )
    await _notify_admins(client, request_id, user, claimed_plan, extracted, file_id)


async def _grant_premium(client, request_id, user_id, plan: str, auto: bool, admin_id=None):
    """Shared by the auto-approve path above and the manual Approve
    button below — same premium-granting logic either way, only the
    payment_request's recorded status differs."""
    seconds = PLAN_SECONDS[plan]
    expiry_time = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
    await db.update_user({
        "id": user_id, "expiry_time": expiry_time,
        "expiry_reminder_sent": False, "expired_notified": False,
    })
    await db.set_payment_request_status(request_id, "auto_approved" if auto else "approved", admin_id)
    try:
        await client.send_message(
            chat_id=user_id,
            text=(
                "<b>👑 ᴄᴏɴɢʀᴀᴛꜱ 👑</b>\n\n"
                f"💎 <b>ᴘʀᴇᴍɪᴜᴍ ᴜɴʟᴏᴄᴋᴇᴅ ꜰᴏʀ {PLAN_LABELS[plan]}</b>\n"
                "🌟 ᴀʟʟ ᴘʀᴇᴍɪᴜᴍ ꜰᴇᴀᴛᴜʀᴇꜱ ᴀʀᴇ ɴᴏᴡ ᴀᴄᴄᴇꜱꜱɪʙʟᴇ\n\n"
                "🚀 <b>ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ ɢᴏꜰʟɪx!</b>"
            ),
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"Couldn't DM user {user_id} after granting premium: {e}")


async def _log_auto_approval(client, request_id, user, plan: str, extracted, file_id):
    """Posts a record-only copy to LOG_CHANNEL for auto-approved payments
    — no buttons, nothing for an admin to action, just an audit trail
    ('the premium list') of who got premium and why."""
    caption = (
        f"<b>✅ Auto-approved UPI payment</b>\n\n"
        f"👤 User: {user.mention} (<code>{user.id}</code>)\n"
        f"📦 Plan: <b>{PLAN_LABELS[plan]}</b>\n"
        f"🔎 OCR amount: {extracted['amount']}Rs\n"
        f"🕐 OCR date: {extracted['raw_date'] or 'not detected'}\n"
        f"🟢 High confidence — granted automatically, no admin action needed.\n\n"
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


async def _notify_admins(client, request_id, user, claimed_plan, extracted, file_id):
    date_line = extracted["raw_date"] or "not detected"
    amount_line = f"{extracted['amount']}Rs" if extracted["amount"] else "not detected"
    confidence_line = "🟢 High confidence (amount + recent date matched)" if extracted["confidence"] == "high" \
        else "🟡 Needs a look (amount/date unclear or didn't match)"

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

@Client.on_message(filters.private & filters.incoming & ~filters.user(ADMINS) & ~filters.command("start"), group=5)
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
        await message.reply_text("📨 Got your message — an admin will reply here shortly.")
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
