"""Real MCP fixture with durable mutation evidence and a changed-schema switch."""
import json
from pathlib import Path
import sys

from mcp.server import MCPServer
from mcp.server.apps import Apps

html, calls, mode = map(Path, sys.argv[1:4])
apps = Apps()


def add(amount):
    prior = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
    count = (prior[-1]['count'] if prior else 0) + int(amount)
    with calls.open('a') as log:
        log.write(json.dumps({'amount': amount, 'count': count}) + '\n')
    return {'count': count}


if mode.exists() and mode.read_text() == 'changed':
    @apps.tool(resource_uri='ui://recovery/app', structured_output=True)
    def counter_add(amount: str = '1') -> dict[str, int]:
        """Add to the retained synthetic counter."""
        return add(amount)
else:
    @apps.tool(resource_uri='ui://recovery/app', structured_output=True)
    def counter_add(amount: int = 1) -> dict[str, int]:
        """Add to the retained synthetic counter."""
        return add(amount)

apps.add_html_resource('ui://recovery/app', html.read_text())
MCPServer('Recovery fixture', extensions=[apps]).run()
