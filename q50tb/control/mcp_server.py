"""MCP server (stdio) for AI agents.

Default: proxies every tool to the running GUI app's control server
(127.0.0.1:8765), so the agent and the human see and drive the same bench.
``--headless`` instead creates the bench in this process (no GUI) - useful
for CI or when nobody is at the bench.

Register with an MCP client, e.g. Claude Code:

    claude mcp add q50-bench -- <repo>/.venv/Scripts/python -m q50tb.control.mcp_server
    (Linux: <repo>/.venv/bin/python -m q50tb.control.mcp_server)

Tools are generated from ``q50tb.control.api`` - every @tool method there is
exposed with its docstring and parameter schema.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import anyio  # noqa: E402
import mcp.types as types  # noqa: E402
from mcp.server.lowlevel import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402

from q50tb.control.api import TOOLS, ApiError, BenchAPI  # noqa: E402
from q50tb.control.server import DEFAULT_PORT, ControlClient  # noqa: E402

INSTRUCTIONS = """Infiniti Q50 combination-meter test bench. Call `help` first, then `status`.
Bench (TX) mode must never be used on a real vehicle; use start_buses(monitor=true) there.
Signal paths look like main.180.ENGINE_RPM (see list_signals). Meter settings writes succeed only
when the meter echoes the value ('confirmed'). Factory reset needs explicit user confirmation."""


def build_backend(headless: bool, port: int, token: str):
    if headless:
        from q50tb.app_context import AppContext
        ctx = AppContext()
        api = BenchAPI(ctx)
        return lambda name, args: api.call(name, args)
    client = ControlClient(port=port, token=token)

    def call(name, args):
        try:
            return client.call(name, args)
        except (ConnectionError, OSError) as exc:
            raise ApiError(f"cannot reach the Q50 TestBench app on 127.0.0.1:{port} ({exc}). "
                           "Start the GUI (python -m q50tb) or run this server with --headless.")
    return call


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true", help="run the bench in-process without the GUI")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--token", default=os.environ.get("Q50TB_TOKEN", ""))
    args = ap.parse_args()
    backend = build_backend(args.headless, args.port, args.token)

    async def on_list_tools(ctx, params):
        return types.ListToolsResult(tools=[
            types.Tool(name=t["name"], description=t["description"], input_schema=t["input_schema"])
            for t in TOOLS.values()])

    async def on_call_tool(ctx, params: types.CallToolRequestParams):
        name = params.name
        arguments = params.arguments or {}
        try:
            result = await anyio.to_thread.run_sync(lambda: backend(name, arguments))
            text = json.dumps(result, indent=1, default=str)
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=False)
        except Exception as exc:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"error: {exc}")],
                                        is_error=True)

    server = Server("q50-testbench", version="1.0", instructions=INSTRUCTIONS,
                    on_list_tools=on_list_tools, on_call_tool=on_call_tool)

    async def run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)


if __name__ == "__main__":
    main()
