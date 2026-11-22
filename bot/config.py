"""Centralized configuration loaded from environment variables."""
import os
from dotenv import load_dotenv

load_dotenv()


def _parse_admins(raw: str) -> set[int]:
    if not raw:
        return set()
    out = set()
    for piece in raw.split(","):
        piece = piece.strip()
        if piece:
            try:
                out.add(int(piece))
            except ValueError:
                continue
    return out


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip().rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "telegram-webhook-secret").strip()
WEBHOOK_PATH = "/webhook"

def _parse_chat_id(raw: str) -> str | int:
    raw = raw.strip()
    if not raw:
        return ""
    try:
        return int(raw)
    except ValueError:
        return raw  # leave as-is if it's a username like "@foo"


CHANNEL_ID = _parse_chat_id(os.getenv("CHANNEL_ID", ""))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip().lstrip("@")

LOG_CHANNEL_ID = _parse_chat_id(os.getenv("LOG_CHANNEL_ID", ""))

ADMIN_USER_IDS = _parse_admins(os.getenv("ADMIN_USER_IDS", ""))

MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "anonsmbot").strip()

WELCOME_GIF_URL = os.getenv("WELCOME_GIF_URL", "").strip()
SUPPORT_CHANNEL_URL = os.getenv("SUPPORT_CHANNEL_URL", "").strip()
SMS_SENDER_NAME = os.getenv("SMS_SENDER_NAME", "Anonymous").strip()

PORT = int(os.getenv("PORT", "8000"))
USE_POLLING = os.getenv("USE_POLLING", "false").strip().lower() in ("1", "true", "yes")

# --- TON payments (verified on-chain via TON Center; see services/ton.py) ---
# Receiving wallet can also be set from /admin -> Settings -> Set TON Wallet.
TON_WALLET_ADDRESS = os.getenv("TON_WALLET_ADDRESS", "").strip()
TONCENTER_URL = os.getenv("TONCENTER_URL", "https://toncenter.com").strip().rstrip("/")
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY", "").strip()  # optional, raises rate limit
TON_POLL_SECONDS = int(os.getenv("TON_POLL_SECONDS", "20"))
TON_ORDER_TTL_MINUTES = int(os.getenv("TON_ORDER_TTL_MINUTES", "60"))
# Payments arriving after the TTL are still honoured for this long.
TON_WATCH_HOURS = 24

# --- Bitcoin payments (verified on-chain; see services/btc.py) ---
# Esplora-compatible APIs, tried in order (first one that answers wins).
BTC_API_URLS = [
    u.strip().rstrip("/")
    for u in os.getenv("BTC_API_URLS", "https://mempool.space,https://blockstream.info").split(",")
    if u.strip()
]
BTC_MIN_CONFIRMATIONS = int(os.getenv("BTC_MIN_CONFIRMATIONS", "1"))
BTC_POLL_SECONDS = int(os.getenv("BTC_POLL_SECONDS", "60"))
BTC_ORDER_TTL_MINUTES = int(os.getenv("BTC_ORDER_TTL_MINUTES", "60"))
BTC_WATCH_HOURS = 24

# --- UPI (manual approval): max unreviewed submissions per user ---
UPI_MAX_PENDING_PER_USER = 3

# Colored buttons (Bot API 9.4). Set BUTTON_STYLES=false to show plain grey buttons.
BUTTON_STYLES = os.getenv("BUTTON_STYLES", "true").strip().lower() in ("1", "true", "yes")

SMS_PER_PROVIDER_KEY = 5
SMS_API_TIMEOUT = 10

WELCOME_TEXT = (
    "👋 *Welcome to Anonymous SMS Bot*\n\n"
    "Send anonymous SMS worldwide using prepaid keys.\n\n"
    "1. Buy a key (pay with Telegram Stars ⭐, crypto or UPI)\n"
    "2. Use the key to send SMS\n"
    "3. Each plan has a fixed SMS limit\n\n"
    "Join our channel below, then verify membership to continue."
)

HELP_TEXT = (
    "ℹ️ *How to use this bot*\n\n"
    "• `/start` — register & verify channel membership\n"
    "• `/menu` — open main menu\n"
    "• `/cancel` — cancel current operation\n\n"
    "*Buying a key:* tap 🛒 Buy Key, choose a plan, then pick a payment method.\n"
    "*Sending SMS:* tap 📤 Send SMS, choose a key, enter number then message.\n"
    "*Your keys:* tap 🗝 My Keys to see remaining SMS.\n\n"
    "Numbers must include country code (digits only, 10–15 digits).\n"
)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS
