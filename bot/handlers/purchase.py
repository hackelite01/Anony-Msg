"""🛒 Buy Key — plan picking, Stars invoice, fulfillment."""
import json
import logging
from math import ceil
from types import SimpleNamespace

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
from services import db, key_generator, payments, promo
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
            "🛒 *Choose a plan*\n\nYou'll pick how to pay next.",
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


async def build_method_screen(plan: dict, user_id: int) -> tuple[str, object]:
    """What the user sees after picking a plan: the free-claim screen, or payment methods
    (each with its price, discounted if they applied a promo code)."""
    if plan.get("is_free"):
        claimed = await db.has_free_claim(user_id, str(plan["_id"]))
        text = f"🎁 *{plan['name']}* • {plan['sms_limit']} SMS\n\nThis plan is *free* — one per user."
        if claimed:
            text += "\n\n✅ You've already claimed it."
        return text, keyboards.free_plan_screen(str(plan["_id"]), claimed)

    plan = await promo.priced_plan(plan, user_id)
    methods = await payments.available_methods(plan)
    has_promos = await db.has_active_promos()
    if not methods and not has_promos:
        return (
            "❌ No payment method is available for this plan right now. Please contact admin.",
            keyboards.back_to_menu(),
        )
    note = (
        f"\n🎟 Code `{plan['promo_code']}` applied: {plan['promo_percent']}% off"
        if plan.get("promo_code")
        else ""
    )
    body = (
        "Choose how to pay:"
        if methods
        else "❌ No payment method is available for this plan right now."
    )
    text = f"💳 *{plan['name']}* • {plan['sms_limit']} SMS{note}\n\n{body}"
    return text, keyboards.payment_methods(plan, methods, promo_button=has_promos)


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

    # Always ask for the payment method (prices are on the buttons). A method is listed only if
    # the admin enabled it, it is configured, and this plan has a price in that currency.
    text, markup = await build_method_screen(plan, user_id)
    await q.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)


async def on_pay_stars(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if await db.is_banned(update.effective_user.id):
        return
    plan = await db.get_plan(q.data.split(":", 2)[2])
    if not plan or not plan.get("active"):
        await q.edit_message_text(
            "❌ That plan is no longer available.",
            reply_markup=keyboards.back_to_menu(),
        )
        return
    if payments.STARS not in await payments.available_methods(plan):
        await q.edit_message_text(
            "❌ Stars payments aren't available for this plan right now.",
            reply_markup=keyboards.back_to_menu(),
        )
        return
    plan = await promo.priced_plan(plan, update.effective_user.id)
    await _send_stars_invoice(update, context, plan)


async def _send_stars_invoice(
    update: Update, context: ContextTypes.DEFAULT_TYPE, plan: dict
) -> None:
    q = update.callback_query
    user_id = update.effective_user.id
    payload = json.dumps(
        {"plan_id": str(plan["_id"]), "telegram_id": user_id, "promo": plan.get("promo_code")}
    )
    desc = f"{plan['sms_limit']} SMS sends"
    if plan.get("promo_code"):
        desc += f" (🎟 {plan['promo_code']} −{plan['promo_percent']}%)"

    try:
        await context.bot.send_invoice(
            chat_id=update.effective_chat.id,
            title=f"🔑 {plan['name']} Plan",
            description=desc,
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
    await promo.finalize(payload.get("promo"), user.id)

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


async def assign_key_for_request(
    context, user, request: dict, plan: dict, *, log_it: bool = True
) -> str:
    """Generate a code, reserve provider keys, create the user key, approve the request.

    Returns the code; raises on failure. Shared by non-Stars payment paths (TON).
    """
    code = await key_generator.generate_unique_code()
    reserved_ids = await db.reserve_keys_for(code, request["keys_needed"])
    await db.create_user_key(code, user.id, plan, reserved_ids)
    await db.set_purchase_status(request["_id"], "approved")
    if log_it:
        await activity_log.key_assigned(
            context, user, code, plan["name"], plan["sms_limit"], auto=True
        )
    return code


async def deliver_paid_request(
    bot,
    user,
    plan: dict,
    request: dict,
    *,
    allow_auto: bool = True,
    pending_text: str | None = None,
) -> None:
    """After a payment is confirmed: assign the key now (auto-approve) or queue it for an admin.

    allow_auto=False forces manual approval (UPI). `pending_text` overrides what the user is
    told while the request waits for an admin.
    """
    ctx = SimpleNamespace(bot=bot)
    cfg = await db.get_config()
    pool_ok = await db.free_keys_count() >= request["keys_needed"]

    async def tell(text: str, markdown: bool = False, markup=None) -> None:
        try:
            await bot.send_message(
                chat_id=user.id,
                text=text,
                reply_markup=markup or keyboards.back_to_menu(),
                parse_mode=ParseMode.MARKDOWN if markdown else None,
            )
        except TelegramError as e:
            log.warning("notify user %s failed: %s", user.id, e)

    if allow_auto and pool_ok and cfg.get("auto_approve"):
        try:
            code = await assign_key_for_request(ctx, user, request, plan)
        except Exception:
            log.exception("Auto-fulfil failed for request %s", request["_id"])
            await tell("⚠️ Could not assign your key automatically. Admin has been notified.")
            await _notify_admins_pending(ctx, user, plan, request, pool_ok=False)
            return
        await tell(
            f"🎉 *Your key is ready!*\n\n"
            f"Code: `{code}`\n"
            f"Plan: {plan['name']}\n"
            f"SMS: {plan['sms_limit']} sends\n\n"
            "Use /menu to start sending.",
            markdown=True,
        )
        return

    await _notify_admins_pending(ctx, user, plan, request, pool_ok=pool_ok)
    if pending_text:
        await tell(pending_text)
    elif pool_ok:
        await tell("⏳ Payment received! Your key will be assigned shortly.")
    else:
        admin_user = (cfg.get("admin_username") or "").lstrip("@")
        contact = f"@{admin_user}" if admin_user else "admin"
        await tell(
            f"✅ Payment received! Please contact {contact} to complete your setup.",
            markup=keyboards.contact_admin_button(admin_user or None),
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
            f"Paid: {payments.paid_label(request)}\n"
            f"Keys needed: {request['keys_needed']}\n"
            f"Request ID: `{request['_id']}`"
        )
    else:
        text = (
            "🚨 *ACTION NEEDED*\n\n"
            f"User: {uname} paid *{payments.paid_label(request)}* for *{plan['name']}*.\n"
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
        await q.edit_message_text(
            "⚠️ Still no keys available. Add keys first.",
            reply_markup=keyboards.admin_pk_menu(),
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

    manual_refund = request.get("payment_method") in payments.MANUAL_REFUND_METHODS
    refund_ok = True
    refund_err = None
    if manual_refund:
        # The Bot API can't refund on-chain or UPI payments: the admin sends it back manually.
        refund_ok = False
        refund_err = (
            f"{(request.get('payment_method') or '').upper()} payment — refund manually: "
            f"{payments.paid_label(request)}, ref {payments.payment_ref(request)}"
        )
    else:
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
                    "❌ Your purchase was rejected. Your payment will be returned "
                    "manually — please contact admin."
                    if manual_refund
                    else "❌ Your purchase was rejected. Refund failed automatically — "
                    "please contact admin."
                ),
            )
    except TelegramError as e:
        log.warning("notify reject failed: %s", e)

    await q.edit_message_text(
        f"{'💸 Refunded' if refund_ok else '⚠️ Rejected without refund'} — reason: {reason}"
        + (f"\nNote: {refund_err}" if refund_err else "")
    )


def register(app) -> None:
    app.add_handler(CallbackQueryHandler(on_buy_menu, pattern=r"^menu:buy$"))
    app.add_handler(
        MessageHandler(filters.Regex(rf"^{keyboards.BTN_BUY}$"), on_buy_menu)
    )
    app.add_handler(CallbackQueryHandler(on_buy_plan, pattern=r"^buy:[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(on_pay_stars, pattern=r"^pay:stars:[a-f0-9]+$"))
    app.add_handler(PreCheckoutQueryHandler(on_pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, on_successful_payment))
    app.add_handler(CallbackQueryHandler(admin_approve, pattern=r"^adm:approve:"))
    app.add_handler(CallbackQueryHandler(admin_reject, pattern=r"^adm:reject:"))
    app.add_handler(CallbackQueryHandler(admin_reject_reason, pattern=r"^adm:rsn:"))
