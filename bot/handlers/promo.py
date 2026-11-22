"""🎟 Promo codes — admin management and user redemption.

Admin: /admin -> Promo Codes: create (percent off, max uses, expiry, optionally one user only),
list, deactivate. User: "🎟 Have a promo code?" on the payment-method screen.
  * 100% off -> consumed immediately and a free key is granted.
  * otherwise -> the discount is applied to that plan's prices for a while; the use is counted
    when the payment is confirmed.
"""
import logging
from datetime import timedelta
from typing import Optional

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from handlers import grants, purchase
from handlers.admin import _admin_only
from services import db, promo
from utils import keyboards

log = logging.getLogger(__name__)


# ======================= admin =======================

P_CODE, P_PERCENT, P_USES, P_DAYS, P_USER = range(5)
_NEW_KEYS = ("np_code", "np_percent", "np_uses", "np_days")


def _clear_new(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in _NEW_KEYS:
        context.user_data.pop(k, None)


def _describe(p: dict) -> str:
    uses = f"{p.get('uses', 0)}/{p['max_uses']}" if p.get("max_uses") is not None else f"{p.get('uses', 0)}/∞"
    exp = p["expires_at"].strftime("%Y-%m-%d") if p.get("expires_at") else "no expiry"
    who = f"user {p['only_user_id']}" if p.get("only_user_id") else "anyone"
    state = "ACTIVE" if p.get("active") else "off"
    return f"{p['code']} — {p['percent_off']}% — used {uses} — {exp} — {who} — {state}"


@_admin_only
async def on_promo_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("🎟 Promo Codes", reply_markup=keyboards.admin_promo_menu())


@_admin_only
async def on_promo_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    promos = await db.list_promos()
    text = "🎟 Promo Codes\n\n" + (
        "\n".join(_describe(p) for p in promos) if promos else "No promo codes yet."
    )
    await q.edit_message_text(text, reply_markup=keyboards.admin_promo_menu())


@_admin_only
async def on_promo_deact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    active = [p for p in await db.list_promos() if p.get("active")]
    if not active:
        await q.edit_message_text("No active codes.", reply_markup=keyboards.admin_promo_menu())
        return
    rows = [
        [keyboards.btn(f"🚫 {p['code']} ({p['percent_off']}%)", f"adm:promo:deact_do:{p['code']}", style=keyboards.DANGER)]
        for p in active
    ]
    rows.append([keyboards.btn("⬅️ Back", "adm:promo")])
    await q.edit_message_text("Choose a code to deactivate:", reply_markup=InlineKeyboardMarkup(rows))


@_admin_only
async def on_promo_deact_do(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    ok = await db.set_promo_active(q.data.rsplit(":", 1)[1], False)
    await q.edit_message_text(
        "✅ Code deactivated." if ok else "❌ Code not found.",
        reply_markup=keyboards.admin_promo_menu(),
    )


@_admin_only
async def new_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    _clear_new(context)
    await q.edit_message_text(
        "➕ New promo code\n\nStep 1/5: send the code (3–32 chars: A–Z, 0–9, - _), "
        "or 'auto' to generate one.\n\n/cancel to abort."
    )
    return P_CODE


async def new_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    code = promo.new_code() if raw.lower() == "auto" else promo.normalize(raw)
    if not promo.is_valid_code(code):
        await update.message.reply_text("❌ Use 3–32 characters: A–Z, 0–9, - or _. Try again or /cancel.")
        return P_CODE
    if await db.get_promo(code):
        await update.message.reply_text("❌ That code already exists. Pick another or /cancel.")
        return P_CODE
    context.user_data["np_code"] = code
    await update.message.reply_text("Step 2/5: discount percent (1–100). 100 = free key.")
    return P_PERCENT


async def new_percent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        pct = int((update.message.text or "").strip())
        if not 1 <= pct <= 100:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Send a whole number from 1 to 100. Try again or /cancel.")
        return P_PERCENT
    context.user_data["np_percent"] = pct
    await update.message.reply_text("Step 3/5: max total uses (0 = unlimited).")
    return P_USES


async def new_uses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        n = int((update.message.text or "").strip())
        if n < 0 or n > 10_000_000:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Send 0 or a positive whole number. Try again or /cancel.")
        return P_USES
    context.user_data["np_uses"] = n or None
    await update.message.reply_text("Step 4/5: valid for how many days? (0 = no expiry)")
    return P_DAYS


async def new_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        n = int((update.message.text or "").strip())
        if n < 0 or n > 3650:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Send 0–3650. Try again or /cancel.")
        return P_DAYS
    context.user_data["np_days"] = n
    await update.message.reply_text(
        "Step 5/5: restrict to one user? Send their Telegram ID or @username "
        "(they must have started the bot), or 0 for anyone."
    )
    return P_USER


async def new_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    only_user: Optional[int] = None
    if raw != "0":
        target = await (db.get_user(int(raw)) if raw.isdigit() else db.find_user_by_username(raw))
        if target is None:
            await update.message.reply_text(
                "❌ I don't know that user (they must open the bot first). "
                "Try again, send 0 for anyone, or /cancel."
            )
            return P_USER
        only_user = target["telegram_id"]

    d = context.user_data
    days = d.get("np_days") or 0
    expires = (db.now_utc().replace(tzinfo=None) + timedelta(days=days)) if days else None
    created = await db.create_promo(
        d["np_code"], d["np_percent"], d.get("np_uses"), expires, only_user, update.effective_user.id
    )
    _clear_new(context)
    if created is None:
        await update.message.reply_text("❌ That code was just created by someone else. Start again.",
                                        reply_markup=keyboards.admin_promo_menu())
        return ConversationHandler.END
    await update.message.reply_text(
        "✅ Promo code created\n\n" + _describe(created) + "\n\nShare the code with the user(s).",
        reply_markup=keyboards.admin_promo_menu(),
    )
    return ConversationHandler.END


async def new_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_new(context)
    await update.effective_message.reply_text("❎ Cancelled.", reply_markup=keyboards.main_reply_keyboard())
    return ConversationHandler.END


# ======================= user =======================

PROMO_CODE = 0


async def promo_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    if await db.is_banned(user.id):
        return ConversationHandler.END
    plan_id = q.data.split(":", 2)[2]
    plan = await db.get_plan(plan_id)
    if not plan or not plan.get("active") or plan.get("is_free"):
        await q.edit_message_text("❌ That plan is no longer available.", reply_markup=keyboards.back_to_menu())
        return ConversationHandler.END
    context.user_data["promo_plan"] = plan_id
    await q.edit_message_text("🎟 Send your promo code.\n\n/cancel to go back.")
    return PROMO_CODE


async def promo_got(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    plan_id = context.user_data.get("promo_plan")
    if promo.too_many_failures(user.id):
        context.user_data.pop("promo_plan", None)
        await update.message.reply_text(
            "⏳ Too many invalid attempts. Please try again in a few minutes.",
            reply_markup=keyboards.main_reply_keyboard(),
        )
        return ConversationHandler.END

    code = promo.normalize(update.message.text or "")
    p, err = (None, "❌ Invalid promo code.") if not promo.is_valid_code(code) else await promo.validate(code, user.id)
    if err:
        promo.note_failure(user.id)
        await update.message.reply_text(f"{err}\nTry again or /cancel.")
        return PROMO_CODE

    plan = await db.get_plan(plan_id or "")
    if not plan or not plan.get("active"):
        await update.message.reply_text("❌ That plan is no longer available.", reply_markup=keyboards.main_reply_keyboard())
        return ConversationHandler.END
    context.user_data.pop("promo_plan", None)

    if p["percent_off"] >= 100:
        # Free key: consume atomically first so a single-use code can't be double-spent.
        if not await promo.try_consume(code, user.id):
            await update.message.reply_text("❌ This code has already been fully used.", reply_markup=keyboards.main_reply_keyboard())
            return ConversationHandler.END
        try:
            key = await grants.grant_key(
                context.bot, user, plan, method="promo", extra={"promo_code": code, "promo_percent": 100}
            )
        except Exception as e:
            await promo.release(code, user.id)  # code stays usable
            if not isinstance(e, grants.OutOfStock):
                log.exception("promo grant failed: %s", e)
            await update.message.reply_text(
                "😕 Keys are temporarily out of stock. Your code was not used — please try again later.",
                reply_markup=keyboards.main_reply_keyboard(),
            )
            return ConversationHandler.END
        await update.message.reply_text(
            grants.key_message(key, plan, f"🎟 *Code {code} applied — your key is free!*"),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboards.main_reply_keyboard(),
        )
        return ConversationHandler.END

    await promo.apply(user.id, plan_id, p)
    text, markup = await purchase.build_method_screen(plan, user.id)
    await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


async def promo_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("promo_plan", None)
    await update.effective_message.reply_text("❎ Cancelled.", reply_markup=keyboards.main_reply_keyboard())
    return ConversationHandler.END


async def _exit_on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("promo_plan", None)
    _clear_new(context)
    await update.effective_message.reply_text(
        "↩️ Cancelled — tap your selection again.", reply_markup=keyboards.main_reply_keyboard()
    )
    return ConversationHandler.END


def register(app) -> None:
    menu_filter = filters.Regex(keyboards.MENU_BUTTONS_REGEX)
    text_state = lambda fn: [
        MessageHandler(menu_filter, _exit_on_menu),
        MessageHandler(filters.TEXT & ~filters.COMMAND, fn),
    ]

    app.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(new_start, pattern=r"^adm:promo:new$")],
            states={
                P_CODE: text_state(new_code),
                P_PERCENT: text_state(new_percent),
                P_USES: text_state(new_uses),
                P_DAYS: text_state(new_days),
                P_USER: text_state(new_user),
            },
            fallbacks=[CommandHandler("cancel", new_cancel)],
            name="admin_new_promo",
            persistent=False,
        )
    )
    app.add_handler(CallbackQueryHandler(on_promo_menu, pattern=r"^adm:promo$"))
    app.add_handler(CallbackQueryHandler(on_promo_list, pattern=r"^adm:promo:list$"))
    app.add_handler(CallbackQueryHandler(on_promo_deact, pattern=r"^adm:promo:deact$"))
    app.add_handler(CallbackQueryHandler(on_promo_deact_do, pattern=r"^adm:promo:deact_do:[A-Z0-9_\-]+$"))

    app.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(promo_ask, pattern=r"^promo:ask:[a-f0-9]+$")],
            states={PROMO_CODE: text_state(promo_got)},
            fallbacks=[CommandHandler("cancel", promo_cancel)],
            name="user_promo",
            persistent=False,
        )
    )
