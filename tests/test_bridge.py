import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import bridge


@pytest.fixture
def temp_bridge(tmp_path, monkeypatch):
    """Create isolated bridge environment with temp files."""
    messages_file = tmp_path / "messages.json"
    sessions_file = tmp_path / "sessions.json"
    monkeypatch.setattr(bridge, "MESSAGES_FILE", str(messages_file))
    monkeypatch.setattr(bridge, "SESSIONS_FILE", str(sessions_file))
    return {"messages": messages_file, "sessions": sessions_file, "tmp": tmp_path}


class TestLogMessage:
    def test_writes_ndjson_line(self, temp_bridge):
        """log_message appends one valid JSON line per call."""
        bridge.log_message("telegram", "hello", "hi there", [], [])
        lines = temp_bridge["messages"].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["source"] == "telegram"
        assert entry["user"] == "hello"
        assert entry["claude"] == "hi there"
        assert entry["files_read"] == []
        assert entry["files_written"] == []

    def test_writes_multiple_lines(self, temp_bridge):
        """Multiple calls produce multiple NDJSON lines."""
        bridge.log_message("telegram", "msg1", "reply1", [], [])
        bridge.log_message("terminal", "msg2", "reply2", ["file.py"], [])
        lines = temp_bridge["messages"].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["user"] == "msg1"
        assert json.loads(lines[1])["source"] == "terminal"

    def test_handles_unicode(self, temp_bridge):
        """Unicode characters are preserved correctly."""
        bridge.log_message("telegram", "你好", "回复", [], [])
        lines = temp_bridge["messages"].read_text(encoding="utf-8").strip().split("\n")
        entry = json.loads(lines[0])
        assert entry["user"] == "你好"
        assert entry["claude"] == "回复"


class TestDetectProject:
    def test_returns_none_when_no_file(self, temp_bridge):
        """No messages file returns None."""
        assert bridge.detect_project() is None

    def test_skips_malformed_entries(self, temp_bridge):
        """Malformed lines are skipped without crashing."""
        temp_bridge["messages"].write_text("[]\n", encoding="utf-8")
        assert bridge.detect_project() is None

    def test_infers_from_file_paths(self, temp_bridge):
        """Detects project from file paths in messages."""
        # Use Windows-style absolute path to avoid drive letter issue
        bridge.log_message("telegram", "hi", "reply", [],
                           ["C:/Users/project/src/app.py"])
        bridge.log_message("telegram", "hi", "reply", [],
                           ["C:/Users/project/src/main.py"])
        result = bridge.detect_project()
        # Normalize slashes for comparison
        assert Path(result).as_posix() == Path("C:/Users/project/src").as_posix()

    def test_falls_back_to_current_project_file(self, temp_bridge, monkeypatch):
        """Falls back to ~/.current_project when no file paths in messages."""
        # Create a .current_project file inside temp dir
        # Use the temp dir itself as a valid path (it's a real directory)
        cp_file = temp_bridge["tmp"] / ".current_project"
        cp_file.write_text(str(temp_bridge["tmp"]), encoding="utf-8")

        # Patch os.path.expanduser to simulate ~ expansion on Windows
        original_expanduser = os.path.expanduser
        def patched_expanduser(path):
            if path == "~/.current_project":
                return str(cp_file)
            return original_expanduser(path)

        with patch.object(os.path, "expanduser", patched_expanduser):
            result = bridge.detect_project()

        assert result is not None, "detect_project returned None"
        # The result should be the temp dir path (which is a real directory)
        assert result == str(temp_bridge["tmp"])


class TestLoadContext:
    def test_returns_empty_when_no_messages(self, temp_bridge):
        """No messages returns empty string."""
        assert bridge.load_context(None) == ""

    def test_includes_project_line(self, temp_bridge):
        """Context includes the project path line."""
        bridge.log_message("telegram", "hello", "hi", [], [])
        result = bridge.load_context("/path/to/project")
        lines = result.split("\n")
        assert any("path/to/project" in line for line in lines)

    def test_includes_history_lines(self, temp_bridge):
        """Context includes formatted conversation history."""
        bridge.log_message("telegram", "hello", "hi there", [], [])
        bridge.log_message("terminal", "from term", "from ai", [], [])
        result = bridge.load_context(None)
        assert "[telegram] hello" in result
        assert "[terminal] from term" in result
        assert "最近对话" in result

    def test_token_budget_does_not_overflow(self, temp_bridge):
        """Very long messages don't cause errors and respect budget."""
        bridge.log_message("telegram", "a" * 20000, "b" * 20000, [], [])
        bridge.log_message("telegram", "a" * 20000, "b" * 20000, [], [])
        result = bridge.load_context(None)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_skips_malformed_lines(self, temp_bridge):
        """Malformed JSON doesn't break context loading."""
        temp_bridge["messages"].write_text("[]\n", encoding="utf-8")
        bridge.log_message("telegram", "test", "result", [], [])
        result = bridge.load_context(None)
        assert "test" in result
        assert "result" in result


class TestIntegration:
    def test_log_then_detect_project(self, temp_bridge):
        """Log messages with paths, then detect returns that project."""
        bridge.log_message("telegram", "hello", "hi", [],
                           ["C:/Users/myproj/main.py"])
        result = bridge.detect_project()
        assert Path(result).as_posix() == "C:/Users/myproj"

    def test_log_then_load_context_contains_history(self, temp_bridge):
        """Log messages, then load_context returns them in history."""
        bridge.log_message("telegram", "hello", "hi there", [], [])
        bridge.log_message("terminal", "from term", "from ai", [], [])
        result = bridge.load_context(None)
        assert "hello" in result
        assert "hi there" in result
        assert "from term" in result

    def test_full_flow(self, temp_bridge):
        """End-to-end: log messages, detect project, load context."""
        bridge.log_message("telegram", "hello", "hi", [],
                           ["C:/my-project/file.py"])
        project = bridge.detect_project()
        assert project is not None
        assert "my-project" in project
        context = bridge.load_context(project)
        assert "my-project" in context
        assert "[telegram] hello" in context


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
