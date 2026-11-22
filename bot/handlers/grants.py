"""🎁 Keys that aren't bought: admin gifts and free-plan claims.

`grant_key` is the single place that turns an unpaid grant (gift / free plan / 100% promo code)
into a real key: it records an audit request (payment_method gift | free | promo, no revenue),
checks the provider-key pool and assigns the key. Callers handle messaging the user.
"""
import logging
from types import SimpleNamespace
from typing import Optional

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from handlers import purchase
from handlers.admin import _admin_only
from services import db
from utils import activity_log, keyboards

log = logging.getLogger(__name__)


class OutOfStock(RuntimeError):
    """Not enough free provider keys to cover this grant."""


async def grant_key(bot, user, plan: dict, *, method: str, extra: Optional[dict] = None) -> str:
    """Create and return a key code for an unpaid grant. Raises OutOfStock / other on failure."""
    request = await db.create_grant_request(user.id, user.username or "", plan, method, extra)
    if await db.free_keys_count() < request["keys_needed"]:
        await db.set_purchase_status(request["_id"], "rejected", "out_of_stock")
        raise OutOfStock(f"need {request['keys_needed']} free provider keys")
    ctx = SimpleNamespace(bot=bot)
    try:
        code = await purchase.assign_key_for_request(ctx, user, request, plan, log_it=False)
    except Exception:
        await db.set_purchase_status(request["_id"], "rejected", "grant_failed")
        raise
    await activity_log.grant_given(ctx, user, code, plan["name"], plan["sms_limit"], method)
    return code


def key_message(code: str, plan: dict, title: str) -> str:
    return (
        f"{title}\n\n"
        f"Code: `{code}`\n"
        f"Plan: {plan['name']}\n"
        f"SMS: {plan['sms_limit']} sends\n\n"
        "Use /menu to start sending."
    )


def _tag(uid: int, username: Optional[str]) -> str:
    return f"@{username} ({uid})" if username else f"id {uid}"


# ---------- user: claim a free plan ----------

async def on_free_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    if await db.is_banned(user.id):
        return

    plan = await db.get_plan(q.data.split(":", 2)[2])
    if not plan or not plan.get("active") or not plan.get("is_free"):
        await q.edit_message_text(
            "❌ That plan is no longer available.", reply_markup=keyboards.back_to_menu()
        )
        return

    plan_id = str(plan["_id"])
    if not await db.claim_free(user.id, plan_id):
        await q.edit_message_text(
            "✅ You've already claimed this free plan.", reply_markup=keyboards.back_to_menu()
        )
        return

    try:
        code = await grant_key(context.bot, user, plan, method="free")
    except Exception as e:
        await db.release_free_claim(user.id, plan_id)  # let them try again later
        if not isinstance(e, OutOfStock):
            log.exception("free claim failed: %s", e)
        await q.edit_message_text(
            "😕 Free keys are temporarily out of stock. Please try again later.",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    await q.edit_message_text(
        key_message(code, plan, "🎁 *Your free key is ready!*"),
        reply_markup=keyboards.back_to_menu(),
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------- admin: gift a key ----------

GIFT_USER, GIFT_PLAN, GIFT_CONFIRM = range(3)
_GIFT_KEYS = ("gift_uid", "gift_uname", "gift_plan")


def _clear(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in _GIFT_KEYS:
        context.user_data.pop(k, None)


async def _resolve_user(raw: str) -> Optional[dict]:
    raw = raw.strip()
    if raw.isdigit():
        return await db.get_user(int(raw))
    return await db.find_user_by_username(raw)


def _cancel_row() -> list:
    return [keyboards.btn("❌ Cancel", "adm:gift:cancel", style=keyboards.DANGER)]


@_admin_only
async def gift_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    _clear(context)
    await q.edit_message_text(
        "🎁 *Gift a key*\n\nSend the user's Telegram ID or @username "
        "(they must have started the bot).\n\n/cancel to abort.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return GIFT_USER


async def gift_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    target = await _resolve_user(update.message.text or "")
    if target is None:
        await update.message.reply_text(
            "❌ I don't know that user. They must open the bot (/start) first. "
            "Send another ID/@username or /cancel."
        )
        return GIFT_USER
    if target.get("is_banned"):
        await update.message.reply_text("🚫 That user is banned. Send another or /cancel.")
        return GIFT_USER

    plans = await db.list_plans(active_only=True)
    if not plans:
        await update.message.reply_text("No active plans to gift.", reply_markup=keyboards.admin_back())
        return ConversationHandler.END

    context.user_data["gift_uid"] = target["telegram_id"]
    context.user_data["gift_uname"] = target.get("username") or ""
    rows = [
        [
            keyboards.btn(
                f"💎 {p['name']} • {p['sms_limit']} SMS",
                f"adm:gift:plan:{p['_id']}",
                style=keyboards.PRIMARY,
            )
        ]
        for p in plans
    ]
    rows.append(_cancel_row())
    await update.message.reply_text(
        f"Which plan do you want to give to {_tag(target['telegram_id'], target.get('username'))}?",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return GIFT_PLAN


async def gift_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    plan = await db.get_plan(q.data.rsplit(":", 1)[1])
    uid = context.user_data.get("gift_uid")
    if not plan or not plan.get("active") or not uid:
        await q.edit_message_text("❌ Plan not found.", reply_markup=keyboards.admin_back())
        _clear(context)
        return ConversationHandler.END
    context.user_data["gift_plan"] = str(plan["_id"])
    who = _tag(uid, context.user_data.get("gift_uname"))
    await q.edit_message_text(
        f"Give '{plan['name']}' ({plan['sms_limit']} SMS) to {who} for free?",
        reply_markup=InlineKeyboardMarkup(
            [[keyboards.btn("🎁 Yes, give key", "adm:gift:yes", style=keyboards.SUCCESS)], _cancel_row()]
        ),
    )
    return GIFT_CONFIRM


async def gift_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    uid = context.user_data.get("gift_uid")
    username = context.user_data.get("gift_uname") or None
    plan = await db.get_plan(context.user_data.get("gift_plan") or "")
    _clear(context)
    if not uid or not plan:
        await q.edit_message_text("❌ Something went wrong. Start again.", reply_markup=keyboards.admin_back())
        return ConversationHandler.END

    target = SimpleNamespace(id=uid, username=username)
    try:
        code = await grant_key(
            context.bot, target, plan, method="gift", extra={"gifted_by": update.effective_user.id}
        )
    except OutOfStock:
        await q.edit_message_text(
            "❌ Not enough free provider keys to cover this plan. Add keys first (Provider Keys).",
            reply_markup=keyboards.admin_back(),
        )
        return ConversationHandler.END
    except Exception as e:
        log.exception("gift failed: %s", e)
        await q.edit_message_text("❌ Could not assign the key. Check the logs.", reply_markup=keyboards.admin_back())
        return ConversationHandler.END

    delivered = True
    try:
        await context.bot.send_message(
            chat_id=uid,
            text=key_message(code, plan, "🎁 *You received a free key!*"),
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError as e:
        delivered = False
        log.warning("gift DM to %s failed: %s", uid, e)

    note = "" if delivered else "\n⚠️ I couldn't message them (they may have blocked the bot) — send them the code yourself."
    await q.edit_message_text(
        f"✅ Gift key created for {_tag(uid, username)}.\nCode: `{code}`{note}",
        reply_markup=keyboards.admin_back(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


async def gift_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    _clear(context)
    await q.edit_message_text("❎ Cancelled.", reply_markup=keyboards.admin_back())
    return ConversationHandler.END


async def gift_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear(context)
    await update.effective_message.reply_text(
        "❎ Cancelled.", reply_markup=keyboards.main_reply_keyboard()
    )
    return ConversationHandler.END


async def _exit_on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear(context)
    await update.effective_message.reply_text(
        "↩️ Exited admin flow — tap your selection again.",
        reply_markup=keyboards.main_reply_keyboard(),
    )
    return ConversationHandler.END


def register(app) -> None:
    app.add_handler(CallbackQueryHandler(on_free_claim, pattern=r"^free:claim:[a-f0-9]+$"))
    menu_filter = filters.Regex(keyboards.MENU_BUTTONS_REGEX)
    app.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(gift_start, pattern=r"^adm:gift$")],
            states={
                GIFT_USER: [
                    MessageHandler(menu_filter, _exit_on_menu),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, gift_user),
                ],
                GIFT_PLAN: [CallbackQueryHandler(gift_plan, pattern=r"^adm:gift:plan:[a-f0-9]+$")],
                GIFT_CONFIRM: [CallbackQueryHandler(gift_yes, pattern=r"^adm:gift:yes$")],
            },
            fallbacks=[
                CommandHandler("cancel", gift_cancel_cmd),
                CallbackQueryHandler(gift_cancel_cb, pattern=r"^adm:gift:cancel$"),
            ],
            name="admin_gift",
            persistent=False,
        )
    )
