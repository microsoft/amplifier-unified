"""One external execution binding, using the upstream ACP client transport."""
from __future__ import annotations

import asyncio
from pathlib import Path

class ACPParticipant:
    """The experience owns messages; this object owns only its ACP connection."""

    def __init__(self, endpoint, cwd, *, update, permission):
        self.endpoint, self.cwd = endpoint, cwd
        self.update, self.permission = update, permission
        self.connection = self.process = self.context = None
        self.session_id = None
        self.capabilities = {}

    async def connect(self, binding, save_binding):
        from acp import PROTOCOL_VERSION, spawn_agent_process
        from acp.schema import ClientCapabilities

        if self.connection is not None and self.process.returncode is None:
            return
        await self.close()
        self.context = spawn_agent_process(
            self, self.endpoint['command'], *self.endpoint.get('args', []),
            cwd=self.endpoint.get('cwd'),
            transport_kwargs={'stderr': asyncio.subprocess.DEVNULL})
        try:
            self.connection, self.process = await self.context.__aenter__()
            initialized = await self.connection.initialize(
                protocol_version=PROTOCOL_VERSION, client_capabilities=ClientCapabilities())
            if initialized.protocol_version != PROTOCOL_VERSION:
                raise ValueError('The external agent selected an unsupported ACP protocol version.')
            sessions = initialized.agent_capabilities.session_capabilities
            self.capabilities = {'text': True, 'streaming': True, 'stop': True,
                                 'resume': bool(sessions and sessions.resume is not None),
                                 'nativeControls': False}
            identity = binding.get('sessionId')
            if identity:
                if not self.capabilities['resume']:
                    raise ValueError('This agent cannot resume its saved session. The binding was preserved.')
                await self.connection.resume_session(session_id=identity, cwd=self.cwd, mcp_servers=[])
            else:
                created = await self.connection.new_session(cwd=self.cwd, mcp_servers=[])
                identity = created.session_id
            self.session_id = identity
            # The host commits the execution identity before any prompt can run.
            await save_binding({'sessionId': identity, 'capabilities': self.capabilities})
        except BaseException:
            await self.close()
            raise

    async def prompt(self, text, *, context=None):
        from acp.schema import TextContentBlock

        if self.connection is None or self.session_id is None:
            raise RuntimeError('The external participant is not connected.')
        blocks = ([TextContentBlock(text=context)] if context else []) + [TextContentBlock(text=text)]
        return await self.connection.prompt(session_id=self.session_id, prompt=blocks)

    async def cancel(self):
        if self.connection is not None and self.session_id is not None:
            await self.connection.cancel(session_id=self.session_id)

    async def session_update(self, session_id, update, **_kwargs):
        if session_id != self.session_id:
            return
        await self.update(update.model_dump(mode='json', by_alias=True, exclude_none=True))

    async def request_permission(self, session_id, tool_call, options, **_kwargs):
        if session_id != self.session_id:
            return {'outcome': {'outcome': 'cancelled'}}
        return await self.permission(options)

    async def read_text_file(self, **_kwargs):
        from acp import RequestError
        raise RequestError(-32601, 'Client filesystem access was not advertised.')

    async def write_text_file(self, **_kwargs):
        from acp import RequestError
        raise RequestError(-32601, 'Client filesystem access was not advertised.')

    async def ext_method(self, method, params):
        from acp import RequestError
        raise RequestError(-32601, 'Unsupported ACP extension.')

    async def ext_notification(self, method, params):
        pass

    async def close(self):
        context, self.context = self.context, None
        self.connection = self.process = None
        if context is not None:
            await context.__aexit__(None, None, None)


def endpoint(config, identity):
    """Read operator-seeded launch settings; never expose them as chat metadata."""
    rows = config.get('externalAgents', [])
    selected = next((row for row in rows if isinstance(row, dict) and row.get('id') == identity), None)
    if selected is None:
        raise ValueError('This external agent is not configured on this host.')
    if (not isinstance(selected.get('command'), str) or not Path(selected['command']).is_absolute()
            or not isinstance(selected.get('args', []), list)
            or not all(isinstance(arg, str) for arg in selected.get('args', []))
            or not isinstance(selected.get('label'), str) or not selected['label'].strip()):
        raise ValueError('The configured external agent needs an absolute executable, arguments and label.')
    for key in ('cwd', 'sessionCwd'):
        if key in selected and (not isinstance(selected[key], str) or not Path(selected[key]).is_absolute()):
            raise ValueError('External agent working directories must be absolute paths.')
    return {key: selected[key] for key in ('id', 'label', 'command', 'args', 'cwd', 'sessionCwd') if key in selected}
