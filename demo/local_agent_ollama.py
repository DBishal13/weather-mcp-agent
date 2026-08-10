"""Free, fully-local variant of local_agent.py - same MCP tool loop, driven by
Ollama (llama3.1 or any other tool-calling-capable local model) instead of the
Claude API. Zero cost, zero API key, everything runs on your machine.

Prereqs:
  1. Install Ollama (https://ollama.com) and pull a tool-calling model:
       ollama pull llama3.1
  2. In one terminal: `ollama serve` (skip if it's already running)
  3. In another:      `cd mcp_server && python weather_mcp_server.py`
  4. In a third:      `pip install -r demo/requirements.txt`
                       `python demo/local_agent_ollama.py`

Trade-off vs. local_agent.py: local models are far less reliable at tool
selection and argument formatting than Claude, especially smaller ones. Treat
this as a free way to sanity-check the MCP server end to end, not as a
substitute for the Claude-driven (or Databricks Agent Bricks) demonstration.
"""
import asyncio
import json
import os
import sys

import requests
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")

# Same contract as demo/local_agent.py and the Databricks Agent Bricks agent -
# see the README's "Agent system prompt" section.
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
    "What's the weather like in Chicago right now?",
    "Should I bring a jacket to Austin, TX today?",
    "Compare the weather in Seattle and Miami right now.",
]


def mcp_tools_to_ollama(tools):
    """OpenAI-style function-calling schema, which Ollama's /api/chat tools param expects."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in tools
    ]


def stringify_tool_result(result) -> str:
    parts = []
    for block in result.content:
        text = getattr(block, "text", None)
        parts.append(text if text is not None else str(block))
    return "\n".join(parts)


def chat(messages, tools) -> dict:
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {"temperature": 0.2},
        },
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["message"]


async def ask(session: ClientSession, tools: list, question: str) -> None:
    print(f"\n{'=' * 72}\nQ: {question}\n{'=' * 72}")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    for _ in range(6):  # hard cap so a confused local model can't loop forever
        message = chat(messages, tools)
        messages.append(message)

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            if message.get("content", "").strip():
                print(f"\n[assistant] {message['content'].strip()}")
            return

        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            if isinstance(args, str):  # some models emit a JSON string instead of an object
                args = json.loads(args)
            print(f"[tool_call] {name}({json.dumps(args)})")
            result = await session.call_tool(name, args)
            result_text = stringify_tool_result(result)
            preview = result_text if len(result_text) <= 400 else result_text[:400] + "..."
            print(f"[tool_result] {preview}")
            messages.append({"role": "tool", "content": result_text, "name": name})

    print("\n[assistant] (gave up after 6 tool-calling rounds - try a bigger local model)")


async def main() -> None:
    questions = sys.argv[1:] or DEMO_QUESTIONS

    async with streamablehttp_client(MCP_SERVER_URL) as (read_stream, write_stream, _get_session_id):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tools = mcp_tools_to_ollama(tools_result.tools)
            print(f"Connected to {MCP_SERVER_URL} - {len(tools)} tools available: "
                  f"{', '.join(t['function']['name'] for t in tools)}")
            print(f"Using local Ollama model: {OLLAMA_MODEL}")

            for question in questions:
                await ask(session, tools, question)


if __name__ == "__main__":
    asyncio.run(main())
