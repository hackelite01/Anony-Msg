"""TON helpers: address validation, amounts, order memos, TON Center lookups.

Bot API 10.4's "TON wallet" additions (wallet cards, transfer service messages)
are NOT a payment method for bots, so TON payments are verified on-chain:
every order gets a unique comment (memo); we watch the receiving wallet for an
incoming transfer carrying that memo and at least the expected amount.
"""
import base64
import binascii
import logging
import secrets
from decimal import Decimal, InvalidOperation
from typing import Optional

import aiohttp

import config
from services import db

log = logging.getLogger(__name__)

NANO = 10**9
MEMO_PREFIX = "ASB"
MAX_PRICE_NANO = 10**6 * NANO  # 1,000,000 TON sanity cap


class TonApiError(RuntimeError):
    """TON Center request failed (network, rate limit, bad response)."""


# ---------- addresses ----------

def _crc16(data: bytes) -> int:
    """CRC16-XMODEM, as used by TON user-friendly addresses."""
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def parse_address(addr: str) -> Optional[tuple[int, bytes]]:
    """(workchain, 32-byte hash) for a raw '0:<hex>' or friendly address; None if invalid.

    Friendly addresses are checksum-verified, so typos are rejected.
    """
    addr = (addr or "").strip()
    try:
        if ":" in addr:
            wc_s, h = addr.split(":", 1)
            wc, hb = int(wc_s), bytes.fromhex(h)
            if wc not in (0, -1) or len(hb) != 32:
                return None
            return wc, hb
        if len(addr) != 48:
            return None
        raw = base64.urlsafe_b64decode(addr.replace("+", "-").replace("/", "_"))
        if len(raw) != 36 or _crc16(raw[:34]) != int.from_bytes(raw[34:], "big"):
            return None
        wc = int.from_bytes(raw[1:2], "big", signed=True)
        if wc not in (0, -1):
            return None
        return wc, raw[2:34]
    except (ValueError, binascii.Error):
        return None


def same_address(a: str, b: str) -> bool:
    pa, pb = parse_address(a), parse_address(b)
    return pa is not None and pa == pb


def friendly(addr: str) -> str:
    """Non-bounceable friendly form, safe for wallets that aren't deployed yet."""
    parsed = parse_address(addr)
    if parsed is None:
        return addr
    wc, h = parsed
    body = bytes([0x51, wc & 0xFF]) + h
    return base64.urlsafe_b64encode(body + _crc16(body).to_bytes(2, "big")).decode()


async def get_wallet() -> Optional[str]:
    """Receiving wallet (admin setting, else env fallback). None if unset/invalid."""
    cfg = await db.get_config()
    wallet = (cfg.get("ton_wallet") or config.TON_WALLET_ADDRESS or "").strip()
    if wallet and parse_address(wallet) is not None:
        return wallet
    return None


async def is_enabled() -> bool:
    """TON is offered to users only if the admin switched it on AND a valid wallet exists.

    Note: the payment watcher uses get_wallet() and ignores this switch, so transfers for
    orders created before TON was switched off are still honoured.
    """
    cfg = await db.get_config()
    return bool(cfg.get("ton_enabled")) and await get_wallet() is not None


# ---------- amounts / memo ----------

def parse_ton_amount(text: str) -> Optional[int]:
    """'1.5' -> 1_500_000_000 nanoTON. None if not a positive amount with <=9 decimals."""
    try:
        d = Decimal((text or "").strip().replace(",", "."))
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    nano = d * NANO
    if nano != nano.to_integral_value():
        return None
    nano_i = int(nano)
    if nano_i <= 0 or nano_i > MAX_PRICE_NANO:
        return None
    return nano_i


def fmt_ton(nano: int) -> str:
    s = f"{Decimal(int(nano)) / NANO:f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def new_memo() -> str:
    return MEMO_PREFIX + secrets.token_hex(5).upper()


def paid_label(request: dict) -> str:
    if request.get("payment_method") == "ton":
        return f"{fmt_ton(request.get('ton_amount_nano') or 0)} TON"
    return f"{request.get('stars_paid', 0)}⭐"


# ---------- TON Center ----------

async def fetch_incoming(wallet: str, limit: int = 50) -> list[dict]:
    """Recent successful incoming transfers to `wallet`, newest first.

    Each item: {hash, memo, value (nanoTON), source, time}.
    """
    headers = {"X-API-Key": config.TONCENTER_API_KEY} if config.TONCENTER_API_KEY else {}
    params = {"account": wallet, "limit": str(limit), "sort": "desc"}
    url = f"{config.TONCENTER_URL}/api/v3/transactions"
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise TonApiError(f"TON Center HTTP {resp.status}")
                data = await resp.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        raise TonApiError(f"TON Center request failed: {e}") from e

    out: list[dict] = []
    for tx in data.get("transactions") or []:
        if (tx.get("description") or {}).get("aborted"):
            continue
        msg = tx.get("in_msg") or {}
        if not msg.get("source"):  # external message -> outgoing wallet action, not a deposit
            continue
        dest = msg.get("destination")
        if not dest or not same_address(dest, wallet):
            continue
        decoded = (msg.get("message_content") or {}).get("decoded") or {}
        comment = decoded.get("comment") if decoded.get("@type") == "text_comment" else ""
        try:
            value = int(msg.get("value") or 0)
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "hash": tx.get("hash", ""),
                "memo": (comment or "").strip(),
                "value": value,
                "source": msg["source"],
                "time": tx.get("now"),
            }
        )
    return out
