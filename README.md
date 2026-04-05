# Telegram Bridge for Claude Code

A Telegram bot that relays messages to a local Claude Code CLI process and streams back responses. Messages are logged to `messages.json` for cross-session continuity.

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/sekayee/telegram-bridge.git
cd telegram-bridge
pip install -r requirements.txt
```

### 2. Configure Bot Token

Create a `.env` file:

```
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

To get a bot token, message [@BotFather](https://t.me/BotFather) on Telegram.

### 3. Run

```bash
py bridge.py
```

The bot will start and send you a notification on Telegram: **"✅ Claude Bridge 已启动上线！"**

### 4. Auto-start on Windows

A shortcut is created in your Startup folder — the bot will automatically start when you log in.

## Usage

Send any message to your Telegram bot and get an AI response powered by Claude Code. Conversation history is preserved across sessions.

## Project Structure

```
telegram-bridge/
├── bridge.py           # Main bot entry point
├── tests/
│   └── test_bridge.py  # pytest test suite
├── requirements.txt    # python-telegram-bot, python-dotenv
├── start_bridge.bat   # Windows startup script
└── .env               # Bot token (not committed)
```

## Architecture

```
Telegram message → bridge.py → subprocess (claude.cmd --print --output-format stream-json) → streaming JSON parse → Telegram response
                                                      ↓
                                              messages.json (NDJSON log)
```

## Commands

```bash
# Run the bot
py bridge.py

# Run tests
py -m pytest tests/test_bridge.py -v

# Install dependencies
pip install -r requirements.txt
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Your Telegram bot token |

## License

MIT
