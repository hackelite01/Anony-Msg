"""Bitcoin helpers: address validation, amounts, unique per-order amounts, chain lookups.

Bitcoin has no memo/comment field, so each order gets a slightly unique amount
(plan price + a few sats). An incoming output paying exactly that amount to our
address identifies the order. Outputs are matched one by one (never summed) and
each (txid, vout) can only ever be used once.
"""
import hashlib
import logging
import secrets
from decimal import Decimal, InvalidOperation
from typing import Optional

import aiohttp

import config
from services import db

log = logging.getLogger(__name__)

SATS = 10**8
MIN_PRICE_SATS = 1_000  # keeps order amounts well above the dust limit (546 sats)
MAX_PRICE_SATS = 21_000_000 * SATS
TIME_SLACK_S = 2 * 3600  # block timestamps may lag real time
NEAR_TOLERANCE_SATS = 5_000  # "you almost paid the right amount" window for admin alerts


class BtcApiError(RuntimeError):
    """BTC provider request failed (network, rate limit, bad response)."""


# ---------- address validation (mainnet only) ----------

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _b58check_decode(s: str) -> Optional[bytes]:
    n = 0
    for ch in s:
        i = _B58.find(ch)
        if i < 0:
            return None
        n = n * 58 + i
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    raw = b"\x00" * (len(s) - len(s.lstrip("1"))) + body
    if len(raw) != 25:
        return None
    if hashlib.sha256(hashlib.sha256(raw[:21]).digest()).digest()[:4] != raw[21:]:
        return None
    return raw


def _polymod(values: list[int]) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            if (top >> i) & 1:
                chk ^= gen[i]
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _bech32_decode(addr: str) -> Optional[tuple[str, list[int], str]]:
    if addr.lower() != addr and addr.upper() != addr:
        return None  # mixed case is invalid
    addr = addr.lower()
    pos = addr.rfind("1")
    if pos < 1 or pos + 7 > len(addr) or len(addr) > 90:
        return None
    hrp = addr[:pos]
    data = [_BECH_CHARSET.find(c) for c in addr[pos + 1:]]
    if -1 in data:
        return None
    const = _polymod(_hrp_expand(hrp) + data)
    if const == 1:
        spec = "bech32"
    elif const == 0x2BC830A3:
        spec = "bech32m"
    else:
        return None
    return hrp, data[:-6], spec


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool) -> Optional[list[int]]:
    acc, bits, ret = 0, 0, []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for v in data:
        if v < 0 or (v >> frombits):
            return None
        acc = ((acc << frombits) | v) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret


def is_valid_address(addr: str) -> bool:
    """Mainnet P2PKH (1...), P2SH (3...), bech32 (bc1q...) and bech32m/taproot (bc1p...)."""
    addr = (addr or "").strip()
    if not addr or len(addr) > 90:
        return False
    if addr[0] in "13":
        raw = _b58check_decode(addr)
        return raw is not None and raw[0] in (0x00, 0x05)
    decoded = _bech32_decode(addr)
    if decoded is None:
        return False
    hrp, data, spec = decoded
    if hrp != "bc" or not data or data[0] > 16:
        return False
    program = _convertbits(data[1:], 5, 8, False)
    if program is None or not 2 <= len(program) <= 40:
        return False
    if data[0] == 0:
        return spec == "bech32" and len(program) in (20, 32)
    return spec == "bech32m"


def normalize_address(addr: str) -> str:
    addr = addr.strip()
    return addr.lower() if addr[:3].lower() == "bc1" else addr


async def get_address(cfg: Optional[dict] = None) -> Optional[str]:
    """Configured receiving address (admin setting). None if unset/invalid."""
    cfg = cfg if cfg is not None else await db.get_config()
    addr = (cfg.get("btc_address") or "").strip()
    return normalize_address(addr) if addr and is_valid_address(addr) else None


# ---------- amounts ----------

def parse_btc_amount(text: str) -> Optional[int]:
    """'0.0005' -> 50_000 sats. None unless a sane amount with <= 8 decimals."""
    try:
        d = Decimal((text or "").strip().replace(",", "."))
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    sats = d * SATS
    if sats != sats.to_integral_value():
        return None
    sats_i = int(sats)
    if sats_i < MIN_PRICE_SATS or sats_i > MAX_PRICE_SATS:
        return None
    return sats_i


def fmt_btc(sats: int) -> str:
    """Trimmed, for labels: 50000 -> '0.0005'."""
    s = f"{Decimal(int(sats)) / SATS:.8f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def fmt_btc_exact(sats: int) -> str:
    """All 8 decimals, for payment instructions."""
    return f"{Decimal(int(sats)) / SATS:.8f}"


def unique_offset(max_offset: int) -> int:
    """1..max_offset extra sats that make an order's amount unique."""
    return 1 + secrets.randbelow(max_offset)


# ---------- chain lookups (Esplora-compatible: mempool.space, blockstream.info) ----------

async def _fetch_from(base: str, address: str) -> tuple[list, Optional[int]]:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"{base}/api/address/{address}/txs") as resp:
            if resp.status != 200:
                raise BtcApiError(f"{base} HTTP {resp.status}")
            txs = await resp.json()
        tip: Optional[int] = None
        if any((t.get("status") or {}).get("confirmed") for t in txs):
            async with session.get(f"{base}/api/blocks/tip/height") as resp:
                if resp.status != 200:
                    raise BtcApiError(f"{base} tip HTTP {resp.status}")
                tip = int((await resp.text()).strip())
    return txs, tip


async def fetch_incoming(address: str) -> list[dict]:
    """Outputs paying `address` in its recent transactions (mempool + latest confirmed).

    Tries each provider in config.BTC_API_URLS until one answers.
    Each item: {txid, vout, value (sats), confirmations, block_time}.
    """
    errors: list[str] = []
    txs: list = []
    tip: Optional[int] = None
    for base in config.BTC_API_URLS:
        try:
            txs, tip = await _fetch_from(base, address)
            break
        except (aiohttp.ClientError, TimeoutError, ValueError, BtcApiError) as e:
            errors.append(f"{base}: {e or type(e).__name__}")
    else:
        raise BtcApiError("all BTC providers failed: " + "; ".join(errors))

    target = normalize_address(address)
    out: list[dict] = []
    for tx in txs:
        st = tx.get("status") or {}
        conf = 0
        if st.get("confirmed") and tip is not None and st.get("block_height") is not None:
            conf = max(tip - int(st["block_height"]) + 1, 0)
        for idx, vo in enumerate(tx.get("vout") or []):
            vaddr = vo.get("scriptpubkey_address")
            if vaddr and normalize_address(vaddr) == target:
                out.append(
                    {
                        "txid": tx["txid"],
                        "vout": idx,
                        "value": int(vo.get("value") or 0),
                        "confirmations": conf,
                        "block_time": st.get("block_time"),
                    }
                )
    return out
