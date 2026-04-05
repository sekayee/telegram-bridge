import os
import json
import asyncio
import subprocess
import time
from datetime import datetime
from dotenv import load_dotenv

# Load .env file before accessing env vars
load_dotenv()

from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Use native Windows batch script — avoids bash path issues in Python subprocess
CLAUDE_BIN = "C:/nvm4w/nodejs/claude.cmd"
EDIT_DEBOUNCE = 0.5
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge.log")
MESSAGES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "messages.json")
SESSIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions.json")
AUTO_CONFIRM_FILES = True  # TODO: per-chat setting, for now True (skip confirmations)


def load_sessions() -> dict:
    if os.path.exists(SESSIONS_FILE):
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_sessions(sessions: dict):
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False)


def get_session_id(chat_id: int, sessions: dict) -> str:
    if str(chat_id) in sessions:
        return sessions[str(chat_id)]
    import uuid
    new_id = str(uuid.uuid4())
    sessions[str(chat_id)] = new_id
    save_sessions(sessions)
    return new_id


def detect_project() -> str | None:
    """Detect current project from messages.json file paths or .current_project fallback.

    Returns absolute path to project directory, or None if undetectable.
    """
    # Try from messages.json
    if os.path.exists(MESSAGES_FILE):
        entries = []
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        if isinstance(entry, dict):
                            entries.append(entry)
                    except (json.JSONDecodeError, ValueError):
                        pass  # Skip malformed lines

        # Scan last 50 entries for file paths
        from collections import Counter
        dir_counts = Counter()
        for entry in entries[-50:]:
            if not isinstance(entry, dict):
                continue
            for path in entry.get("files_read", []) + entry.get("files_written", []):
                d = os.path.dirname(os.path.abspath(path))
                dir_counts[d] += 1

        if dir_counts:
            return dir_counts.most_common(1)[0][0]

    # Fallback to ~/.current_project
    home_project = os.path.expanduser("~/.current_project")
    if os.path.exists(home_project):
        with open(home_project, "r", encoding="utf-8") as f:
            path = f.read().strip()
            if path and os.path.isdir(path):
                return path

    return None


MAX_CONTEXT_TOKENS = 180_000  # budget, leaves room for overhead

def load_context(project_path: str | None) -> str:
    """Build --append-system-prompt string from recent messages.

    Collects entries from newest to oldest until token budget is reached.
    Returns empty string if no messages.
    """
    if not os.path.exists(MESSAGES_FILE):
        return ""

    entries = []
    with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entry = json.loads(line)
                    if isinstance(entry, dict):  # skip malformed lines like []
                        entries.append(entry)
                except json.JSONDecodeError:
                    pass

    if not entries:
        return ""

    # Build conversation history text
    lines = []
    total_chars = 0
    for entry in reversed(entries):
        segment = f"- [{entry['source']}] {entry['user']}\n  {entry['claude']}\n"
        # Rough char-to-token ratio ~4:1
        segment_tokens = len(segment) // 4
        if total_chars + segment_tokens > MAX_CONTEXT_TOKENS:
            break
        lines.insert(0, segment)
        total_chars += segment_tokens

    history = "".join(lines)

    project_line = f"当前项目: {project_path}\n" if project_path else "当前项目: unknown\n"

    return project_line + "最近对话:\n" + history


async def parse_stream(line: str, seen_ids: set):
    """Parse a single JSON line from stream-json output. Returns text chunk or None.
    Deduplicates by message ID to avoid double-accumulating partial + final versions."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None

    t = obj.get("type", "")

    if t == "assistant":
        msg = obj.get("message", {})
        msg_id = msg.get("id")
        # Skip if we've already processed this message ID
        if msg_id and msg_id in seen_ids:
            return None
        if msg_id:
            seen_ids.add(msg_id)
        content = msg.get("content", [])
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")

    if t == "result":
        return obj.get("result", "")

    return None


def log_message(source: str, user_text: str, claude_text: str, files_read: list, files_written: list):
    """Append a single entry to messages.json (NDJSON format)."""
    entry = {
        "time": datetime.now().isoformat(),
        "source": source,
        "user": user_text,
        "claude": claude_text,
        "files_read": files_read,
        "files_written": files_written,
    }
    with open(MESSAGES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.message.chat_id
    start_time = time.time()
    print(f"[DEBUG] Received message from {chat_id}: {user_text[:50]}")

    project_path = detect_project()
    context_prompt = load_context(project_path)

    # Send placeholder
    status_msg = await context.bot.send_message(chat_id=chat_id, text="🤔 thinking...")

    args = [
        CLAUDE_BIN,
        "--print",
        "--output-format", "stream-json",
        "--verbose",
    ]
    if context_prompt:
        args.extend(["--append-system-prompt", context_prompt])

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=os.path.dirname(os.path.abspath(__file__)) or ".",
    )

    # Write prompt via stdin (avoids Windows batch file arg-passing issues)
    stdout_data, stderr_data = await proc.communicate(input=user_text.encode("utf-8"))

    buffer = ""
    seen_ids = set()
    for line in stdout_data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        chunk = await parse_stream(line, seen_ids)
        if chunk:
            buffer += chunk

    if not buffer:
        stderr_text = stderr_data.decode("gbk", errors="replace").strip()
        if stderr_text:
            buffer = f"⚠️ error:\n{stderr_text[:500]}"
        else:
            buffer = "⚠️ no response (check session or network)"

    final = buffer.strip()
    try:
        await status_msg.edit_text(final[:4096])
    except Exception:
        pass  # MessageNotModified or identical content — no need to resend

    duration_ms = (time.time() - start_time) * 1000
    log_message("telegram", user_text, final, [], [])


def send_startup_notification_sync():
    """Send a startup notification via direct HTTP call (no async needed)."""
    import urllib.request
    import urllib.parse
    sessions = load_sessions()
    if not sessions:
        print("No known chat_id, skipping startup notification")
        return
    first_chat_id = next(iter(sessions.keys()))
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("No bot token, skipping startup notification")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": first_chat_id, "text": "✅ Claude Bridge 已启动上线！"}).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"Startup notification sent to {first_chat_id}, response: {resp.status}")
    except Exception as e:
        print(f"Failed to send startup notification: {e}")


async def confirm_file_edit(context: ContextTypes.DEFAULT_TYPE, chat_id: int, filepath: str) -> bool:
    """Ask user via Telegram if a file edit should proceed. Returns True if confirmed.

    Sends confirmation message and waits for user reply.
    Implementation: Simplified MVP - for now returns True immediately.
    Full implementation would poll for user response.
    """
    try:
        reply_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"要修改文件 {filepath} 吗？(回复 是 或 否)"
        )
        # Simplified: Auto-confirm for now. Full implementation would track
        # this message ID and wait for user reply in next message.
        return True
    except Exception:
        return False


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bridge running... Press Ctrl+C to stop.")

    # Send startup notification in a background thread (non-blocking)
    import threading
    t = threading.Thread(target=send_startup_notification_sync, daemon=True)
    t.start()

    app.run_polling()


if __name__ == "__main__":
    main()
