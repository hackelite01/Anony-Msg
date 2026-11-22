"""All keyboard builders centralized for consistency.

Two flavors:
- ReplyKeyboardMarkup: persistent keyboard at the bottom of the chat. Used for
  the main menu so it's always one tap away.
- InlineKeyboardMarkup: buttons attached to a specific message. Used for
  sub-menus, picker lists, and admin actions where context matters.
"""
import re

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

import config
from services.ton import fmt_ton

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


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Persistent menu keyboard at the bottom of the chat."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_SEND), KeyboardButton(BTN_BUY)],
            [KeyboardButton(BTN_KEYS), KeyboardButton(BTN_SUPPORT)],
            [KeyboardButton(BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def remove_reply_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


# Inline main_menu() removed — replaced by the persistent reply keyboard above.
# For "back to home" UX, just call main_reply_keyboard() in a new send_message.


def membership_gate() -> InlineKeyboardMarkup:
    rows = []
    if config.CHANNEL_USERNAME:
        rows.append(
            [
                InlineKeyboardButton(
                    "📢  Join Channel",
                    url=f"https://t.me/{config.CHANNEL_USERNAME}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton("✅  Check Membership", callback_data="check_membership")]
    )
    return InlineKeyboardMarkup(rows)


def buy_key_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛒  Buy Key", callback_data="menu:buy")],
            [InlineKeyboardButton("⬅️  Main Menu", callback_data="menu:home")],
        ]
    )


def back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️  Main Menu", callback_data="menu:home")]]
    )


def _plan_label(p: dict, ton_enabled: bool) -> str:
    label = f"💎 {p['name']} • {p['sms_limit']} SMS • {p['price_stars']}⭐"
    if ton_enabled and p.get("price_ton_nano"):
        label += f" / {fmt_ton(p['price_ton_nano'])} TON"
    return label


def plans_keyboard(plans: list[dict], ton_enabled: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                _plan_label(p, ton_enabled),
                callback_data=f"buy:{p['_id']}",
            )
        ]
        for p in plans
    ]
    rows.append([InlineKeyboardButton("⬅️  Cancel", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def payment_methods(plan: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"⭐  Pay {plan['price_stars']} Stars",
                    callback_data=f"pay:stars:{plan['_id']}",
                )
            ],
            [
                InlineKeyboardButton(
                    f"💎  Pay {fmt_ton(plan['price_ton_nano'])} TON",
                    callback_data=f"pay:ton:{plan['_id']}",
                )
            ],
            [InlineKeyboardButton("⬅️  Back", callback_data="menu:buy")],
        ]
    )


def ton_order(order_id: str, pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👛  Open in Tonkeeper", url=pay_url)],
            [InlineKeyboardButton("✅  I've paid — check now", callback_data=f"ton:chk:{order_id}")],
            [InlineKeyboardButton("❌  Cancel order", callback_data=f"ton:cancel:{order_id}")],
        ]
    )


def keys_picker(keys: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for k in keys:
        remaining = k["sms_limit"] - k["sms_used"]
        rows.append(
            [
                InlineKeyboardButton(
                    f"🔑 {k['code']} • {remaining} SMS left",
                    callback_data=f"sendkey:{k['code']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("⬅️  Cancel", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def contact_admin_button(admin_username: str | None) -> InlineKeyboardMarkup:
    rows = []
    if admin_username:
        clean = admin_username.lstrip("@")
        rows.append(
            [InlineKeyboardButton("📩  Contact Admin", url=f"https://t.me/{clean}")]
        )
    rows.append([InlineKeyboardButton("⬅️  Main Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def admin_request_decision(req_id: str) -> InlineKeyboardMarkup:
    # paired action → keep on one row so user sees them as a single decision
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"adm:approve:{req_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"adm:reject:{req_id}"),
            ]
        ]
    )


def admin_reject_reasons(req_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📦  Out of Stock", callback_data=f"adm:rsn:{req_id}:out_of_stock")],
            [InlineKeyboardButton("🚫  Suspicious", callback_data=f"adm:rsn:{req_id}:suspicious")],
            [InlineKeyboardButton("❔  Other", callback_data=f"adm:rsn:{req_id}:other")],
        ]
    )


# ---------- admin panel ----------

def admin_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👥  Users", callback_data="adm:users")],
            [InlineKeyboardButton("📊  Stats", callback_data="adm:stats")],
            [InlineKeyboardButton("🗄  Provider Keys", callback_data="adm:pk")],
            [InlineKeyboardButton("📋  Plans", callback_data="adm:plans")],
            [InlineKeyboardButton("🔑  Pending Approvals", callback_data="adm:pending")],
            [InlineKeyboardButton("📜  Logs", callback_data="adm:logs:0")],
            [InlineKeyboardButton("📢  Broadcast", callback_data="adm:broadcast")],
            [InlineKeyboardButton("⚙️  Settings", callback_data="adm:settings")],
        ]
    )


def admin_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️  Admin Menu", callback_data="adm:home")]]
    )


def admin_pk_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋  List Keys", callback_data="adm:pk:list")],
            [InlineKeyboardButton("➕  Add Keys", callback_data="adm:pk:add")],
            [InlineKeyboardButton("🔴  Pool Status", callback_data="adm:pk:pool")],
            [InlineKeyboardButton("⬅️  Admin Menu", callback_data="adm:home")],
        ]
    )


def admin_plans_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋  List Plans", callback_data="adm:plans:list")],
            [InlineKeyboardButton("➕  Add Plan", callback_data="adm:plans:add")],
            [InlineKeyboardButton("💎  Set TON Price", callback_data="adm:plans:tonp")],
            [InlineKeyboardButton("🔴  Deactivate Plan", callback_data="adm:plans:deact")],
            [InlineKeyboardButton("🗑  Delete Plan", callback_data="adm:plans:del")],
            [InlineKeyboardButton("⬅️  Admin Menu", callback_data="adm:home")],
        ]
    )


def admin_confirm_delete_plan(plan_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑  Yes, delete", callback_data=f"adm:plans:del_yes:{plan_id}"),
                InlineKeyboardButton("⬅️  No", callback_data="adm:plans"),
            ]
        ]
    )


def admin_plans_picker(plans: list[dict], action: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"💎 {p['name']} • {p['sms_limit']} SMS • {p['price_stars']}⭐",
                callback_data=f"adm:plans:{action}:{p['_id']}",
            )
        ]
        for p in plans
    ]
    rows.append([InlineKeyboardButton("⬅️  Back", callback_data="adm:plans")])
    return InlineKeyboardMarkup(rows)


def admin_settings(cfg: dict) -> InlineKeyboardMarkup:
    auto = "ON" if cfg.get("auto_approve") else "OFF"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"🔄  Auto-Approve: {auto}",
                    callback_data="adm:settings:toggle_auto",
                )
            ],
            [
                InlineKeyboardButton(
                    f"💎  TON Payments: {'ON' if cfg.get('ton_enabled') else 'OFF'}",
                    callback_data="adm:settings:toggle_ton",
                )
            ],
            [
                InlineKeyboardButton(
                    "📱  Set Admin Username",
                    callback_data="adm:settings:set_admin_user",
                )
            ],
            [
                InlineKeyboardButton(
                    "💰  Set TON Wallet", callback_data="adm:settings:set_ton"
                )
            ],
            [InlineKeyboardButton("⬅️  Admin Menu", callback_data="adm:home")],
        ]
    )


def admin_logs_pager(skip: int, has_more: bool) -> InlineKeyboardMarkup:
    rows = []
    nav = []
    if skip > 0:
        nav.append(
            InlineKeyboardButton(
                "⬅️ Prev", callback_data=f"adm:logs:{max(skip - 10, 0)}"
            )
        )
    if has_more:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"adm:logs:{skip + 10}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️  Admin Menu", callback_data="adm:home")])
    return InlineKeyboardMarkup(rows)
