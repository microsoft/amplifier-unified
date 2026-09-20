"""A domain-agnostic standards fixture: no Amplifier imports or host URLs."""
import asyncio
import os
from pathlib import Path
import sys
from typing import Any

from mcp.server import MCPServer

server = MCPServer("Unfamiliar board", instructions="Use board_set for shared state. Read the latest revision before changing it.")
counter = Path(sys.argv[1])


@server.tool(meta={"ui": {"resourceUri": "ui://board/main"}}, structured_output=True)
def board_set(value: str) -> dict[str, Any]:
    """Save a shared board value and return a durable revision."""
    counter.write_text(value)
    return {"value": value, "revision": counter.stat().st_mtime_ns}


@server.tool(meta={"ui": {"visibility": ["app"]}}, structured_output=True)
def view_only() -> dict[str, Any]:
    return {"view": "compact"}


@server.tool(meta={"ui": {"visibility": ["model"]}}, structured_output=True)
def model_only() -> dict[str, Any]:
    return {"model": True}


@server.tool(structured_output=True)
def environment() -> dict[str, Any]:
    return {"explicit": os.environ.get("EXPLICIT_TOKEN"), "unrelated": os.environ.get("UNRELATED_PRIVATE_KEY")}


@server.tool()
async def slow() -> str:
    counter.write_text("started")
    await asyncio.sleep(2)
    counter.write_text("finished")
    return "finished"


@server.tool()
def failing() -> str:
    raise ValueError("Fixture rejected the request")


@server.tool()
def exit_fixture() -> str:
    """Test-only abrupt transport death."""
    os._exit(17)


@server.resource("ui://board/main", mime_type="text/html;profile=mcp-app", meta={"ui": {"csp": {"resourceDomains": ["https://example.com"]}}})
def board_view() -> str:
    return "<!doctype html><html><body><button>Shared board</button></body></html>"


@server.tool(meta={"ui": {"resourceUri": "ui://board/wrong-mime"}})
def bad_view() -> str:
    return "bad"


@server.resource("ui://board/wrong-mime", mime_type="text/plain")
def wrong_view() -> str:
    return "not HTML"


@server.resource("board://retained/{identity}", mime_type="application/octet-stream")
def retained_resource(identity: str) -> bytes:
    if identity == "large":
        return b"x" * 2_000_001
    if identity == "secret":
        return os.environ.get("EXPLICIT_TOKEN", "unset").encode()
    return b"retained media"


if __name__ == "__main__":
    server.run()
