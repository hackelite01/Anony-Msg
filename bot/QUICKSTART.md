# ⚡ Quick Start Guide

Get the bot running in 5 minutes.

## Clone & Setup

```bash
cd bot
cp .env.example .env
pip install -r requirements.txt
```

## Configure .env

Edit `bot/.env` with:

```env
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN_FROM_BOTFATHER
WEBHOOK_URL=https://your-ngrok-url.ngrok.io
WEBHOOK_SECRET=any-long-random-string-here
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/
ADMIN_USER_IDS=YOUR_TELEGRAM_ID
```

Get your Telegram ID: send any message to https://t.me/userinfobot

## Local Testing (with ngrok)

```bash
# Terminal 1: Start ngrok tunnel
ngrok http 8000

# Terminal 2: Update WEBHOOK_URL in .env with ngrok output
# Copy the https://... URL

# Terminal 3: Start bot
python bot.py

# Wait for: "Webhook set: https://..."
```

Then message your bot on Telegram:
- `/start` → should respond

## Deploy to Render

1. **Commit & push** your `.env` (or don't — Render UI has env var support)
2. **Go to https://render.com** → New Web Service
3. **Connect your Git repo**
4. **Set environment variables** (use "Render dashboard", not .env file):
   ```
   TELEGRAM_BOT_TOKEN=...
   WEBHOOK_URL=https://your-app-name.onrender.com
   WEBHOOK_SECRET=random-string
   MONGODB_URI=...
   ADMIN_USER_IDS=your_id
   ```
5. **Build command**: `pip install -r requirements.txt`
6. **Start command**: `python bot.py`
7. **Deploy**
8. Wait 3-5 min for build
9. Check logs: "Webhook set: https://..."

## First Time Admin Setup

1. Message bot: `/admin`
2. Add provider keys: 🗄 → ➕ Add Keys
   - Format: `Label|SEVEN_IO_API_KEY`
3. Add a plan: 📋 → ➕ Add Plan
   - Name: "Basic", SMS: 10, Stars: 50
4. Done! Now test: 🛒 Buy Key

## Verify It Works

✅ `/start` responds with welcome
✅ Channel membership check works (if configured)
✅ User can see main menu
✅ Admin can access `/admin` panel
✅ Can buy key (needs at least 1 provider key + 1 plan)
✅ Can send SMS (if you have an active key)

---

For detailed setup, see `README.md` or `DEPLOYMENT.md`.
