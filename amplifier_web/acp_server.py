"""Public ACP SDK attachment to the running host's execution owner.

The WebSocket carries one JSON-RPC object per text message. It is the private
carrier for the supported stdio launcher, not a second execution service.
EOF releases observations and transient connectors, never accepted native work.
"""
from __future__ import annotations

import asyncio
import hmac
import json
from pathlib import Path
from urllib.parse import urlsplit
import uuid

from acp import AgentSideConnection, RequestError
from acp.schema import (AgentCapabilities, AgentMessageChunk, InitializeResponse,
                        McpCapabilities, NewSessionResponse, PromptResponse,
                        ResumeSessionResponse, SessionCapabilities,
                        SessionResumeCapabilities, TextContentBlock,
                        ToolCallProgress, ToolCallStart)
from aiohttp import WSMsgType, web

from .execution_owner import ExecutionOwner
from .errors import AppError
from .runtime_protocol import MAX_MESSAGE_BYTES


def _http_servers(servers):
    """Translate standard HTTP descriptors without accepting process launches."""
    if not isinstance(servers, list):
        raise ValueError('mcpServers must be an array.')
    result = {}
    for row in servers:
        if not isinstance(row, dict) or row.get('type') != 'http':
            raise ValueError('Only HTTP MCP servers are supported.')
        name, url, headers = row.get('name'), row.get('url'), row.get('headers')
        if not isinstance(name, str) or not name or name in result:
            raise ValueError('MCP server names must be nonempty and unique.')
        if not isinstance(url, str) or urlsplit(url).scheme not in {'http', 'https'} or not urlsplit(url).hostname:
            raise ValueError('HTTP MCP servers require an HTTP or HTTPS URL.')
        if not isinstance(headers, list):
            raise ValueError('HTTP MCP headers must be an array.')
        values = {}
        for header in headers:
            if (not isinstance(header, dict) or not isinstance(header.get('name'), str)
                    or not header['name'] or not isinstance(header.get('value'), str)
                    or header['name'].lower() in {key.lower() for key in values}):
                raise ValueError('HTTP MCP headers require unique names and string values.')
            values[header['name']] = header['value']
        result[name] = {'type': 'http', 'url': url, 'headers': values}
    return result


def _validate_content(message):
    # SDK schemas intentionally skip malformed list entries. Validate these
    # request lists first so a requested connector or prompt is never dropped.
    method, params = message.get('method'), message.get('params')
    if method not in {'session/new', 'session/resume', 'session/prompt'}:
        return
    if not isinstance(params, dict):
        raise ValueError('Request parameters must be an object.')
    if method in {'session/new', 'session/resume'}:
        _http_servers(params.get('mcpServers', []))
        if params.get('additionalDirectories') not in (None, []):
            raise ValueError('Additional directories are not supported.')
        cwd = params.get('cwd')
        if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
            raise ValueError('cwd must be an existing absolute directory.')
    else:
        blocks = params.get('prompt')
        if not isinstance(blocks, list) or not blocks or any(
                not isinstance(block, dict) or block.get('type') != 'text'
                or not isinstance(block.get('text'), str) for block in blocks):
            raise ValueError('Only text prompt blocks are supported.')


class WebSocketTransport:
    """The SDK's structural message-transport interface, using existing aiohttp."""
    def __init__(self, socket):
        self.socket = socket

    async def send(self, message):
        text = json.dumps(message, ensure_ascii=False, separators=(',', ':'))
        if len(text.encode('utf-8')) > MAX_MESSAGE_BYTES:
            await self.socket.close(code=1009)
            raise ValueError('ACP output exceeds the host message limit.')
        await self.socket.send_str(text)

    async def receive(self):
        async for frame in self.socket:
            if frame.type != WSMsgType.TEXT:
                await self.socket.close(code=1003)
                return None
            try:
                message = json.loads(frame.data)
                if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                    raise ValueError('Expected a JSON-RPC object.')
                if 'id' in message and type(message['id']) not in {str, int}:
                    raise ValueError('Invalid request identity.')
                if 'method' in message and not isinstance(message['method'], str):
                    raise ValueError('Invalid method.')
            except (ValueError, TypeError):
                await self.socket.close(code=1007)
                return None
            try:
                _validate_content(message)
            except ValueError as error:
                if 'id' in message:
                    await self.send({'jsonrpc': '2.0', 'id': message['id'], 'error': {
                        'code': -32602, 'message': str(error)}})
                continue
            return message
        return None

    async def close(self):
        await self.socket.close()


class HostAgent:
    """ACP observations only; admission and authority stay with ExecutionOwner."""
    def __init__(self, owner):
        self.owner = owner
        self.initialized = False
        self.creation = asyncio.Lock()

    def on_connect(self, connection):
        self.client = connection

    def require_initialized(self):
        if not self.initialized:
            raise RequestError.invalid_request({'details': 'Initialize the connection first.'})

    async def initialize(self, protocol_version, **kwargs):
        if self.initialized:
            raise RequestError.invalid_request({'details': 'Connection already initialized.'})
        self.initialized = True
        return InitializeResponse(protocol_version=1, agent_capabilities=AgentCapabilities(
            load_session=False, mcp_capabilities=McpCapabilities(http=True, sse=False),
            session_capabilities=SessionCapabilities(resume=SessionResumeCapabilities())))

    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        self.require_initialized()
        async with self.creation:
            try:
                identity = await self.owner.create(cwd, mcp_servers=_http_servers([
                    row.model_dump(mode='json', by_alias=True) for row in (mcp_servers or [])]))
            except (AppError, ValueError, OSError) as error:
                raise RequestError(-32000, str(error)) from None
        return NewSessionResponse(session_id=identity)

    async def resume_session(self, session_id, cwd, mcp_servers=None, **kwargs):
        self.require_initialized()
        async with self.creation:
            if session_id in self.owner.attached:
                raise RequestError.invalid_params({'details': 'Session already attached.'})
            try:
                identity = await self.owner.attach(session_id, cwd, mcp_servers=_http_servers([
                    row.model_dump(mode='json', by_alias=True) for row in (mcp_servers or [])]))
            except (AppError, ValueError, OSError) as error:
                raise RequestError(-32000, str(error)) from None
            if identity != session_id:
                raise RequestError.internal_error({'details': 'Owner did not attach the exact saved identity.'})
        return ResumeSessionResponse()

    async def prompt(self, session_id, prompt, **kwargs):
        self.require_initialized()
        input_id = str(uuid.uuid4())
        streamed = had_deltas = False
        last_model = None
        tools = set()

        async def text(value):
            await self.client.session_update(session_id=session_id,
                update=AgentMessageChunk(content=TextContentBlock(text=value)))

        async def observe(row):
            nonlocal streamed, had_deltas, last_model
            kind = row.get('type')
            if kind == 'assistant.delta':
                streamed = had_deltas = True
                await text(row.get('text', ''))
            elif kind == 'assistant.message' and not had_deltas and row.get('text'):
                streamed = True
                await text(row['text'])
            elif kind == 'execution.event':
                event = row.get('event', {})
                if (event.get('kind') == 'llm' and event.get('phase') == 'running'
                        and event.get('lifecycle') != 'background'
                        and event.get('sessionId') == session_id and event.get('id') != last_model):
                    last_model = event.get('id')
                    streamed = had_deltas = False
                if event.get('kind') != 'tool':
                    return
                identity = event.get('toolCallId') or event.get('id')
                if not identity:
                    return
                identity = f"{input_id}:{event.get('sessionId') or session_id}:{identity}"
                values = {'tool_call_id': identity, 'status': {
                    'running': 'in_progress', 'completed': 'completed', 'error': 'failed',
                    'cancelled': 'failed'}.get(event.get('phase'), 'pending')}
                if 'input' in event:
                    values['raw_input'] = event['input']
                if 'output' in event:
                    output = event['output']
                    values['raw_output'] = output
                    values['content'] = [{'type': 'content', 'content': {'type': 'text',
                        'text': output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)}}]
                if identity not in tools:
                    tools.add(identity)
                    update = ToolCallStart(title=event.get('label') or 'Tool', kind='other', **values)
                else:
                    update = ToolCallProgress(**values)
                await self.client.session_update(session_id=session_id, update=update)

        try:
            result = await self.owner.prompt(session_id, input_id, '\n'.join(block.text for block in prompt), observe)
        except (AppError, ValueError, RuntimeError) as error:
            raise RequestError(-32000, str(error)) from None
        if result.get('outcome') not in {'completed', 'cancelled'}:
            raise RequestError(-32000, 'Outcome unresolved; inspect retained owner state before retrying.')
        if result.get('text') and not streamed:
            await text(result['text'])
        return PromptResponse(stop_reason='end_turn' if result['outcome'] == 'completed' else 'cancelled')

    async def cancel(self, session_id, **kwargs):
        self.require_initialized()
        await self.owner.cancel(session_id)


async def attach(request):
    authorization = request.headers.get('Authorization', '')
    if (not authorization.lower().startswith('bearer ')
            or not hmac.compare_digest(authorization[7:], request.app['control_token'])):
        raise web.HTTPForbidden(text='ACP attachment requires the existing owner control credential.')
    try:
        workspace = Path(request.query.get('workspace', ''))
        if not workspace.is_absolute() or not workspace.is_dir():
            raise ValueError('workspace must be an existing absolute directory.')
        workspace = workspace.resolve(strict=True)
        bundle = request.query.get('bundle') or request.app['service'].state['settings']['bundle']
        if not isinstance(bundle, str) or not bundle.strip():
            raise ValueError('A configured bundle is required.')
    except (ValueError, OSError) as error:
        raise web.HTTPBadRequest(text=str(error)) from None
    socket = web.WebSocketResponse(max_msg_size=MAX_MESSAGE_BYTES)
    await socket.prepare(request)
    owner = ExecutionOwner(request.app['service'], workspace=workspace, bundle=bundle,
                           origin='ui', enable_mcp=True)
    connection = AgentSideConnection(HostAgent(owner), WebSocketTransport(socket), listening=False)
    done = asyncio.Event()
    request.app['acp_connections'][socket] = done
    try:
        await connection.listen()
    finally:
        try:
            try:
                await owner.disconnect()
            finally:
                await connection.close()
        finally:
            request.app['acp_connections'].pop(socket, None)
            done.set()
    return socket


def setup_routes(app):
    app['acp_connections'] = {}
    app.router.add_get('/api/acp', attach)

    async def shutdown(_):
        active = tuple(app['acp_connections'].items())
        await asyncio.gather(*(socket.close(code=1001) for socket, _ in active))
        await asyncio.gather(*(done.wait() for _, done in active))

    app.on_shutdown.append(shutdown)
