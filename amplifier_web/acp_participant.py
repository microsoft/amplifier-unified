"""One external execution binding, using the upstream ACP client transport."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

ATTENTION = 'com.microsoft.amplifier.attention'
INSTANCE = 'io.github.bkrabach.agent-instance'
AGENT_INSTANCE = 'com.microsoft.amplifier.agent-instance'
RETIREMENT = 'com.microsoft.amplifier.retirement'
RETIREMENT_TIMEOUT = 30  # Same bounded operation policy as connection setup.


class ACPParticipant:
    """The experience owns messages; this object owns only its ACP connection."""

    def __init__(self, endpoint, cwd, *, update, permission):
        self.endpoint, self.cwd = endpoint, cwd
        self.update, self.permission = update, permission
        self.connection = self.process = self.context = None
        self.session_id = None
        self.capabilities = {}
        self.initialization_metadata = {}
        self._active_prompt_id = None
        self._prompt_running = False
        self.instance_binding = {}
        self._instance_refresh = False
        self.retirement_available = None
        self._retirement_uncertain = False

    @property
    def supports_retirement(self):
        value = self.initialization_metadata.get(RETIREMENT, {})
        return (self.capabilities.get('resume') is True
                and isinstance(value, dict) and value.get('version') == 1
                and value.get('method') == '_amplifier/retire'
                and value.get('notification') == '_amplifier/retirementRequested')

    async def retire(self):
        """A method advertisement is not permission to close the transport."""
        if not self.supports_retirement or self.connection is None:
            return 'unsupported'
        self._retirement_uncertain = True
        result = await asyncio.wait_for(self.connection.ext_method(
            'amplifier/retire', {'sessionId': self.session_id}), RETIREMENT_TIMEOUT)
        if (not isinstance(result, dict) or result.get('sessionId') != self.session_id
                or result.get('disposition') not in {'retired', 'busy', 'unknown'}):
            raise ValueError('Retirement was not confirmed for the saved execution. Its connection was retained.')
        self._retirement_uncertain = result['disposition'] == 'unknown'
        return result['disposition']

    @property
    def supports_attention(self):
        value = self.initialization_metadata.get(ATTENTION, {})
        return (isinstance(value, dict) and value.get('version') == 1
                and value.get('enabled') is True and value.get('method') == '_amplifier/attention')

    @property
    def supports_awareness(self):
        meta = self.initialization_metadata
        steering = meta.get('steering', {})
        instance = meta.get(INSTANCE, {})
        continuity = instance.get('inputContinuity', {}) if isinstance(instance, dict) else {}
        value = continuity.get('awareness', {}) if isinstance(continuity, dict) else {}
        return (isinstance(steering, dict) and steering.get('supported') is True
                and isinstance(value, dict) and value.get('version') == 1
                and value.get('activeOnly') is True and value.get('promptIdRequired') is True)

    def _observe_wire(self, event):
        """Keep only the current request identity; never retain wire content."""
        message = event.message
        if (event.direction == 'outgoing' and message.get('method') == 'session/prompt'
                and message.get('params', {}).get('sessionId') == self.session_id):
            self._active_prompt_id = message.get('id')
        elif (event.direction == 'incoming' and 'method' not in message
              and message.get('id') == self._active_prompt_id):
            self._active_prompt_id = None

    async def connect(self, binding, save_binding, *, room_context=None, source=None):
        from acp import PROTOCOL_VERSION, spawn_agent_process
        from acp.schema import ClientCapabilities, HttpMcpServer

        if self.connection is not None and self.process.returncode is None:
            if not self._retirement_uncertain and not self._instance_refresh:
                return
            # Reconcile cleanup, never replay an input. A lost successful
            # acknowledgement is repeatable for this exact attachment.
            if self._retirement_uncertain:
                disposition = await self.retire()
                if disposition == 'busy':
                    return
                if disposition != 'retired':
                    raise ValueError('Retirement is still unconfirmed. No input was sent; the binding was retained.')
        await self.close()
        self.context = spawn_agent_process(
            self, self.endpoint['command'], *self.endpoint.get('args', []),
            cwd=self.endpoint.get('cwd'),
            transport_kwargs={'stderr': asyncio.subprocess.DEVNULL}, observers=[self._observe_wire])
        try:
            self.connection, self.process = await self.context.__aenter__()
            # An existing plain ACP session stays plain when its peer later
            # advertises an optional profile. Never convert its saved identity.
            offer_instance = source is not None and (
                not binding.get('sessionId') or bool(binding.get('publicAgentId')))
            initialized = await self.connection.initialize(
                protocol_version=PROTOCOL_VERSION, client_capabilities=ClientCapabilities(),
                **({AGENT_INSTANCE: {'version': 1}} if offer_instance else {}))
            if initialized.protocol_version != PROTOCOL_VERSION:
                raise ValueError('The external agent selected an unsupported ACP protocol version.')
            self.initialization_metadata = deepcopy(initialized.field_meta or {})
            profile = self.initialization_metadata.get(AGENT_INSTANCE)
            agent_id = (profile.get('agentId') if isinstance(profile, dict)
                        and type(profile.get('version')) is int and profile['version'] == 1 else None)
            if binding.get('publicAgentId') and binding['publicAgentId'] != agent_id:
                raise ValueError('The public agent identity changed. Its saved execution was preserved.')
            instance = {}
            if offer_instance and profile is not None:
                if not isinstance(agent_id, str) or not agent_id:
                    raise ValueError('The external agent advertised an invalid public identity.')
                if binding.get('instanceSource', source) != source:
                    raise ValueError('The saved public conversation source changed.')
                instance = {AGENT_INSTANCE: {'agentId': agent_id, 'source': deepcopy(source)}}
            sessions = initialized.agent_capabilities.session_capabilities
            self.capabilities = {'text': True, 'streaming': True, 'stop': True,
                                 'resume': bool(sessions and sessions.resume is not None),
                                 'nativeControls': False}
            servers = []
            if room_context is not None:
                http = initialized.agent_capabilities.mcp_capabilities
                descriptor = room_context() if http and http.http else None
                if descriptor is not None:
                    servers = [HttpMcpServer.model_validate(descriptor)]
                self.capabilities['roomContext'] = bool(servers)
            identity = binding.get('sessionId')
            if identity:
                if not self.capabilities['resume']:
                    raise ValueError('This agent cannot resume its saved session. The binding was preserved.')
                created = await self.connection.resume_session(session_id=identity, cwd=self.cwd, mcp_servers=servers, **instance)
            else:
                created = await self.connection.new_session(cwd=self.cwd, mcp_servers=servers, **instance)
                identity = created.session_id
            self._save_options = save_binding
            self.session_id = identity
            from .participant_options import visible_options, revision
            options = visible_options(getattr(created, 'config_options', None))
            saved = {'sessionId': identity, 'capabilities': self.capabilities,
                     'configOptions': options, 'optionRevision': revision(options)}
            if instance:
                receipt = (created.field_meta or {}).get(AGENT_INSTANCE, {})
                if (not isinstance(receipt, dict) or receipt.get('agentId') != agent_id
                        or receipt.get('source') != source
                        or type(receipt.get('expectedInterruptionRevision')) is not int
                        or receipt['expectedInterruptionRevision'] < 0):
                    # Even a malformed profile acknowledgement must not lose a
                    # newly allocated native identity or create a replacement.
                    await save_binding({**saved, 'publicAgentId': agent_id, 'instanceSource': deepcopy(source)})
                    raise ValueError('The external agent did not confirm the exact public source binding.')
                saved.update(publicAgentId=agent_id, instanceSource=deepcopy(source),
                             instanceInterruptionRevision=receipt['expectedInterruptionRevision'])
                self.instance_binding = {key: deepcopy(receipt[key]) for key in
                    ('agentId', 'source', 'expectedInterruptionRevision')}
            # The host commits the execution identity before any prompt can run.
            await save_binding(saved)
        except BaseException:
            await self.close()
            raise

    def source_metadata(self, input_id, actor, conversation_id):
        """Original immutable public input, not transport identity or new authority."""
        if not self.instance_binding:
            return {}
        return {AGENT_INSTANCE: {**deepcopy(self.instance_binding),
            'input': {'id': input_id, 'revision': 0, 'actor': actor,
                      'audience': ['conversation'], 'replyTo': conversation_id}}}

    async def prompt(self, text, *, context=None, metadata=None):
        from acp.schema import TextContentBlock

        if self.connection is None or self.session_id is None:
            raise RuntimeError('The external participant is not connected.')
        if self._prompt_running:
            raise RuntimeError('The external participant already has an active prompt.')
        blocks = ([TextContentBlock(text=context)] if context else []) + [TextContentBlock(text=text)]
        self._prompt_running = True
        try:
            # The public SDK maps keyword metadata directly to the wire _meta.
            return await self.connection.prompt(session_id=self.session_id, prompt=blocks,
                                                **deepcopy(metadata or {}))
        finally:
            self._prompt_running = False
            self._active_prompt_id = None

    async def attention(self, context, occurred_at):
        """Offer original source facts, without replay, admission or new authority.

        None selects the ordinary ACP fallback. An advertised peer error must
        propagate; it is not permission to fall back or repeat an evaluation.
        The room owner checks source freshness before using an engage ticket.
        """
        if self.connection is None or self.session_id is None:
            raise RuntimeError('The external participant is not connected.')
        if not self.supports_attention:
            return None
        if type(occurred_at) is not int or occurred_at < 0 or not isinstance(context, dict):
            raise ValueError('Attention requires source context and its original timestamp.')
        offer = await self.connection.ext_method('amplifier/attention', {
            'sessionId': self.session_id, 'occurredAt': occurred_at, 'context': deepcopy(context)})
        if (not isinstance(offer, dict) or offer.get('action') not in ('hold', 'engage')
                or not isinstance(offer.get('id'), str) or not offer['id']):
            raise ValueError('The external agent returned an invalid attention offer.')
        if offer['action'] == 'engage' and (not isinstance(offer.get('ticket'), str)
                or not offer['ticket'] or type(offer.get('expiresAt')) is not int):
            raise ValueError('The external agent returned an invalid attention ticket.')
        return offer

    async def observe(self, text, *, input_id, metadata=None):
        """Deliver negotiated awareness to this exact active prompt, once.

        The owner supplies attributed source evidence, preserves the active
        requester/audience and interprets the receipt. No fallback prompt or
        retry follows an idle, failed or ambiguous delivery.
        """
        if (not self.supports_awareness or self._active_prompt_id is None
                or self.connection is None):
            return None
        if not isinstance(input_id, str) or not input_id:
            raise ValueError('Awareness requires a stable input identity.')
        meta = deepcopy(metadata or {})
        meta['steering'] = {'inputId': input_id, 'idleBehavior': 'promptRequired',
                            'intent': 'awareness', 'activePromptId': self._active_prompt_id}
        result = await self.connection.ext_method('session/steering', {
            'sessionId': self.session_id, 'prompt': [{'type': 'text', 'text': text}], '_meta': meta})
        result_meta = result.get('_meta') if isinstance(result, dict) else None
        receipt = result_meta.get('steering') if isinstance(result_meta, dict) else None
        if not isinstance(receipt, dict) or receipt.get('inputId') != input_id:
            raise ValueError('The external agent did not confirm the awareness input identity.')
        if result.get('status') not in ('injected', 'promptRequired', 'failed'):
            raise ValueError('The external agent returned an invalid awareness disposition.')
        return result

    async def cancel(self):
        if self.connection is not None and self.session_id is not None:
            # ACP cancel has no revision readback. Before a later explicit input,
            # resume the exact binding to observe its current native Stop fence.
            self._instance_refresh = bool(self.instance_binding)
            await self.connection.cancel(session_id=self.session_id)

    async def session_update(self, session_id, update, **_kwargs):
        if session_id != self.session_id:
            return
        value = update.model_dump(mode='json', by_alias=True, exclude_none=True)
        if value.get('sessionUpdate') == 'config_option_update':
            from .participant_options import visible_options, revision
            options = visible_options(value.get('configOptions'))
            await self._save_options({'configOptions': options, 'optionRevision': revision(options)})
        else:
            await self.update(value)

    async def request_permission(self, session_id, tool_call, options, **_kwargs):
        if session_id != self.session_id:
            return {'outcome': {'outcome': 'cancelled'}}
        return await self.permission(tool_call, options)

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
        if (method == 'amplifier/retirementRequested' and self.supports_retirement
                and isinstance(params, dict) and params.get('sessionId') == self.session_id
                and self.retirement_available is not None):
            await self.retirement_available(self)

    async def close(self):
        context, self.context = self.context, None
        self.connection = self.process = None
        self.initialization_metadata = {}
        self.instance_binding = {}
        self._instance_refresh = False
        self._active_prompt_id = None
        self._retirement_uncertain = False
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
