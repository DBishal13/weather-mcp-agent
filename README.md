# Weather MCP Server + Agent

[![CI](https://github.com/DBishal13/weather-mcp-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/DBishal13/weather-mcp-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A weather-forecast [MCP](https://modelcontextprotocol.io) server (FastMCP, streamable-HTTP)
and a tool-using agent built on top of it — current conditions, multi-day
forecasts, and two "judgment" tools (umbrella / travel recommendations) that
apply an explicit threshold to the raw forecast instead of just echoing it
back. The MCP server itself is just a Python process; this branch runs it
locally or in Docker and drives it with two interchangeable agent runtimes:

- **Claude API** (`demo/local_agent.py`) — the more capable option, needs an
  Anthropic API key.
- **A free, fully-local model via [Ollama](https://ollama.com)** (`demo/local_agent_ollama.py`)
  — zero cost, zero API key, everything runs on your machine.

> Looking for the Databricks Apps + Agent Bricks deployment path (the
> original version of this project)? See the [`databricks`](https://github.com/DBishal13/weather-mcp-agent/tree/databricks)
> branch — same MCP server, plus `app.yaml` manifests and the steps to
> register it as an external MCP tool for a Databricks Agent Bricks agent.

## Architecture

```
                     ┌─────────────────────────────┐
  natural language   │   Agent (Claude API or a     │
  question  ───────► │   local Ollama model,        │
                      │   tool-use loop)             │
                     └──────────────┬────────────────┘
                                    │ MCP tool calls (streamable-HTTP)
                                    ▼
                     ┌─────────────────────────────┐
                     │  mcp_server/                 │
                     │  weather_mcp_server.py       │  local process, Docker,
                     │  (FastMCP, @mcp.tool funcs)  │  or any Python host
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
                     │  dashboard/app.py (optional) │
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
  (contact info), read from the `NWS_USER_AGENT` env var (see `.env.example`)
  — not a secret, just contact info.
- **No secrets are required for this build.** If you swap in a keyed API
  (e.g. WeatherAPI.com) later, `weather_mcp_server.py` already has a `_secret()`
  helper as a starting point for wiring up whatever secret store you deploy
  behind — never hardcode a key.

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

### 2. See it work — pick an agent runtime

With the server running from step 1, in a second terminal, either:

**Free, fully local (Ollama)** — no API key, no cost:

```bash
ollama pull llama3.1          # once; any tool-calling-capable model works
pip install -r demo/requirements.txt
python demo/local_agent_ollama.py
```

**Or Claude API** — more capable tool selection and reasoning:

```bash
pip install -r demo/requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # or: ant auth login
python demo/local_agent.py
```

Both spin up a real tool-use loop against the running MCP server and print
the full transcript (tool calls, arguments, results, final answer). Pass your
own question instead of the built-in set:

```bash
python demo/local_agent.py "Will it snow in Denver tomorrow?"
python demo/local_agent_ollama.py "Will it snow in Denver tomorrow?"
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

### 5. Deploying elsewhere

The MCP server is a plain FastMCP process (`python weather_mcp_server.py`,
binds `PORT`/`0.0.0.0`) — deploy it anywhere that runs Python, point any
MCP-compatible agent at its `/mcp` endpoint. For the specific Databricks Apps
+ Agent Bricks path (this project's original deployment target), see the
[`databricks`](https://github.com/DBishal13/weather-mcp-agent/tree/databricks) branch.

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

This is the exact prompt used by both `demo/local_agent.py` and
`demo/local_agent_ollama.py` — same tool contract, same guardrails,
regardless of which model is driving it. (The `databricks` branch's Agent
Bricks agent uses the identical prompt too.)

## Demonstration

Run either demo script for a live, real transcript (tool calls, arguments,
and results all printed) — the fastest way to see this working. Two runs
against a locally-running server, captured verbatim:

**`python demo/local_agent_ollama.py "What's the weather like in Chicago right now?"`**
(free, `llama3.1:8b`, CPU inference):

```
[tool_call] get_current_weather({"location": "Chicago"})
[tool_result] {
  "resolved_location": "Chicago, Illinois, United States",
  "observed_at": "2026-08-10T15:45",
  "temperature_f": 77.2,
  "feels_like_f": 85.2,
  "humidity_pct": 92,
  "wind_mph": 6.3,
  "wind_direction_deg": 48,
  "precipitation_in": 0.0,
  "conditions": "Clear sky"
}

[assistant] The current weather in Chicago is clear skies with a temperature
of 77°F, feeling like 85°F due to the humidity. The wind is blowing at about
6 mph from the northeast, and there's no precipitation currently.
```

**`python demo/local_agent_ollama.py "Should I bring an umbrella in Seattle today?"`**
— the smaller local model guessed a stale training-era date instead of
"today"; the tool correctly rejected it as outside the forecast window, and
the agent surfaced that instead of guessing an answer (exactly the guardrail
the system prompt asks for):

```
[tool_call] predict_umbrella_needed({"date": "2023-12-01", "location": "Seattle"})
[tool_result] {
  "error": "No forecast available for 'Seattle' on '2023-12-01' (forecast window is the next 10 days)."
}

[assistant] I'm having trouble getting a weather report for Seattle. Could
you please clarify or try again? I'll do my best to help once we get a
valid location and date.
```

The Claude-driven `demo/local_agent.py` script runs the same four questions
end to end (tool selection, arguments, and final answer) with noticeably more
reliable argument handling — worth running if you have an Anthropic API key:

1. **"Will it rain in Chicago tomorrow?"** → agent calls
   `predict_umbrella_needed(location="Chicago, IL", date="<tomorrow>")` → answer.
2. **"Should I bring a jacket to Austin, TX today?"** → agent calls
   `get_travel_recommendation(location="Austin, TX")` → answer.
3. **"Compare the weather in Seattle and Miami right now."** → agent calls
   `compare_weather(locations=["Seattle, WA", "Miami, FL"])` → answer.
4. *(stretch)* **"Are there any severe weather alerts for Oklahoma City?"** →
   agent calls `get_weather_alerts(location="Oklahoma City, OK")` → answer.

_Paste the actual Claude-driven transcript here once you've run it._

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
- `demo/local_agent_ollama.py` trades reliability for cost: small local
  models are noticeably worse than Claude at tool selection and argument
  formatting (see the umbrella example above). It's a good free sanity check
  that the MCP server works end to end, not a substitute for the Claude- or
  Agent Bricks-driven demonstration.

## License

[MIT](LICENSE)
