"""Provider-free MCP dashboard aliases with explicit retained entity identity."""
import json
from pathlib import Path
import sys
from mcp.server import MCPServer
from mcp.server.apps import Apps
from mcp.types import CallToolResult, TextContent, ToolAnnotations

html, calls = map(Path, sys.argv[1:3])
apps = Apps()


def present(name, entity_id, value):
    result = {'entity': entity_id, 'value': value, 'method': name}
    with calls.open('a') as stream:
        stream.write(json.dumps(result) + '\n')
    return CallToolResult(content=[TextContent(type='text', text=json.dumps(result))],
                          structuredContent=result,
                          _meta={'amplifier/presentationId': 'fixture:entity:' + entity_id})


@apps.tool(resource_uri='ui://fixture/dashboard', annotations=ToolAnnotations(readOnlyHint=True))
def dashboard_read(entity_id: str, value: int) -> CallToolResult:
    return present('read', entity_id, value)


@apps.tool(resource_uri='ui://fixture/dashboard', annotations=ToolAnnotations(readOnlyHint=True))
def dashboard_detail(entity_id: str, value: int) -> CallToolResult:
    return present('detail', entity_id, value)


apps.add_html_resource('ui://fixture/dashboard', html.read_text())
MCPServer('Presentation fixture', extensions=[apps]).run()
