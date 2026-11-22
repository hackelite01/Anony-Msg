"""/admin panel — restricted to ADMIN_USER_IDS."""
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

import config
from services import db, ton
from utils import activity_log, keyboards

log = logging.getLogger(__name__)


# Conversation states
ADD_PLAN_NAME, ADD_PLAN_LIMIT, ADD_PLAN_PRICE, ADD_PLAN_TON = range(4)
ADD_KEYS_INPUT = 10
BROADCAST_INPUT = 20
SET_ADMIN_USER = 30
SET_TON = 31
SET_PLAN_TON = 32


def _admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **kw):
        user = update.effective_user
        if user is None or not config.is_admin(user.id):
            # Log the attempt, then deny.
            what = func.__name__
            if update.callback_query and update.callback_query.data:
                what = f"{func.__name__} ({update.callback_query.data})"
            elif update.message and update.message.text:
                what = update.message.text[:60]
            try:
                await activity_log.unauthorized_admin_attempt(context, user, what)
            except Exception:
                pass
            if update.callback_query:
                await update.callback_query.answer(
                    "🚫 Not authorized.", show_alert=True
                )
            return ConversationHandler.END if "Conv" in func.__name__ else None
        return await func(update, context, *a, **kw)

    wrapper.__name__ = func.__name__
    return wrapper


# ---------- entry ----------

@_admin_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🛠 *Admin Panel*", reply_markup=keyboards.admin_main(), parse_mode=ParseMode.MARKDOWN
    )


@_admin_only
async def on_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🛠 *Admin Panel*", reply_markup=keyboards.admin_main(), parse_mode=ParseMode.MARKDOWN
    )


# ---------- users ----------

@_admin_only
async def on_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    total = await db.count_users()
    await q.edit_message_text(
        f"👥 *Users*\n\nTotal registered: *{total}*",
        reply_markup=keyboards.admin_back(),
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------- stats ----------

@_admin_only
async def on_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    sent = await db.count_logs("sent")
    failed = await db.count_logs("failed")
    revenue = await db.total_revenue_stars()
    revenue_ton = await db.total_revenue_ton_nano()
    ton_part = f" + *{ton.fmt_ton(revenue_ton)} TON*" if revenue_ton else ""
    active_keys = await db.count_active_keys()
    pool = await db.pool_status()

    if pool["free"] == 0:
        light = "🔴 empty"
    elif pool["free"] < 5:
        light = "🟡 low"
    else:
        light = "🟢 healthy"

    text = (
        "📊 *Stats*\n\n"
        f"📨 SMS sent: *{sent}*\n"
        f"❌ SMS failed: *{failed}*\n"
        f"💰 Revenue: *{revenue}⭐*{ton_part}\n"
        f"🔑 Active user keys: *{active_keys}*\n"
        f"🏊 Pool: {light} — {pool['free']} free / {pool['total']} total"
    )
    await q.edit_message_text(text, reply_markup=keyboards.admin_back(), parse_mode=ParseMode.MARKDOWN)


# ---------- provider keys ----------

@_admin_only
async def on_pk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🗄 *Provider Keys*",
        reply_markup=keyboards.admin_pk_menu(),
        parse_mode=ParseMode.MARKDOWN,
    )


@_admin_only
async def on_pk_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    keys = await db.list_provider_keys(limit=30)
    if not keys:
        await q.edit_message_text(
            "No provider keys yet.", reply_markup=keyboards.admin_pk_menu()
        )
        return
    lines = ["🗄 *Provider Keys* (latest 30)\n"]
    for k in keys:
        if k.get("exhausted"):
            status = "🟥 exhausted"
        elif k.get("reserved"):
            status = f"🟨 reserved → {k.get('reserved_for') or '?'}"
        else:
            status = "🟩 free"
        lines.append(
            f"`{str(k['_id'])[-6:]}` | {k.get('label', '?')} | {k['sms_used']}/{k['sms_capacity']} | {status}"
        )
    await q.edit_message_text(
        "\n".join(lines), reply_markup=keyboards.admin_pk_menu(), parse_mode=ParseMode.MARKDOWN
    )


@_admin_only
async def on_pk_pool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    p = await db.pool_status()
    if p["free"] == 0:
        light = "🔴"
    elif p["free"] < 5:
        light = "🟡"
    else:
        light = "🟢"
    await q.edit_message_text(
        f"{light} *Pool Status*\n\n"
        f"Total: {p['total']}\n"
        f"Free: {p['free']}\n"
        f"Reserved: {p['reserved']}\n"
        f"Exhausted: {p['exhausted']}",
        reply_markup=keyboards.admin_pk_menu(),
        parse_mode=ParseMode.MARKDOWN,
    )


@_admin_only
async def add_keys_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "➕ *Add Provider Keys*\n\n"
        "Send me keys, one per line, in this format:\n"
        "`LABEL|SEVEN_IO_KEY`\n\n"
        "Example:\n"
        "```\n"
        "Key47|abc123apikey\n"
        "Key48|def456apikey\n"
        "```\n\n"
        "Or /cancel.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADD_KEYS_INPUT


@_admin_only
async def add_keys_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text or ""
    items: list[tuple[str, str]] = []
    bad: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            bad.append(line)
            continue
        label, key = line.split("|", 1)
        label = label.strip()
        key = key.strip()
        if not label or not key:
            bad.append(line)
            continue
        items.append((label, key))

    if not items:
        await update.message.reply_text(
            "❌ No valid lines found. Send `LABEL|KEY` per line, or /cancel.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ADD_KEYS_INPUT

    added = await db.add_provider_keys(items)
    if added:
        await activity_log.provider_keys_added(context, update.effective_user, added)
    msg = f"✅ Added {added} key(s)."
    if bad:
        msg += f"\n⚠️ Skipped {len(bad)} invalid line(s)."
    await update.message.reply_text(msg, reply_markup=keyboards.admin_pk_menu())
    return ConversationHandler.END


# ---------- plans ----------

@_admin_only
async def on_plans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "📋 *Plans*",
        reply_markup=keyboards.admin_plans_menu(),
        parse_mode=ParseMode.MARKDOWN,
    )


@_admin_only
async def on_plans_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    plans = await db.list_plans(active_only=False)
    if not plans:
        await q.edit_message_text(
            "No plans configured.", reply_markup=keyboards.admin_plans_menu()
        )
        return
    lines = ["📋 *Plans*\n"]
    for p in plans:
        st = "✅" if p.get("active") else "❌"
        ton_part = (
            f" / {ton.fmt_ton(p['price_ton_nano'])} TON" if p.get("price_ton_nano") else ""
        )
        lines.append(
            f"{st} {p['name']} — {p['sms_limit']} SMS — {p['price_stars']}⭐{ton_part}"
        )
    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=keyboards.admin_plans_menu(),
        parse_mode=ParseMode.MARKDOWN,
    )


@_admin_only
async def on_plans_deact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    plans = await db.list_plans(active_only=True)
    if not plans:
        await q.edit_message_text(
            "No active plans to deactivate.",
            reply_markup=keyboards.admin_plans_menu(),
        )
        return
    await q.edit_message_text(
        "Choose plan to deactivate:",
        reply_markup=keyboards.admin_plans_picker(plans, "deact_do"),
    )


@_admin_only
async def on_plans_deact_do(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    plan_id = q.data.rsplit(":", 1)[1]
    ok = await db.deactivate_plan(plan_id)
    if ok:
        await activity_log.plan_deactivated(context, update.effective_user, plan_id)
    await q.edit_message_text(
        "✅ Plan deactivated." if ok else "❌ Plan not found.",
        reply_markup=keyboards.admin_plans_menu(),
    )


@_admin_only
async def on_plans_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    plans = await db.list_plans(active_only=False)
    if not plans:
        await q.edit_message_text("No plans to delete.", reply_markup=keyboards.admin_plans_menu())
        return
    await q.edit_message_text(
        "Choose a plan to delete:",
        reply_markup=keyboards.admin_plans_picker(plans, "del_do"),
    )


@_admin_only
async def on_plans_del_do(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    plan_id = q.data.rsplit(":", 1)[1]
    plan = await db.get_plan(plan_id)
    if not plan or plan.get("deleted"):
        await q.edit_message_text("❌ Plan not found.", reply_markup=keyboards.admin_plans_menu())
        return
    await q.edit_message_text(
        f"Delete plan '{plan['name']}' ({plan['sms_limit']} SMS)?\n\n"
        "It disappears from the lists and can't be bought. Existing user keys and "
        "payments already in progress are not affected.",
        reply_markup=keyboards.admin_confirm_delete_plan(plan_id),
    )


@_admin_only
async def on_plans_del_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    plan_id = q.data.rsplit(":", 1)[1]
    plan = await db.get_plan(plan_id)
    ok = await db.delete_plan(plan_id)
    if ok and plan:
        await activity_log.plan_deleted(context, update.effective_user, plan["name"])
    await q.edit_message_text(
        "✅ Plan deleted." if ok else "❌ Plan not found or already deleted.",
        reply_markup=keyboards.admin_plans_menu(),
    )


@_admin_only
async def on_plans_tonp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    plans = await db.list_plans(active_only=True)
    if not plans:
        await q.edit_message_text(
            "No active plans.", reply_markup=keyboards.admin_plans_menu()
        )
        return
    await q.edit_message_text(
        "Choose a plan to set its TON price:",
        reply_markup=keyboards.admin_plans_picker(plans, "tonp_do"),
    )


@_admin_only
async def set_plan_ton_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data["tonp_plan_id"] = q.data.rsplit(":", 1)[1]
    await q.edit_message_text(
        "💎 Send the TON price for this plan (e.g. 1.5),\n"
        "or 0 to remove TON payment for it. /cancel to abort."
    )
    return SET_PLAN_TON


async def set_plan_ton_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    plan_id = context.user_data.get("tonp_plan_id")
    if not plan_id:
        return ConversationHandler.END
    nano = None
    if raw.lower() not in ("0", "skip"):
        nano = ton.parse_ton_amount(raw)
        if nano is None:
            await update.message.reply_text(
                "❌ Send an amount like 1.5 (max 9 decimals), or 0 to remove. Try again or /cancel."
            )
            return SET_PLAN_TON
    ok = await db.set_plan_ton_price(plan_id, nano)
    context.user_data.pop("tonp_plan_id", None)
    if not ok:
        msg = "❌ Plan not found."
    elif nano is None:
        msg = "✅ TON payment removed for this plan."
    else:
        msg = f"✅ TON price set: {ton.fmt_ton(nano)} TON."
    await update.message.reply_text(msg, reply_markup=keyboards.admin_plans_menu())
    return ConversationHandler.END


# Add Plan conversation
@_admin_only
async def add_plan_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("➕ *Add Plan*\n\nStep 1/4: Plan name?", parse_mode=ParseMode.MARKDOWN)
    return ADD_PLAN_NAME


async def add_plan_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.message.text or "").strip()
    if not name or len(name) > 50:
        await update.message.reply_text("❌ Name must be 1–50 chars. Try again or /cancel.")
        return ADD_PLAN_NAME
    context.user_data["new_plan_name"] = name
    await update.message.reply_text("Step 2/4: SMS limit (integer > 0)?")
    return ADD_PLAN_LIMIT


async def add_plan_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        limit = int((update.message.text or "").strip())
        if limit <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Send a positive integer. Try again or /cancel.")
        return ADD_PLAN_LIMIT
    context.user_data["new_plan_limit"] = limit
    await update.message.reply_text("Step 3/4: Price in Stars (integer > 0)?")
    return ADD_PLAN_PRICE


async def add_plan_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        price = int((update.message.text or "").strip())
        if price <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Send a positive integer. Try again or /cancel.")
        return ADD_PLAN_PRICE
    context.user_data["new_plan_price"] = price
    await update.message.reply_text(
        "Step 4/4: Price in TON (e.g. 1.5), or 0 to skip TON for this plan?"
    )
    return ADD_PLAN_TON


async def add_plan_ton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    price_ton_nano = None
    if raw.lower() not in ("0", "skip"):
        price_ton_nano = ton.parse_ton_amount(raw)
        if price_ton_nano is None:
            await update.message.reply_text(
                "❌ Send an amount like 1.5 (max 9 decimals), or 0 to skip. Try again or /cancel."
            )
            return ADD_PLAN_TON
    name = context.user_data.pop("new_plan_name")
    limit = context.user_data.pop("new_plan_limit")
    price = context.user_data.pop("new_plan_price")
    plan = await db.add_plan(name, limit, price, price_ton_nano)
    ton_part = f", {ton.fmt_ton(price_ton_nano)} TON" if price_ton_nano else ""
    await update.message.reply_text(
        f"✅ Plan '{plan['name']}' added: {plan['sms_limit']} SMS, "
        f"{plan['price_stars']} Stars{ton_part}",
        reply_markup=keyboards.admin_plans_menu(),
    )
    await activity_log.plan_added(
        context, update.effective_user, plan["name"], plan["sms_limit"], plan["price_stars"]
    )
    return ConversationHandler.END


# ---------- pending ----------

@_admin_only
async def on_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    pending = await db.list_pending_requests(limit=20)
    if not pending:
        await q.edit_message_text("No pending requests.", reply_markup=keyboards.admin_back())
        return

    await q.edit_message_text(
        f"🔑 *Pending Requests* ({len(pending)})\n\nDecisions sent below.",
        reply_markup=keyboards.admin_back(),
        parse_mode=ParseMode.MARKDOWN,
    )
    for r in pending:
        uname = f"@{r['username']}" if r.get("username") else f"id={r['telegram_id']}"
        when = r["requested_at"].strftime("%Y-%m-%d %H:%M")
        text = (
            f"User: {uname}\n"
            f"Plan: {r['plan_name']} ({r['sms_limit']} SMS)\n"
            f"Paid: {ton.paid_label(r)}\n"
            f"Keys needed: {r['keys_needed']}\n"
            f"At: {when}\n"
            f"ID: `{r['_id']}`"
        )
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                reply_markup=keyboards.admin_request_decision(str(r["_id"])),
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError as e:
            log.warning("send pending failed: %s", e)


# ---------- logs ----------

@_admin_only
async def on_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    skip = int(q.data.rsplit(":", 1)[1])
    logs = await db.list_logs(skip=skip, limit=10)
    has_more = len(logs) == 10
    if not logs:
        await q.edit_message_text("No logs.", reply_markup=keyboards.admin_back())
        return
    lines = [f"📜 *Logs* (offset {skip})\n"]
    for entry in logs:
        ts = entry["timestamp"].strftime("%Y-%m-%d %H:%M")
        icon = "✅" if entry["status"] == "sent" else "❌"
        dest = entry["destination"]
        masked = dest[:6] + "…" if len(dest) > 8 else dest
        lines.append(
            f"📞 {masked} | `{entry.get('user_key_code','?')}` | {icon} | {ts}"
        )
    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=keyboards.admin_logs_pager(skip, has_more),
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------- broadcast ----------

@_admin_only
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "📢 Send me the broadcast message (text only). /cancel to abort."
    )
    return BROADCAST_INPUT


async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text or ""
    user_ids = await db.all_user_ids()
    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except TelegramError:
            failed += 1
    await activity_log.broadcast_sent(context, update.effective_user, sent, failed)
    await update.message.reply_text(
        f"📢 Broadcast complete.\nSent: {sent}\nFailed: {failed}",
        reply_markup=keyboards.admin_back(),
    )
    return ConversationHandler.END


# ---------- settings ----------

@_admin_only
async def on_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await _render_settings(q, context)


async def _render_settings(q, context):
    cfg = await db.get_config()
    text = (
        "⚙️ *Settings*\n\n"
        f"Auto-Approve: *{'ON' if cfg.get('auto_approve') else 'OFF'}*\n"
        f"Admin Username: *{cfg.get('admin_username') or '—'}*\n"
        f"TON Payments: *{'ON' if cfg.get('ton_enabled') else 'OFF'}*\n"
        f"TON Wallet: {'`' + cfg['ton_wallet'] + '`' if cfg.get('ton_wallet') else '*—*'}"
    )
    await q.edit_message_text(
        text, reply_markup=keyboards.admin_settings(cfg), parse_mode=ParseMode.MARKDOWN
    )


@_admin_only
async def settings_toggle_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    cfg = await db.get_config()
    await db.update_config(auto_approve=not bool(cfg.get("auto_approve")))
    await _render_settings(q, context)


@_admin_only
async def settings_toggle_ton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    cfg = await db.get_config()
    if cfg.get("ton_enabled"):
        await db.update_config(ton_enabled=False)
        await q.answer("TON payments turned OFF. Users now see Stars only.")
    else:
        if await ton.get_wallet() is None:
            await q.answer(
                "Set a valid TON wallet first (Set TON Wallet), then turn TON on.",
                show_alert=True,
            )
            return
        await db.update_config(ton_enabled=True)
        plans = await db.list_plans(active_only=True)
        if any(p.get("price_ton_nano") for p in plans):
            await q.answer("TON payments turned ON.")
        else:
            await q.answer(
                "TON is ON, but no active plan has a TON price yet. "
                "Use Plans → Set TON Price, otherwise users only see Stars.",
                show_alert=True,
            )
    await _render_settings(q, context)


@_admin_only
async def set_admin_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("Send the admin Telegram username (with or without @). /cancel to abort.")
    return SET_ADMIN_USER


async def set_admin_user_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip().lstrip("@")
    if not raw or len(raw) > 32:
        await update.message.reply_text("❌ Invalid username. Try again or /cancel.")
        return SET_ADMIN_USER
    await db.update_config(admin_username=raw)
    await update.message.reply_text(
        f"✅ Admin username set to @{raw}",
        reply_markup=keyboards.admin_back(),
    )
    return ConversationHandler.END


@_admin_only
async def set_ton_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("Send the TON wallet address (or 'clear'). /cancel to abort.")
    return SET_TON


async def set_ton_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    if raw.lower() == "clear":
        await db.update_config(ton_wallet=None, ton_enabled=False)
        await update.message.reply_text(
            "✅ TON wallet cleared and TON payments turned OFF.",
            reply_markup=keyboards.admin_back(),
        )
        return ConversationHandler.END
    if not raw or ton.parse_address(raw) is None:
        await update.message.reply_text(
            "❌ That's not a valid TON address (wrong format or bad checksum). "
            "Try again or /cancel."
        )
        return SET_TON
    raw = ton.friendly(raw)  # store the non-bounceable form
    await db.update_config(ton_wallet=raw)
    await update.message.reply_text(
        f"✅ TON wallet saved: `{raw}`",
        reply_markup=keyboards.admin_back(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


# ---------- shared cancel ----------

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for k in ("new_plan_name", "new_plan_limit", "new_plan_price", "tonp_plan_id"):
        context.user_data.pop(k, None)
    await update.effective_message.reply_text(
        "❎ Cancelled.", reply_markup=keyboards.main_reply_keyboard()
    )
    return ConversationHandler.END


async def menu_button_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User tapped a main-menu reply-keyboard button mid admin flow → exit."""
    for k in ("new_plan_name", "new_plan_limit", "new_plan_price", "tonp_plan_id"):
        context.user_data.pop(k, None)
    await update.effective_message.reply_text(
        "↩️ Exited admin flow — tap your selection again.",
        reply_markup=keyboards.main_reply_keyboard(),
    )
    return ConversationHandler.END


# ---------- registration ----------

def register(app) -> None:
    app.add_handler(CommandHandler("admin", cmd_admin))

    menu_filter = filters.Regex(keyboards.MENU_BUTTONS_REGEX)
    text_state = lambda fn: [
        MessageHandler(menu_filter, menu_button_exit),
        MessageHandler(filters.TEXT & ~filters.COMMAND, fn),
    ]

    # Conversation: Add Plan
    app.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(add_plan_start, pattern=r"^adm:plans:add$")],
            states={
                ADD_PLAN_NAME: text_state(add_plan_name),
                ADD_PLAN_LIMIT: text_state(add_plan_limit),
                ADD_PLAN_PRICE: text_state(add_plan_price),
                ADD_PLAN_TON: text_state(add_plan_ton),
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            name="admin_add_plan",
            persistent=False,
        )
    )

    # Conversation: Add Provider Keys
    app.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(add_keys_start, pattern=r"^adm:pk:add$")],
            states={
                ADD_KEYS_INPUT: text_state(add_keys_receive),
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            name="admin_add_keys",
            persistent=False,
        )
    )

    # Conversation: Broadcast
    app.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(broadcast_start, pattern=r"^adm:broadcast$")],
            states={
                BROADCAST_INPUT: text_state(broadcast_send),
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            name="admin_broadcast",
            persistent=False,
        )
    )

    # Conversation: Set Admin Username
    app.add_handler(
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(
                    set_admin_user_start, pattern=r"^adm:settings:set_admin_user$"
                )
            ],
            states={
                SET_ADMIN_USER: text_state(set_admin_user_save),
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            name="admin_set_username",
            persistent=False,
        )
    )

    # Conversation: Set TON wallet
    app.add_handler(
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(set_ton_start, pattern=r"^adm:settings:set_ton$")
            ],
            states={
                SET_TON: text_state(set_ton_save),
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            name="admin_set_ton",
            persistent=False,
        )
    )

    # Conversation: Set a plan's TON price
    app.add_handler(
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(
                    set_plan_ton_start, pattern=r"^adm:plans:tonp_do:[a-f0-9]+$"
                )
            ],
            states={
                SET_PLAN_TON: text_state(set_plan_ton_save),
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            name="admin_set_plan_ton",
            persistent=False,
        )
    )

    # Plain callbacks
    app.add_handler(CallbackQueryHandler(on_home, pattern=r"^adm:home$"))
    app.add_handler(CallbackQueryHandler(on_users, pattern=r"^adm:users$"))
    app.add_handler(CallbackQueryHandler(on_stats, pattern=r"^adm:stats$"))
    app.add_handler(CallbackQueryHandler(on_pk, pattern=r"^adm:pk$"))
    app.add_handler(CallbackQueryHandler(on_pk_list, pattern=r"^adm:pk:list$"))
    app.add_handler(CallbackQueryHandler(on_pk_pool, pattern=r"^adm:pk:pool$"))
    app.add_handler(CallbackQueryHandler(on_plans, pattern=r"^adm:plans$"))
    app.add_handler(CallbackQueryHandler(on_plans_list, pattern=r"^adm:plans:list$"))
    app.add_handler(CallbackQueryHandler(on_plans_deact, pattern=r"^adm:plans:deact$"))
    app.add_handler(CallbackQueryHandler(on_plans_tonp, pattern=r"^adm:plans:tonp$"))
    app.add_handler(CallbackQueryHandler(on_plans_del, pattern=r"^adm:plans:del$"))
    app.add_handler(
        CallbackQueryHandler(on_plans_del_do, pattern=r"^adm:plans:del_do:[a-f0-9]+$")
    )
    app.add_handler(
        CallbackQueryHandler(on_plans_del_yes, pattern=r"^adm:plans:del_yes:[a-f0-9]+$")
    )
    app.add_handler(
        CallbackQueryHandler(on_plans_deact_do, pattern=r"^adm:plans:deact_do:[a-f0-9]+$")
    )
    app.add_handler(CallbackQueryHandler(on_pending, pattern=r"^adm:pending$"))
    app.add_handler(CallbackQueryHandler(on_logs, pattern=r"^adm:logs:\d+$"))
    app.add_handler(CallbackQueryHandler(on_settings, pattern=r"^adm:settings$"))
    app.add_handler(
        CallbackQueryHandler(settings_toggle_auto, pattern=r"^adm:settings:toggle_auto$")
    )
    app.add_handler(
        CallbackQueryHandler(settings_toggle_ton, pattern=r"^adm:settings:toggle_ton$")
    )
