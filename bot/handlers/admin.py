"""/admin panel — restricted to ADMIN_USER_IDS."""
import logging
import re

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
from services import btc, db, payments, ton
from services import keys as keystock
from utils import activity_log, keyboards

log = logging.getLogger(__name__)


# Conversation states
ADD_PLAN_NAME, ADD_PLAN_LIMIT, ADD_PLAN_PRICE, ADD_PLAN_TON, ADD_PLAN_BTC, ADD_PLAN_INR = range(6)
ADD_KEYS_INPUT = 10
BROADCAST_INPUT = 20
SET_ADMIN_USER = 30
SET_TON = 31
SET_PLAN_TON = 32
SET_BTC = 33
SET_UPI = 34
SET_PLAN_PRICE = 35
SET_WEB_URL = 36


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
    revenue = await db.total_revenue_stars()
    revenue_ton = await db.total_revenue_ton_nano()
    revenue_btc = await db.total_revenue_method("btc", "btc_amount_sats")
    revenue_inr = await db.total_revenue_method("upi", "upi_amount_paise")
    ton_part = f" + *{ton.fmt_ton(revenue_ton)} TON*" if revenue_ton else ""
    ton_part += f" + *{btc.fmt_btc(revenue_btc)} BTC*" if revenue_btc else ""
    ton_part += f" + *₹{payments.fmt_inr(revenue_inr)}*" if revenue_inr else ""
    issued = await db.count_user_keys()
    pool = await db.pool_status()

    if pool["free"] == 0:
        light = "🔴 empty"
    elif pool["free"] < 5:
        light = "🟡 low"
    else:
        light = "🟢 healthy"

    text = (
        "📊 *Stats*\n\n"
        f"💰 Revenue: *{revenue}⭐*{ton_part}\n"
        f"🔑 Keys sold: *{issued}*\n"
        f"🏊 Pool: {light} — {pool['free']} free / {pool['total']} total"
    )
    await q.edit_message_text(text, reply_markup=keyboards.admin_back(), parse_mode=ParseMode.MARKDOWN)


# ---------- provider keys ----------

@_admin_only
async def on_pk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🗄 *Key Stock*",
        reply_markup=keyboards.admin_pk_menu(),
        parse_mode=ParseMode.MARKDOWN,
    )


def _plain(text: str) -> str:
    """Strip characters legacy Markdown would treat as formatting."""
    return "".join(ch for ch in text if ch not in "_*`[]")


@_admin_only
async def on_pk_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    items = await db.list_provider_keys(limit=30)
    if not items:
        await q.edit_message_text("No keys in stock yet.", reply_markup=keyboards.admin_pk_menu())
        return
    lines = ["🗄 *Key Stock* (oldest 30)\n"]
    for k in items:
        status = "🟨 sold" if k.get("reserved") else "🟩 free"
        note = f" | {_plain(k['label'])}" if k.get("label") else ""
        lines.append(f"`{keystock.mask(k['identifier'])}`{note} | {status}")
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
    text = (
        f"{light} *Key Stock*\n\n"
        f"Total: {p['total']}\n"
        f"Free: {p['free']}\n"
        f"Sold: {p['reserved']}"
    )
    if p["legacy"]:
        text += (
            f"\n\n⚠️ {p['legacy']} old entr{'y' if p['legacy'] == 1 else 'ies'} without an "
            "identifier are ignored (they hold real API keys from the previous setup)."
        )
    await q.edit_message_text(
        text, reply_markup=keyboards.admin_pk_menu(), parse_mode=ParseMode.MARKDOWN
    )


@_admin_only
async def add_keys_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "➕ *Add Keys to stock*\n\n"
        "First create the key on your website (real key + identifier). Then send me only the "
        "*identifier* — one per line, optionally with a note for yourself:\n"
        "`IDENTIFIER` or `IDENTIFIER|NOTE`\n\n"
        "Example:\n"
        "```\n"
        "QSMS-K7M2P-9XQ4T-W3HDN-RB8FA|key1\n"
        "QSMS-AB3CD-EF4GH-JK5LM-NP6QR\n"
        "```\n"
        "I never need the real key. /cancel to abort.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADD_KEYS_INPUT


@_admin_only
async def add_keys_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text or ""
    items: list[tuple[str, str]] = []
    bad: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        ident, _, note = line.partition("|")
        ident, note = ident.strip(), note.strip()[:40]
        if not keystock.is_valid_identifier(ident):
            bad.append(ident)
            continue
        if ident in seen:
            continue
        seen.add(ident)
        items.append((ident, note))

    rule = "An identifier is 16–64 letters, digits, - or _, exactly as it appears on the website."
    if not items:
        hint = ""
        if bad and any(len(b) < 16 for b in bad):
            hint = "\n\nLooks like a short name or the old LABEL|API-KEY format? Send the web *identifier* instead."
        await update.message.reply_text(
            f"❌ No valid identifiers found. {rule}{hint}\n\nTry again or /cancel.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ADD_KEYS_INPUT

    added, dupes = await db.add_provider_keys(items)
    if added:
        await activity_log.provider_keys_added(context, update.effective_user, added)

    deleted = False
    try:  # don't leave the identifiers sitting in the chat
        await update.message.delete()
        deleted = True
    except TelegramError:
        pass

    msg = f"✅ Added {added} key(s) to stock."
    if dupes:
        msg += f"\n⚠️ {len(dupes)} already in stock (skipped)."
    if bad:
        msg += f"\n⚠️ Skipped {len(bad)} invalid line(s). {rule}"
    if deleted:
        msg += "\n🧹 I deleted your message so the identifiers don't stay in the chat."
    await update.effective_chat.send_message(msg, reply_markup=keyboards.admin_pk_menu())
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
        extra = ""
        if p.get("price_ton_nano"):
            extra += f" / {ton.fmt_ton(p['price_ton_nano'])} TON"
        if p.get("price_btc_sats"):
            extra += f" / {btc.fmt_btc(p['price_btc_sats'])} BTC"
        if p.get("price_inr_paise"):
            extra += f" / ₹{payments.fmt_inr(p['price_inr_paise'])}"
        if p.get("is_free"):
            lines.append(f"{st} {p['name']} — {p['sms_limit']} SMS — 🆓 FREE")
        else:
            lines.append(
                f"{st} {p['name']} — {p['sms_limit']} SMS — {p['price_stars']}⭐{extra}"
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
async def on_plans_free(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    plans = await db.list_plans(active_only=False)
    if not plans:
        await q.edit_message_text("No plans.", reply_markup=keyboards.admin_plans_menu())
        return
    await q.edit_message_text(
        "Choose a plan to switch between FREE and PAID:",
        reply_markup=keyboards.admin_plans_picker(plans, "free_do"),
    )


@_admin_only
async def on_plans_free_do(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    plan = await db.get_plan(q.data.rsplit(":", 1)[1])
    if not plan or plan.get("deleted"):
        msg = "❌ Plan not found."
    elif plan.get("is_free"):
        if (plan.get("price_stars") or 0) <= 0:
            msg = (
                "❌ This plan was created as free and has no Stars price, so it can't be switched "
                "to paid. Delete it and create a paid plan."
            )
        else:
            await db.set_plan_free(plan["_id"], False)
            msg = f"✅ '{plan['name']}' is now PAID again."
    else:
        await db.set_plan_free(plan["_id"], True)
        msg = (
            f"✅ '{plan['name']}' is now FREE: one claim per user. "
            "Its prices are kept but hidden."
        )
    await q.edit_message_text(msg, reply_markup=keyboards.admin_plans_menu())


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
    await q.edit_message_text("➕ *Add Plan*\n\nStep 1/6: Plan name?", parse_mode=ParseMode.MARKDOWN)
    return ADD_PLAN_NAME


async def add_plan_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = (update.message.text or "").strip()
    if not name or len(name) > 50:
        await update.message.reply_text("❌ Name must be 1–50 chars. Try again or /cancel.")
        return ADD_PLAN_NAME
    context.user_data["new_plan_name"] = name
    await update.message.reply_text("Step 2/6: SMS limit (integer > 0)?")
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
    await update.message.reply_text("Step 3/6: Price in Stars (integer > 0), or 0 to make this a FREE plan (one claim per user)?")
    return ADD_PLAN_PRICE


async def add_plan_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        price = int((update.message.text or "").strip())
        if price < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Send 0 (free) or a positive integer. Try again or /cancel.")
        return ADD_PLAN_PRICE

    if price == 0:
        # FREE plan: every user can claim it once; no payment steps needed.
        name = context.user_data.pop("new_plan_name")
        limit = context.user_data.pop("new_plan_limit")
        plan = await db.add_plan(name, limit, 0, is_free=True)
        await update.message.reply_text(
            f"✅ FREE plan '{plan['name']}' added: {plan['sms_limit']} SMS (one claim per user).",
            reply_markup=keyboards.admin_plans_menu(),
        )
        await activity_log.plan_added(
            context, update.effective_user, plan["name"], plan["sms_limit"], 0
        )
        return ConversationHandler.END

    context.user_data["new_plan_price"] = price
    await update.message.reply_text(
        "Step 4/6: Price in TON (e.g. 1.5), or 0 to skip TON for this plan?"
    )
    return ADD_PLAN_TON


def _is_skip(raw: str) -> bool:
    return raw.strip().lower() in ("0", "skip")


async def add_plan_ton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    price_ton_nano = None
    if not _is_skip(raw):
        price_ton_nano = ton.parse_ton_amount(raw)
        if price_ton_nano is None:
            await update.message.reply_text(
                "❌ Send an amount like 1.5 (max 9 decimals), or 0 to skip. Try again or /cancel."
            )
            return ADD_PLAN_TON
    context.user_data["new_plan_ton"] = price_ton_nano
    await update.message.reply_text(
        "Step 5/6: Price in BTC (e.g. 0.0005), or 0 to skip Bitcoin for this plan?"
    )
    return ADD_PLAN_BTC


async def add_plan_btc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    price_btc_sats = None
    if not _is_skip(raw):
        price_btc_sats = btc.parse_btc_amount(raw)
        if price_btc_sats is None:
            await update.message.reply_text(
                "❌ Send an amount like 0.0005 (min 0.00001, max 8 decimals), or 0 to skip. "
                "Try again or /cancel."
            )
            return ADD_PLAN_BTC
    context.user_data["new_plan_btc"] = price_btc_sats
    await update.message.reply_text(
        "Step 6/6: Price in INR for UPI (e.g. 199 or 199.50), or 0 to skip UPI for this plan?"
    )
    return ADD_PLAN_INR


async def add_plan_inr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    price_inr_paise = None
    if not _is_skip(raw):
        price_inr_paise = payments.parse_inr_amount(raw)
        if price_inr_paise is None:
            await update.message.reply_text(
                "❌ Send an amount like 199 or 199.50, or 0 to skip. Try again or /cancel."
            )
            return ADD_PLAN_INR
    name = context.user_data.pop("new_plan_name")
    limit = context.user_data.pop("new_plan_limit")
    price = context.user_data.pop("new_plan_price")
    price_ton_nano = context.user_data.pop("new_plan_ton", None)
    price_btc_sats = context.user_data.pop("new_plan_btc", None)
    plan = await db.add_plan(
        name, limit, price, price_ton_nano, price_btc_sats, price_inr_paise
    )
    extra = ""
    if price_ton_nano:
        extra += f", {ton.fmt_ton(price_ton_nano)} TON"
    if price_btc_sats:
        extra += f", {btc.fmt_btc(price_btc_sats)} BTC"
    if price_inr_paise:
        extra += f", ₹{payments.fmt_inr(price_inr_paise)}"
    await update.message.reply_text(
        f"✅ Plan '{plan['name']}' added: {plan['sms_limit']} SMS, "
        f"{plan['price_stars']} Stars{extra}",
        reply_markup=keyboards.admin_plans_menu(),
    )
    await activity_log.plan_added(
        context, update.effective_user, plan["name"], plan["sms_limit"], plan["price_stars"]
    )
    return ConversationHandler.END


# ---- set a plan's BTC / INR price on an existing plan ----

_PRICE_KINDS = {
    "btc": {
        "field": "price_btc_sats",
        "title": "Bitcoin",
        "parse": btc.parse_btc_amount,
        "fmt": lambda v: f"{btc.fmt_btc(v)} BTC",
        "hint": "e.g. 0.0005 (min 0.00001, max 8 decimals)",
    },
    "inr": {
        "field": "price_inr_paise",
        "title": "UPI (INR)",
        "parse": payments.parse_inr_amount,
        "fmt": lambda v: f"₹{payments.fmt_inr(v)}",
        "hint": "e.g. 199 or 199.50",
    },
}


@_admin_only
async def on_plans_pp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    kind = q.data.rsplit(":", 1)[1]
    plans = await db.list_plans(active_only=True)
    if not plans:
        await q.edit_message_text("No active plans.", reply_markup=keyboards.admin_plans_menu())
        return
    await q.edit_message_text(
        f"Choose a plan to set its {_PRICE_KINDS[kind]['title']} price:",
        reply_markup=keyboards.admin_plans_picker(plans, f"ppd:{kind}"),
    )


@_admin_only
async def set_plan_price_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    _, _, _, kind, plan_id = q.data.split(":")
    context.user_data["pp_kind"] = kind
    context.user_data["pp_plan"] = plan_id
    await q.edit_message_text(
        f"Send the {_PRICE_KINDS[kind]['title']} price for this plan ({_PRICE_KINDS[kind]['hint']}),\n"
        "or 0 to remove it. /cancel to abort."
    )
    return SET_PLAN_PRICE


async def set_plan_price_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    kind = context.user_data.get("pp_kind")
    plan_id = context.user_data.get("pp_plan")
    if kind not in _PRICE_KINDS or not plan_id:
        return ConversationHandler.END
    spec = _PRICE_KINDS[kind]
    raw = (update.message.text or "").strip()
    value = None
    if not _is_skip(raw):
        value = spec["parse"](raw)
        if value is None:
            await update.message.reply_text(
                f"❌ Invalid amount ({spec['hint']}), or 0 to remove. Try again or /cancel."
            )
            return SET_PLAN_PRICE
    ok = await db.set_plan_price(plan_id, spec["field"], value)
    context.user_data.pop("pp_kind", None)
    context.user_data.pop("pp_plan", None)
    if not ok:
        msg = "❌ Plan not found."
    elif value is None:
        msg = f"✅ {spec['title']} payment removed for this plan."
    else:
        msg = f"✅ {spec['title']} price set: {spec['fmt'](value)}."
    await update.message.reply_text(msg, reply_markup=keyboards.admin_plans_menu())
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
            f"Paid: {payments.paid_label(r)}\n"
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
    web = (cfg.get("web_url") or config.WEB_SMS_URL or "").strip()
    web_line = f"`{web}`" if web else "*—*"
    text = (
        "⚙️ *Settings*\n\n"
        f"Auto-Approve: *{'ON' if cfg.get('auto_approve') else 'OFF'}*\n"
        f"Admin Username: *{cfg.get('admin_username') or '—'}*\n"
        f"Website: {web_line}\n\n"
        "Payment methods are under 💳 Payment Methods."
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


_METHOD_LABELS = {"stars": "Stars", "ton": "TON", "btc": "Bitcoin", "upi": "UPI"}
_METHOD_PRICE_FIELD = {
    "stars": "price_stars",
    "ton": "price_ton_nano",
    "btc": "price_btc_sats",
    "upi": "price_inr_paise",
}
_METHOD_PRICE_HINT = {
    "ton": "Plans → Set TON Price",
    "btc": "Plans → Set BTC Price",
    "upi": "Plans → Set UPI (INR) Price",
}


async def _render_payments(q, context) -> None:
    cfg = await db.get_config()

    def st(key: str, default: bool = False) -> str:
        return "ON" if cfg.get(key, default) else "OFF"

    wallet = await ton.get_wallet(cfg)
    address = await btc.get_address(cfg)
    upi_id = cfg.get("upi_id") if payments.is_valid_upi_id(cfg.get("upi_id") or "") else None

    def code(v) -> str:
        return f"`{v}`" if v else "*—*"

    text = (
        "💳 *Payment Methods*\n\n"
        f"⭐ Stars: *{st('stars_enabled', True)}*\n"
        f"💎 TON: *{st('ton_enabled')}* — wallet: {code(wallet)}\n"
        f"₿ Bitcoin: *{st('btc_enabled')}* — address: {code(address)}\n"
        f"🇮🇳 UPI: *{st('upi_enabled')}* — ID: {code(upi_id)}\n\n"
        "Users see a method only when it is ON, configured, and the plan has a price for it "
        "(set prices under Plans)."
    )
    await q.edit_message_text(
        text, reply_markup=keyboards.admin_payments_menu(cfg), parse_mode=ParseMode.MARKDOWN
    )


@_admin_only
async def on_payments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await _render_payments(q, context)


@_admin_only
async def on_pay_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    method = q.data.rsplit(":", 1)[1]
    label = _METHOD_LABELS[method]
    key = f"{method}_enabled"
    cfg = await db.get_config()

    if cfg.get(key, method == "stars"):
        await db.update_config(**{key: False})
        await q.answer(f"{label} payments turned OFF.")
    else:
        missing = None
        if method == "ton" and await ton.get_wallet(cfg) is None:
            missing = "Set a valid TON wallet first (Set TON Wallet), then turn TON on."
        elif method == "btc" and await btc.get_address(cfg) is None:
            missing = "Set a valid Bitcoin address first (Set BTC Address), then turn Bitcoin on."
        elif method == "upi" and not payments.is_valid_upi_id(cfg.get("upi_id") or ""):
            missing = "Set your UPI ID first (Set UPI ID), then turn UPI on."
        if missing:
            await q.answer(missing, show_alert=True)
            return
        await db.update_config(**{key: True})
        plans = await db.list_plans(active_only=True)
        field = _METHOD_PRICE_FIELD[method]
        if method != "stars" and not any(p.get(field) for p in plans):
            await q.answer(
                f"{label} is ON, but no active plan has a {label} price yet. "
                f"Use {_METHOD_PRICE_HINT[method]}, otherwise users won't see it.",
                show_alert=True,
            )
        else:
            await q.answer(f"{label} payments turned ON.")
    await _render_payments(q, context)


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


@_admin_only
async def set_btc_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "Send your Bitcoin receiving address (mainnet: starts with 1, 3 or bc1), "
        "or 'clear'. Use an address you control. /cancel to abort."
    )
    return SET_BTC


async def set_btc_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    if raw.lower() == "clear":
        await db.update_config(btc_address=None, btc_enabled=False)
        await update.message.reply_text(
            "✅ Bitcoin address cleared and Bitcoin payments turned OFF.",
            reply_markup=keyboards.admin_back(),
        )
        return ConversationHandler.END
    if not btc.is_valid_address(raw):
        await update.message.reply_text(
            "❌ That's not a valid Bitcoin mainnet address (wrong format or bad checksum). "
            "Try again or /cancel."
        )
        return SET_BTC
    address = btc.normalize_address(raw)
    await db.update_config(btc_address=address)
    await update.message.reply_text(
        f"✅ Bitcoin address saved: `{address}`",
        reply_markup=keyboards.admin_back(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


@_admin_only
async def set_upi_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "Send your UPI ID (like name@bank), or 'clear'. /cancel to abort."
    )
    return SET_UPI


async def set_upi_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    if raw.lower() == "clear":
        await db.update_config(upi_id=None, upi_enabled=False)
        await update.message.reply_text(
            "✅ UPI ID cleared and UPI payments turned OFF.",
            reply_markup=keyboards.admin_back(),
        )
        return ConversationHandler.END
    if not payments.is_valid_upi_id(raw):
        await update.message.reply_text(
            "❌ That doesn't look like a UPI ID (expected name@bank). Try again or /cancel."
        )
        return SET_UPI
    await db.update_config(upi_id=raw)
    await update.message.reply_text(
        f"✅ UPI ID saved: `{raw}`",
        reply_markup=keyboards.admin_back(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


@_admin_only
async def set_web_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "Send the address of your website where users send SMS (https://…), or 'clear'. "
        "/cancel to abort."
    )
    return SET_WEB_URL


async def set_web_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    if raw.lower() == "clear":
        await db.update_config(web_url=None)
        await update.message.reply_text("✅ Website address cleared.", reply_markup=keyboards.admin_back())
        return ConversationHandler.END
    if not re.match(r"^https?://[^\s]{3,200}$", raw):
        await update.message.reply_text(
            "❌ Send a full address starting with http:// or https:// (no spaces). "
            "Try again or /cancel."
        )
        return SET_WEB_URL
    await db.update_config(web_url=raw)
    await update.message.reply_text(
        f"✅ Website saved: `{raw}`",
        reply_markup=keyboards.admin_back(),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


# ---------- shared cancel ----------

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for k in (
        "new_plan_name", "new_plan_limit", "new_plan_price", "new_plan_ton", "new_plan_btc",
        "tonp_plan_id", "pp_kind", "pp_plan",
    ):
        context.user_data.pop(k, None)
    await update.effective_message.reply_text(
        "❎ Cancelled.", reply_markup=keyboards.main_reply_keyboard()
    )
    return ConversationHandler.END


async def menu_button_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User tapped a main-menu reply-keyboard button mid admin flow → exit."""
    for k in (
        "new_plan_name", "new_plan_limit", "new_plan_price", "new_plan_ton", "new_plan_btc",
        "tonp_plan_id", "pp_kind", "pp_plan",
    ):
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
                ADD_PLAN_BTC: text_state(add_plan_btc),
                ADD_PLAN_INR: text_state(add_plan_inr),
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

    # Conversations: BTC address, UPI ID, per-plan BTC/INR price
    app.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(set_btc_start, pattern=r"^adm:pay:set_btc$")],
            states={SET_BTC: text_state(set_btc_save)},
            fallbacks=[CommandHandler("cancel", cancel)],
            name="admin_set_btc",
            persistent=False,
        )
    )
    app.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(set_upi_start, pattern=r"^adm:pay:set_upi$")],
            states={SET_UPI: text_state(set_upi_save)},
            fallbacks=[CommandHandler("cancel", cancel)],
            name="admin_set_upi",
            persistent=False,
        )
    )
    app.add_handler(
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(
                    set_plan_price_start, pattern=r"^adm:plans:ppd:(btc|inr):[a-f0-9]+$"
                )
            ],
            states={SET_PLAN_PRICE: text_state(set_plan_price_save)},
            fallbacks=[CommandHandler("cancel", cancel)],
            name="admin_set_plan_price",
            persistent=False,
        )
    )

    # Conversation: website URL
    app.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(set_web_start, pattern=r"^adm:settings:set_web$")],
            states={SET_WEB_URL: text_state(set_web_save)},
            fallbacks=[CommandHandler("cancel", cancel)],
            name="admin_set_web",
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
    app.add_handler(CallbackQueryHandler(on_plans_free, pattern=r"^adm:plans:free$"))
    app.add_handler(
        CallbackQueryHandler(on_plans_free_do, pattern=r"^adm:plans:free_do:[a-f0-9]+$")
    )
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
    app.add_handler(CallbackQueryHandler(on_payments, pattern=r"^adm:pay$"))
    app.add_handler(
        CallbackQueryHandler(on_pay_toggle, pattern=r"^adm:pay:t:(stars|ton|btc|upi)$")
    )
    app.add_handler(CallbackQueryHandler(on_plans_pp, pattern=r"^adm:plans:pp:(btc|inr)$"))
