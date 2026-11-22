"""Payment methods: what a user may pick for a plan, plus shared formatting/validation.

A method is offered only if (1) the admin switched it on, (2) it is configured
(wallet / address / UPI ID), and (3) the plan has a price in that currency.
"""
import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from services import btc, db, ton

STARS, TON, BTC, UPI = "stars", "ton", "btc", "upi"
ALL_METHODS = (STARS, TON, BTC, UPI)

# Looks like name@bank, e.g. shop@okhdfcbank. (Syntax check only — we can't verify it exists.)
UPI_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{2,256}@[A-Za-z][A-Za-z0-9.\-]{1,63}$")
MAX_INR_PAISE = 10_000_000 * 100


def is_valid_upi_id(value: str) -> bool:
    return bool(UPI_ID_RE.match((value or "").strip()))


def parse_inr_amount(text: str) -> Optional[int]:
    """'199' / '199.50' -> paise. None unless positive with <= 2 decimals."""
    try:
        d = Decimal((text or "").strip().replace(",", "."))
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    paise = d * 100
    if paise != paise.to_integral_value():
        return None
    paise_i = int(paise)
    if paise_i <= 0 or paise_i > MAX_INR_PAISE:
        return None
    return paise_i


def fmt_inr(paise: int) -> str:
    rupees, rem = divmod(int(paise), 100)
    return f"{rupees}" if rem == 0 else f"{rupees}.{rem:02d}"


async def available_methods(plan: dict, cfg: Optional[dict] = None) -> list[str]:
    """Methods the user can choose for this plan right now (display order)."""
    cfg = cfg if cfg is not None else await db.get_config()
    out: list[str] = []
    if cfg.get("stars_enabled", True) and plan.get("price_stars"):
        out.append(STARS)
    if cfg.get("ton_enabled") and plan.get("price_ton_nano") and await ton.get_wallet(cfg):
        out.append(TON)
    if cfg.get("btc_enabled") and plan.get("price_btc_sats") and await btc.get_address(cfg):
        out.append(BTC)
    if (
        cfg.get("upi_enabled")
        and plan.get("price_inr_paise")
        and is_valid_upi_id(cfg.get("upi_id") or "")
    ):
        out.append(UPI)
    return out


def paid_label(request: dict) -> str:
    """Human-readable amount paid for a purchase request, any method."""
    method = request.get("payment_method") or STARS
    if method == TON:
        return f"{ton.fmt_ton(request.get('ton_amount_nano') or 0)} TON"
    if method == BTC:
        return f"{btc.fmt_btc_exact(request.get('btc_amount_sats') or 0)} BTC"
    if method == UPI:
        return f"₹{fmt_inr(request.get('upi_amount_paise') or 0)} (UTR {request.get('upi_utr')})"
    if method == "gift":
        return "🎁 gift (free)"
    if method == "free":
        return "🆓 free plan"
    if method == "promo":
        return f"🎟 promo {request.get('promo_code')} (100% off)"
    return f"{request.get('stars_paid', 0)}⭐"


def payment_ref(request: dict) -> str:
    """Transaction reference to quote when refunding by hand."""
    return str(
        request.get("ton_tx_hash")
        or request.get("btc_txid")
        or request.get("upi_utr")
        or request.get("payment_charge_id")
        or "?"
    )


MANUAL_REFUND_METHODS = (TON, BTC, UPI)  # no Bot API refund exists for these
