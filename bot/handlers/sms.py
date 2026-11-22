"""📤 Send SMS — SMS is sent on the website; the bot only sells and lists the keys.

The website holds the real provider keys and does the sending, so this button just points the
user there. (There is intentionally no connection between the bot and the website.)
"""
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from services import db
from services import keys as keystock
from utils import keyboards

log = logging.getLogger(__name__)


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """From the inline button OR the reply-keyboard 📤 Send SMS button."""
    q = update.callback_query
    if q:
        await q.answer()

    user_id = update.effective_user.id
    if await db.is_banned(user_id):
        return

    target = update.effective_message
    if not await db.list_user_keys(user_id):
        await target.reply_text(
            "📤 You have no keys yet. Buy one first, then use it on our website.",
            reply_markup=keyboards.buy_key_button(),
        )
        return

    url = await keystock.web_url()
    text = (
        "📤 *Send SMS on our website*\n\n"
        "1. Copy your key from 🗝 My Keys\n"
        "2. Open the website, paste the key, then enter the number and your message."
    )
    if not url:
        text += "\n\nAsk the admin for the website address."
    await target.reply_text(
        text, reply_markup=keyboards.web_actions(url), parse_mode=ParseMode.MARKDOWN
    )


def register(app) -> None:
    app.add_handler(CallbackQueryHandler(entry, pattern=r"^menu:send$"))
    app.add_handler(MessageHandler(filters.Regex(rf"^{keyboards.BTN_SEND}$"), entry))
