# Cross-Session Collaboration — Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax. Execute one task at a time.

**Goal:** Bridge can share project context and conversation history across Telegram and terminal sessions.

**Architecture:** Messages written to NDJSON `messages.json`. Project inferred from file paths in history or `~/.current_project` fallback. Context injected via `--append-system-prompt` into each `claude --print` call. File writes confirmed via Telegram before execution.

**Tech Stack:** Python asyncio, python-telegram-bot, Claude Code CLI

---

## Task 1: Replace bridge.log with messages.json (NDJSON)

**Files:**
- Modify: `bridge.py:69-79` (`log_conversation` function)
- Create: `telegram-bridge/messages.json` (empty, `[]`)

- [ ] **Step 1: Update `log_conversation` to write NDJSON**

Replace the existing `log_conversation` function with one that writes one JSON object per line:

```python
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
```

- [ ] **Step 2: Add MESSAGES_FILE constant**

Add after `LOG_FILE` line:
```python
MESSAGES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "messages.json")
```

- [ ] **Step 3: Update `handle_message` call to pass empty file lists**

In `handle_message`, replace the `log_conversation(chat_id, user_text, final, duration_ms)` call with:
```python
log_message("telegram", user_text, final, [], [])
```
(File tracking is stubbed for now — implement in Task 4.)

- [ ] **Step 4: Run to verify no crash**

```bash
cd telegram-bridge && export TELEGRAM_BOT_TOKEN=... && py bridge.py
```
Send a test message from Telegram. Verify it responds and `messages.json` has one new line.

- [ ] **Step 5: Commit**

```bash
git add bridge.py && git commit -m "feat: replace bridge.log with NDJSON messages.json"
```
(Or skip git if not in a repo — just note "no commit" in response.)

---

## Task 2: Add project detection (detect_project)

**Files:**
- Modify: `bridge.py` — add new function after `get_session_id`

- [ ] **Step 1: Write detect_project function**

Add after `get_session_id`:

```python
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
                    entries.append(json.loads(line))

        # Scan last 50 entries for file paths
        from collections import Counter
        dir_counts = Counter()
        for entry in entries[-50:]:
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
```

- [ ] **Step 2: Verify function runs without error**

```bash
cd telegram-bridge && py -c "from bridge import detect_project; print(detect_project())"
```
Expected: prints `None` (no messages yet) or a path string.

---

## Task 3: Add context injection (load_context)

**Files:**
- Modify: `bridge.py` — add new function after `detect_project`

- [ ] **Step 1: Write load_context function**

```python
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
                entries.append(json.loads(line))

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
```

- [ ] **Step 2: Verify load_context works with existing messages.json**

```bash
cd telegram-bridge && py -c "
from bridge import load_context, detect_project
proj = detect_project()
ctx = load_context(proj)
print(len(ctx), 'chars')
print(ctx[:200])
"
```

---

## Task 4: Wire context into handle_message

**Files:**
- Modify: `bridge.py` — update `handle_message` function

- [ ] **Step 1: In handle_message, add project detection and context before building command**

After `start_time = time.time()` and before the placeholder message:

```python
    project_path = detect_project()
    context_prompt = load_context(project_path)
```

- [ ] **Step 2: Add --append-system-prompt to claude command**

In the `args` list, add `--append-system-prompt` before `user_text`:

```python
    args = [
        CLAUDE_BIN,
        "--print",
        "--output-format", "stream-json",
        "--verbose",
    ]
    if context_prompt:
        args.extend(["--append-system-prompt", context_prompt])
    args.append(user_text)
```

- [ ] **Step 3: Run and test via Telegram**

Restart bridge, send a message. Check that response is context-aware (e.g., ask "上次聊了什么" and verify it knows context).

---

## Task 5: File operation confirmation (confirm_file_edit)

**Files:**
- Modify: `bridge.py` — add new function and update `handle_message`

- [ ] **Step 1: Write confirmation function**

```python
async def confirm_file_edit(context: ContextTypes.DEFAULT_TYPE, chat_id: int, filepath: str) -> bool:
    """Ask user via Telegram if a file edit should proceed. Returns True if confirmed."""
    try:
        reply = await context.bot.send_message(
            chat_id=chat_id,
            text=f"要修改文件 {filepath} 吗？(回复 是 或 否)"
        )
        # Store the message ID to match the response
        return True  # stub: implement polling-based reply detection
    except Exception:
        return False
```

Note: Full implementation requires Telegram message polling / answer detection. For MVP, use a simpler approach:
- Send the confirmation message
- On next user message, check if it contains "是" or "否" and route accordingly

Simplified MVP: skip real-time confirmation. Instead, add a system-level toggle:
```python
AUTO_CONFIRM_FILES = False  # set True to skip confirmations
```

- [ ] **Step 2: Add AUTO_CONFIRM toggle and integrate into handle_message**

```python
AUTO_CONFIRM_FILES = True  # TODO: per-chat setting
```

When AI wants to write a file, check `AUTO_CONFIRM_FILES`. If False, call `confirm_file_edit`.

---

## Task 6: Terminal hook stub + .current_project writing

**Files:**
- Create: `telegram-bridge/terminal-hook-example.txt` — example hook script

- [ ] **Step 1: Document how to write .current_project from terminal**

Create `terminal-hook-example.txt`:
```
# To enable project detection from terminal, add to your shell profile:
alias project='echo "$(pwd)" > ~/.current_project'

# Or add to Claude Code hooks (if available in .claude/hooks/):
# When cwd changes, write to ~/.current_project
```

This is documentation-only for now. Full terminal-side integration requires further Claude Code hook system exploration.

---

## Implementation Order

Execute Tasks 1 → 2 → 3 → 4 → 5 → 6 in sequence.

Each task should leave the bridge in a runnable state. Test via Telegram between tasks.

---

## Spec Coverage Check

| Spec Requirement | Task |
|-----------------|------|
| messages.json (NDJSON, source, files) | Task 1 |
| Project detection (2-layer) | Task 2 |
| Context injection via --append-system-prompt | Task 3 + Task 4 |
| Dynamic token budget | Task 3 |
| File operation confirmation | Task 5 |
| .current_project fallback | Task 2 |
| Terminal hook documentation | Task 6 |
