"""Standard price vs. one-time offer price, per user.

How it works
------------
* STANDARD price  - the regular price of each plan (info.STANDARD_PLAN_RATES,
  editable live with /plan_standard).
* SELLING price   - what /plan_rate sets ("upi" rates). When a plan's selling
  price is BELOW its standard price, that plan is on OFFER.
* Every time /plan_rate saves a different set of offer prices, it gets a new
  offer id. A user can use ONE offer purchase per offer id: after their first
  approved offer payment they see (and must pay) the standard price. A brand
  new offer (new id) is available to everybody once again.

Pure functions only (no database / Telegram), so they are easy to test. The
async wrappers that read the rates and the user's claim live in
plugins/commands.py (get_user_pricing).
"""

import datetime
import secrets

PLAN_ORDER = ("week", "month", "3months", "6months")

# Small-caps labels, same look as the existing plan list in /plan.
_LIST_LABELS = {
    "week": "1 ᴡᴇᴇᴋ", "month": "1 ᴍᴏɴᴛʜs", "3months": "3 ᴍᴏɴᴛʜs", "6months": "6 ᴍᴏɴᴛʜs",
}


def new_offer_id() -> str:
    """Unique id for a new offer: readable timestamp + random suffix, so two
    offers can never share an id (a plain timestamp can repeat within a second,
    which would make users who used the old offer look like they used the new one)."""
    return datetime.datetime.now().strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3)


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def is_discounted(selling, standard) -> bool:
    s, t = _as_int(selling), _as_int(standard)
    return s is not None and t is not None and s < t


def any_offer(upi: dict, standard: dict) -> bool:
    return any(is_discounted(upi.get(p), standard.get(p)) for p in PLAN_ORDER)


def active_offer_id(rates: dict):
    """None when no plan is below its standard price. 'legacy' for an offer
    that was already running before offer ids existed."""
    if any_offer(rates["upi"], rates["standard"]):
        return rates.get("offer_id") or "legacy"
    return None


def user_pricing(rates: dict, claimed_offer_id=None) -> dict:
    """What THIS user pays right now.

    rates            : load_plan_rates() result ({"upi", "standard", "offer_id", ...})
    claimed_offer_id : the offer id this user already used (None if never)

    Returns {"prices": {plan: "15"}, "standard": {plan: "40"}, "is_offer": {plan: bool},
             "offer_active": bool, "eligible": bool, "offer_id": str|None}
    """
    upi, std = rates["upi"], rates["standard"]
    offer_id = active_offer_id(rates)
    eligible = offer_id is not None and claimed_offer_id != offer_id

    prices, standard, is_offer = {}, {}, {}
    for p in PLAN_ORDER:
        sell, reg = upi.get(p), std.get(p)
        discounted = is_discounted(sell, reg)
        if discounted and not eligible:
            prices[p] = str(_as_int(reg))        # offer already used -> standard price
            is_offer[p] = False
        else:
            prices[p] = str(sell)
            is_offer[p] = bool(discounted and eligible)
        standard[p] = str(reg)
    return {
        "prices": prices, "standard": standard, "is_offer": is_offer,
        "offer_active": offer_id is not None, "eligible": eligible, "offer_id": offer_id,
    }


def format_user_plan_rates(pricing: dict) -> str:
    """The plan list for /plan: standard price struck through next to the
    offer price while the user can still use the offer."""
    lines = []
    for p in PLAN_ORDER:
        price, label = pricing["prices"][p], _LIST_LABELS[p]
        if pricing["is_offer"][p]:
            lines.append(f"- <s>{pricing['standard'][p]}ʀs</s> {price}ʀs - {label} 🎁")
        else:
            lines.append(f"- {price}ʀs - {label}")
    text = "\n".join(lines)
    if any(pricing["is_offer"].values()):
        text += "\n\n🎁 <b>ᴏꜰꜰᴇʀ ᴘʀɪᴄᴇ — ᴠᴀʟɪᴅ ᴏɴᴄᴇ ᴘᴇʀ ᴜsᴇʀ</b>"
    elif pricing["offer_active"] and not pricing["eligible"]:
        text += "\n\n✅ <i>ʏᴏᴜ ᴀʟʀᴇᴀᴅʏ ᴜsᴇᴅ ᴛʜɪs ᴏꜰꜰᴇʀ — sᴛᴀɴᴅᴀʀᴅ ᴘʀɪᴄᴇs ᴀᴘᴘʟʏ</i>"
    return text
