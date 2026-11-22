"""🇮🇳 Pay with UPI — manual approval.

UPI payments can't be read automatically without a payment gateway, so:
1. The user sees the admin's UPI ID and the exact amount, pays in any UPI app,
2. taps "I've paid" and sends the UTR (transaction reference) and, optionally, a screenshot,
3. the request goes to the admins with Approve / Reject. It is never auto-approved —
   a human checks the money actually arrived.

Safeguards: each UTR can be submitted once (unique index), and a user can have at most
config.UPI_MAX_PENDING_PER_USER unreviewed submissions.
"""
import html
import logging
import re
from types import SimpleNamespace

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
from handlers import purchase
from services import db, payments, promo
from utils import activity_log, keyboards

log = logging.getLogger(__name__)

UPI_UTR, UPI_SHOT = range(2)
UTR_RE = re.compile(r"^[A-Za-z0-9]{8,30}$")
_KEYS = ("upi_plan_id", "upi_utr", "upi_amount", "upi_promo")


def _clear(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in _KEYS:
        context.user_data.pop(k, None)


# ---------- instructions ----------

async def on_pay_upi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if await db.is_banned(update.effective_user.id):
        return

    plan = await db.get_plan(q.data.split(":", 2)[2])
    cfg = await db.get_config()
    if (
        not plan
        or not plan.get("active")
        or payments.UPI not in await payments.available_methods(plan, cfg)
    ):
        await q.edit_message_text(
            "❌ UPI payment isn't available for this plan right now.",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    plan = await promo.priced_plan(plan, update.effective_user.id)
    amount = payments.fmt_inr(plan["price_inr_paise"])
    text = (
        "🇮🇳 <b>Pay with UPI</b>\n\n"
        f"Plan: <b>{html.escape(plan['name'])}</b> • {plan['sms_limit']} SMS\n"
        f"Amount: <b>₹{amount}</b>\n\n"
        "Pay to this UPI ID:\n"
        f"<code>{html.escape(cfg['upi_id'])}</code>\n\n"
        f"1. Open any UPI app and pay exactly ₹{amount}.\n"
        "2. Note the UTR / transaction reference number of the payment.\n"
        "3. Tap the button below and send it to me.\n\n"
        "An admin checks every UPI payment before releasing your key."
    )
    try:
        await q.edit_message_text(
            text,
            reply_markup=keyboards.upi_instructions(str(plan["_id"])),
            parse_mode=ParseMode.HTML,
        )
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


# ---------- submit UTR (+ optional screenshot) ----------

async def upi_paid_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    if await db.is_banned(user.id):
        return ConversationHandler.END

    plan_id = q.data.split(":", 2)[2]
    plan = await db.get_plan(plan_id)
    if (
        not plan
        or not plan.get("active")
        or payments.UPI not in await payments.available_methods(plan)
    ):
        await q.edit_message_text(
            "❌ UPI payment isn't available for this plan right now.",
            reply_markup=keyboards.back_to_menu(),
        )
        return ConversationHandler.END

    if await db.count_pending_upi(user.id) >= config.UPI_MAX_PENDING_PER_USER:
        await q.edit_message_text(
            "⏳ You already have several UPI payments waiting for review. "
            "Please wait for an admin to process them first.",
            reply_markup=keyboards.back_to_menu(),
        )
        return ConversationHandler.END

    priced = await promo.priced_plan(plan, user.id)  # fix the amount the user was shown
    context.user_data["upi_plan_id"] = plan_id
    context.user_data["upi_amount"] = priced["price_inr_paise"]
    context.user_data["upi_promo"] = priced.get("promo_code")
    await q.edit_message_text(
        "Send the <b>UTR / transaction reference</b> of your UPI payment "
        "(8–30 letters or digits).\n\n/cancel to abort.",
        parse_mode=ParseMode.HTML,
    )
    return UPI_UTR


async def upi_got_utr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    utr = (update.message.text or "").strip().upper()
    if not UTR_RE.match(utr):
        await update.message.reply_text(
            "❌ That doesn't look like a UTR. It's 8–30 letters/digits with no spaces. "
            "Try again or /cancel."
        )
        return UPI_UTR
    context.user_data["upi_utr"] = utr
    await update.message.reply_text(
        "Optional: send a screenshot of the payment to speed up approval, "
        "or send /skip to continue without one."
    )
    return UPI_SHOT


async def upi_got_shot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    file_id = update.message.photo[-1].file_id
    return await _finish(update, context, file_id)


async def upi_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _finish(update, context, None)


async def upi_need_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Please send a photo (screenshot), or /skip, or /cancel.")
    return UPI_SHOT


async def _finish(update: Update, context: ContextTypes.DEFAULT_TYPE, file_id) -> int:
    user = update.effective_user
    plan_id = context.user_data.get("upi_plan_id")
    utr = context.user_data.get("upi_utr")
    amount = context.user_data.get("upi_amount")
    promo_code = context.user_data.get("upi_promo")
    _clear(context)
    if not plan_id or not utr:
        await update.message.reply_text(
            "Something went wrong — please start again from 🛒 Buy Key.",
            reply_markup=keyboards.main_reply_keyboard(),
        )
        return ConversationHandler.END

    plan = await db.get_plan(plan_id)
    if not plan or not plan.get("price_inr_paise"):
        await update.message.reply_text(
            "❌ That plan is no longer available. Contact admin with your UTR.",
            reply_markup=keyboards.main_reply_keyboard(),
        )
        return ConversationHandler.END

    request = await db.create_upi_purchase_request(
        telegram_id=user.id,
        username=user.username or "",
        plan=plan,
        utr=utr,
        amount_paise=amount or plan["price_inr_paise"],
        screenshot_file_id=file_id,
        promo_code=promo_code,
    )
    if request is None:
        await update.message.reply_text(
            "⚠️ That UTR has already been submitted. If you think this is a mistake, "
            "contact admin.",
            reply_markup=keyboards.main_reply_keyboard(),
        )
        return ConversationHandler.END

    await promo.finalize(promo_code, user.id)
    ctx = SimpleNamespace(bot=context.bot)
    await activity_log.upi_submitted(ctx, user, plan["name"], amount or plan["price_inr_paise"], utr)

    if file_id:  # screenshot goes to admins first, then the Approve/Reject message
        who = f"@{user.username}" if user.username else f"id={user.id}"
        for admin_id in config.ADMIN_USER_IDS:
            try:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=file_id,
                    caption=f"UPI screenshot • UTR {utr} • ₹{payments.fmt_inr(amount or plan['price_inr_paise'])} • {who}",
                )
            except TelegramError as e:
                log.warning("UPI screenshot to admin %s failed: %s", admin_id, e)

    await purchase.deliver_paid_request(
        context.bot,
        user,
        plan,
        request,
        allow_auto=False,
        pending_text="⏳ Submitted! An admin will verify your payment and send your key shortly.",
    )
    await update.message.reply_text("Thanks!", reply_markup=keyboards.main_reply_keyboard())
    return ConversationHandler.END


async def upi_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear(context)
    await update.effective_message.reply_text(
        "❎ Cancelled.", reply_markup=keyboards.main_reply_keyboard()
    )
    return ConversationHandler.END


async def _exit_on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User tapped a main-menu button mid-flow -> leave the flow."""
    _clear(context)
    await update.effective_message.reply_text(
        "↩️ Cancelled — tap your selection again.",
        reply_markup=keyboards.main_reply_keyboard(),
    )
    return ConversationHandler.END


def register(app) -> None:
    app.add_handler(CallbackQueryHandler(on_pay_upi, pattern=r"^pay:upi:[a-f0-9]+$"))

    menu_filter = filters.Regex(keyboards.MENU_BUTTONS_REGEX)
    app.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(upi_paid_start, pattern=r"^upi:paid:[a-f0-9]+$")],
            states={
                UPI_UTR: [
                    MessageHandler(menu_filter, _exit_on_menu),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, upi_got_utr),
                ],
                UPI_SHOT: [
                    MessageHandler(menu_filter, _exit_on_menu),
                    MessageHandler(filters.PHOTO, upi_got_shot),
                    CommandHandler("skip", upi_skip),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, upi_need_photo),
                ],
            },
            fallbacks=[CommandHandler("cancel", upi_cancel)],
            name="upi_submit",
            persistent=False,
        )
    )
