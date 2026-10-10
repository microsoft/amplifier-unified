"""Independent public MCP SDK peer for installed distribution qualification."""
import json
import os
import sys
from pathlib import Path
from mcp.server import MCPServer
from mcp.server.apps import Apps

ledger = Path(sys.argv[1])
apps = Apps()

@apps.tool(resource_uri="ui://counter/app", structured_output=True)
async def increment(amount: int = 1) -> dict[str, int]:
    with ledger.open("a") as output:
        output.write(json.dumps({"amount": amount}) + "\n")
        output.flush()
        os.fsync(output.fileno())
    return {"amount": amount}

apps.add_html_resource("ui://counter/app", "<!doctype html><html><p>Independent saved app</p></html>")
MCPServer("Distribution fixture", extensions=[apps]).run()
