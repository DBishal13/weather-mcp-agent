"""Unit tests for query_log.py - the JSONL tool-call log used by the dashboard."""
import importlib
import json

import query_log


def _fresh_log(tmp_path, monkeypatch):
    """Reloads query_log with LOG_PATH pointed at a scratch file."""
    log_path = tmp_path / "query_log.jsonl"
    monkeypatch.setenv("QUERY_LOG_PATH", str(log_path))
    module = importlib.reload(query_log)
    return module, log_path


class TestRecord:
    def test_appends_one_jsonl_entry(self, tmp_path, monkeypatch):
        module, log_path = _fresh_log(tmp_path, monkeypatch)
        module.record("get_current_weather", {"location": "Chicago"}, {"temperature_f": 75})

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["tool"] == "get_current_weather"
        assert entry["arguments"] == {"location": "Chicago"}
        assert entry["result"] == {"temperature_f": 75}
        assert "timestamp" in entry

    def test_never_raises_on_unwritable_path(self, tmp_path, monkeypatch):
        module, _ = _fresh_log(tmp_path, monkeypatch)
        monkeypatch.setattr(module, "LOG_PATH", str(tmp_path / "no_such_dir" / "log.jsonl"))
        module.record("get_current_weather", {"location": "Chicago"}, {"error": "boom"})  # must not raise


class TestRecent:
    def test_returns_empty_list_when_no_log_exists(self, tmp_path, monkeypatch):
        module, _ = _fresh_log(tmp_path, monkeypatch)
        assert module.recent() == []

    def test_returns_newest_first_and_respects_limit(self, tmp_path, monkeypatch):
        module, _ = _fresh_log(tmp_path, monkeypatch)
        for i in range(5):
            module.record(f"tool_{i}", {}, {"i": i})

        entries = module.recent(limit=3)
        assert len(entries) == 3
        assert [e["tool"] for e in entries] == ["tool_4", "tool_3", "tool_2"]

    def test_skips_malformed_lines(self, tmp_path, monkeypatch):
        module, log_path = _fresh_log(tmp_path, monkeypatch)
        module.record("good_tool", {}, {})
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("not valid json\n")

        entries = module.recent()
        assert len(entries) == 1
        assert entries[0]["tool"] == "good_tool"
