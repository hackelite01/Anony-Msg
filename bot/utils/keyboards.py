"""All keyboard builders in one place, with one consistent visual system.

Layout rules
  * Choices the user must pick (plans, payment methods) are easy to hit; closely related small
    actions share a row. Payment methods sit in a 2-column grid with the price on the button.
  * Admin menus are 2-column grids. Toggles show a 🟢/🔴 state and sit next to their setup button.
  * Every screen ends with a navigation row (⬅️ Back / 🏠 Main Menu / ⬅️ Admin Menu).

Colors (Bot API 9.4 button styles — older clients just show normal buttons)
  primary (blue)  the main forward action      success (green)  confirm / turn on / add
  danger  (red)   cancel / reject / delete     (none, grey)     navigation and neutral actions
Set BUTTON_STYLES=false to switch colors off.

Two flavors of keyboard:
  * ReplyKeyboardMarkup — the persistent main menu at the bottom of the chat.
  * InlineKeyboardMarkup — buttons attached to a specific message.
"""
import re
from typing import Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

import config
from services.btc import fmt_btc
from services.payments import fmt_inr
from services.ton import fmt_ton

PRIMARY, SUCCESS, DANGER = "primary", "success", "danger"

# Button labels (also used as MessageHandler regex matches)
BTN_SEND = "📤 Send SMS"
BTN_BUY = "🛒 Buy Key"
BTN_KEYS = "🗝 My Keys"
BTN_SUPPORT = "💬 Contact Admin"
BTN_HELP = "ℹ️ Help"

ALL_MENU_BUTTONS = (BTN_SEND, BTN_BUY, BTN_KEYS, BTN_SUPPORT, BTN_HELP)

# Regex matching any menu button label exactly (used as MessageHandler filter)
MENU_BUTTONS_REGEX = re.compile(
    "^(" + "|".join(re.escape(b) for b in ALL_MENU_BUTTONS) + ")$"
)


# ---------- building blocks ----------

def btn(
    text: str,
    cb: Optional[str] = None,
    *,
    url: Optional[str] = None,
    style: Optional[str] = None,
) -> InlineKeyboardButton:
    """Inline button; `style` (primary/success/danger) is sent only when BUTTON_STYLES is on."""
    extra = {"api_kwargs": {"style": style}} if style and config.BUTTON_STYLES else {}
    return InlineKeyboardButton(text, callback_data=cb, url=url, **extra)


def _grid(buttons: list, cols: int = 2) -> list[list]:
    return [buttons[i : i + cols] for i in range(0, len(buttons), cols)]


def _markup(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([list(r) for r in rows if r])


def _home() -> InlineKeyboardButton:
    return btn("🏠 Main Menu", "menu:home")


def _admin_home() -> InlineKeyboardButton:
    return btn("⬅️ Admin Menu", "adm:home")


def _state(on: bool) -> str:
    return "🟢" if on else "🔴"


def _toggle(label: str, on: bool, cb: str) -> InlineKeyboardButton:
    """Green when ON, grey when OFF, with a matching 🟢/🔴 dot."""
    return btn(f"{_state(on)} {label}", cb, style=SUCCESS if on else None)


# ---------- reply keyboard (main menu) ----------

def _rk(text: str, style: Optional[str] = None) -> KeyboardButton:
    if style and config.BUTTON_STYLES:
        return KeyboardButton(text, api_kwargs={"style": style})
    return KeyboardButton(text)


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Persistent menu keyboard at the bottom of the chat."""
    return ReplyKeyboardMarkup(
        [
            [_rk(BTN_SEND, PRIMARY), _rk(BTN_BUY, SUCCESS)],
            [_rk(BTN_KEYS), _rk(BTN_SUPPORT)],
            [_rk(BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Pick an option…",
    )


def remove_reply_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


# ---------- user: onboarding / navigation ----------

def membership_gate() -> InlineKeyboardMarkup:
    rows = []
    if config.CHANNEL_USERNAME:
        rows.append(
            [btn("📢 Join Channel", url=f"https://t.me/{config.CHANNEL_USERNAME}", style=PRIMARY)]
        )
    rows.append([btn("✅ Check Membership", "check_membership", style=SUCCESS)])
    return _markup(*rows)


def buy_key_button() -> InlineKeyboardMarkup:
    return _markup([btn("🛒 Buy Key", "menu:buy", style=SUCCESS)], [_home()])


def back_to_menu() -> InlineKeyboardMarkup:
    return _markup([_home()])


def contact_admin_button(admin_username: str | None) -> InlineKeyboardMarkup:
    rows = []
    if admin_username:
        rows.append(
            [btn("📩 Contact Admin", url=f"https://t.me/{admin_username.lstrip('@')}", style=PRIMARY)]
        )
    rows.append([_home()])
    return _markup(*rows)


# ---------- user: buying ----------

def plans_keyboard(plans: list[dict]) -> InlineKeyboardMarkup:
    """One plan per row: name + SMS count only. Prices appear on the payment-method screen."""
    rows = []
    for p in plans:
        if p.get("is_free"):
            rows.append([btn(f"🎁 {p['name']} • {p['sms_limit']} SMS • FREE", f"buy:{p['_id']}", style=SUCCESS)])
        else:
            rows.append([btn(f"💎 {p['name']} • {p['sms_limit']} SMS", f"buy:{p['_id']}", style=PRIMARY)])
    rows.append([_home()])
    return _markup(*rows)


def payment_methods(
    plan: dict, methods: list[str], promo_button: bool = False
) -> InlineKeyboardMarkup:
    """Payment methods in a 2-column grid, each button showing its price."""
    labels = {
        "stars": lambda: f"⭐ {plan['price_stars']} Stars",
        "ton": lambda: f"💎 {fmt_ton(plan['price_ton_nano'])} TON",
        "btc": lambda: f"₿ {fmt_btc(plan['price_btc_sats'])} BTC",
        "upi": lambda: f"🇮🇳 ₹{fmt_inr(plan['price_inr_paise'])} UPI",
    }
    pay = [
        btn(labels[m](), f"pay:{m}:{plan['_id']}", style=PRIMARY)
        for m in ("stars", "ton", "btc", "upi")
        if m in methods
    ]
    rows = _grid(pay, 2)
    if promo_button:
        rows.append([btn("🎟 Have a promo code?", f"promo:ask:{plan['_id']}")])
    rows.append([btn("⬅️ Back", "menu:buy")])
    return _markup(*rows)


def free_plan_screen(plan_id: str, claimed: bool) -> InlineKeyboardMarkup:
    rows = []
    if not claimed:
        rows.append([btn("🎁 Claim free key", f"free:claim:{plan_id}", style=SUCCESS)])
    rows.append([btn("⬅️ Back", "menu:buy")])
    return _markup(*rows)


def ton_order(order_id: str, pay_url: str) -> InlineKeyboardMarkup:
    return _markup(
        [btn("👛 Open in Tonkeeper", url=pay_url, style=PRIMARY)],
        [btn("✅ I've paid — check now", f"ton:chk:{order_id}", style=SUCCESS)],
        [btn("❌ Cancel order", f"ton:cancel:{order_id}", style=DANGER)],
    )


def btc_order(order_id: str) -> InlineKeyboardMarkup:
    return _markup(
        [btn("✅ I've paid — check now", f"btc:chk:{order_id}", style=SUCCESS)],
        [btn("❌ Cancel order", f"btc:cancel:{order_id}", style=DANGER)],
    )


def upi_instructions(plan_id: str) -> InlineKeyboardMarkup:
    return _markup(
        [btn("✅ I've paid — send UTR", f"upi:paid:{plan_id}", style=SUCCESS)],
        [btn("⬅️ Back", f"buy:{plan_id}")],
    )


def keys_picker(keys: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [btn(f"🔑 {k['code']} • {k['sms_limit'] - k['sms_used']} SMS left", f"sendkey:{k['code']}", style=PRIMARY)]
        for k in keys
    ]
    rows.append([_home()])
    return _markup(*rows)


# ---------- admin: purchase decisions ----------

def admin_request_decision(req_id: str) -> InlineKeyboardMarkup:
    # paired action → one row so it reads as a single decision
    return _markup(
        [
            btn("✅ Approve", f"adm:approve:{req_id}", style=SUCCESS),
            btn("❌ Reject", f"adm:reject:{req_id}", style=DANGER),
        ]
    )


def admin_reject_reasons(req_id: str) -> InlineKeyboardMarkup:
    return _markup(
        [
            btn("📦 Out of Stock", f"adm:rsn:{req_id}:out_of_stock"),
            btn("🚫 Suspicious", f"adm:rsn:{req_id}:suspicious"),
        ],
        [btn("❔ Other", f"adm:rsn:{req_id}:other")],
    )


# ---------- admin: main panel ----------

def admin_main() -> InlineKeyboardMarkup:
    return _markup(
        [btn("👥 Users", "adm:users"), btn("📊 Stats", "adm:stats")],
        [btn("🗄 Provider Keys", "adm:pk"), btn("📋 Plans", "adm:plans")],
        [btn("🔑 Pending", "adm:pending", style=PRIMARY), btn("📜 Logs", "adm:logs:0")],
        [btn("🎁 Gift Key", "adm:gift"), btn("🎟 Promo Codes", "adm:promo")],
        [btn("📢 Broadcast", "adm:broadcast"), btn("⚙️ Settings", "adm:settings")],
    )


def admin_back() -> InlineKeyboardMarkup:
    return _markup([_admin_home()])


def admin_pk_menu() -> InlineKeyboardMarkup:
    return _markup(
        [btn("📋 List Keys", "adm:pk:list"), btn("➕ Add Keys", "adm:pk:add", style=SUCCESS)],
        [btn("🏊 Pool Status", "adm:pk:pool")],
        [_admin_home()],
    )


def admin_plans_menu() -> InlineKeyboardMarkup:
    return _markup(
        [btn("📋 List", "adm:plans:list"), btn("➕ Add Plan", "adm:plans:add", style=SUCCESS)],
        [btn("💎 TON price", "adm:plans:tonp"), btn("₿ BTC price", "adm:plans:pp:btc")],
        [btn("🇮🇳 UPI price", "adm:plans:pp:inr"), btn("🆓 Free ↔ Paid", "adm:plans:free")],
        [btn("⏸ Deactivate", "adm:plans:deact"), btn("🗑 Delete", "adm:plans:del", style=DANGER)],
        [_admin_home()],
    )


def admin_promo_menu() -> InlineKeyboardMarkup:
    return _markup(
        [btn("➕ New Code", "adm:promo:new", style=SUCCESS), btn("📋 List", "adm:promo:list")],
        [btn("🚫 Deactivate Code", "adm:promo:deact", style=DANGER)],
        [_admin_home()],
    )


def admin_confirm_delete_plan(plan_id: str) -> InlineKeyboardMarkup:
    return _markup(
        [
            btn("🗑 Yes, delete", f"adm:plans:del_yes:{plan_id}", style=DANGER),
            btn("⬅️ No", "adm:plans"),
        ]
    )


def admin_plans_picker(plans: list[dict], action: str) -> InlineKeyboardMarkup:
    rows = [
        [btn(f"💎 {p['name']} • {p['sms_limit']} SMS • {p['price_stars']}⭐", f"adm:plans:{action}:{p['_id']}")]
        for p in plans
    ]
    rows.append([btn("⬅️ Back", "adm:plans")])
    return _markup(*rows)


def admin_settings(cfg: dict) -> InlineKeyboardMarkup:
    return _markup(
        [_toggle("Auto-Approve", bool(cfg.get("auto_approve")), "adm:settings:toggle_auto")],
        [
            btn("💳 Payment Methods", "adm:pay", style=PRIMARY),
            btn("📱 Admin Username", "adm:settings:set_admin_user"),
        ],
        [_admin_home()],
    )


def admin_payments_menu(cfg: dict) -> InlineKeyboardMarkup:
    """Each method: its on/off toggle (🟢/🔴) next to its setup button."""
    on = lambda key, default=False: bool(cfg.get(key, default))
    return _markup(
        [_toggle("⭐ Stars", on("stars_enabled", True), "adm:pay:t:stars")],
        [
            _toggle("💎 TON", on("ton_enabled"), "adm:pay:t:ton"),
            btn("💰 Wallet", "adm:settings:set_ton"),
        ],
        [
            _toggle("₿ Bitcoin", on("btc_enabled"), "adm:pay:t:btc"),
            btn("📥 Address", "adm:pay:set_btc"),
        ],
        [
            _toggle("🇮🇳 UPI", on("upi_enabled"), "adm:pay:t:upi"),
            btn("🆔 UPI ID", "adm:pay:set_upi"),
        ],
        [btn("⬅️ Settings", "adm:settings")],
    )


def admin_logs_pager(skip: int, has_more: bool) -> InlineKeyboardMarkup:
    nav = []
    if skip > 0:
        nav.append(btn("⬅️ Prev", f"adm:logs:{max(skip - 10, 0)}"))
    if has_more:
        nav.append(btn("Next ➡️", f"adm:logs:{skip + 10}"))
    return _markup(nav, [_admin_home()])
