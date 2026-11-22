# Anonymous SMS Telegram Bot

Production-ready SMS sending bot built with python-telegram-bot v20+, Motor (async MongoDB), and deployed on Render with webhook mode.

## Features

- **Anonymous SMS Sending**: Users purchase "keys" (memorable codes like NOVA-3841) to send SMS
- **Telegram Stars Payment**: Integrated Stars invoice system with auto-approval or manual admin workflow
- **seven.io Integration**: Real API keys are hidden; each user key is reserved a pool of provider keys
- **Admin Panel**: Full management dashboard (users, stats, pool, plans, pending approvals, logs, broadcast, settings)
- **Membership Gating**: Optional channel verification before allowing SMS
- **Async Webhook**: Uses aiohttp + motor for production-scale async operations
- **Atomic Key Reservation**: Safe multi-key allocation with rollback on failure

## Project Structure

```
bot/
├── bot.py                  # Entry point: aiohttp webhook, app lifecycle
├── config.py               # Environment config & constants
├── handlers/
│   ├── start.py            # /start, /menu, membership gate
│   ├── sms.py              # Send SMS conversation flow
│   ├── purchase.py         # Buy Key, Stars invoice, fulfillment
│   ├── mykeys.py           # My Keys list
│   └── admin.py            # Full admin panel (/admin)
├── services/
│   ├── db.py               # Motor async MongoDB layer
│   ├── sms_service.py      # seven.io API calls
│   └── key_generator.py    # Unique key code generator (WORD-NNNN)
├── utils/
│   └── keyboards.py        # All inline keyboard builders
├── requirements.txt
├── Procfile
├── render.yaml
└── .env.example
```

## Quick Start

### Local Setup

1. **Copy `.env.example` and configure:**
   ```bash
   cp .env.example .env
   ```

2. **Set required variables in `.env`:**
   - `TELEGRAM_BOT_TOKEN`: Get from @BotFather
   - `WEBHOOK_URL`: Public URL (for local testing, use ngrok)
   - `WEBHOOK_SECRET`: Any random long string
   - `CHANNEL_ID` & `CHANNEL_USERNAME`: Your verification channel
   - `ADMIN_USER_IDS`: Comma-separated, e.g. `123456,789012`
   - `MONGODB_URI`: MongoDB Atlas connection string
   - `WELCOME_GIF_URL`: Optional welcome animation
   - `SUPPORT_CHANNEL_URL`: Optional support link

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the bot:**
   ```bash
   python bot.py
   ```

   The bot will:
   - Initialize MongoDB (create collections & indexes)
   - Set the webhook to `WEBHOOK_URL/webhook`
   - Start aiohttp server on `PORT` (default 8000)

### Render Deployment

1. **Create a new Web Service on Render**
2. **Connect your Git repo**
3. **Use `render.yaml` for auto-config** (all env vars are templated)
4. **Set secrets in Render dashboard:**
   - `TELEGRAM_BOT_TOKEN`
   - `WEBHOOK_URL` (your Render app domain)
   - `WEBHOOK_SECRET`
   - `MONGODB_URI` (MongoDB Atlas)
   - Other env vars as needed

5. **Deploy** — Render will run `python bot.py`

## MongoDB Collections & Indexes

| Collection | Description | Key Indexes |
|------------|-------------|------------|
| `bot_users` | Registered users | `telegram_id` (unique) |
| `provider_keys` | seven.io API keys pool | `(reserved, exhausted)` compound |
| `user_keys` | User's memorable key codes | `code` (unique), `telegram_id` |
| `plans` | SMS plans for purchase | — |
| `purchase_requests` | Payment records | `status` |
| `logs` | SMS send history | `timestamp` |
| `config` | Singleton settings | — |

## User Flow

### Buy & Send

1. User `/start` → channel membership verified → main menu
2. Tap 🛒 **Buy Key** → choose plan → pay in Stars ⭐
3. On success:
   - **Auto-approve mode**: Key instantly assigned → ready to send
   - **Manual mode**: Admin gets pending request → approves/rejects + refunds on reject
4. Tap 📤 **Send SMS** → pick key → enter number → message → receive → sent/failed confirmation
5. Tap 🗝 **My Keys** → see all keys with remaining SMS counts

### Admin Actions (`/admin`)

- **👥 Users**: Total count
- **📊 Stats**: SMS sent/failed, revenue, pool health
- **🗄 Provider Keys**: List, add, pool status
- **📋 Plans**: CRUD plans (name, SMS limit, Stars price)
- **🔑 Pending**: Approve/reject purchases with refund
- **📜 Logs**: Paginated SMS send history (10 at a time)
- **📢 Broadcast**: Send message to all users
- **⚙️ Settings**: Toggle auto-approve, set admin username, TON wallet

## API Integration: seven.io

### Send SMS
```http
POST https://gateway.seven.io/api/sms
Authorization: basic {api_key}
Content-Type: application/x-www-form-urlencoded

to=+1234567890&from=Anonymous&text=Your+message
```

**Response parsing:**
- `messages[0].error_text` present → failed
- `messages[0].success == true` → sent
- Any other response → error (logged)

### Balance Check
```http
GET https://gateway.seven.io/api/balance
X-Api-Key: {api_key}
```

Returns: plain float string (e.g., `"9.99"`)

## Key Features

### Atomic Key Reservation
When a user buys a plan, the bot:
1. Calculates keys needed: `ceil(sms_limit / 5)`
2. Atomically reserves that many free keys from the pool
3. Rolls back on any failure
4. Generates a unique memorable code (NOVA-3841 style)
5. Assigns the key and marks request approved

### Per-Provider-Key Exhaustion
- Each provider key handles up to 5 SMS (seven.io limit)
- After 5 SMSes, automatically mark exhausted and advance to next reserved key
- User doesn't notice — seamless transition

### Error Handling
- DB connection errors → graceful user message + admin notification
- seven.io timeout (10s) → retry-friendly message
- Payment processing errors → logged + admin notified
- Banned users → silently dropped before any handler runs

### Webhook Security
- Secret token validation: `X-Telegram-Bot-Api-Secret-Token` header
- Render can regenerate on each deploy

## Configuration

### Auto-Approve Toggle
- **ON** (default): Purchases auto-fulfill instantly if keys available
- **OFF**: All purchases await admin approval

### Admin Username & TON Wallet
- **Admin Username**: Shown to users when contacting admin (format: just `username`, no @)
- **TON Wallet**: Currently stored but not used in payments (placeholder for future)

## Commands

- `/start` — Register & show membership gate
- `/menu` — Show main menu (quick shortcut)
- `/admin` — Admin panel (admin-only)
- `/cancel` — Cancel any conversation

## Error Recovery

**Pool Drained Mid-Fulfillment?**
- Admin gets alert: "🚨 ACTION NEEDED"
- User's payment stays pending
- Admin adds more keys, approves manually

**Bot Crashes?**
- Drop pending updates: `drop_pending_updates=true` in webhook setup
- User's unsent message state is lost, but they can retry

**MongoDB Down?**
- All async operations timeout gracefully
- User sees friendly "Database temporarily unavailable" message
- Admin gets exception alert

## Development Notes

- **Async all the way**: motor + aiohttp + python-telegram-bot ApplicationBuilder (no polling)
- **ConversationHandler** for multi-step flows (SMS send, plan add, key add)
- **user_data** for per-user state during flows
- **Timezone**: All datetimes stored UTC via `datetime.now(timezone.utc)`
- **Logging**: Built-in `logging` module; level set to `INFO`

## Testing Locally with ngrok

```bash
# Terminal 1: Start ngrok
ngrok http 8000

# Terminal 2: Set in .env
WEBHOOK_URL=https://YOUR-NGROK-URL

# Terminal 3: Start bot
python bot.py
```

## Production Checklist

- [ ] All env vars set in Render dashboard
- [ ] MongoDB Atlas cluster created & IP whitelist configured
- [ ] At least one provider key added via `/admin` → 🗄 Provider Keys → ➕ Add Keys
- [ ] At least one plan added via `/admin` → 📋 Plans → ➕ Add Plan
- [ ] Admin user IDs verified in `ADMIN_USER_IDS`
- [ ] Telegram bot username and avatar set
- [ ] Support channel URL & welcome GIF URLs (if used)
- [ ] Bot commands registered via @BotFather (optional but recommended)
- [ ] Health check working: `curl https://your-app.onrender.com/health`

## Troubleshooting

**Bot not responding to commands?**
- Check `TELEGRAM_BOT_TOKEN` is correct
- Verify webhook is set: look for "Webhook set: https://..." in logs
- Ensure `PORT` and `WEBHOOK_URL` match

**Payments not working?**
- Stars are only available in certain markets; check Telegram docs
- Ensure `TELEGRAM_BOT_TOKEN` is activated for Star payments

**SMS not sending?**
- Verify seven.io API keys are correct and have balance
- Check destination number format (must be digits only, 10–15 chars)
- Review logs via `/admin` → 📜 Logs

**MongoDB connection hangs?**
- Verify IP whitelist in MongoDB Atlas (should include Render's IPs or 0.0.0.0)
- Check `MONGODB_URI` format and credentials
- Test locally with: `python -c "import motor.motor_asyncio; print('ok')"`

---

**Built with:** python-telegram-bot 21.6, motor 3.6, pymongo 4.9, aiohttp 3.10+

## TON payments

Besides Telegram Stars, a plan can be paid in TON. Bot API 10.4's "TON wallet" additions are
not a payment method for bots, so TON payments are verified on-chain:

1. `/admin` -> Settings -> **Set TON Wallet** (or `TON_WALLET_ADDRESS`): the wallet that receives payments.
   Then switch **TON Payments** ON in the same menu (OFF by default; needs a wallet).
2. `/admin` -> Plans -> **Add Plan** (step 4) or **Set TON Price** for existing plans. Plans without a
   TON price keep using Stars only.
3. With TON Payments ON, a user picking a plan that has a TON price chooses Stars or TON. For TON they get the amount, the
   wallet and a unique comment (memo) to include.
4. A background watcher scans the wallet (TON Center) every `TON_POLL_SECONDS` and matches transfers
   by memo with amount >= price. A match creates the key (auto-approve) or a pending approval,
   exactly like a Stars payment. The user can also tap "I've paid - check now".

Notes: underpaid transfers and transfers with an unknown memo are reported to admins, not fulfilled.
TON payments are not refunded automatically — rejecting a TON purchase tells you the amount and
transaction to send back manually. The comment must be sent exactly as shown.
