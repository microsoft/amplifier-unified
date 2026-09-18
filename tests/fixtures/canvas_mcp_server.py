"""Unfamiliar, deterministic MCP App. No Unified imports and no model calls."""
from pathlib import Path
import sys
from typing import Any
from mcp.server import MCPServer
from mcp.server.apps import Apps

apps = Apps()
count = 0

@apps.tool(resource_uri='ui://counter/app', structured_output=True)
def counter_read() -> dict[str, Any]:
    """Read the shared counter without changing it."""
    return {'count':count}

@apps.tool(resource_uri='ui://counter/app', structured_output=True)
def counter_add(amount: int = 1) -> dict[str, Any]:
    """Add an amount to the shared counter."""
    global count
    count += amount
    return {'count':count}

apps.add_html_resource('ui://counter/app', Path(sys.argv[1]).read_text())
server = MCPServer('Independent counter', extensions=[apps])
server.run()
