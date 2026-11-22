"""Entry point: webhook server (prod) or polling (local), handler registration, lifecycle."""
import asyncio
import logging
from http import HTTPStatus

from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
)

import config
from handlers import admin, mykeys, purchase, sms, start, support, ton_purchase
from services import db
from utils import activity_log, keyboards

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# httpx logs every request URL at INFO — and Telegram's URLs contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("bot")


# ---------- shared middleware-ish helpers ----------

async def cmd_cancel_global(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fallback /cancel when no conversation is active."""
    await update.effective_message.reply_text(
        "Nothing to cancel.", reply_markup=keyboards.main_reply_keyboard()
    )


async def cmd_diag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: live diagnostic for channel + bot setup."""
    if not config.is_admin(update.effective_user.id):
        try:
            await activity_log.unauthorized_admin_attempt(
                context, update.effective_user, "/diag"
            )
        except Exception:
            pass
        return
    lines = [f"🔧 *Diagnostic*", f"CHANNEL_ID: `{config.CHANNEL_ID!r}`"]
    if not config.CHANNEL_ID:
        lines.append("⚠️ No channel configured — membership gate is OFF.")
    else:
        try:
            chat = await context.bot.get_chat(config.CHANNEL_ID)
            lines.append(f"✅ Channel found: *{chat.title}* (type: {chat.type})")
        except Exception as e:
            lines.append(f"❌ get_chat failed: `{e}`")
            lines.append("→ Either the ID is wrong, OR the bot isn't in the channel.")
        try:
            me = await context.bot.get_chat_member(
                config.CHANNEL_ID, context.bot.id
            )
            lines.append(f"✅ Bot's own status in channel: *{me.status}*")
        except Exception as e:
            lines.append(f"❌ get_chat_member(bot) failed: `{e}`")
            lines.append("→ Bot is not in the channel. Add it as admin.")
        try:
            you = await context.bot.get_chat_member(
                config.CHANNEL_ID, update.effective_user.id
            )
            lines.append(f"✅ Your status in channel: *{you.status}*")
        except Exception as e:
            lines.append(f"❌ get_chat_member(you) failed: `{e}`")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="Markdown"
    )


async def block_banned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs before any other handler. Silently drops banned users."""
    user = update.effective_user
    if user is None:
        return
    if await db.is_banned(user.id):
        raise ApplicationHandlerStop


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Local log + post to log channel. User sees only a friendly message."""
    err = context.error
    log.exception("Unhandled exception", exc_info=err)

    # Friendly note to the user — no traceback, no internals.
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong. Please try again."
            )
    except Exception:
        pass

    # Where did this happen? Best-effort context.
    where = None
    if isinstance(update, Update):
        if update.effective_user:
            where = f"user={update.effective_user.id}"
        if update.effective_message and update.effective_message.text:
            snippet = update.effective_message.text[:60].replace("`", "'")
            where = (where or "") + f" msg='{snippet}'"
        elif update.callback_query and update.callback_query.data:
            where = (where or "") + f" cb='{update.callback_query.data}'"

    # Forward to log channel (no admin DMs).
    try:
        await activity_log.bot_error(
            context,
            type(err).__name__,
            f"{err}",
            where=where,
        )
    except Exception:
        pass


# ---------- aiohttp webhook server ----------

async def health(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


def build_webhook_handler(application: Application):
    async def handler(request: web.Request) -> web.Response:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if config.WEBHOOK_SECRET and secret != config.WEBHOOK_SECRET:
            return web.Response(status=HTTPStatus.UNAUTHORIZED, text="bad secret")
        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=HTTPStatus.BAD_REQUEST, text="bad json")
        update = Update.de_json(payload, application.bot)
        await application.process_update(update)
        return web.Response(text="ok")

    return handler


# ---------- main ----------

def _build_application() -> Application:
    builder = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN)
    if not config.USE_POLLING:
        builder = builder.updater(None)  # webhook mode, no polling Updater
    application = builder.build()

    from telegram.ext import TypeHandler

    application.add_handler(TypeHandler(Update, block_banned), group=-1)

    start.register(application)
    mykeys.register(application)
    application.add_handler(sms.build_conversation())
    purchase.register(application)
    ton_purchase.register(application)
    support.register(application)
    admin.register(application)

    application.add_handler(CommandHandler("cancel", cmd_cancel_global))
    application.add_handler(CommandHandler("diag", cmd_diag))
    application.add_error_handler(on_error)
    return application


async def _channel_self_check(application: Application) -> None:
    """At startup: report whether the bot can read its configured channel."""
    if not config.CHANNEL_ID:
        log.warning("CHANNEL_ID not set — membership gate is disabled.")
        return
    try:
        chat = await application.bot.get_chat(config.CHANNEL_ID)
        me = await application.bot.get_chat_member(config.CHANNEL_ID, application.bot.id)
        log.info(
            "Channel OK: %r (id=%s, type=%s); bot status in channel: %s",
            chat.title, chat.id, chat.type, me.status,
        )
    except Exception as e:
        log.error(
            "CHANNEL CHECK FAILED for CHANNEL_ID=%r: %s\n"
            "→ Verify the ID is correct (forward a channel message to @userinfobot to get it),\n"
            "→ AND that the bot is added as an administrator in THAT channel.",
            config.CHANNEL_ID, e,
        )


async def _run_polling(application: Application) -> None:
    log.info("Starting in POLLING mode (local development)")
    # Drop any webhook so polling can receive updates.
    await application.bot.delete_webhook(drop_pending_updates=True)
    await application.initialize()
    await application.start()
    await _channel_self_check(application)
    ton_purchase.start_watcher(application)
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    log.info("Polling started — send /start to your bot in Telegram")
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    finally:
        log.info("Shutting down")
        await ton_purchase.stop_watcher()
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


async def _run_webhook(application: Application) -> None:
    if not config.WEBHOOK_URL or "your-app.onrender.com" in config.WEBHOOK_URL:
        raise SystemExit(
            "WEBHOOK_URL is missing or still set to the placeholder. "
            "Set USE_POLLING=true for local dev, or set a real public URL."
        )

    webhook_url = config.WEBHOOK_URL + config.WEBHOOK_PATH
    await application.bot.set_webhook(
        url=webhook_url,
        secret_token=config.WEBHOOK_SECRET or None,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )
    log.info("Webhook set: %s", webhook_url)

    await application.initialize()
    await application.start()
    await _channel_self_check(application)
    ton_purchase.start_watcher(application)

    web_app = web.Application()
    web_app.router.add_post(config.WEBHOOK_PATH, build_webhook_handler(application))
    web_app.router.add_get("/", health)
    web_app.router.add_get("/health", health)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=config.PORT)
    await site.start()
    log.info("HTTP server listening on 0.0.0.0:%s", config.PORT)

    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    finally:
        log.info("Shutting down")
        await ton_purchase.stop_watcher()
        await application.stop()
        await application.shutdown()
        await runner.cleanup()


async def main_async() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")

    await db.init_db()
    application = _build_application()

    try:
        if config.USE_POLLING:
            await _run_polling(application)
        else:
            await _run_webhook(application)
    finally:
        await db.close_db()


def main() -> None:
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
