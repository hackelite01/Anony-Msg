#!/usr/bin/env bash
# Quick setup script for local development

set -e

echo "🚀 Anonymous SMS Bot - Local Setup"
echo "=================================="
echo

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Install Python 3.11+ first."
    exit 1
fi

echo "✅ Python found: $(python3 --version)"
echo

# Create venv if not exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
    echo "✅ Virtual environment created"
else
    echo "✅ Virtual environment already exists"
fi

echo

# Activate venv
echo "🔧 Activating virtual environment..."
source venv/bin/activate || . venv/Scripts/activate 2>/dev/null || true

# Install dependencies
echo "📥 Installing dependencies..."
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
echo "✅ Dependencies installed"
echo

# Setup .env
if [ ! -f ".env" ]; then
    echo "📋 Copying .env.example to .env..."
    cp .env.example .env
    echo "⚠️  Edit .env with your credentials (Telegram bot token, MongoDB URI, etc.)"
    echo
else
    echo "✅ .env already exists"
fi

echo "✨ Setup complete!"
echo
echo "Next steps:"
echo "1. Edit .env with your Telegram bot token, MongoDB URI, etc."
echo "2. Ensure MongoDB Atlas is set up"
echo "3. Add provider keys via admin panel after starting bot"
echo "4. Run: python bot.py"
echo
