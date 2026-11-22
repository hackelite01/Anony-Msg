"""🗝 My Keys — list user's keys. Callable from inline button OR reply keyboard."""
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from services import db
from services import keys as keystock
from utils import keyboards

log = logging.getLogger(__name__)


async def show_my_keys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if await db.is_banned(user_id):
        return

    keys = await db.list_user_keys(user_id)
    if not keys:
        body = "🗝 You have no keys yet."
        markup = keyboards.buy_key_button()
    else:
        lines = ["🗝 *Your Keys*\n"]
        for k in keys:
            lines.append(f"🔑 `{k['code']}`\n    {k['plan_name']} • up to {k['sms_limit']} SMS")
        lines.append("\nCopy a key and paste it on our website to send SMS. Usage is shown there.")
        body = "\n".join(lines)
        markup = keyboards.web_actions(await keystock.web_url(), show_keys=False)

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


def register(app) -> None:
    app.add_handler(CallbackQueryHandler(show_my_keys, pattern=r"^menu:mykeys$"))
    app.add_handler(
        MessageHandler(filters.Regex(rf"^{keyboards.BTN_KEYS}$"), show_my_keys)
    )
