# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bridge for Claude Code — a Telegram bot that relays messages to a local Claude Code CLI process and streams back responses. Messages are logged to `messages.json` for cross-session continuity.

## Commands

```bash
# Run the bot
python bridge.py

# Run tests
python -m pytest tests/test_bridge.py -v

# Install dependencies
pip install -r requirements.txt
```

**Environment Variables:**
- `TELEGRAM_BOT_TOKEN` — Telegram bot token (required)

## Architecture

```
Telegram message → bridge.py → subprocess (claude.cmd --print --output-format stream-json) → streaming JSON parse → Telegram response
                                                      ↓
                                              messages.json (NDJSON log)
```

**Key files:**
- `bridge.py` — Main entry point. Contains Telegram bot handlers, subprocess spawning, streaming JSON parser, project detection, and context loading.
- `tests/test_bridge.py` — pytest suite using monkeypatched temp files.

**Data files (at project root):**
- `messages.json` — NDJSON log of all conversations (one JSON object per line, with `source`, `user`, `claude`, `files_read`, `files_written`)
- `sessions.json` — Maps chat_id → session_id (UUID per Telegram chat)

**Internal modules** (in `.claude/remember/`):
- `pipeline/` — Session memory pipeline (consolidate, extract, log, prompts, shell)
- `scripts/` — Hook scripts for session lifecycle
- `prompts/` — Prompt templates for memory operations

## Key Implementation Details

- Uses `python-telegram-bot>=20.0` with `Application.builder()` and `MessageHandler`
- Claude CLI invocation: `claude.cmd --print --output-format stream-json --verbose` (Windows batch file at `C:/nvm4w/nodejs/claude.cmd`)
- Streaming JSON: parses `type=assistant` → `message.content[].text` and `type=result` → `result`, deduplicates by `message.id`
- Windows stderr may be GBK-encoded: `decode("gbk", errors="replace")`
- `detect_project()` infers project from file paths in `messages.json` or `~/.current_project` fallback
- `load_context()` builds `--append-system-prompt` string from recent NDJSON entries, with ~180k token budget

## Phase 2 Design (future)

See `docs/superpowers/specs/2026-04-06-telegram-remote-control-design.md` for PTY-based streaming with Windows Terminal tab integration and ANSI color preservation.
