"""Key stock: the bot sells identifiers of keys that already exist on the website.

The website owns the real SMS provider keys and does all the sending. In the admin panel you add
only the *identifier* of each web key (the value people paste into the website). A purchase hands
the buyer one identifier per SMS_PER_PROVIDER_KEY SMS (a 10-SMS plan -> 2 identifiers at the
default of 5). The bot never sees a real provider key and does not talk to the website.
"""
import re
from typing import Optional, Union

import config
from services import db

# Same rule the web admin enforces for custom keys (its generated QSMS-XXXXX-... keys fit too).
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def is_valid_identifier(value: str) -> bool:
    return bool(IDENTIFIER_RE.match(value or ""))


def mask(code: str) -> str:
    """Safe-to-show form of an identifier: QSMS-K7M2P-…-RB8FA -> 'QSMS-…RB8FA'."""
    if len(code) <= 12:
        return code[:3] + "…" + code[-3:]
    return f"{code[:5]}…{code[-5:]}"


def as_list(codes: Union[str, list, tuple]) -> list[str]:
    return [codes] if isinstance(codes, str) else list(codes)


async def web_url(cfg: Optional[dict] = None) -> Optional[str]:
    """Website address users send SMS from (admin setting, else WEB_SMS_URL env)."""
    cfg = cfg if cfg is not None else await db.get_config()
    return (cfg.get("web_url") or config.WEB_SMS_URL or "").strip() or None


async def issue_keys(
    request_id: str, telegram_id: int, plan: dict, keys_needed: int
) -> list[str]:
    """Reserve `keys_needed` identifiers from stock for this buyer and record them.

    Returns the identifiers. Raises if the stock can't cover it (nothing stays reserved).
    """
    docs = await db.reserve_keys_for(f"req:{request_id}", keys_needed)
    try:
        return await db.create_user_keys(telegram_id, plan, docs)
    except Exception:
        await db.release_reserved([d["_id"] for d in docs])
        raise


def _md_url(url: str) -> str:
    # legacy Markdown treats _ * ` as formatting; escape them so the link stays clickable
    return url.replace("\\", "\\\\").replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")


def key_message(codes, plan: dict, title: str, url: Optional[str] = None) -> str:
    """Markdown text delivering the identifier(s) to the buyer."""
    codes = as_list(codes)
    lines = [title, ""]
    if len(codes) == 1:
        lines += ["🔑 Your key:", f"`{codes[0]}`"]
    else:
        lines += [f"🔑 Your {len(codes)} keys (each works on its own):"]
        lines += [f"`{c}`" for c in codes]
    lines += ["", f"Plan: {plan['name']} • {plan['sms_limit']} SMS"]
    if len(codes) > 1:
        lines.append(f"Each key sends up to {config.SMS_PER_PROVIDER_KEY} SMS.")
    lines += ["", "Copy a key and paste it on our website to send SMS."]
    if url:
        lines.append(f"🌐 {_md_url(url)}")
    return "\n".join(lines)
