"""💬 Contact Admin — two-way messaging between users and admins via the bot.

User flow: menu → 💬 Contact Admin → user types one message → bot forwards
to every admin's DM with a 📩 Reply button.

Admin flow: clicks 📩 Reply on a forwarded support message → enters reply text
→ bot DMs the user as "Admin reply: ...". Each forward carries the target user
id in its callback_data, so admins can reply from any of their devices and
it'll route correctly.
"""
import logging
import time

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
from services import db
from utils import activity_log, keyboards

log = logging.getLogger(__name__)

USER_INPUT, ADMIN_REPLY = range(100, 102)  # offset to avoid collisions

# Anti-spam: minimum seconds between contact submissions per user
CONTACT_COOLDOWN_SECONDS = 30


# ---------- USER SIDE ----------

async def on_contact_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q:
        await q.answer()
    user_id = update.effective_user.id
    if await db.is_banned(user_id):
        return ConversationHandler.END

    target = update.effective_message
    last = context.user_data.get("contact_last_ts", 0)
    elapsed = time.time() - last
    if elapsed < CONTACT_COOLDOWN_SECONDS:
        wait = int(CONTACT_COOLDOWN_SECONDS - elapsed)
        await target.reply_text(
            f"⏳ Please wait {wait}s before sending another message.",
        )
        return ConversationHandler.END

    await target.reply_text(
        "💬 *Contact Admin*\n\n"
        "Type your message in one go and send it.\n"
        "Use /cancel to abort.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return USER_INPUT


async def on_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("❌ Empty message. Try again or /cancel.")
        return USER_INPUT
    if len(text) > 2000:
        await update.message.reply_text("❌ Too long (max 2000 chars). Try again.")
        return USER_INPUT

    context.user_data["contact_last_ts"] = time.time()

    forwarded = 0
    failed = 0
    uname = f"@{user.username}" if user.username else f"id={user.id}"
    header = (
        "📨 *Message from user*\n"
        f"From: {uname}\n"
        f"User ID: `{user.id}`\n\n"
        f"{_escape_md(text)}"
    )
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📩 Reply", callback_data=f"sup:reply:{user.id}")]]
    )
    for admin_id in config.ADMIN_USER_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=header,
                reply_markup=markup,
                parse_mode=ParseMode.MARKDOWN,
            )
            forwarded += 1
        except TelegramError as e:
            log.warning("Forward to admin %s failed: %s", admin_id, e)
            failed += 1

    # Mirror to log channel without buttons
    try:
        await activity_log.post(
            context,
            "💬 *User contacted admin*\n"
            f"From: {uname}\n"
            f"User ID: `{user.id}`\n\n"
            f"{_escape_md(text)}",
        )
    except Exception:
        pass

    if forwarded == 0:
        await update.message.reply_text(
            "⚠️ Could not deliver to any admin right now. Please try again later.",
            reply_markup=keyboards.back_to_menu(),
        )
    else:
        await update.message.reply_text(
            "✅ Message delivered. An admin will reply soon.",
            reply_markup=keyboards.back_to_menu(),
        )
    return ConversationHandler.END


# ---------- ADMIN SIDE ----------

async def on_admin_reply_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    user = update.effective_user
    if not config.is_admin(user.id):
        try:
            await activity_log.unauthorized_admin_attempt(
                context, user, q.data or "support reply"
            )
        except Exception:
            pass
        await q.answer("🚫 Not authorized.", show_alert=True)
        return ConversationHandler.END

    target_id = int(q.data.split(":")[2])
    context.user_data["support_reply_to"] = target_id
    await q.answer()
    await q.message.reply_text(
        f"✍️ Type your reply to user `{target_id}`.\n/cancel to abort.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_REPLY


async def on_admin_reply_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    target_id = context.user_data.pop("support_reply_to", None)
    if target_id is None:
        await update.message.reply_text("❌ No active reply target. Tap 📩 Reply again.")
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("❌ Empty reply. Try again or /cancel.")
        context.user_data["support_reply_to"] = target_id  # restore
        return ADMIN_REPLY
    if len(text) > 2000:
        await update.message.reply_text("❌ Too long (max 2000 chars). Try again.")
        context.user_data["support_reply_to"] = target_id
        return ADMIN_REPLY

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"📬 *Admin reply:*\n\n{_escape_md(text)}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboards.back_to_menu(),
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ Couldn't reach user: {e}")
        return ConversationHandler.END

    await update.message.reply_text("✅ Reply sent.")

    # Mirror to log channel
    try:
        admin = update.effective_user
        admin_tag = f"@{admin.username}" if admin.username else f"id={admin.id}"
        await activity_log.post(
            context,
            "📩 *Admin replied*\n"
            f"By: {admin_tag}\n"
            f"To user: `{target_id}`\n\n"
            f"{_escape_md(text)}",
        )
    except Exception:
        pass

    return ConversationHandler.END


# ---------- shared ----------

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("support_reply_to", None)
    await update.effective_message.reply_text("❎ Cancelled.")
    return ConversationHandler.END


def _escape_md(text: str) -> str:
    """Minimal escape so user-supplied text doesn't break Markdown rendering."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


async def menu_button_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Tapped a main-menu button mid-conversation → exit cleanly."""
    context.user_data.pop("support_reply_to", None)
    await update.effective_message.reply_text(
        "↩️ Returning to menu — tap your selection again.",
        reply_markup=keyboards.main_reply_keyboard(),
    )
    return ConversationHandler.END


def register(app) -> None:
    menu_filter = filters.Regex(keyboards.MENU_BUTTONS_REGEX)

    # User flow: contact admin
    app.add_handler(
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(on_contact_entry, pattern=r"^menu:support$"),
                MessageHandler(filters.Regex(rf"^{keyboards.BTN_SUPPORT}$"), on_contact_entry),
            ],
            states={
                USER_INPUT: [
                    MessageHandler(menu_filter, menu_button_exit),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, on_user_message),
                ],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            name="support_user",
            persistent=False,
        )
    )

    # Admin flow: reply
    app.add_handler(
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(on_admin_reply_entry, pattern=r"^sup:reply:\d+$")
            ],
            states={
                ADMIN_REPLY: [
                    MessageHandler(menu_filter, menu_button_exit),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, on_admin_reply_text),
                ],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            name="support_admin_reply",
            persistent=False,
        )
    )
