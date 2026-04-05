# Cross-Session Collaboration — Telegram Bridge

## Status

Approved for implementation.

## Goal

Enable seamless continuation of work across Telegram and local terminal. When the user switches from terminal to Telegram mid-task, the AI on Telegram knows:
1. Which project they are working on
2. What was discussed/done previously

## Data Storage

### messages.json (replaces bridge.log)

New structured log file at `telegram-bridge/messages.json`.

```json
{"time": "...", "source": "telegram", "user": "...", "claude": "...", "files_read": [], "files_written": []}
{"time": "...", "source": "terminal", "user": "...", "claude": "...", "files_read": ["src/app.py"], "files_written": ["src/app.py"]}
```

Fields:
- `time`: ISO timestamp
- `source`: "telegram" or "terminal"
- `user`: raw user message text
- `claude`: raw AI response text
- `files_read`: list of file paths read during this exchange
- `files_written`: list of file paths written during this exchange

Terminal side writes to this file via a hook (see below).

### .current_project

File at `~/.current_project` containing the absolute path of the current working project.

Written by terminal hook when cwd changes. Read by bridge as fallback when messages.json has no file references.

## Project Detection

Two-layer detection, in priority order:

1. **From messages.json**: Scan last 50 entries, collect all `files_read`/`files_written` paths, determine the most frequent parent directory. Use that as current project.

2. **Fallback**: Read `~/.current_project`. If missing or unreadable, ask user via Telegram: "你现在在哪个项目？"

Rationale: Most practical work involves files, so file-path inference covers 90% of cases.

## Context Injection

Before each `claude --print` call:

1. Load `messages.json`
2. Collect entries from newest to oldest, adding text to a prompt, until ~180k tokens budget is reached (~200k max context minus overhead)
3. Format as `--append-system-prompt`:
   ```
   当前项目: /path/to/project
   最近对话:
   - [telegram] user: ...
     claude: ...
   - [terminal] user: ...
     claude: ...
   ```
4. Pass via `claude --append-system-prompt "<context>" --print ...`

## File Operation Confirmation

When AI response contains a file write/edit:
1. Bridge sends confirmation message to Telegram: "要改文件 X 吗？"
2. User replies "是" or "否"
3. Bridge executes or discards accordingly

## Terminal Hook

Location: `~/.claude/hooks/on_cwd_change.sh` (or `.ps1` on Windows)

Triggered when working directory changes during terminal session. Writes new cwd to `~/.current_project`.

Implementation: Claude Code hook system — check `.claude/hooks/` directory for hook capability.

Fallback if hooks unavailable: User manually runs `/project /path/to/project` via Telegram.

## File Structure

```
telegram-bridge/
├── bridge.py              # main program (updated)
├── messages.json         # shared message store (replaces bridge.log)
├── requirements.txt
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-04-05-cross-session-collaboration-design.md
```

## Changes to bridge.py

1. Replace `log_conversation()` to write `messages.json` (NDJSON, one JSON per line) instead of `bridge.log`
2. Add `detect_project()` — reads messages.json + .current_project fallback
3. Add `load_context()` — builds `--append-system-prompt` from recent messages
4. Add `confirm_file_edit()` — Telegram confirmation flow
5. Add `append_history(user, claude, files_read, files_written)` — write to messages.json from terminal side (stub for now)

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| JSON file corruption on concurrent write | File locking (fcntl), fallback to append-only log |
| Token limit overflow | Strict budget ~180k, oldest entries dropped first |
| Project detection wrong | Fallback to explicit .current_project or user prompt |
| Terminal hook not working | Fallback to /project command via Telegram |
