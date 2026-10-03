"""Read-only synthetic observer. Its sole source is one controlled record file."""
import json
import sys
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

server = MCPServer('Synthetic observer')
root = Path(sys.argv[1]).resolve()


@server.tool(structured_output=True)
async def fixture_observe(observation: dict[str, Any], ctx: Context) -> dict[str, Any]:
    if observation.get('contract') != 'amplifier.observation.v1': raise ValueError('Unsupported contract')
    if observation.get('target') != {'record': 'one', 'owner': 'fixture'} or observation.get('scope') != {'read': 'one'}:
        raise ValueError('The record does not belong to this target/read scope')
    row = json.loads((root / 'record.json').read_text())
    if row.get('request') == 'roots': await ctx.session.list_roots()
    return {'contract': 'amplifier.observation.v1', 'status': row['status'], 'target': observation['target'],
        'source': {'id': 'fixture://one', 'revision': row['revision']}, 'observedAt': 1800000000,
        'semanticKey': row['revision'], 'summary': row['summary'], 'evidence': row.get('evidence', []), 'cursor': {'revision': row['revision']}, 'presentation': row.get('presentation')}


server.run()
