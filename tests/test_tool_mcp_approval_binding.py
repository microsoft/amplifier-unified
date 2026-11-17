"""Real Core/live/host plus canonical MCP; scripted transport/source eligibility.

This is not installed-package provenance or an external server/model trial.
"""
import pytest

pytest.importorskip('amplifier_module_tool_mcp.approval_binding')
from amplifier_module_tool_mcp.approval_binding import ConnectionBinding
from amplifier_module_tool_mcp.client import MCPClient, ConnectionState
from amplifier_module_tool_mcp.streamable_http_client import MCPStreamableHTTPClient
from amplifier_module_tool_mcp.wrapper import MCPToolWrapper
from mcp.types import CallToolResult, TextContent

from amplifier_web import tool_approval_scope
from test_tool_request_memory import flow, decide  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('transport', ['stdio', 'http'])
async def test_canonical_client_uses_host_bound_invocation(flow, monkeypatch, transport):
    effects = []
    class Session:
        async def call_tool(self, name, arguments):
            effects.append((name, arguments.copy()))
            return CallToolResult(content=[TextContent(type='text', text='Fixture')])
    client = (MCPClient('selected', 'fixture-command', [], env={'AUTH': 'fixture-only'})
              if transport == 'stdio' else
              MCPStreamableHTTPClient('selected', 'https://fixture.invalid/mcp',
                                      headers={'Authorization': 'fixture-only'}))
    client._state, client._connected = ConnectionState.CONNECTED, True
    client._protocol_version = '2026-07-28'
    client.tools = [{'name': 'remote/name', 'input_schema': flow.tools['mcp'].input_schema}]
    def connected_session():
        client.session = Session()
        client._approval_connection = ConnectionBinding(client, client.session,
            client._approval_configuration(), {'transport': transport, 'protocol': client._protocol_version})
    connected_session()
    tool = MCPToolWrapper('selected', client.tools[0], client, flow.coordinator.hooks)
    tool.role = 'mcp'  # Explicit private source shim, never product authority.
    flow.tools['mcp'] = tool
    await flow.coordinator.mount('tools', tool, name='mcp')
    prior = tool_approval_scope._implementation
    monkeypatch.setattr(tool_approval_scope, '_implementation', lambda item, proof, method='execute':
        {'fixture': 'canonical-client-source'} if item is client else prior(item, proof, method))
    first = await decide(flow, 'mcp')
    assert effects == [('remote/name', {'command': 'exact request'})]
    connected_session()
    second = await decide(flow, 'mcp', decision='deny')
    assert first['toolScope']['descriptor']['key'] == second['toolScope']['descriptor']['key']
    # A reconnect during the pending decision is not the new-call case above.
    await decide(flow, 'mcp', change=connected_session)
    assert len(effects) == 1
    # A change after the decision is caught at the normal Work boundary.
    flow.callbacks.append(connected_session)
    await decide(flow, 'mcp')
    assert len(effects) == 1
