# Weather MCP Server + Agent

[![CI](https://github.com/DBishal13/weather-mcp-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/DBishal13/weather-mcp-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A weather-forecast [MCP](https://modelcontextprotocol.io) server (FastMCP, streamable-HTTP)
and a tool-using agent built on top of it — current conditions, multi-day
forecasts, and two "judgment" tools (umbrella / travel recommendations) that
apply an explicit threshold to the raw forecast instead of just echoing it
back. The MCP server is transport-agnostic: it's demonstrated here two ways —

- **Locally**, driven by the Claude API directly (`demo/local_agent.py`) — no
  cloud account needed, just an Anthropic API key.
- **Deployed as a Databricks App**, driven by a Databricks Agent Bricks agent
  registered against it as an external MCP tool.

## Architecture

```
                     ┌─────────────────────────────┐
  natural language   │   Agent (Claude API tool-use │
  question  ───────► │   loop, or Databricks Agent  │
                      │   Bricks + external MCP)     │
                     └──────────────┬────────────────┘
                                    │ MCP tool calls (streamable-HTTP)
                                    ▼
                     ┌─────────────────────────────┐
                     │  mcp_server/                 │
                     │  weather_mcp_server.py       │  Databricks App #1
                     │  (FastMCP, @mcp.tool funcs)  │  or a local process
                     └──────────────┬────────────────┘
                                    │ calls (no raw requests here)
                                    ▼
                     ┌─────────────────────────────┐
                     │  mcp_server/weather_broker.py│
                     │  HTTP + parsing adapter      │
                     └──────┬────────────────┬───────┘
                            ▼                ▼
                    Open-Meteo API      NWS api.weather.gov
                 (current + forecast)   (US-only alerts, stretch)

                     ┌─────────────────────────────┐
                     │  dashboard/app.py (optional) │  Databricks App #2
                     │  reads mcp_server/query_log  │
                     └─────────────────────────────┘
```

`weather_mcp_server.py` keeps `@mcp.tool` functions thin: parse args, call
into `weather_broker.py`, shape the result, log it. `weather_broker.py` owns
every `requests` call and all response parsing — no tool function talks to an
HTTP API directly, which is what makes `mcp_server/tests/test_weather_broker.py`
possible without a network connection (everything mocks `requests.get`).

## Tools exposed by `mcp_server/weather_mcp_server.py`

| Tool | Purpose |
|---|---|
| `get_current_weather(location)` | Current temperature, feels-like, humidity, wind, conditions. |
| `get_forecast(location, days=3)` | Daily forecast (1-16 days): highs/lows, precip chance, conditions. |
| `predict_umbrella_needed(location, date=None)` | **Derived judgment**: umbrella if precip probability > 40% or expected precip > 0.1in for that date. Returns the numbers + the reason, not just a bool. |
| `get_travel_recommendation(location, date=None)` | **Derived judgment**: umbrella / jacket (low < 55°F) / sun protection (high > 90°F) / high-wind caution (gusts > 25mph), each independently explained. |
| `get_weather_alerts(location)` | *Stretch.* Active NWS severe weather alerts, US-only; returns `supported: false` + a note for non-US locations instead of erroring. |
| `compare_weather(locations)` | *Stretch.* Current conditions for 2-6 locations side by side; one bad location doesn't fail the others. |

All tools catch `WeatherAPIError` and return `{"error": "<message>"}` — the
agent never sees a raw stack trace.

## Weather API + auth

- **Primary: [Open-Meteo](https://open-meteo.com)** — no signup, no API key,
  ~10k calls/day non-commercial. Used for geocoding, current conditions, and
  daily forecasts.
- **Stretch: [NWS](https://api.weather.gov)** — no key either, but US-only.
  Used only for `get_weather_alerts`. Requires a courtesy `User-Agent` header
  (contact info), read from the `NWS_USER_AGENT` env var — not a secret, just
  set as a plain value in `mcp_server/app.yaml`.
- **No secrets are required for this build.** If you swap in a keyed API
  (e.g. WeatherAPI.com) later, `weather_mcp_server.py` already has a `_secret()`
  helper (`WorkspaceClient().secrets.get_secret(scope, key)`) — store the key
  with `databricks secrets put-secret <scope> <key>` and reference it from
  `app.yaml` via `valueFrom`, never hardcode it.

## Setup

### 1. Run the MCP server locally

```bash
cd mcp_server
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env                             # edit NWS_USER_AGENT with your contact info
python weather_mcp_server.py
```

Server listens on `http://0.0.0.0:8000/mcp` (streamable-HTTP transport).

### 2. See it work — local demo (no Databricks account needed)

With the server running from step 1, in a second terminal:

```bash
pip install -r demo/requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # or: ant auth login
python demo/local_agent.py
```

This spins up a real Claude tool-use loop against the running MCP server and
runs four demo questions end to end (tool calls, results, and final answers
all printed). Pass your own question instead of the built-in set:

```bash
python demo/local_agent.py "Will it snow in Denver tomorrow?"
```

### 3. Or run everything in Docker

```bash
docker compose up --build
```

Starts the MCP server on `:8000` and the optional dashboard on `:8001`,
sharing a Docker volume for the query log.

### 4. Tests

```bash
pip install -r mcp_server/requirements.txt pytest
pytest
```

`mcp_server/tests/` unit-tests `weather_broker.py` (all HTTP mocked, no
network calls) and `query_log.py`. Runs on every push via
[GitHub Actions](.github/workflows/ci.yml).

### 5. Deploy as a Databricks App

```bash
databricks auth login --host <your-workspace-url>          # one-time
databricks apps create weather-mcp-server
databricks sync mcp_server /Workspace/Users/<you>/weather-mcp-server
databricks apps deploy weather-mcp-server \
  --source-code-path /Workspace/Users/<you>/weather-mcp-server
```

(Optional stretch dashboard, same pattern against `dashboard/`:)

```bash
databricks apps create weather-dashboard
databricks sync dashboard /Workspace/Users/<you>/weather-dashboard
databricks apps deploy weather-dashboard \
  --source-code-path /Workspace/Users/<you>/weather-dashboard
```

Grab the deployed app's URL (`databricks apps get weather-mcp-server`) — you'll
need it to register the external MCP tool in the next step.

### 6. Register the MCP server as an external MCP tool

In the Databricks workspace UI: **Agents → Agent Bricks → (your agent) → Tools
→ Add tool → External MCP server**, and point it at the deployed app's
`/mcp` endpoint URL from step 5.

### 7. Build the Agent Bricks agent

Create a new Agent Bricks agent, attach the external MCP server from step 6,
and set the system prompt below.

## Agent system prompt

```
You are a weather assistant. You answer questions about current conditions,
forecasts, and simple packing/travel recommendations using ONLY the tools
provided by the weather MCP server — never answer from your own knowledge of
typical weather for a place or season.

Tool selection:
- "What's the weather like right now / today" → get_current_weather.
- "Forecast for the next N days" → get_forecast.
- "Will it rain", "do I need an umbrella" → predict_umbrella_needed.
- "What should I pack / wear", "should I bring a jacket", general travel
  planning → get_travel_recommendation.
- "Any warnings/alerts" (US locations only) → get_weather_alerts.
- Comparing 2+ cities → compare_weather.

Rules:
- Always call a tool before answering a weather question. Do not guess or
  fabricate temperatures, precipitation chances, or conditions.
- If a tool returns {"error": ...}, do not retry blindly — tell the user
  the location couldn't be resolved (or the API failed) and ask them to
  clarify or try again, instead of inventing an answer.
- If the user doesn't give a date, assume they mean today/the soonest
  available forecast day, and say which date you used.
- get_weather_alerts only covers US locations — if a tool response has
  supported: false, tell the user alerts aren't available there rather
  than implying there are none.
- When you use predict_umbrella_needed or get_travel_recommendation, quote
  the actual numbers/reasons the tool returned (e.g. "60% chance of rain")
  rather than just stating the conclusion.
- Keep answers short and conversational; you're a weather assistant, not a
  meteorology report generator.
```

This is the exact prompt used both by `demo/local_agent.py` and the
Databricks Agent Bricks agent — same tool contract, same guardrails,
regardless of which runtime is driving it.

## Demonstration

Run `python demo/local_agent.py` for a live, real transcript against four
questions (tool calls, arguments, and results all printed) — the fastest way
to see this working without any cloud setup. Sample of what it produces:

1. **"Will it rain in Chicago tomorrow?"** → agent calls
   `predict_umbrella_needed(location="Chicago, IL", date="<tomorrow>")` → answer.
2. **"Should I bring a jacket to Austin, TX today?"** → agent calls
   `get_travel_recommendation(location="Austin, TX")` → answer.
3. **"Compare the weather in Seattle and Miami right now."** → agent calls
   `compare_weather(locations=["Seattle, WA", "Miami, FL"])` → answer.
4. *(stretch)* **"Are there any severe weather alerts for Oklahoma City?"** →
   agent calls `get_weather_alerts(location="Oklahoma City, OK")` → answer.

_Paste the actual transcript (or a screenshot from the deployed Agent Bricks
agent) here once you've run it._

## Notes / limitations

- No secrets are committed; `.env` is git-ignored, only `.env.example` is checked in.
- The optional dashboard logs to a local JSONL file per container — fine for a
  single-instance demo, not for a scaled deployment. For that, swap
  `query_log.py`'s file-backed `record()`/`recent()` for a real store (e.g. a
  Postgres table) so the dashboard can read from the same database the MCP
  server writes to, instead of a local file.
- Geocoding picks the top Open-Meteo match for ambiguous names (e.g. "Springfield");
  the resolved location name is always echoed back in tool output so mistakes are visible.
- `mcp_server/Dockerfile` and `dashboard/Dockerfile` build from the repo root
  (`docker compose up --build` handles this) since the dashboard imports
  `mcp_server/query_log.py`.

## License

[MIT](LICENSE)
