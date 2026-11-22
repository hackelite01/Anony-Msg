# Anony-Msg Project Structure

This project is organized to separate the production bot from legacy code:

```
Anony-Msg/
├── bot/                    # 🚀 PRODUCTION BOT (async, webhook, Render-ready)
│   ├── bot.py              # Entry point
│   ├── config.py           # Environment configuration
│   ├── handlers/           # Feature handlers (start, sms, purchase, admin, mykeys)
│   ├── services/           # DB, SMS, key generation
│   ├── utils/              # Keyboards & helpers
│   ├── requirements.txt    # Python dependencies
│   ├── .env.example        # Environment template
│   ├── Procfile            # Render deployment config
│   ├── render.yaml         # Render service config
│   ├── README.md           # Full bot documentation
│   └── setup.sh            # Quick local setup
│
├── legacy/                 # 📦 ARCHIVED CODE (old bot versions & scripts)
│   ├── bot.py              # Original single-file bot
│   ├── bot1.py, botenc.py, botf.py, botterminal.py  # Previous iterations
│   ├── send.py, Run.sh, send.sh  # Old utility scripts
│   ├── prospeo/            # Previous project data
│   ├── bot_data/           # Legacy data files
│   └── images/             # Old media files
│
├── README.md               # This file
├── .env                    # Your live secrets (DO NOT commit)
├── .git/                   # Version control
└── .version, .notify       # Metadata
```

## 🚀 Quick Start (Production Bot)

Navigate to the **`bot/`** folder:

```bash
cd bot

# Install dependencies
pip install -r requirements.txt

# Copy & configure environment
cp .env.example .env
# Edit .env with your Telegram bot token, MongoDB URI, etc.

# Run locally (with webhook via ngrok)
python bot.py

# Or deploy to Render using render.yaml
```

See `bot/README.md` for complete documentation.

## 📦 Legacy Code

The **`legacy/`** folder contains:
- Previous bot implementations (`bot.py`, `bot1.py`, `botenc.py`, etc.)
- Old utility scripts (`send.py`, `Run.sh`)
- Archived project data (`prospeo/`, `bot_data/`, images)

**Do not use these files** — they are kept for reference only. The production bot in `bot/` is the current, maintained version.

## 🎯 What to Do

1. **Start the bot**: `cd bot && python bot.py`
2. **Configure**: Edit `bot/.env` with your credentials
3. **Deploy**: Use `bot/render.yaml` for Render
4. **Manage**: Use `/admin` command once running
5. **Archive**: Ignore the `legacy/` folder

---

**Production Bot:** Uses async/await, webhooks, Motor (async MongoDB), ready for Render.
**Recommended:** Always work in `bot/` — never edit `legacy/` code.
