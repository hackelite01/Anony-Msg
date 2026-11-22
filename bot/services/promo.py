"""Promo codes: validation, discounted plan pricing, use accounting.

A code gives `percent_off` (100 = a free key). It can be limited by total uses and an expiry,
and optionally restricted to one Telegram user (`only_user_id`). Each user can redeem a given
code only once.

* 100% codes are consumed immediately (atomically) and the key is granted right away.
* Partial discounts are "applied" to one plan for PROMO_APPLY_MINUTES; the use is consumed when
  the payment is confirmed. A payment already in flight is always honoured, even if the code ran
  out in the meantime.
"""
import re
import secrets
import time
from datetime import timedelta
from math import ceil
from typing import Optional

from services import btc, db

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_\-]{2,31}$")
PROMO_APPLY_MINUTES = 60

# in-memory brute-force guard (single bot instance): failed guesses per user
_FAIL_WINDOW_S = 600
_FAIL_LIMIT = 8
_failures: dict[int, list[float]] = {}


def normalize(raw: str) -> str:
    return (raw or "").strip().upper()


def is_valid_code(code: str) -> bool:
    return bool(CODE_RE.match(code))


def new_code() -> str:
    return "PROMO-" + secrets.token_hex(4).upper()


def note_failure(telegram_id: int) -> None:
    _failures.setdefault(telegram_id, []).append(time.monotonic())


def too_many_failures(telegram_id: int) -> bool:
    cutoff = time.monotonic() - _FAIL_WINDOW_S
    recent = [t for t in _failures.get(telegram_id, []) if t >= cutoff]
    _failures[telegram_id] = recent
    return len(recent) >= _FAIL_LIMIT


async def validate(code: str, telegram_id: int) -> tuple[Optional[dict], Optional[str]]:
    """(promo, None) if the user can use it now, else (None, user-facing error)."""
    promo = await db.get_promo(code)
    # Wrong code and "not yours" look identical so codes can't be probed.
    if promo is None or (promo.get("only_user_id") and promo["only_user_id"] != telegram_id):
        return None, "❌ Invalid promo code."
    if not promo.get("active"):
        return None, "❌ This code is no longer active."
    exp = promo.get("expires_at")
    if exp and exp <= db.now_utc().replace(tzinfo=None):
        return None, "❌ This code has expired."
    max_uses = promo.get("max_uses")
    if max_uses is not None and promo.get("uses", 0) >= max_uses:
        return None, "❌ This code has already been fully used."
    if await db.has_redeemed_promo(code, telegram_id):
        return None, "❌ You've already used this code."
    return promo, None


def discounted(plan: dict, percent: int, code: str) -> dict:
    """Copy of `plan` with every price reduced by `percent` (never below the method's minimum)."""
    keep = 100 - percent

    def cut(value, floor: int):
        return None if not value else max(floor, ceil(value * keep / 100))

    out = dict(plan)
    out["price_stars"] = cut(plan.get("price_stars"), 1)
    out["price_ton_nano"] = cut(plan.get("price_ton_nano"), 1)
    out["price_btc_sats"] = cut(plan.get("price_btc_sats"), btc.MIN_PRICE_SATS)
    out["price_inr_paise"] = cut(plan.get("price_inr_paise"), 100)
    out["promo_code"] = code
    out["promo_percent"] = percent
    return out


async def apply(telegram_id: int, plan_id: str, promo: dict) -> None:
    expires = db.now_utc() + timedelta(minutes=PROMO_APPLY_MINUTES)
    await db.set_applied_promo(telegram_id, str(plan_id), promo["code"], promo["percent_off"], expires)


async def priced_plan(plan: dict, telegram_id: int) -> dict:
    """The plan as this user should pay for it: discounted if they applied a valid code."""
    applied = await db.get_applied_promo(telegram_id, str(plan["_id"]))
    if not applied:
        return plan
    promo = await db.get_promo(applied["code"])
    if not promo or not promo.get("active") or applied["percent"] >= 100:
        return plan
    return discounted(plan, int(applied["percent"]), applied["code"])


async def try_consume(code: str, telegram_id: int) -> bool:
    return await db.consume_promo(code, telegram_id)


async def release(code: str, telegram_id: int) -> None:
    await db.release_promo(code, telegram_id)


async def finalize(code: Optional[str], telegram_id: int) -> None:
    """Count the use once a discounted payment is confirmed. Never blocks fulfilment."""
    if not code:
        return
    await db.consume_promo(code, telegram_id)
    await db.clear_applied_promo(telegram_id)
