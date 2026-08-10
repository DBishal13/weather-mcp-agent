"""Optional dashboard: shows recent MCP tool calls (query/prediction) from the log
written by mcp_server/query_log.py. See the README for setup.

For a real multi-container deployment, point QUERY_LOG_PATH at a shared location,
or swap query_log.py's file-backed record()/recent() for a real database (e.g. a
Postgres table) so this dashboard can read from the same store the MCP server
writes to instead of a local file.
"""
import os
import sys
from datetime import datetime

from flask import Flask, jsonify, render_template_string

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))
from query_log import recent  # noqa: E402

app = Flask(__name__)

PAGE_TEMPLATE = """
<!doctype html>
<html>
<head>
  <title>Weather MCP - Recent Queries</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; background: #0b1220; color: #e5e7eb; }
    h1 { font-size: 1.25rem; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #263045; vertical-align: top; }
    th { color: #93c5fd; font-weight: 600; }
    tr:hover { background: #111a2e; }
    .tool { font-family: monospace; color: #86efac; }
    .err { color: #fca5a5; }
    pre { white-space: pre-wrap; word-break: break-word; margin: 0; font-size: 0.85rem; }
  </style>
</head>
<body>
  <h1>Weather MCP server - recent tool calls</h1>
  <table>
    <tr><th>Time (UTC)</th><th>Tool</th><th>Arguments</th><th>Result</th></tr>
    {% for e in entries %}
    <tr>
      <td>{{ e.timestamp }}</td>
      <td class="tool">{{ e.tool }}</td>
      <td><pre>{{ e.arguments }}</pre></td>
      <td class="{{ 'err' if e.result is mapping and e.result.get('error') else '' }}"><pre>{{ e.result }}</pre></td>
    </tr>
    {% else %}
    <tr><td colspan="4">No queries logged yet - call a tool on the MCP server first.</td></tr>
    {% endfor %}
  </table>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE_TEMPLATE, entries=recent(50))


@app.route("/api/recent")
def api_recent():
    return jsonify(recent(50))


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("DATABRICKS_APP_PORT", os.environ.get("PORT", 8001))))
