"""Unfamiliar, deterministic MCP App. No Unified imports and no model calls."""
from pathlib import Path
import asyncio
import sys
from typing import Any
from mcp.server import MCPServer
from mcp.server.apps import Apps
from mcp.types import ToolAnnotations

apps = Apps()
count = 0

@apps.tool(resource_uri='ui://counter/app', structured_output=True,
           annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
def counter_read() -> dict[str, Any]:
    """Read the shared counter without changing it."""
    return {'count':count}

@apps.tool(resource_uri='ui://counter/app', structured_output=True)
async def counter_add(amount: int = 1, delay_ms: int = 0) -> dict[str, Any]:
    """Add an amount to the shared counter."""
    global count
    await asyncio.sleep(min(max(delay_ms, 0), 5000) / 1000)
    count += amount
    return {'count':count}

apps.add_html_resource('ui://counter/app', Path(sys.argv[1]).read_text())
server = MCPServer('Independent counter', extensions=[apps])

@server.resource('counter://media/{identity}', mime_type='text/plain')
def media(identity: str) -> str:
    return f'Retained {identity}'

server.run()
