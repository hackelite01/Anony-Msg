"""₿ Pay with Bitcoin — order creation, on-chain verification, fulfillment.

Flow:
1. User picks a plan, then "Pay with Bitcoin" -> we create an order whose amount is the
   plan price plus a few extra sats, unique among open orders. Bitcoin has no memo, so that
   exact amount is what identifies the order.
2. A background watcher reads the receiving address (mempool.space / blockstream.info) and
   matches incoming outputs to open orders by exact amount. (txid, vout) is single-use.
3. Once the output has BTC_MIN_CONFIRMATIONS confirmations the order is claimed atomically
   (awaiting -> paid), a purchase request is created, and the key is assigned or queued for
   admin approval — same outcome as any other payment method.

Everything that mutates orders runs under one lock, so the watcher and the user's
"check now" button can never fulfil the same order twice.
"""
import asyncio
import html
import logging
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

from pymongo.errors import DuplicateKeyError
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import CallbackQueryHandler, ContextTypes

import config
from handlers import purchase
from services import btc, db, payments, promo
from utils import activity_log, keyboards

log = logging.getLogger(__name__)

CHECK_COOLDOWN_S = 15

_lock = asyncio.Lock()
_task: Optional[asyncio.Task] = None


# ---------- helpers ----------

async def _notify_user(bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except TelegramError as e:
        log.warning("BTC notify user %s failed: %s", chat_id, e)


async def _alert_admins(bot, html_text: str) -> None:
    for admin_id in config.ADMIN_USER_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=html_text, parse_mode=ParseMode.HTML)
        except TelegramError as e:
            log.warning("BTC admin alert to %s failed: %s", admin_id, e)


def _ts(dt: datetime) -> float:
    """Mongo returns naive UTC datetimes; treat them as UTC, not local time."""
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def _order_text(order: dict) -> str:
    n = config.BTC_MIN_CONFIRMATIONS
    return (
        "₿ <b>Pay with Bitcoin</b>\n\n"
        f"Plan: <b>{html.escape(order['plan_name'])}</b> • {order['sms_limit']} SMS\n\n"
        "Send <b>exactly</b>:\n"
        f"<code>{btc.fmt_btc_exact(order['amount_sats'])}</code> BTC\n\n"
        "to this address:\n"
        f"<code>{order['address']}</code>\n\n"
        "⚠️ The exact amount (all 8 decimals) is what identifies your order — "
        "a different amount can't be matched automatically.\n\n"
        f"Detected automatically; your key is released after {n} confirmation"
        f"{'s' if n != 1 else ''} (usually 10–30 min). "
        f"This order is valid for {config.BTC_ORDER_TTL_MINUTES} min."
    )


async def _new_order(user, plan: dict, address: str) -> Optional[dict]:
    """Create an order with an amount no other open order has (retries on collisions)."""
    base = plan["price_btc_sats"]
    for attempt in range(40):
        amount = base + btc.unique_offset(99 if attempt < 20 else 999)
        try:
            return await db.create_btc_order(
                user.id, user.username or "", plan, amount, base, address,
                promo=plan.get("promo_code"),
            )
        except DuplicateKeyError:
            continue
    return None


# ---------- user: start a BTC payment ----------

async def on_pay_btc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    if await db.is_banned(user.id):
        return

    plan = await db.get_plan(q.data.split(":", 2)[2])
    address = await btc.get_address()
    if (
        not plan
        or not plan.get("active")
        or not address
        or payments.BTC not in await payments.available_methods(plan)
    ):
        await q.edit_message_text(
            "❌ Bitcoin payment isn't available for this plan right now.",
            reply_markup=keyboards.back_to_menu(),
        )
        return

    plan = await promo.priced_plan(plan, user.id)  # discounted if the user applied a promo code
    since = db.now_utc() - timedelta(minutes=config.BTC_ORDER_TTL_MINUTES)
    order = await db.find_open_btc_order(user.id, plan["_id"], since)
    if order and (order["address"] != address or order["base_sats"] != plan["price_btc_sats"]):
        await db.cancel_btc_order(order["_id"], user.id)  # address/price changed: stale terms
        order = None
    if order is None:
        order = await _new_order(user, plan, address)
        if order is None:
            await q.edit_message_text(
                "⚠️ Too many open Bitcoin orders right now. Please try again in a few minutes.",
                reply_markup=keyboards.back_to_menu(),
            )
            return

    try:
        await q.edit_message_text(
            _order_text(order),
            reply_markup=keyboards.btc_order(str(order["_id"])),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise


async def on_btc_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    order = await db.get_btc_order(q.data.split(":", 2)[2])
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
    if now - context.user_data.get("btc_last_check", 0.0) < CHECK_COOLDOWN_S:
        await q.answer("Checking… give it a few seconds.")
        return
    context.user_data["btc_last_check"] = now

    try:
        await run_cycle(context.bot)
    except btc.BtcApiError as e:
        log.warning("BTC manual check failed: %s", e)
        await q.answer("Couldn't reach the Bitcoin network. Try again shortly.", show_alert=True)
        return

    order = await db.get_btc_order(order["_id"])
    if order and order["status"] in ("paid", "fulfilled"):
        await q.answer("✅ Payment received!", show_alert=True)
    elif order and order.get("seen_txid"):
        await q.answer(
            "Your payment is in the mempool — waiting for confirmations. We'll message you.",
            show_alert=True,
        )
    else:
        await q.answer(
            "Not seen on-chain yet. Transfers can take a minute to appear — try again shortly.",
            show_alert=True,
        )


async def on_btc_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    ok = await db.cancel_btc_order(q.data.split(":", 2)[2], update.effective_user.id)
    if not ok:
        await q.answer("This order can't be cancelled (already paid or closed).", show_alert=True)
        return
    await q.answer()
    await q.edit_message_text(
        "❌ Order cancelled. If you already sent Bitcoin, contact admin with your transaction ID.",
        reply_markup=keyboards.back_to_menu(),
    )


# ---------- watcher ----------

async def run_cycle(bot) -> None:
    """One verification pass. Raises btc.BtcApiError if every provider is unreachable."""
    async with _lock:
        for order in await db.list_paid_unfulfilled_btc_orders():
            try:
                await _fulfill_order(bot, order)
            except Exception:
                log.exception("BTC retry fulfil failed for order %s", order["_id"])

        since = db.now_utc() - timedelta(hours=config.BTC_WATCH_HOURS)
        orders = await db.list_watchable_btc_orders(since)
        if not orders:
            return
        address = await btc.get_address()
        if not address:
            return

        by_amount = {o["amount_sats"]: o for o in orders}
        outputs = await btc.fetch_incoming(address)
        for out in outputs:
            try:
                await _process_output(bot, out, by_amount)
            except Exception:
                log.exception("BTC output processing failed: %s:%s", out.get("txid"), out.get("vout"))


def _fresh_for(order: dict, out: dict) -> bool:
    """Reject outputs that predate the order (can't be a payment for it)."""
    bt = out.get("block_time")
    return bt is None or bt >= _ts(order["created_at"]) - btc.TIME_SLACK_S


async def _process_output(bot, out: dict, by_amount: dict) -> None:
    order = by_amount.get(out["value"])

    if order is not None:
        if not _fresh_for(order, out):
            return
        if out["confirmations"] < config.BTC_MIN_CONFIRMATIONS:
            if await db.mark_btc_seen(order["_id"], out["txid"]):
                n = config.BTC_MIN_CONFIRMATIONS
                await _notify_user(
                    bot,
                    order["telegram_id"],
                    "👀 We can see your Bitcoin payment. It needs "
                    f"{n} confirmation{'s' if n != 1 else ''} — we'll message you when it's done.",
                )
            return
        if not await db.record_btc_output(out, "claimed", order["_id"]):
            return  # this exact output was already used
        claimed = await db.claim_btc_order(order["_id"], out["txid"], out["vout"], out["value"])
        if claimed is None:
            await _alert_admins(
                bot,
                "⚠️ <b>BTC payment for a closed order</b>\n"
                f"Amount: {btc.fmt_btc_exact(out['value'])} BTC\n"
                f"Tx: <code>{out['txid']}</code>:{out['vout']}\n"
                "The order was cancelled or already paid — handle manually.",
            )
            return
        by_amount.pop(out["value"], None)
        await _fulfill_order(bot, claimed)
        return

    # No exact match: tell admins once if it's suspiciously close to an open order
    # (e.g. the sender's wallet deducted the network fee from the amount).
    candidates = [o for o in by_amount.values() if _fresh_for(o, out)]
    near = min(candidates, key=lambda o: abs(o["amount_sats"] - out["value"]), default=None)
    if near is None or abs(near["amount_sats"] - out["value"]) > btc.NEAR_TOLERANCE_SATS:
        return
    if await db.record_btc_output(out, "near_miss", near["_id"]):
        await _alert_admins(
            bot,
            "⚠️ <b>BTC payment close to an open order</b>\n"
            f"Order: {html.escape(near['plan_name'])} expects "
            f"{btc.fmt_btc_exact(near['amount_sats'])} BTC\n"
            f"Received: {btc.fmt_btc_exact(out['value'])} BTC\n"
            f"Tx: <code>{out['txid']}</code>:{out['vout']}\n"
            f"Customer id: <code>{near['telegram_id']}</code>\n"
            "Not fulfilled automatically — handle manually.",
        )
        await _notify_user(
            bot,
            near["telegram_id"],
            f"⚠️ We received {btc.fmt_btc_exact(out['value'])} BTC, but your order needs exactly "
            f"{btc.fmt_btc_exact(near['amount_sats'])} BTC. Please contact admin.",
        )


async def _fulfill_order(bot, order: dict) -> None:
    ctx = SimpleNamespace(bot=bot)
    user = SimpleNamespace(id=order["telegram_id"], username=order.get("username") or None)

    plan = await db.get_plan(order["plan_id"])
    if plan is None:
        await db.set_btc_order_status(order["_id"], "error")
        await _alert_admins(
            bot,
            "🚨 <b>BTC order paid but plan is missing</b>\n"
            f"Tx: <code>{order['txid']}</code>:{order['vout']}",
        )
        return

    request = await db.create_btc_purchase_request(order, plan)
    await promo.finalize(order.get("promo"), order["telegram_id"])
    await db.set_btc_order_status(order["_id"], "fulfilled")  # a purchase request exists from here
    if request["status"] != "pending":
        return

    await activity_log.btc_payment_received(
        ctx, user, plan["name"], order["paid_sats"], order["txid"]
    )
    await purchase.deliver_paid_request(bot, user, plan, request, allow_auto=True)


async def _watch_loop(bot) -> None:
    log.info("BTC watcher started (every %ss)", config.BTC_POLL_SECONDS)
    while True:
        try:
            await run_cycle(bot)
        except asyncio.CancelledError:
            raise
        except btc.BtcApiError as e:
            log.warning("BTC watcher: %s", e)
        except Exception:
            log.exception("BTC watcher crashed this cycle")
        await asyncio.sleep(max(10, config.BTC_POLL_SECONDS))


def start_watcher(application) -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_watch_loop(application.bot), name="btc-watcher")


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
    app.add_handler(CallbackQueryHandler(on_pay_btc, pattern=r"^pay:btc:[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(on_btc_check, pattern=r"^btc:chk:[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(on_btc_cancel, pattern=r"^btc:cancel:[a-f0-9]+$"))
