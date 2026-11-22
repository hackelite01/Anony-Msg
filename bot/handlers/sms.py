"""📤 Send SMS conversation flow."""
import logging
import re

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
from services import db, sms_service
from utils import activity_log, keyboards

log = logging.getLogger(__name__)

PICK_KEY, ASK_NUMBER, ASK_MESSAGE = range(3)
PHONE_RE = re.compile(r"^\d{10,15}$")


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry from inline button OR reply-keyboard 📤 Send SMS button."""
    q = update.callback_query
    if q:
        await q.answer()

    user_id = update.effective_user.id
    if await db.is_banned(user_id):
        return ConversationHandler.END

    target = update.effective_message
    keys = await db.list_user_keys(user_id, active_only=True)
    if not keys:
        await target.reply_text(
            "📤 You have no active keys.",
            reply_markup=keyboards.buy_key_button(),
        )
        return ConversationHandler.END

    if len(keys) == 1:
        context.user_data["sms_key_code"] = keys[0]["code"]
        await target.reply_text(
            f"🔑 Using key `{keys[0]['code']}`\n\n"
            "📞 Enter destination number (with country code, digits only):",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ASK_NUMBER

    await target.reply_text(
        "📤 Choose a key to send from:",
        reply_markup=keyboards.keys_picker(keys),
    )
    return PICK_KEY


async def on_pick_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    code = q.data.split(":", 1)[1]
    key = await db.get_user_key(code)
    if not key or key["telegram_id"] != update.effective_user.id or not key.get("active"):
        await q.edit_message_text("❌ That key isn't available.", reply_markup=keyboards.back_to_menu())
        return ConversationHandler.END
    context.user_data["sms_key_code"] = code
    await q.edit_message_text(
        f"🔑 Using key `{code}`\n\n"
        "📞 Enter destination number (with country code, digits only):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ASK_NUMBER


async def on_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip().lstrip("+").replace(" ", "").replace("-", "")
    if not PHONE_RE.match(raw):
        await update.message.reply_text(
            "❌ Invalid number. Use 10–15 digits with country code.\n"
            "Try again or /cancel."
        )
        return ASK_NUMBER
    context.user_data["sms_to"] = raw
    await update.message.reply_text("💬 Enter your message:")
    return ASK_MESSAGE


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("❌ Message cannot be empty. Try again or /cancel.")
        return ASK_MESSAGE
    if len(text) > 1000:
        await update.message.reply_text("❌ Message too long (max 1000 chars). Try again.")
        return ASK_MESSAGE

    code = context.user_data.get("sms_key_code")
    to = context.user_data.get("sms_to")
    user_id = update.effective_user.id

    user_key = await db.get_user_key(code)
    if not user_key or not user_key.get("active") or user_key["telegram_id"] != user_id:
        await update.message.reply_text(
            "❌ Key no longer available.", reply_markup=keyboards.back_to_menu()
        )
        return ConversationHandler.END

    idx = user_key.get("active_provider_key_index", 0)
    reserved_ids = user_key.get("reserved_key_ids", [])
    if idx >= len(reserved_ids):
        await update.message.reply_text(
            "❌ Internal error: no provider key slot available. Contact admin.",
            reply_markup=keyboards.back_to_menu(),
        )
        return ConversationHandler.END

    provider_key = await db.get_provider_key(reserved_ids[idx])
    if not provider_key:
        await update.message.reply_text(
            "❌ Internal error: provider key missing. Contact admin.",
            reply_markup=keyboards.back_to_menu(),
        )
        return ConversationHandler.END

    notice = await update.message.reply_text("📡 Sending…")

    result = await sms_service.send_sms(
        provider_key["seven_io_key"], to, text, sender=config.SMS_SENDER_NAME
    )

    if not result.ok:
        await db.log_sms(
            user_id, code, provider_key["_id"], to, text, "failed", error=result.error
        )
        await activity_log.sms_failed(context, update.effective_user, code, to, result.error)
        try:
            await notice.edit_text(
                f"❌ {result.message}\n\n"
                "📞 Enter another number to try again or /menu to go back."
            )
        except Exception:
            await update.message.reply_text(f"❌ {result.message}")
        return ASK_NUMBER

    # Success path: increment counters
    updated_pk = await db.increment_provider_key_usage(provider_key["_id"])
    if updated_pk and updated_pk.get("exhausted"):
        await db.advance_user_key_provider(code)

    updated_uk = await db.increment_user_key_usage(code)

    await db.log_sms(user_id, code, provider_key["_id"], to, text, "sent")
    await activity_log.sms_sent(
        context,
        update.effective_user,
        code,
        to,
        updated_uk["sms_used"] if updated_uk else 0,
        updated_uk["sms_limit"] if updated_uk else 0,
    )

    preview = text if len(text) <= 80 else text[:77] + "…"
    confirm = (
        f"✅ *SMS sent!*\n\n"
        f"📞 To: `{to}`\n"
        f"💬 Message: {preview}\n"
        f"🔑 Key: `{code}` ({updated_uk['sms_used']}/{updated_uk['sms_limit']} used)"
    )
    try:
        await notice.edit_text(confirm, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await update.message.reply_text(confirm, parse_mode=ParseMode.MARKDOWN)

    if updated_uk and not updated_uk.get("active"):
        await update.message.reply_text(
            "🔚 *Plan exhausted!* Buy a new key to keep sending.",
            reply_markup=keyboards.buy_key_button(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📞 Enter next destination number, or /menu to go back."
    )
    return ASK_NUMBER


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("sms_key_code", None)
    context.user_data.pop("sms_to", None)
    await update.effective_message.reply_text(
        "❎ Cancelled.", reply_markup=keyboards.main_reply_keyboard()
    )
    return ConversationHandler.END


async def menu_button_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User tapped a main-menu reply-keyboard button mid-conversation.
    Exit cleanly so the top-level dispatcher picks up the action."""
    context.user_data.pop("sms_key_code", None)
    context.user_data.pop("sms_to", None)
    await update.effective_message.reply_text(
        "↩️ Returning to menu — tap your selection again.",
        reply_markup=keyboards.main_reply_keyboard(),
    )
    return ConversationHandler.END


def build_conversation() -> ConversationHandler:
    menu_filter = filters.Regex(keyboards.MENU_BUTTONS_REGEX)
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(entry, pattern=r"^menu:send$"),
            MessageHandler(filters.Regex(rf"^{keyboards.BTN_SEND}$"), entry),
        ],
        states={
            PICK_KEY: [CallbackQueryHandler(on_pick_key, pattern=r"^sendkey:")],
            ASK_NUMBER: [
                MessageHandler(menu_filter, menu_button_exit),
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_number),
            ],
            ASK_MESSAGE: [
                MessageHandler(menu_filter, menu_button_exit),
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_message),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("menu", cancel),
            CallbackQueryHandler(cancel, pattern=r"^menu:home$"),
        ],
        name="sms_send",
        persistent=False,
        per_message=False,
    )
