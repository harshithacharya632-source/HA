"""Amount-reading guard for the UPI screenshot checker (admin bot).

Put this file in  admin_plugins/  next to payment_approval.py.  Nothing else needs editing:
the admin bot's plugin loader imports it and it quietly upgrades payment_approval.py in memory.
To switch it off, just delete this file.

What went wrong (real case: user paid Rs160 for 6 months, bot said "OCR amount: 88Rs")
------------------------------------------------------------------------------------
Phone screenshots start with the STATUS BAR (clock, signal, battery "88%").  The old amount
fallbacks accept a lone number line sitting next to "Transaction Successful" - so the battery
level was read as the payment amount, ahead of the real "Rs160" (which OCR often prints as
"7160" because the rupee sign fuses onto the digits).  88 is not the plan price, so the payment
was auto-rejected even though the user had paid correctly.

What this file changes
----------------------
1. Status-bar noise (lines with %, KB/s, LTE, 5G, Wi-Fi, a bare clock) is thrown away before any
   amount is looked for.
2. When the amount that was read is NOT one of your plan prices, the screenshot gets a second
   opinion: every number the OCR saw is collected (from the text already read, then from the
   other Tesseract passes with the status bar cropped off, then the cloud OCR if a key is set).
   If one of them IS a plan price - either exactly, or after removing a rupee sign that fused
   onto the front/back of the digits ("7160" -> 160) - that is the amount.
   Plan prices = the live UPI + standard prices of all plans (same source /plan uses), so
   changing a price with /plan_rate or /plan_standard is picked up automatically.
3. The payee check also accepts the BharatPe UPI ID itself (taken from UPI_ID) and the generic
   "BharatPe Merchant" name, besides "harshith".  Extra names: env PAYEE_NAME_HINTS="name1,name2".

What it does NOT change: a screenshot whose real amount is wrong (e.g. paid Rs15, claimed Rs160)
is still rejected, the date / payee / duplicate rules are untouched, and nothing is ever
approved from a number that is not one of your plan prices.

This file never raises while loading: if anything is unexpected it logs a warning and the bot
simply keeps working exactly as before.
"""
import asyncio
import datetime
import io
import logging
import os
import re

logger = logging.getLogger(__name__)

try:
    from admin_plugins import payment_approval as pa
except Exception as _e:                      # never take the bot down
    pa = None
    logger.warning(f"amount guard not active (could not import payment_approval): {_e}")


# ───────────────────────── 1. phone status-bar noise ─────────────────────────
_NOISE_LINE = re.compile(
    r"%"                                          # battery level  "88%"
    r"|\b(?:kb|mb)\s*/\s*s\b"                      # network speed  "12.4 KB/s"
    r"|\b(?:volte|lte|5g|4g|3g|wi-?fi)\b"          # signal / data icons
    r"|^\W*\d{1,2}:\d{2}\W*(?:am|pm)?\W*$",        # a lone clock "12:22"
    re.IGNORECASE,
)


def strip_phone_status_noise(text: str) -> str:
    """Drops the lines a phone's status bar leaves in OCR text."""
    return "\n".join(l for l in (text or "").splitlines() if not _NOISE_LINE.search(l))


# ───────────────────────── 2. every number the OCR saw ─────────────────────────
# a short number alone on its line, with or without a stray character in front/behind
# (that stray character is usually the rupee sign the OCR could not read)
_STANDALONE = re.compile(r"^(?:[^\w\n]{0,2}|[A-Za-z])([0-9]{1,6}(?:\.\d{1,2})?)[^\w\n]{0,2}$")


def amount_candidates(text: str, _orig_extract) -> list:
    """Every plausible amount in `text`, strongest first, status-bar lines already removed."""
    clean = strip_phone_status_noise(text)
    out = []

    def add(v):
        v = (v or "").replace(",", "").strip()
        if v and v not in out:
            out.append(v)

    for pat in pa._AMOUNT_PATTERNS:               # explicit Rs / INR / rupee-sign, bare 12.00 line, bare 500/-
        for m in pat.finditer(clean):
            add(m.group(1))
    add(_orig_extract(clean))                     # whatever the existing heuristics pick on clean text
    lines = [l.strip() for l in clean.splitlines() if l.strip()]
    for line in lines:                            # number alone on its line
        m = _STANDALONE.match(line)
        if m:
            add(m.group(1))
    for line in lines:                            # "Paid to Harshith  160" style rows
        low = line.lower()
        if "paid to" in low or "sent to" in low or pa.PAYEE_NAME_HINT in low:
            m = pa._TRAILING_AMOUNT_RE.search(line)
            if m:
                add(m.group(2))
    return out


def _as_valid_int(value, valid):
    try:
        f = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if f == int(f) and int(f) in valid:
        return int(f)
    return None


def choose_valid_amount(cands, valid):
    """-> (amount_string, raw_candidate_it_was_fixed_from or None), or (None, None).
    Only ever returns one of the plan prices in `valid`."""
    for c in cands:                               # a candidate that already IS a plan price
        v = _as_valid_int(c, valid)
        if v is not None:
            return str(v), None
    for c in cands:                               # rupee sign fused onto the front / back ("7160", "1600")
        if re.fullmatch(r"\d{3,6}", c):
            for stripped in (c[1:], c[:-1]):
                if int(stripped) in valid:
                    return str(int(stripped)), c
    return None, None


async def _valid_prices() -> set:
    """Live prices of every plan (selling + standard) - the same data /plan and the checker use."""
    rates = await pa.load_plan_rates(pa.MAIN_BOT_ID)
    prices = set()
    for group in ("upi", "standard"):
        for value in (rates.get(group) or {}).values():
            try:
                prices.add(int(str(value).strip()))
            except (TypeError, ValueError):
                pass
    return prices


def _looks_like_payment(result: dict) -> bool:
    text = (result.get("ocr_text") or "").lower()
    return bool(result.get("payee_ok")) or any(
        w in text for w in ("paid to", "successful", "completed", "transaction", "utr", "upi")
    )


def _extra_ocr_texts(photo_bytes: bytes):
    """Lazily yields more OCR text of the same screenshot, status bar cropped off."""
    try:
        image = pa.Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    except Exception:
        return
    if image.height > 400:
        image = image.crop((0, int(image.height * 0.055), image.width, image.height))
    for variant, config in pa._ocr_variants(image):
        try:
            yield pa._strip_ad_content(pa.pytesseract.image_to_string(variant, config=config))
        except Exception as e:
            logger.info(f"amount guard: one extra OCR pass failed: {e}")
    if pa.GOOGLE_VISION_API_KEY or pa.OCR_SPACE_API_KEY:
        cloud = pa._cloud_ocr_text(photo_bytes)
        if cloud:
            yield pa._strip_ad_content(cloud)


def _second_opinion(photo_bytes: bytes, result: dict, valid: set, orig_extract) -> dict:
    original = result.get("amount")
    seen = []                                     # every candidate from every text, in order
    chosen = raw = None

    def texts():
        yield result.get("ocr_text") or ""        # what was already read - costs nothing
        yield from _extra_ocr_texts(photo_bytes)  # only reached if the text above had no plan price

    for text in texts():
        for c in amount_candidates(text, orig_extract):
            if c not in seen:
                seen.append(c)
        chosen, raw = choose_valid_amount(seen, valid)
        # fill in what the first pass missed, from this pass
        if not result.get("payee_ok") and pa._payee_name_matches(text):
            result["payee_ok"] = True
        if result.get("parsed_date") is None:
            raw_date, parsed = pa._extract_datetime(text)
            if parsed:
                result["raw_date"], result["parsed_date"] = raw_date, parsed
        if not result.get("txn_id"):
            result["txn_id"] = pa._extract_txn_id(text)
        if chosen:
            break

    result["amount_guard"] = {"read_first": original, "chosen": chosen, "fixed_from": raw, "candidates": seen[:10]}
    if chosen:
        result["amount"] = chosen
        result["ocr_read_ok"] = True
        logger.info(f"amount guard: first read {original!r} is not a plan price -> using {chosen!r} (candidates {seen[:10]})")
        if result.get("amount") and result.get("parsed_date") is not None:
            try:
                age = pa._now_ist_naive() - result["parsed_date"]
                if datetime.timedelta(0) <= age <= datetime.timedelta(hours=pa.MAX_SCREENSHOT_AGE_HOURS):
                    result["confidence"] = "high"
            except Exception:
                pass
    else:
        logger.info(f"amount guard: no plan price among the numbers read {seen[:10]} (first read {original!r}) - leaving the result as is")
    return result


# ───────────────────────── 3. payee name ─────────────────────────
def _norm_vpa(s: str) -> str:
    """lower-case, letters that OCR swaps with digits mapped to digits, punctuation removed"""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower()).translate(str.maketrans("oil", "011"))


def _payee_hints():
    names = ["bharatpe merchant"]
    names += [h.strip().lower() for h in os.environ.get("PAYEE_NAME_HINTS", "").split(",") if h.strip()]
    vpa_local = ""
    try:
        from info import UPI_ID
        vpa_local = (UPI_ID or "").split("@")[0]
    except Exception:
        pass
    return names, _norm_vpa(vpa_local)


# ───────────────────────── 4. switch it on ─────────────────────────
def _install():
    if pa is None or getattr(pa, "_amount_guard_installed", False):
        return
    needed = ("_extract_amount", "_payee_name_matches", "ocr_screenshot", "load_plan_rates", "_AMOUNT_PATTERNS",
              "_TRAILING_AMOUNT_RE", "_ocr_variants", "_strip_ad_content", "_extract_datetime", "_extract_txn_id",
              "_cloud_ocr_text", "_now_ist_naive", "PAYEE_NAME_HINT", "MAX_SCREENSHOT_AGE_HOURS", "MAIN_BOT_ID")
    missing = [n for n in needed if not hasattr(pa, n)]
    if missing:
        logger.warning(f"amount guard not active - payment_approval.py no longer has: {missing}")
        return

    orig_extract = pa._extract_amount
    orig_payee = pa._payee_name_matches
    orig_ocr = pa.ocr_screenshot

    def guarded_extract_amount(text):
        return orig_extract(strip_phone_status_noise(text))

    def guarded_payee_name_matches(text):
        if orig_payee(text):
            return True
        names, vpa_local = _payee_hints()
        low = (text or "").lower()
        if any(n in low for n in names):
            return True
        return len(vpa_local) >= 10 and vpa_local in _norm_vpa(text)

    async def guarded_ocr_screenshot(photo_bytes, *args, **kwargs):
        result = await orig_ocr(photo_bytes, *args, **kwargs)
        try:
            if not isinstance(result, dict) or not getattr(pa, "OCR_AVAILABLE", False):
                return result
            valid = await _valid_prices()
            if not valid or _as_valid_int(result.get("amount"), valid) is not None:
                return result                      # the amount read is already a plan price: nothing to fix
            if not _looks_like_payment(result):
                return result                      # an unrelated photo: don't burn CPU on it
            return await asyncio.to_thread(_second_opinion, photo_bytes, result, valid, orig_extract)
        except Exception as e:
            logger.warning(f"amount guard failed, using the plain OCR result: {e}")
            return result

    pa._extract_amount = guarded_extract_amount
    pa._payee_name_matches = guarded_payee_name_matches
    pa.ocr_screenshot = guarded_ocr_screenshot
    pa._amount_guard_installed = True
    logger.info("amount guard active: status-bar filter, plan-price second opinion, BharatPe payee check.")


try:
    _install()
except Exception as _e:                          # never take the bot down
    logger.warning(f"amount guard not active: {_e}")
