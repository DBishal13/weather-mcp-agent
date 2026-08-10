"""Local, Databricks-free demo of the weather MCP server + a Claude tool-use agent.

Talks to the *same* weather_mcp_server.py used in Databricks Apps over
streamable-HTTP - only the agent runtime differs (Claude API here vs. Agent
Bricks in the deployed version). Useful for showing the MCP server actually
works end to end without needing a Databricks workspace.

Prereqs:
  1. In one terminal: `cd mcp_server && python weather_mcp_server.py`
  2. In another:       `export ANTHROPIC_API_KEY=...` (or `ant auth login`)
  3. `pip install -r demo/requirements.txt && python demo/local_agent.py`

Run a single question instead of the built-in demo set:
  python demo/local_agent.py "Will it snow in Denver tomorrow?"
"""
import asyncio
import json
import os
import sys

import anthropic
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")

# Kept in sync with the "Agent system prompt" section of the README - this is
# the same prompt registered on the Databricks Agent Bricks agent.
SYSTEM_PROMPT = """\
You are a weather assistant. You answer questions about current conditions,
forecasts, and simple packing/travel recommendations using ONLY the tools
provided by the weather MCP server - never answer from your own knowledge of
typical weather for a place or season.

Tool selection:
- "What's the weather like right now / today" -> get_current_weather.
- "Forecast for the next N days" -> get_forecast.
- "Will it rain", "do I need an umbrella" -> predict_umbrella_needed.
- "What should I pack / wear", "should I bring a jacket", general travel
  planning -> get_travel_recommendation.
- "Any warnings/alerts" (US locations only) -> get_weather_alerts.
- Comparing 2+ cities -> compare_weather.

Rules:
- Always call a tool before answering a weather question. Do not guess or
  fabricate temperatures, precipitation chances, or conditions.
- If a tool returns {"error": ...}, do not retry blindly - tell the user
  the location couldn't be resolved (or the API failed) and ask them to
  clarify or try again, instead of inventing an answer.
- If the user doesn't give a date, assume they mean today/the soonest
  available forecast day, and say which date you used.
- get_weather_alerts only covers US locations - if a tool response has
  supported: false, tell the user alerts aren't available there rather
  than implying there are none.
- When you use predict_umbrella_needed or get_travel_recommendation, quote
  the actual numbers/reasons the tool returned (e.g. "60% chance of rain")
  rather than just stating the conclusion.
- Keep answers short and conversational; you're a weather assistant, not a
  meteorology report generator.
"""

DEMO_QUESTIONS = [
    "Will it rain in Chicago tomorrow?",
    "Should I bring a jacket to Austin, TX today?",
    "Compare the weather in Seattle and Miami right now.",
    "Are there any severe weather alerts for Oklahoma City?",
]


def mcp_tools_to_anthropic(tools):
    return [
        {"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
        for t in tools
    ]


def stringify_tool_result(result) -> str:
    parts = []
    for block in result.content:
        text = getattr(block, "text", None)
        parts.append(text if text is not None else str(block))
    return "\n".join(parts)


async def ask(session: ClientSession, tools: list, client: anthropic.Anthropic, question: str) -> None:
    print(f"\n{'=' * 72}\nQ: {question}\n{'=' * 72}")
    messages = [{"role": "user", "content": question}]

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        tool_results = []
        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"\n[assistant] {block.text.strip()}")
            elif block.type == "tool_use":
                print(f"[tool_call] {block.name}({json.dumps(block.input)})")
                result = await session.call_tool(block.name, block.input)
                result_text = stringify_tool_result(result)
                preview = result_text if len(result_text) <= 400 else result_text[:400] + "..."
                print(f"[tool_result] {preview}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                    "is_error": bool(getattr(result, "isError", False)),
                })

        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        messages.append({"role": "user", "content": tool_results})


async def main() -> None:
    questions = sys.argv[1:] or DEMO_QUESTIONS
    client = anthropic.Anthropic()

    async with streamablehttp_client(MCP_SERVER_URL) as (read_stream, write_stream, _get_session_id):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tools = mcp_tools_to_anthropic(tools_result.tools)
            print(f"Connected to {MCP_SERVER_URL} - {len(tools)} tools available: "
                  f"{', '.join(t['name'] for t in tools)}")

            for question in questions:
                await ask(session, tools, client, question)


if __name__ == "__main__":
    asyncio.run(main())
