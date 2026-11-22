"""/start, /menu, membership gating."""
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from services import db
from utils import activity_log, keyboards

log = logging.getLogger(__name__)


async def is_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, str | None]:
    """Returns (is_member, error_kind).
    error_kind: None if check succeeded, "bot_setup" if bot can't access channel,
    "user" if call worked but user isn't in channel."""
    if not config.CHANNEL_ID:
        return True, None
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(config.CHANNEL_ID, user_id)
        if member.status in ("member", "administrator", "creator", "owner"):
            return True, None
        return False, "user"
    except BadRequest as e:
        msg = str(e).lower()
        if "chat not found" in msg or "bot is not a member" in msg or "member list is inaccessible" in msg:
            log.error(
                "BOT SETUP ERROR: cannot read channel %s membership — "
                "make sure the bot is added as an ADMINISTRATOR in the channel. (%s)",
                config.CHANNEL_ID, e,
            )
            return False, "bot_setup"
        log.warning("Membership check failed for %s: %s", user_id, e)
        return False, "bot_setup"
    except TelegramError as e:
        log.warning("Membership check failed for %s: %s", user_id, e)
        return False, "bot_setup"


async def gate_or_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the main menu if user is a member, else send membership gate."""
    ok, kind = await is_member(update, context)
    if ok:
        await _send_menu(update, context)
    elif kind == "bot_setup":
        await _send_setup_warning(update)
    else:
        await _send_gate(update, context)


async def _send_setup_warning(update: Update) -> None:
    await update.effective_message.reply_text(
        "⚙️ The bot can't verify channel membership right now.\n"
        "Please contact admin — the bot needs to be added as an *administrator* "
        "in the verification channel.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _send_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🔒 *Channel membership required*\n\n"
        "Please join our channel and then tap *Check Membership*."
    )
    target = update.effective_message
    await target.reply_text(
        text,
        reply_markup=keyboards.membership_gate(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _send_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = update.effective_message
    await target.reply_text(
        "🏠 *Main Menu*\nUse the buttons below ↓",
        reply_markup=keyboards.main_reply_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    is_new = False
    try:
        _, is_new = await db.upsert_user(user.id, user.username or "", user.first_name or "")
    except Exception as e:
        log.exception("upsert_user failed: %s", e)

    if is_new:
        await activity_log.new_user(context, user)

    if await db.is_banned(user.id):
        return  # silently ignore

    # Welcome GIF
    if config.WELCOME_GIF_URL:
        try:
            await update.effective_message.reply_animation(config.WELCOME_GIF_URL)
        except TelegramError as e:
            log.warning("Welcome GIF failed: %s", e)

    await update.effective_message.reply_text(
        config.WELCOME_TEXT, parse_mode=ParseMode.MARKDOWN
    )
    await gate_or_menu(update, context)


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await db.is_banned(update.effective_user.id):
        return
    await gate_or_menu(update, context)


async def on_check_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    ok, kind = await is_member(update, context)
    if ok:
        await q.answer("Verified!")
        await _safe_edit(q, "✅ Membership confirmed!")
        # ReplyKeyboard can't be set via edit — send a new message.
        await q.message.reply_text(
            "🏠 Use the menu below ↓",
            reply_markup=keyboards.main_reply_keyboard(),
        )
        return
    if kind == "bot_setup":
        await q.answer("Bot setup issue — contact admin.", show_alert=True)
        await _safe_edit(
            q,
            "⚙️ The bot can't verify membership right now (setup issue). "
            "Please contact admin.",
        )
        return
    # User just isn't in the channel yet
    await q.answer("Not in channel yet — join and try again.", show_alert=True)
    await _safe_edit(
        q,
        "❌ You're not in the channel yet. Please join, then tap *Check Membership* again.",
        reply_markup=keyboards.membership_gate(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _safe_edit(query, text: str, **kwargs) -> None:
    """Edit message, swallowing 'Message is not modified' since it's harmless."""
    try:
        await query.edit_message_text(text, **kwargs)
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        raise


async def on_menu_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    ok, kind = await is_member(update, context)
    if not ok:
        if kind == "bot_setup":
            await _safe_edit(q, "⚙️ Membership check unavailable. Contact admin.")
        else:
            await _safe_edit(
                q,
                "🔒 Please verify channel membership first.",
                reply_markup=keyboards.membership_gate(),
            )
        return
    await _safe_edit(q, "🏠 Use the menu below ↓")


async def on_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Help, callable from inline button OR reply-keyboard text."""
    cfg = await db.get_config()
    extra = ""
    if cfg.get("admin_username"):
        extra += f"\n👤 Admin: @{cfg['admin_username'].lstrip('@')}"
    if config.SUPPORT_CHANNEL_URL:
        extra += f"\n📣 Support: {config.SUPPORT_CHANNEL_URL}"
    body = config.HELP_TEXT + extra

    if update.callback_query:
        await update.callback_query.answer()
        await _safe_edit(update.callback_query, body, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.effective_message.reply_text(body, parse_mode=ParseMode.MARKDOWN)


def register(app) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("help", on_help))
    app.add_handler(CallbackQueryHandler(on_check_membership, pattern=r"^check_membership$"))
    app.add_handler(CallbackQueryHandler(on_menu_home, pattern=r"^menu:home$"))
    app.add_handler(CallbackQueryHandler(on_help, pattern=r"^menu:help$"))
    # Reply-keyboard button: ℹ️ Help
    app.add_handler(
        MessageHandler(filters.Regex(rf"^{keyboards.BTN_HELP}$"), on_help)
    )
