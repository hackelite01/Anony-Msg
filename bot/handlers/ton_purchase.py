"""💎 Pay with TON — order creation, on-chain verification, fulfillment.

Flow:
1. User picks a plan, then "Pay with TON" -> we create an order with a unique
   memo and show wallet + exact amount + memo (plus a Tonkeeper deep link).
2. A background watcher polls TON Center for incoming transfers to our wallet
   and matches them to open orders by memo, requiring value >= price.
3. A match atomically claims the order (awaiting -> paid), creates a purchase
   request, then either auto-assigns a key or asks admins to approve — the same
   outcome as a Stars payment.

Everything that mutates orders runs under one lock so the watcher and the
user's "check now" button can never fulfil the same order twice.
"""
import asyncio
import html
import logging
import time
from datetime import timedelta
from types import SimpleNamespace
from typing import Optional
from urllib.parse import quote

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import CallbackQueryHandler, ContextTypes

import config
from handlers import purchase
from services import db, ton
from utils import activity_log, keyboards

log = logging.getLogger(__name__)

CHECK_COOLDOWN_S = 8

_lock = asyncio.Lock()
_task: Optional[asyncio.Task] = None


# ---------- small helpers ----------

async def _notify_user(bot, chat_id: int, text: str, markup=None, markdown: bool = False) -> None:
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=markup,
            parse_mode=ParseMode.MARKDOWN if markdown else None,
        )
    except TelegramError as e:
        log.warning("TON notify user %s failed: %s", chat_id, e)


async def _alert_admins(bot, html_text: str) -> None:
    for admin_id in config.ADMIN_USER_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=html_text, parse_mode=ParseMode.HTML)
        except TelegramError as e:
            log.warning("TON admin alert to %s failed: %s", admin_id, e)


def _order_text(order: dict) -> str:
    return (
        "💎 <b>Pay with TON</b>\n\n"
        f"Plan: <b>{html.escape(order['plan_name'])}</b> • {order['sms_limit']} SMS\n"
        f"Amount: <code>{ton.fmt_ton(order['amount_nano'])}</code> TON\n\n"
        "Send <b>exactly</b> this amount to:\n"
        f"<code>{ton.friendly(order['wallet'])}</code>\n\n"
        "⚠️ <b>Include this comment</b> — without it your payment can't be matched:\n"
        f"<code>{order['memo']}</code>\n\n"
        "Payment is detected automatically, usually within a minute. "
        f"This order is valid for {config.TON_ORDER_TTL_MINUTES} min."
    )


def _pay_url(order: dict) -> str:
    return (
        f"https://app.tonkeeper.com/transfer/{ton.friendly(order['wallet'])}"
        f"?amount={order['amount_nano']}&text={quote(order['memo'])}"
    )


# ---------- user: start a TON payment ----------

async def on_pay_ton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    if await db.is_banned(user.id):
        return

    plan = await db.get_plan(q.data.split(":", 2)[2])
    wallet = await ton.get_wallet()
    if (
        not plan
        or not plan.get("active")
        or not plan.get("price_ton_nano")
        or not wallet
        or not await ton.is_enabled()
    ):
        await q.edit_message_text(
            "❌ TON payment isn't available for this plan right now.",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    since = db.now_utc() - timedelta(minutes=config.TON_ORDER_TTL_MINUTES)
    order = await db.find_open_ton_order(user.id, plan["_id"], since)
    if order and (order["wallet"] != wallet or order["amount_nano"] != plan["price_ton_nano"]):
        # wallet or price changed since this order was made -> don't honour stale terms
        await db.cancel_ton_order(order["_id"], user.id)
        order = None
    if order is None:
        order = await db.create_ton_order(
            telegram_id=user.id,
            username=user.username or "",
            plan=plan,
            amount_nano=plan["price_ton_nano"],
            memo=ton.new_memo(),
            wallet=wallet,
        )

    try:
        await q.edit_message_text(
            _order_text(order),
            reply_markup=keyboards.ton_order(str(order["_id"]), _pay_url(order)),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def on_ton_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    order = await db.get_ton_order(q.data.split(":", 2)[2])
    if not order or order["telegram_id"] != update.effective_user.id:
        await q.answer("Order not found.", show_alert=True)
        return
    if order["status"] in ("paid", "fulfilled"):
        await q.answer("✅ Payment already received — check your messages.", show_alert=True)
        return
    if order["status"] != "awaiting":
        await q.answer("This order is no longer active.", show_alert=True)
        return

    now = time.monotonic()
    if now - context.user_data.get("ton_last_check", 0.0) < CHECK_COOLDOWN_S:
        await q.answer("Checking… give it a few seconds.")
        return
    context.user_data["ton_last_check"] = now

    try:
        await run_cycle(context.bot)
    except ton.TonApiError as e:
        log.warning("TON manual check failed: %s", e)
        await q.answer("Couldn't reach the TON network. Try again shortly.", show_alert=True)
        return

    order = await db.get_ton_order(order["_id"])
    if order and order["status"] in ("paid", "fulfilled"):
        await q.answer("✅ Payment received!", show_alert=True)
    else:
        await q.answer(
            "Not seen on-chain yet. Transfers usually confirm within a minute — try again shortly.",
            show_alert=True,
        )


async def on_ton_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    ok = await db.cancel_ton_order(q.data.split(":", 2)[2], update.effective_user.id)
    if not ok:
        await q.answer("This order can't be cancelled (already paid or closed).", show_alert=True)
        return
    await q.answer()
    await q.edit_message_text(
        "❌ Order cancelled. If you already sent TON, contact admin with your transaction.",
        reply_markup=keyboards.back_to_menu(),
    )


# ---------- watcher ----------

async def run_cycle(bot) -> None:
    """One verification pass. Raises ton.TonApiError if TON Center is unreachable."""
    async with _lock:
        # Retry orders that were claimed but crashed before a purchase request existed.
        for order in await db.list_paid_unfulfilled_ton_orders():
            try:
                await _fulfill_order(bot, order)
            except Exception:
                log.exception("TON retry fulfil failed for order %s", order["_id"])

        since = db.now_utc() - timedelta(hours=config.TON_WATCH_HOURS)
        orders = await db.list_watchable_ton_orders(since)
        if not orders:
            return
        wallet = await ton.get_wallet()
        if not wallet:
            return

        by_memo = {o["memo"]: o for o in orders}
        txs = await ton.fetch_incoming(wallet)
        for tx in reversed(txs):  # oldest first
            try:
                await _process_tx(bot, tx, by_memo)
            except Exception:
                log.exception("TON tx processing failed: %s", tx.get("hash"))


async def _process_tx(bot, tx: dict, by_memo: dict) -> None:
    memo = tx["memo"]
    order = by_memo.get(memo)

    if order is None:
        # Our memo format but no open order (cancelled / expired / already paid): tell admins once.
        if memo.startswith(ton.MEMO_PREFIX) and await db.record_ton_payment(tx, "unmatched"):
            await _alert_admins(
                bot,
                "⚠️ <b>TON payment with no open order</b>\n"
                f"Memo: <code>{html.escape(memo)}</code>\n"
                f"Amount: {ton.fmt_ton(tx['value'])} TON\n"
                f"Tx: <code>{tx['hash']}</code>\n"
                "The order was cancelled, expired or already paid — handle manually.",
            )
        return

    if tx["value"] < order["amount_nano"]:
        if await db.record_ton_payment(tx, "underpaid", order["_id"]):
            await _alert_admins(
                bot,
                "⚠️ <b>TON underpayment</b>\n"
                f"Order memo: <code>{memo}</code> ({html.escape(order['plan_name'])})\n"
                f"Expected {ton.fmt_ton(order['amount_nano'])} TON, "
                f"received {ton.fmt_ton(tx['value'])} TON\n"
                f"Tx: <code>{tx['hash']}</code>\n"
                "Not fulfilled — handle manually.",
            )
            await _notify_user(
                bot,
                order["telegram_id"],
                f"⚠️ We received {ton.fmt_ton(tx['value'])} TON but this order needs "
                f"{ton.fmt_ton(order['amount_nano'])} TON. Please contact admin.",
            )
        return

    claimed = await db.claim_ton_order(order["_id"], tx["hash"], tx["value"])
    if claimed is None:
        return  # someone else already claimed it
    by_memo.pop(memo, None)
    await _fulfill_order(bot, claimed)


async def _fulfill_order(bot, order: dict) -> None:
    ctx = SimpleNamespace(bot=bot)
    user = SimpleNamespace(id=order["telegram_id"], username=order.get("username") or None)
    uid = order["telegram_id"]

    plan = await db.get_plan(order["plan_id"])
    if plan is None:
        await db.set_ton_order_status(order["_id"], "error")
        await _alert_admins(
            bot,
            "🚨 <b>TON order paid but plan is missing</b>\n"
            f"Order memo: <code>{order['memo']}</code>\nTx: <code>{order['tx_hash']}</code>",
        )
        return

    request = await db.create_ton_purchase_request(order, plan)
    await db.set_ton_order_status(order["_id"], "fulfilled")  # a purchase request exists from here on
    if request["status"] != "pending":
        return

    await activity_log.ton_payment_received(
        ctx, user, plan["name"], order["paid_nano"], order["tx_hash"]
    )

    cfg = await db.get_config()
    pool_ok = await db.free_keys_count() >= request["keys_needed"]

    if pool_ok and cfg.get("auto_approve"):
        try:
            code = await purchase.assign_key_for_request(ctx, user, request, plan)
        except Exception:
            log.exception("TON auto-fulfil failed for order %s", order["_id"])
            await _notify_user(
                bot, uid, "⚠️ Could not assign your key automatically. Admin has been notified."
            )
            await purchase._notify_admins_pending(ctx, user, plan, request, pool_ok=False)
            return
        await _notify_user(
            bot,
            uid,
            f"🎉 *Your key is ready!*\n\n"
            f"Code: `{code}`\n"
            f"Plan: {plan['name']}\n"
            f"SMS: {plan['sms_limit']} sends\n\n"
            "Use /menu to start sending.",
            keyboards.back_to_menu(),
            markdown=True,
        )
        return

    await purchase._notify_admins_pending(ctx, user, plan, request, pool_ok=pool_ok)
    if pool_ok:
        await _notify_user(
            bot, uid, "⏳ Payment received! Your key will be assigned shortly.",
            keyboards.back_to_menu(),
        )
    else:
        admin_user = (cfg.get("admin_username") or "").lstrip("@")
        contact = f"@{admin_user}" if admin_user else "admin"
        await _notify_user(
            bot, uid,
            f"✅ Payment received! Please contact {contact} to complete your setup.",
            keyboards.contact_admin_button(admin_user or None),
        )


async def _watch_loop(bot) -> None:
    log.info("TON watcher started (every %ss)", config.TON_POLL_SECONDS)
    while True:
        try:
            await run_cycle(bot)
        except asyncio.CancelledError:
            raise
        except ton.TonApiError as e:
            log.warning("TON watcher: %s", e)
        except Exception:
            log.exception("TON watcher crashed this cycle")
        await asyncio.sleep(max(5, config.TON_POLL_SECONDS))


def start_watcher(application) -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_watch_loop(application.bot), name="ton-watcher")


async def stop_watcher() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None


def register(app) -> None:
    app.add_handler(CallbackQueryHandler(on_pay_ton, pattern=r"^pay:ton:[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(on_ton_check, pattern=r"^ton:chk:[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(on_ton_cancel, pattern=r"^ton:cancel:[a-f0-9]+$"))
