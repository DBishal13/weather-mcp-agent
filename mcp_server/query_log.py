"""Tiny append-only JSONL log of tool calls, read by the optional dashboard app.

Not a substitute for a real store like Postgres - if this MCP server needs to
survive restarts or be read by a dashboard running in a separate Databricks
App container, point LOG_PATH at a shared volume (see docker-compose.yml) or
swap this for a database table. For a single-container demo, a local file is
enough.
"""
import json
import os
import threading
from datetime import datetime, timezone

LOG_PATH = os.environ.get("QUERY_LOG_PATH", os.path.join(os.path.dirname(__file__), "query_log.jsonl"))
_lock = threading.Lock()


def record(tool_name, arguments, result):
    """Appends one {timestamp, tool, arguments, result} entry. Never raises - logging must not break a tool call."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "arguments": arguments,
        "result": result,
    }
    try:
        with _lock:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def recent(limit=25):
    """Returns up to `limit` most recent log entries, newest first."""
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    entries = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(entries))
