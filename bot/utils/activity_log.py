"""Forward bot activity to a Telegram channel.

If LOG_CHANNEL_ID is unset, every call is a no-op. If sending fails (channel
gone, bot kicked, network blip), the failure is logged locally and swallowed
— activity logging must never break the user-facing flow.
"""
import logging
from typing import Any

from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

import config
from services.ton import fmt_ton

log = logging.getLogger(__name__)


def _user_tag(user: Any) -> str:
    if user is None:
        return "unknown"
    if isinstance(user, dict):
        uname = user.get("username")
        uid = user.get("telegram_id") or user.get("id")
    else:
        uname = getattr(user, "username", None)
        uid = getattr(user, "id", None)
    return f"@{uname}" if uname else f"id={uid}"


async def post(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if not config.LOG_CHANNEL_ID:
        return
    try:
        await context.bot.send_message(
            chat_id=config.LOG_CHANNEL_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except TelegramError as e:
        log.warning("activity_log post failed: %s", e)


# ---------- event helpers ----------

async def new_user(context, user) -> None:
    await post(context, f"👤 *New user* — {_user_tag(user)}")


async def sms_sent(context, user, code: str, to: str, used: int, limit: int) -> None:
    masked = to[:6] + "…" if len(to) > 8 else to
    await post(
        context,
        "📨 *SMS sent*\n"
        f"User: {_user_tag(user)}\n"
        f"Key: `{code}` ({used}/{limit})\n"
        f"To: `{masked}`",
    )


async def sms_failed(context, user, code: str, to: str, error: str | None) -> None:
    masked = to[:6] + "…" if len(to) > 8 else to
    err = (error or "unknown")[:200]
    await post(
        context,
        "❌ *SMS failed*\n"
        f"User: {_user_tag(user)}\n"
        f"Key: `{code}`\n"
        f"To: `{masked}`\n"
        f"Error: `{err}`",
    )


async def payment_received(context, user, plan_name: str, stars: int) -> None:
    await post(
        context,
        "💳 *Payment received*\n"
        f"User: {_user_tag(user)}\n"
        f"Plan: {plan_name}\n"
        f"Paid: {stars}⭐",
    )


async def ton_payment_received(context, user, plan_name: str, amount_nano: int, tx_hash: str) -> None:
    await post(
        context,
        "💎 *TON payment received*\n"
        f"User: {_user_tag(user)}\n"
        f"Plan: {plan_name}\n"
        f"Paid: {fmt_ton(amount_nano)} TON\n"
        f"Tx: `{tx_hash}`",
    )


async def key_assigned(context, user, code: str, plan_name: str, sms_limit: int, auto: bool) -> None:
    await post(
        context,
        f"🔑 *Key assigned* {'(auto)' if auto else '(manual)'}\n"
        f"User: {_user_tag(user)}\n"
        f"Code: `{code}`\n"
        f"Plan: {plan_name} ({sms_limit} SMS)",
    )


async def refund_issued(context, user, plan_name: str, stars: int, reason: str) -> None:
    await post(
        context,
        "💸 *Refund issued*\n"
        f"User: {_user_tag(user)}\n"
        f"Plan: {plan_name}\n"
        f"Refunded: {stars}⭐\n"
        f"Reason: {reason}",
    )


async def provider_keys_added(context, admin, count: int) -> None:
    await post(
        context,
        f"🗄 *Provider keys added* — {count} key(s) by {_user_tag(admin)}",
    )


async def plan_added(context, admin, name: str, sms_limit: int, price_stars: int) -> None:
    await post(
        context,
        "📋 *Plan added*\n"
        f"By: {_user_tag(admin)}\n"
        f"Name: {name}\n"
        f"Limit: {sms_limit} SMS\n"
        f"Price: {price_stars}⭐",
    )


async def plan_deleted(context, admin, name: str) -> None:
    await post(
        context,
        f"🗑 *Plan deleted* — {name} by {_user_tag(admin)}",
    )


async def plan_deactivated(context, admin, plan_id: str) -> None:
    await post(
        context,
        f"🔴 *Plan deactivated* — `{plan_id}` by {_user_tag(admin)}",
    )


async def broadcast_sent(context, admin, sent: int, failed: int) -> None:
    await post(
        context,
        "📢 *Broadcast sent*\n"
        f"By: {_user_tag(admin)}\n"
        f"Delivered: {sent}\n"
        f"Failed: {failed}",
    )


async def bot_error(context, error_type: str, error_msg: str, where: str | None = None) -> None:
    """Send raw exception details to the log channel."""
    msg = f"🐞 *Bot error*\nType: `{error_type}`"
    if where:
        msg += f"\nWhere: `{where}`"
    msg += f"\n\n```\n{error_msg[:1500]}\n```"
    await post(context, msg)


async def unauthorized_admin_attempt(context, user, what: str) -> None:
    """Someone who isn't on ADMIN_USER_IDS tried to use an admin command."""
    await post(
        context,
        "🚨 *Unauthorized admin attempt*\n"
        f"User: {_user_tag(user)}\n"
        f"Tried: `{what}`",
    )
