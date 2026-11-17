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
async def test_canonical_stdio_client_uses_host_bound_invocation(flow, monkeypatch):
    effects = []
    class Session:
        async def call_tool(self, name, arguments):
            effects.append((name, arguments.copy()))
            return CallToolResult(content=[TextContent(type='text', text='Fixture')])
    client = MCPClient('selected', 'fixture-command', [], env={'AUTH': 'fixture-only'})
    client._state, client._connected = ConnectionState.CONNECTED, True
    client._protocol_version = '2026-07-28'
    client.tools = [{'name': 'remote/name', 'input_schema': flow.tools['mcp'].input_schema}]
    def connected_session():
        client.session = Session()
        client._approval_connection = ConnectionBinding(client, client.session,
            client._approval_configuration(), {'transport': 'stdio', 'protocol': client._protocol_version})
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


@pytest.mark.asyncio
@pytest.mark.parametrize('capability', [None, False, True, 0, '1', 2])
async def test_canonical_http_client_retains_once_and_deny(flow, monkeypatch, capability):
    effects = []
    class Session:
        async def call_tool(self, name, arguments):
            effects.append((name, arguments.copy()))
            return CallToolResult(content=[TextContent(type='text', text='Fixture')])
    client = MCPStreamableHTTPClient('selected', 'https://fixture.invalid/mcp')
    client.session, client._connected = Session(), True
    client.tools = [{'name': 'remote/name', 'input_schema': flow.tools['mcp'].input_schema}]
    # Even an older candidate exposing configuration-only APIs must not create
    # a reusable HTTP offer. Actual send-time authorization is not enforced.
    client.approval_binding = lambda: {'version': 1, 'key': 'a' * 64,
                                      'httpSendAuthorization': capability}
    prior = tool_approval_scope._implementation
    monkeypatch.setattr(tool_approval_scope, '_implementation', lambda item, proof, method='execute':
        {'fixture': 'canonical-client-source'} if item is client else prior(item, proof, method))
    tool = MCPToolWrapper('selected', client.tools[0], client, flow.coordinator.hooks)
    tool.role = 'mcp'
    flow.tools['mcp'] = tool
    await flow.coordinator.mount('tools', tool, name='mcp')
    await decide(flow, 'mcp', decision='deny', scoped=False)
    assert effects == []
    await decide(flow, 'mcp', scoped=False)
    assert effects == [('remote/name', {'command': 'exact request'})]


@pytest.mark.asyncio
async def test_http_send_capability_reaches_host_scope_and_work_recheck(flow, monkeypatch):
    from amplifier_module_tool_mcp.http_approval import HTTPApprovalGuard
    import httpx

    effects = []
    class Session:
        async def call_tool(self, name, arguments):
            effects.append((name, arguments.copy()))
            return CallToolResult(content=[TextContent(type='text', text='Fixture')])

    # This is the host/descriptor seam with scripted session effects. The MCP
    # repository separately exercises the actual SDK queue and HTTP send.
    client = MCPStreamableHTTPClient('selected', 'https://fixture.invalid/mcp')
    client.session, client._connected = Session(), True
    client._protocol_version = '2025-11-25'
    client.tools = [{'name': 'remote/name', 'input_schema': flow.tools['mcp'].input_schema}]
    async with httpx.AsyncClient(headers={'Authorization': 'Bearer fixture-only'}) as http:
        client._approval_http = HTTPApprovalGuard(client, http)
        client._approval_http.session = client.session
        prior = tool_approval_scope._implementation
        monkeypatch.setattr(tool_approval_scope, '_implementation', lambda item, proof, method='execute':
            {'fixture': 'canonical-client-source'} if item is client else prior(item, proof, method))
        tool = MCPToolWrapper('selected', client.tools[0], client, flow.coordinator.hooks)
        tool.role = 'mcp'
        flow.tools['mcp'] = tool
        await flow.coordinator.mount('tools', tool, name='mcp')
        allowed = await decide(flow, 'mcp')
        denied = await decide(flow, 'mcp', decision='deny')
        assert allowed['toolScope']['descriptor']['key'] == denied['toolScope']['descriptor']['key']
        assert effects == [('remote/name', {'command': 'exact request'})]
        flow.callbacks.append(lambda: http.headers.update({'Authorization': 'Bearer changed'}))
        await decide(flow, 'mcp')
        assert len(effects) == 1
