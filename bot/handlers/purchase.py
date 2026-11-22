"""🛒 Buy Key — plan picking, Stars invoice, fulfillment."""
import json
import logging
from math import ceil

from bson import ObjectId
from telegram import LabeledPrice, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

import config
from services import db, key_generator
from utils import activity_log, keyboards

log = logging.getLogger(__name__)


# ---------- buy entry ----------

async def on_buy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await db.is_banned(update.effective_user.id):
        return

    plans = await db.list_plans(active_only=True)
    if not plans:
        body, markup = (
            "🛒 No plans available right now. Contact admin.",
            keyboards.back_to_menu(),
        )
    else:
        body, markup = (
            "🛒 *Choose a plan*\n\nPay in Telegram Stars ⭐.",
            keyboards.plans_keyboard(plans),
        )

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(
                body, reply_markup=markup, parse_mode=ParseMode.MARKDOWN
            )
        except BadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise
    else:
        await update.effective_message.reply_text(
            body, reply_markup=markup, parse_mode=ParseMode.MARKDOWN
        )


async def on_buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user_id = update.effective_user.id
    if await db.is_banned(user_id):
        return

    plan_id = q.data.split(":", 1)[1]
    plan = await db.get_plan(plan_id)
    if not plan or not plan.get("active"):
        await q.edit_message_text(
            "❌ That plan is no longer available.",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    payload = json.dumps(
        {"plan_id": str(plan["_id"]), "telegram_id": user_id}
    )

    try:
        await context.bot.send_invoice(
            chat_id=update.effective_chat.id,
            title=f"🔑 {plan['name']} Plan",
            description=f"{plan['sms_limit']} SMS sends",
            payload=payload,
            provider_token="",  # Stars uses empty provider token
            currency="XTR",
            prices=[LabeledPrice(f"{plan['name']} Plan", plan["price_stars"])],
        )
    except TelegramError as e:
        log.exception("send_invoice failed: %s", e)
        await q.message.reply_text(
            "❌ Could not start checkout. Try again later.",
            reply_markup=keyboards.back_to_menu(),
        )


# ---------- pre-checkout ----------

async def on_pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.pre_checkout_query
    try:
        await q.answer(ok=True)
    except TelegramError as e:
        log.warning("pre_checkout answer failed: %s", e)


# ---------- successful payment ----------

async def on_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sp = update.message.successful_payment
    user = update.effective_user

    try:
        payload = json.loads(sp.invoice_payload)
        plan_id = payload["plan_id"]
    except Exception:
        log.exception("Bad invoice payload: %s", sp.invoice_payload)
        await update.message.reply_text(
            "⚠️ Payment received but checkout payload was malformed. Contact admin."
        )
        return

    plan = await db.get_plan(plan_id)
    if not plan:
        await update.message.reply_text(
            "⚠️ Payment received but plan no longer exists. Contact admin for a refund."
        )
        return

    request = await db.create_purchase_request(
        telegram_id=user.id,
        username=user.username or "",
        plan=plan,
        stars_paid=sp.total_amount,
        payment_charge_id=sp.telegram_payment_charge_id,
    )
    await activity_log.payment_received(context, user, plan["name"], sp.total_amount)

    cfg = await db.get_config()
    keys_needed = request["keys_needed"]
    free = await db.free_keys_count()

    pool_ok = free >= keys_needed
    auto_approve = bool(cfg.get("auto_approve"))

    if pool_ok and auto_approve:
        await _auto_fulfill(update, context, request, plan)
        return

    # Notify admins either way
    await _notify_admins_pending(context, user, plan, request, pool_ok=pool_ok)

    if pool_ok:
        await update.message.reply_text(
            "⏳ Payment received! Your key will be assigned shortly.",
            reply_markup=keyboards.back_to_menu(),
        )
    else:
        admin_user = (cfg.get("admin_username") or "").lstrip("@")
        contact = f"@{admin_user}" if admin_user else "admin"
        await update.message.reply_text(
            f"✅ Payment received! Please contact {contact} to complete your setup.",
            reply_markup=keyboards.contact_admin_button(admin_user or None),
        )


async def _auto_fulfill(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    request: dict,
    plan: dict,
) -> None:
    user = update.effective_user
    try:
        code = await key_generator.generate_unique_code()
        reserved_ids = await db.reserve_keys_for(code, request["keys_needed"])
        await db.create_user_key(code, user.id, plan, reserved_ids)
        await db.set_purchase_status(request["_id"], "approved")
    except Exception as e:
        log.exception("Auto-fulfill failed: %s", e)
        await update.message.reply_text(
            "⚠️ Could not assign your key automatically. Admin has been notified."
        )
        await _notify_admins_pending(context, user, plan, request, pool_ok=False)
        return

    await activity_log.key_assigned(context, user, code, plan["name"], plan["sms_limit"], auto=True)

    await update.message.reply_text(
        f"🎉 *Your key is ready!*\n\n"
        f"Code: `{code}`\n"
        f"Plan: {plan['name']}\n"
        f"SMS: {plan['sms_limit']} sends\n\n"
        "Use /menu to start sending.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboards.back_to_menu(),
    )


async def _notify_admins_pending(
    context: ContextTypes.DEFAULT_TYPE,
    user,
    plan: dict,
    request: dict,
    pool_ok: bool,
) -> None:
    uname = f"@{user.username}" if user.username else f"id={user.id}"
    if pool_ok:
        text = (
            "🔔 *New purchase awaits approval*\n\n"
            f"User: {uname}\n"
            f"Plan: {plan['name']} ({plan['sms_limit']} SMS)\n"
            f"Paid: {request['stars_paid']}⭐\n"
            f"Keys needed: {request['keys_needed']}\n"
            f"Request ID: `{request['_id']}`"
        )
    else:
        text = (
            "🚨 *ACTION NEEDED*\n\n"
            f"User: {uname} paid *{request['stars_paid']}⭐* for *{plan['name']}*.\n"
            f"Keys needed: {request['keys_needed']} — *no keys available*.\n"
            "Add keys, then approve manually.\n\n"
            f"Request ID: `{request['_id']}`"
        )
    markup = keyboards.admin_request_decision(str(request["_id"]))
    for admin_id in config.ADMIN_USER_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                reply_markup=markup,
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError as e:
            log.warning("Admin notify failed for %s: %s", admin_id, e)


# ---------- admin approve / reject (used by purchase flow) ----------

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not config.is_admin(update.effective_user.id):
        try:
            await activity_log.unauthorized_admin_attempt(
                context, update.effective_user, q.data or "purchase admin callback"
            )
        except Exception:
            pass
        await q.answer("🚫 Not authorized.", show_alert=True)
        return

    req_id = q.data.split(":", 2)[2]
    request = await db.get_purchase_request(req_id)
    if not request:
        await q.edit_message_text("❌ Request not found.")
        return
    if request["status"] != "pending":
        await q.edit_message_text(f"⚠️ Already {request['status']}.")
        return

    keys_needed = request["keys_needed"]
    free = await db.free_keys_count()
    if free < keys_needed:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        await q.edit_message_text(
            "⚠️ Still no keys available. Add keys first.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("➕ Add Keys", callback_data="adm:pk:add")]]
            ),
        )
        return

    plan = await db.get_plan(request["plan_id"])
    if not plan:
        await q.edit_message_text("❌ Plan no longer exists; cannot fulfill.")
        return

    try:
        code = await key_generator.generate_unique_code()
        reserved_ids = await db.reserve_keys_for(code, keys_needed)
        await db.create_user_key(code, request["telegram_id"], plan, reserved_ids)
        await db.set_purchase_status(req_id, "approved")
    except Exception as e:
        log.exception("Manual approve failed: %s", e)
        await q.edit_message_text(f"❌ Failed to assign: {e}")
        return

    await activity_log.key_assigned(
        context,
        {"telegram_id": request["telegram_id"], "username": request.get("username")},
        code,
        plan["name"],
        plan["sms_limit"],
        auto=False,
    )

    try:
        await context.bot.send_message(
            chat_id=request["telegram_id"],
            text=(
                f"🎉 *Your key is ready!*\n\n"
                f"Code: `{code}`\n"
                f"Plan: {plan['name']}\n"
                f"SMS: {plan['sms_limit']} sends\n\n"
                "Use /menu to start sending."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError as e:
        log.warning("Notify user failed: %s", e)

    await q.edit_message_text(
        f"✅ Approved. Key `{code}` assigned to user {request['telegram_id']}.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def admin_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not config.is_admin(update.effective_user.id):
        try:
            await activity_log.unauthorized_admin_attempt(
                context, update.effective_user, q.data or "purchase admin callback"
            )
        except Exception:
            pass
        await q.answer("🚫 Not authorized.", show_alert=True)
        return

    req_id = q.data.split(":", 2)[2]
    request = await db.get_purchase_request(req_id)
    if not request:
        await q.edit_message_text("❌ Request not found.")
        return
    if request["status"] != "pending":
        await q.edit_message_text(f"⚠️ Already {request['status']}.")
        return

    await q.edit_message_text(
        "Choose rejection reason:",
        reply_markup=keyboards.admin_reject_reasons(req_id),
    )


async def admin_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not config.is_admin(update.effective_user.id):
        try:
            await activity_log.unauthorized_admin_attempt(
                context, update.effective_user, q.data or "purchase admin callback"
            )
        except Exception:
            pass
        await q.answer("🚫 Not authorized.", show_alert=True)
        return

    parts = q.data.split(":")
    # adm:rsn:{req_id}:{reason}
    req_id = parts[2]
    reason = parts[3]
    request = await db.get_purchase_request(req_id)
    if not request or request["status"] != "pending":
        await q.edit_message_text("⚠️ Request no longer pending.")
        return

    refund_ok = True
    refund_err = None
    try:
        await context.bot.refund_star_payment(
            user_id=request["telegram_id"],
            telegram_payment_charge_id=request["payment_charge_id"],
        )
    except TelegramError as e:
        log.exception("refund failed: %s", e)
        refund_ok = False
        refund_err = str(e)

    await db.set_purchase_status(req_id, "refunded" if refund_ok else "rejected", reason)

    if refund_ok:
        await activity_log.refund_issued(
            context,
            {"telegram_id": request["telegram_id"], "username": request.get("username")},
            request.get("plan_name", "?"),
            request.get("stars_paid", 0),
            reason,
        )

    try:
        if refund_ok:
            await context.bot.send_message(
                chat_id=request["telegram_id"],
                text=(
                    "❌ Your purchase was not fulfilled.\n"
                    "Stars have been refunded."
                ),
            )
        else:
            await context.bot.send_message(
                chat_id=request["telegram_id"],
                text=(
                    "❌ Your purchase was rejected. Refund failed automatically — "
                    "please contact admin."
                ),
            )
    except TelegramError as e:
        log.warning("notify reject failed: %s", e)

    await q.edit_message_text(
        f"{'💸 Refunded' if refund_ok else '⚠️ Rejected without refund'} — reason: {reason}"
        + (f"\nError: {refund_err}" if refund_err else "")
    )


def register(app) -> None:
    app.add_handler(CallbackQueryHandler(on_buy_menu, pattern=r"^menu:buy$"))
    app.add_handler(
        MessageHandler(filters.Regex(rf"^{keyboards.BTN_BUY}$"), on_buy_menu)
    )
    app.add_handler(CallbackQueryHandler(on_buy_plan, pattern=r"^buy:[a-f0-9]+$"))
    app.add_handler(PreCheckoutQueryHandler(on_pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, on_successful_payment))
    app.add_handler(CallbackQueryHandler(admin_approve, pattern=r"^adm:approve:"))
    app.add_handler(CallbackQueryHandler(admin_reject, pattern=r"^adm:reject:"))
    app.add_handler(CallbackQueryHandler(admin_reject_reason, pattern=r"^adm:rsn:"))
