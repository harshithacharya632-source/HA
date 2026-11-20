"""Fast + safe screenshot reading for the UPI payment checker (admin bot).

Put this file in  admin_plugins/  next to payment_approval.py  (it replaces the earlier
zz_amount_guard.py - same name, same place).  Nothing else needs editing: the admin bot's plugin
loader imports it and it upgrades payment_approval.py in memory.  Delete the file to switch it off.

Three problems this fixes
-------------------------
1. WRONG AMOUNT (paid Rs160, bot said "OCR amount: 88Rs"): every phone screenshot starts with the
   status bar - clock, signal and the BATTERY LEVEL ("88%").  The old fallbacks accepted a lone
   number next to "Payment Successful", so the battery level beat the real "Rs160" (which OCR
   often prints as "7160" because the rupee sign fuses onto the digits).  Now status-bar lines are
   thrown away, and when the amount read is not one of your plan prices the other numbers the OCR
   saw are checked: one that IS a plan price (exactly, or after removing a fused rupee sign) wins.
   Plan prices = the live UPI + standard prices of every plan, so /plan_rate and /plan_standard
   changes are picked up automatically.  A genuinely wrong amount (paid Rs15, claimed Rs160) is
   still rejected: nothing is ever approved from a number that is not one of your plan prices.

2. "Reading your screenshot..." FOR MINUTES, THEN NOTHING: the old reader only asked the cloud OCR
   first and, whenever that failed or was slow, ran several full Tesseract passes on the bot's own
   CPU - on a small Koyeb instance (a fraction of one core) that takes minutes, with no limit.
   Now: OCR.space is asked FIRST (a JPEG/PNG upload, engine 2, engine 1 as backup), Google Vision
   is only used if its key is set AND it works (an unpaid key is switched off after one failure),
   Tesseract is only the fallback, runs on ONE thread (OMP_THREAD_LIMIT=1 - Tesseract on a throttled
   CPU is much faster that way) and the WHOLE read has a hard time limit (OCR_BUDGET_SECONDS,
   default 25).  It stops the moment amount + date + payee are known, so a normal screenshot is
   answered in a few seconds.  If nothing could be read in time the user still gets an answer
   immediately (the normal "couldn't read it" reject with the Appeal button).

3. NO ANSWER WHEN SOMETHING BREAKS: if anything inside the screenshot check raises, the user now
   gets a message instead of an endless "Reading...", and the full error goes to the logs.

Settings (all optional, Koyeb environment variables)
    OCR_SPACE_API_KEY    your OCR.space key (already used by the bot)
    GOOGLE_VISION_API_KEY  leave EMPTY if you don't use Google Cloud
    OCR_BUDGET_SECONDS   hard limit for reading one screenshot, default 25
    OCR_CLOUD_SECONDS    how long to wait for OCR.space before using Tesseract, default 9
    PAYEE_NAME_HINTS     extra payee names to accept, "name1,name2"

The payee check also accepts the BharatPe UPI ID itself (from UPI_ID) and "BharatPe Merchant".

This file never raises while loading: if anything is unexpected it logs a warning and the bot keeps
working exactly as before.
"""
import asyncio
import datetime
import io
import logging
import os
import re
import time

logger = logging.getLogger(__name__)

# Tesseract uses OpenMP threads; on a CPU-limited container (Koyeb) they fight over a fraction of one
# core and a read that should take 2s takes minutes.  One thread is the known fix.  Must be set before
# the first Tesseract run (the subprocess inherits it).
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

try:
    from admin_plugins import payment_approval as pa
except Exception as _e:                      # never take the bot down
    pa = None
    logger.warning(f"screenshot guard not active (could not import payment_approval): {_e}")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


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


def amount_candidates(text: str, extract) -> list:
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
    add(extract(clean))                           # whatever the existing heuristics pick on clean text
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


async def _valid_prices_safe() -> set:
    try:
        return await asyncio.wait_for(_valid_prices(), timeout=3)
    except Exception as e:
        logger.info(f"screenshot guard: could not load plan prices ({e}) - reading without the plan-price check")
        return set()


# ───────────────────────── 3. the cloud OCR engines ─────────────────────────
_session = None
_google_off_until = 0.0


def _prepare_upload(photo_bytes: bytes):
    """-> (bytes, mime, filename).  OCR.space wants the right file type and < 1 MB on the free plan."""
    head = photo_bytes[:12]
    mime, name = None, "shot"
    if head[:3] == b"\xff\xd8\xff":
        mime, name = "image/jpeg", "shot.jpg"
    elif head[:8] == b"\x89PNG\r\n\x1a\n":
        mime, name = "image/png", "shot.png"
    if mime and len(photo_bytes) <= 900_000:
        return photo_bytes, mime, name
    try:                                          # unknown type or too big: re-encode as a normal JPEG, shrinking until it fits
        image = pa.Image.open(io.BytesIO(photo_bytes)).convert("RGB")
        data = b""
        for width, quality in ((1400, 88), (1200, 80), (1000, 70), (800, 60)):
            shown = image
            if image.width > width:
                shown = image.resize((width, int(image.height * width / image.width)), pa.Image.LANCZOS)
            buf = io.BytesIO()
            shown.save(buf, "JPEG", quality=quality)
            data = buf.getvalue()
            if len(data) <= 900_000:
                break
        return data, "image/jpeg", "shot.jpg"
    except Exception:
        return photo_bytes, mime or "image/jpeg", name


def _ocrspace_call(upload, engine: int, timeout: float) -> str:
    import requests
    global _session
    if _session is None:
        _session = requests.Session()             # keeps the connection open: saves the TLS handshake next time
    body, mime, name = upload
    resp = _session.post(
        pa._OCR_SPACE_API_URL,
        files={"file": (name, body, mime)},
        data={"apikey": pa.OCR_SPACE_API_KEY, "language": "eng", "OCREngine": str(engine),
              "scale": "true", "isOverlayRequired": "false", "detectOrientation": "false"},
        timeout=(4, timeout),
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("IsErroredOnProcessing"):
        logger.warning(f"OCR.space (engine {engine}) error: {data.get('ErrorMessage')}")
        return ""
    return "\n".join((r.get("ParsedText") or "") for r in (data.get("ParsedResults") or []))


def _cloud_text(photo_bytes: bytes, seconds: float) -> str:
    """Blocking (runs in a thread). Google Vision only if its key is set and still working, then OCR.space
    engine 2, then engine 1.  Returns "" if none of them answered in `seconds`."""
    global _google_off_until
    deadline = time.monotonic() + seconds
    left = lambda: deadline - time.monotonic()
    if pa.GOOGLE_VISION_API_KEY and time.time() >= _google_off_until:
        text = pa._vision_ocr_text(photo_bytes)
        if text:
            logger.info("Cloud OCR: Google Vision succeeded.")
            return text
        _google_off_until = time.time() + 600     # unpaid / failing key: don't waste time on it for 10 minutes
        logger.warning("Google Vision gave nothing - skipping it for 10 minutes (leave GOOGLE_VISION_API_KEY empty if you don't use it).")
    if pa.OCR_SPACE_API_KEY:
        upload = _prepare_upload(photo_bytes)
        for engine, cap in ((2, 8.0), (1, 6.0)):
            if left() < 1.5:
                break
            try:
                text = _ocrspace_call(upload, engine, min(cap, left()))
            except Exception as e:
                logger.warning(f"OCR.space (engine {engine}) failed: {e}")
                continue
            if text.strip():
                logger.info(f"Cloud OCR: OCR.space engine {engine} succeeded.")
                return text
    return ""


# ───────────────────────── 4. the fast reader ─────────────────────────
def _empty_result() -> dict:
    return {"amount": None, "raw_date": None, "parsed_date": None, "confidence": "low",
            "ocr_text": "", "payee_ok": False, "ocr_read_ok": False, "txn_id": None}


def _looks_like_payment(text: str) -> bool:
    low = (text or "").lower()
    return any(w in low for w in ("paid to", "paid", "successful", "completed", "transaction", "payment", "utr", "upi", "₹", "rs."))


def _build(texts: list, valid: set):
    """Everything the checker needs, from all the text read so far.  -> (result_dict, amount_is_decided)"""
    result = _empty_result()
    combined = "\n".join(texts)
    clean = strip_phone_status_noise(combined)
    cands = []
    for t in texts:
        for c in amount_candidates(t, pa._extract_amount):
            if c not in cands:
                cands.append(c)
    chosen, fixed_from = choose_valid_amount(cands, valid) if valid else (None, None)
    strong = pa._extract_amount_strong(clean)             # an explicit Rs / rupee-sign amount
    amount = chosen or pa._extract_amount(clean)
    raw_date, parsed_date = pa._extract_datetime(combined)
    result.update(
        amount=amount, ocr_text=combined, ocr_read_ok=len(combined.strip()) >= 20,
        raw_date=raw_date, parsed_date=parsed_date,
        payee_ok=pa._payee_name_matches(combined), txn_id=pa._extract_txn_id(combined),
    )
    if amount and parsed_date:
        age = pa._now_ist_naive() - parsed_date
        if datetime.timedelta(0) <= age <= datetime.timedelta(hours=pa.MAX_SCREENSHOT_AGE_HOURS):
            result["confidence"] = "high"
    result["amount_guard"] = {"chosen": chosen, "fixed_from": fixed_from, "candidates": cands[:10]}
    return result, bool(chosen or strong)


def _complete(result: dict, decided: bool) -> bool:
    return bool(decided and result["amount"] and result["parsed_date"] and result["payee_ok"])


def _not_a_payment(result: dict, texts: list) -> bool:
    return (result["ocr_read_ok"] and not result["payee_ok"] and not result["amount"]
            and not _looks_like_payment("\n".join(texts)))


def _open_image(photo_bytes: bytes):
    try:
        return pa.Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    except Exception:
        return None


def _tess_step(gen, seconds: float):
    """Blocking (runs in a thread): the next Tesseract pass.  None = no more passes, "" = it failed/timed out."""
    try:
        variant, config = next(gen)
    except StopIteration:
        return None
    try:
        return pa.pytesseract.image_to_string(variant, config=config, timeout=max(2.0, seconds))
    except Exception as e:
        logger.info(f"Tesseract pass failed or timed out: {e}")
        return ""


def _top_band_text(image, seconds: float) -> str:
    """The coloured header band (it holds the date) - Tesseract skips it on a tall full-image pass.
    30% of the screen, because on PhonePe's newer "Payment Successful" screen the date sits ~22% down."""
    try:
        band = image.crop((0, 0, image.width, max(1, int(image.height * 0.30))))
        factor = 2 if band.width >= 700 else 3
        band = band.resize((band.width * factor, band.height * factor), pa.Image.LANCZOS)
        return pa.pytesseract.image_to_string(band, config=pa._TESS_CONFIG, timeout=max(2.0, seconds))
    except Exception:
        return ""


async def fast_ocr_screenshot(photo_bytes: bytes) -> dict:
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    budget = _env_float("OCR_BUDGET_SECONDS", 25.0)
    cloud_cap = _env_float("OCR_CLOUD_SECONDS", 9.0)
    remaining = lambda: budget - (loop.time() - t0)
    valid = await _valid_prices_safe()
    texts = []
    result, decided = _empty_result(), False

    async def bounded(func, *args, seconds):
        try:
            return await asyncio.wait_for(asyncio.to_thread(func, *args), timeout=max(0.5, seconds))
        except asyncio.TimeoutError:
            logger.warning(f"screenshot guard: {getattr(func, '__name__', 'step')} did not finish in {seconds:.0f}s")
            return ""

    # ── stage 1: the cloud (a few seconds, almost no CPU on this host)
    if pa.OCR_SPACE_API_KEY or pa.GOOGLE_VISION_API_KEY:
        text = await bounded(_cloud_text, photo_bytes, min(cloud_cap, remaining()), seconds=min(cloud_cap, remaining()) + 1)
        if text and text.strip():
            texts.append(pa._strip_ad_content(text))
            result, decided = _build(texts, valid)
            if _complete(result, decided) or _not_a_payment(result, texts):
                return _done(result, t0, loop, "cloud")
            if decided and result["payee_ok"] and not result["parsed_date"] and getattr(pa, "OCR_AVAILABLE", False):
                # amount + payee are known, only the date is missing: one quick look at the header band, then answer
                image = _open_image(photo_bytes)
                if image is not None:
                    band = await bounded(_top_band_text, image, min(8.0, remaining()), seconds=min(8.0, remaining()))
                    if band.strip():
                        texts.insert(0, pa._strip_ad_content(band))
                        result, decided = _build(texts, valid)
                return _done(result, t0, loop, "cloud")

    # ── stage 2: Tesseract on this host, one pass at a time, always inside the time limit
    image = None
    if getattr(pa, "OCR_AVAILABLE", False) and remaining() > 3:
        image = _open_image(photo_bytes)
    if image is not None:
        body = image
        if image.height > 400:                     # leave the status bar (clock / battery) out of the main passes
            body = image.crop((0, int(image.height * 0.055), image.width, image.height))
        gen = pa._ocr_variants(body)
        passes = 0
        while remaining() > 3:
            text = await bounded(_tess_step, gen, remaining(), seconds=remaining())
            if text is None:
                break
            passes += 1
            if text.strip():
                texts.append(pa._strip_ad_content(text))
                result, decided = _build(texts, valid)
                if _complete(result, decided):
                    break
                if passes >= 2 and _not_a_payment(result, texts):
                    break
                if decided and result["payee_ok"] and not result["parsed_date"]:
                    break                          # only the date is missing - the header pass below finds it
        if not result["parsed_date"] and texts and remaining() > 3:
            band = await bounded(_top_band_text, image, remaining(), seconds=remaining())
            if band.strip():
                texts.insert(0, pa._strip_ad_content(band))
                result, decided = _build(texts, valid)

    if texts and (result["amount"] is None and not result["ocr_read_ok"]):
        result, decided = _build(texts, valid)
    return _done(result, t0, loop, "tesseract" if image is not None else "cloud")


def _done(result: dict, t0: float, loop, via: str) -> dict:
    elapsed = loop.time() - t0
    msg = f"screenshot read via {via} in {elapsed:.1f}s: amount={result.get('amount')!r} date={result.get('raw_date')!r} payee_ok={result.get('payee_ok')}"
    (logger.warning if elapsed > 8 else logger.info)(msg)
    return result


# ───────────────────────── 5. payee name ─────────────────────────
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


# ───────────────────────── 6. switch it on ─────────────────────────
def _install():
    if pa is None or getattr(pa, "_amount_guard_installed", False):
        return
    needed = ("_extract_amount", "_extract_amount_strong", "_payee_name_matches", "ocr_screenshot", "load_plan_rates",
              "_AMOUNT_PATTERNS", "_TRAILING_AMOUNT_RE", "_ocr_variants", "_strip_ad_content", "_extract_datetime",
              "_extract_txn_id", "_vision_ocr_text", "_OCR_SPACE_API_URL", "_TESS_CONFIG", "_now_ist_naive",
              "PAYEE_NAME_HINT", "MAX_SCREENSHOT_AGE_HOURS", "MAIN_BOT_ID", "GOOGLE_VISION_API_KEY",
              "OCR_SPACE_API_KEY", "_handle_screenshot", "Image", "pytesseract")
    missing = [n for n in needed if not hasattr(pa, n)]
    if missing:
        logger.warning(f"screenshot guard not active - payment_approval.py no longer has: {missing}")
        return

    orig_extract = pa._extract_amount
    orig_payee = pa._payee_name_matches
    orig_ocr = pa.ocr_screenshot
    orig_handle = pa._handle_screenshot

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
        try:
            return await fast_ocr_screenshot(photo_bytes)
        except Exception:
            logger.exception("fast screenshot reader failed - falling back to the original reader (time-limited)")
        try:
            return await asyncio.wait_for(orig_ocr(photo_bytes, *args, **kwargs), timeout=_env_float("OCR_BUDGET_SECONDS", 25.0))
        except Exception:
            logger.exception("original screenshot reader failed too")
            return _empty_result()

    async def safe_handle_screenshot(*args, **kwargs):
        try:
            return await orig_handle(*args, **kwargs)
        except Exception:
            logger.exception("checking a payment screenshot failed")
            try:
                client = args[0] if args else kwargs.get("client")
                chat_id = args[2] if len(args) > 2 else kwargs.get("chat_id")
                await client.send_message(
                    chat_id,
                    "⚠️ Something went wrong while checking your screenshot.\n"
                    "Please send it again in a minute, or tap “Talk to Admin” and an admin will check it.",
                )
            except Exception:
                pass

    pa._extract_amount = guarded_extract_amount
    pa._payee_name_matches = guarded_payee_name_matches
    pa.ocr_screenshot = guarded_ocr_screenshot
    pa._handle_screenshot = safe_handle_screenshot
    pa._amount_guard_installed = True
    logger.info("screenshot guard active: cloud-first time-limited reader, status-bar filter, plan-price check, BharatPe payee check.")


try:
    _install()
except Exception as _e:                          # never take the bot down
    logger.warning(f"screenshot guard not active: {_e}")
