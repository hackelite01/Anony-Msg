# 📋 Deployment Checklist

Complete this before deploying to Render:

## Pre-Deployment

### Local Testing
- [ ] Python 3.11+ installed
- [ ] `pip install -r requirements.txt` succeeds
- [ ] If Windows reports `dotenv.exe.deleteme`, use a fresh virtual environment or run `python -m pip install --ignore-installed -r requirements.txt`
- [ ] `.env` file created with all required variables
- [ ] `python bot.py` starts without errors
- [ ] Logs show "Webhook set: https://..."

### Credentials & Services
- [ ] Telegram bot token obtained from @BotFather
- [ ] MongoDB Atlas cluster created (free tier OK)
- [ ] MongoDB IP whitelist includes 0.0.0.0 (or Render's IPs)
- [ ] seven.io API key ready (get from dashboard)
- [ ] Telegram channel created for membership verification (optional)

### Environment Variables (.env)
- [ ] `TELEGRAM_BOT_TOKEN` - bot token from @BotFather
- [ ] `WEBHOOK_URL` - https://your-app.onrender.com (use your real domain)
- [ ] `WEBHOOK_SECRET` - long random string (new on each Render redeploy)
- [ ] `CHANNEL_ID` - channel numeric ID or empty string if not required
- [ ] `CHANNEL_USERNAME` - channel username (no @ prefix) or empty string
- [ ] `ADMIN_USER_IDS` - comma-separated, e.g. 123456,789012
- [ ] `MONGODB_URI` - full connection string from MongoDB Atlas
- [ ] `MONGODB_DB_NAME` - collection name (default: anonsmbot)
- [ ] `WELCOME_GIF_URL` - optional; URL to welcome animation
- [ ] `SUPPORT_CHANNEL_URL` - optional; URL to support channel
- [ ] `SMS_SENDER_NAME` - sender ID for SMS (default: Anonymous)
- [ ] `PORT` - 8000 (Render assigns this)

## Render Deployment

### Create Service
- [ ] Log in to https://render.com
- [ ] New → Web Service
- [ ] Connect Git repo
- [ ] Choose `bot` as root directory (or leave blank and specify in build command)
- [ ] Name: choose a name (e.g., `anony-sms-bot`)
- [ ] Region: Choose closest to your users
- [ ] Plan: Free (or Paid if you need higher tier)

### Configure Environment Variables
In Render dashboard, set all variables from `.env`:
- [ ] TELEGRAM_BOT_TOKEN
- [ ] WEBHOOK_URL (set to https://{your-app-name}.onrender.com)
- [ ] WEBHOOK_SECRET (generate random string)
- [ ] MONGODB_URI
- [ ] MONGODB_DB_NAME
- [ ] CHANNEL_ID
- [ ] CHANNEL_USERNAME
- [ ] ADMIN_USER_IDS
- [ ] WELCOME_GIF_URL (optional)
- [ ] SUPPORT_CHANNEL_URL (optional)
- [ ] SMS_SENDER_NAME (optional, default provided)
- [ ] PORT (8000)

### Build & Deploy
- [ ] Build Command: `pip install -r requirements.txt`
- [ ] Start Command: `python bot.py`
- [ ] Hit "Deploy"
- [ ] Wait for build to complete (~3-5 min)
- [ ] Check logs for "Webhook set: https://..."

### Post-Deployment Verification
- [ ] Test endpoint: `curl https://your-app.onrender.com/health` → should return "ok"
- [ ] Send `/start` to bot
- [ ] Check logs for no errors
- [ ] Verify membership gating works

## First Admin Setup

Once bot is running:

1. **Add yourself as a member** of the verification channel
2. **Send `/start` to the bot** to verify it works
3. **Send `/admin`** to access admin panel

### Add Provider Keys
- [ ] Admin → 🗄 Provider Keys → ➕ Add Keys
- [ ] Add at least one key in format: `LABEL|SEVEN_IO_KEY`
- [ ] Verify keys appear in "Pool Status"

### Add Plans
- [ ] Admin → 📋 Plans → ➕ Add Plan
- [ ] Add at least one plan (e.g., "Basic": 10 SMS, 50 Stars)
- [ ] Test: Buy Key → select plan → verify invoice works

### Configure Settings
- [ ] Admin → ⚙️ Settings
- [ ] Set Admin Username (for user contact link)
- [ ] Toggle Auto-Approve if desired (default: ON)

## Ongoing Maintenance

### Monitor
- [ ] Check `/admin` → 📊 Stats regularly
- [ ] Review pool status: ensure 🟢 (free keys available)
- [ ] Check `📜 Logs` for failed SMS (investigate if high)

### Add Keys
- [ ] When pool drops below 5 free: Admin → 🗄 Provider Keys → ➕ Add Keys

### Handle Issues
- [ ] Pending requests stuck? → Admin → 🔑 Pending → Approve/Reject
- [ ] User reports SMS not sent? → Admin → 📜 Logs → search destination
- [ ] Payment processing issues? → Check admin notifications

## Troubleshooting Deploy Failures

| Error | Solution |
|-------|----------|
| `ModuleNotFoundError: python_telegram_bot` | Run `pip install -r requirements.txt` |
| `WinError 2` while uninstalling `python-dotenv` | Recreate the virtual environment, or run `python -m pip install --ignore-installed -r requirements.txt` |
| Webhook set but bot not responding | Check TELEGRAM_BOT_TOKEN is correct; verify secret matches |
| MongoDB connection timeout | Whitelist Render's IPs or use 0.0.0.0 in MongoDB Atlas |
| Render keeps crashing | Check `/admin` panel logs; look for unhandled db errors |
| "SMS service temporarily unavailable" | Verify seven.io API key is valid and has balance |

## Health Checks

Test these manually before declaring done:

```bash
# 1. Health endpoint
curl https://your-app.onrender.com/health
# Expected: "ok"

# 2. Bot responds to /start
# Send via Telegram: /start
# Expected: Welcome message + membership gate

# 3. Admin panel accessible
# Send `/admin` (must be in ADMIN_USER_IDS)
# Expected: Admin menu appears

# 4. Can add provider keys
# Admin → 🗄 → ➕ Add Keys
# Paste: `TestKey|sk_test_123456`

# 5. Can add plans
# Admin → 📋 → ➕ Add Plan
# Create: "Test", 5 SMS, 10 Stars

# 6. Can buy key (Stars payment)
# User → 🛒 Buy Key → select plan
# Expected: Telegram Stars invoice appears

# 7. Can send SMS
# User → 📤 Send SMS → pick key → enter number → message
# Expected: SMS sent confirmation + logged
```

---

**Done!** Your production bot is live. Monitor logs, keep pool stocked, and handle admin tasks through `/admin`.
