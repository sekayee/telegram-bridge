# Telegram Remote Control - Phase 1 Design

## Concept & Vision

从 Telegram 控制本地 Claude Code 会话，类似于 `/remote-control` 的自建版本。
- Telegram 发送消息 → 启动独立 Claude 进程 → 响应同时显示在 Telegram 和本地专用窗口的新标签页
- 本地终端和 Telegram 对话完全同步，两者都能看到完整输入输出
- 保留 ANSI 颜色和格式

## Architecture

```
Telegram message ("hello")
    │
    ▼
bridge.py (Telegram listener, existing)
    │
    ▼ spawns
pty_spawner.py (MSYS2 bash → python pty_claude.py)
    │
    ├── PTY master side
    │       ├── stream_forwarder → Telegram (real-time, streaming)
    │       └── stream_forwarder → Windows Terminal new tab (real-time, with ANSI colors)
    │
    └── PTY slave side → claude.cmd --print --verbose
```

## Components

### 1. bridge.py (modify)

修改 `handle_message`：
- 不再用 `claude --print` 单次调用
- 改为调用 `pty_spawner.spawn(user_text, chat_id)`
- `pty_spawner` 返回流式输出，bridge 实时转发到 Telegram
- 进程结束后在 Windows Terminal 开新标签显示会话

### 2. pty_spawner.py (new)

负责 PTY 生命周期管理：
- `spawn(user_text, chat_id) -> AsyncGenerator[str, None]`
- 用 MSYS2 bash 启动：`bash -c "python -c 'import pty; pty.spawn([claude.cmd, ...])'"`
- 捕获 PTY master fd 的所有输出（stdout + stderr + 伪终端控制序列）
- 通过 async generator 返回原始字节流（含 ANSI 颜色码）

### 3. terminal_tabs.py (new)

Windows Terminal 窗口管理：
- `open_new_tab(command, title)` — 在当前窗口新建标签页
- 用 `wt` CLI: `wt new-tab --title <title> <command>`
- 关键：复用已有窗口，不是新建窗口

### 4. stream_forwarder.py (new)

输出路由：
- 从 PTY async generator 读取字节流
- 同时：
  - 实时发送给 Telegram（流式 `send_message` 或 `edit_message_text`）
  - 写入 Windows Terminal 新标签页

### 5. claude_local.py (new)

本地会话管理：
- 在 Windows Terminal 专用窗口维护一个长期运行的 Claude 会话
- 记录窗口 ID，供 `terminal_tabs.py` 使用
- 支持注入 Telegram 消息到该会话（Phase 2）

## Technical Details

### PTY on Windows

Windows 原生 Python 没有 `openpty`。方案：用 MSYS2 bash 内置的 Python（自带 `pty` 模块）。

```python
cmd = [
    "bash", "-lc",
    f'python -c "import pty, sys; pty.spawn([\\\"{CLAUDE_BIN}\\\", \\\"--print\\\", \\\"--verbose\\\"], \\'r\\')"'
]
```

问题：PTY slave 输出到 master 的数据在 Windows 上如何读取？

更可行的方案：用 `subprocess.Popen` + `asyncio` + `os.read(master_fd, 1024)` 轮询，在 MSYS2 bash 子进程内运行 Claude：

```python
proc = await asyncio.create_subprocess_shell(
    f'bash -lc \'python -c "import pty; pty.spawn([\\\"{CLAUDE_BIN}\\\", \\\"--print\\\"])"\'',
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
```

但 `pty.spawn` 在 Windows 上通过 ConPTY 工作，需要正确配置。

### 替代方案：直接用 ConPTY API

如果 MSYS2 Python 的 `pty` 不可靠，可以用 `ctypes` 调用 Windows ConPTY API：

```python
import ctypes
from ctypes import wintypes

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
# CreatePseudoConsole, etc.
```

但这更复杂。先用 MSYS2 方案，失败再换。

### Windows Terminal 标签页

```python
import subprocess
subprocess.run([
    'wt', 'new-tab',
    '--title', 'Telegram Session',
    'bash', '-lc',
    f'python -c "import sys; sys.stdin.read()"'
])
```

问题：如何把 PTY 输出同时写入这个新标签页？

需要在 `bash -lc` 里同时运行一个监听脚本，把 PTY 输出写入新标签页的 stdin。

### ANSI 颜色处理

PTY master 输出的字节流已含 ANSI escape codes。直接转发到 Telegram 需要过滤（TG 不支持），但写入 Windows Terminal 标签页保留。

```python
# Telegram: strip ANSI codes before sending
ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]')
telegram_text = ansi_escape.sub('', raw_output)
# Windows Terminal tab: send raw bytes
```

## Data Flow

1. User sends "hello" from Telegram
2. bridge.py receives via Telegram Bot API
3. bridge.py calls `pty_spawner.spawn("hello", chat_id)`
4. pty_spawner creates subprocess via MSYS2 bash
5. Claude --print starts, streams output
6. For each chunk:
   - Forward raw bytes to Windows Terminal tab (preserving ANSI)
   - Strip ANSI, send to Telegram (streaming edit)
7. When complete: close PTY, session ends

## Error Handling

- **ConPTY failure**: Fall back to simpler subprocess with pipe (no PTY)
- **Windows Terminal not running**: Open new WT window instead of tab
- **Claude process crash**: Log error, notify Telegram user
- **Telegram rate limit**: Buffer messages, retry with backoff

## Testing

1. Manual test: Telegram sends message → PTY spawns Claude → output appears in both TG and WT tab
2. Stream correctness: ANSI colors preserved in WT, stripped in TG
3. Concurrent sessions: Multiple TG users can have separate sessions simultaneously
