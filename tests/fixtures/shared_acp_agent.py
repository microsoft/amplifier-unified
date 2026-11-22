"""Source-aware public participation with the real SDK, no model or network."""
import asyncio
import json
import os
import sys

from acp import RequestError, run_agent
from acp.schema import AgentMessageChunk, AgentThoughtChunk, TextContentBlock, PromptResponse, PermissionOption, ToolCallUpdate
from external_acp_agent import Fixture

INSTANCE = 'com.microsoft.amplifier.agent-instance'


class SharedFixture(Fixture):
    def __init__(self, home, identity, profile="plain"):
        super().__init__(home, True)
        self.identity, self.profile = identity, profile
        self.source = None

    async def initialize(self, **kwargs):
        result = await super().initialize(**kwargs)
        if self.profile.startswith('attention'):
            result.field_meta = {'com.microsoft.amplifier.attention': {
                'version': 1, 'enabled': True, 'method': '_amplifier/attention'}}
        if self.profile.startswith('instance'):
            assert kwargs[INSTANCE] == {'version': 1}
            if not (self.home / 'hide-profile').exists():
                suffix = ':changed' if (self.home / 'change-identity').exists() else ''
                result.field_meta[INSTANCE] = {'version': 1, 'agentId': 'public:' + self.identity + suffix}
        return result

    def instance_session(self, result, metadata):
        if self.profile.startswith('instance'):
            assert metadata[INSTANCE]['agentId'] == 'public:' + self.identity
            self.source = metadata[INSTANCE]['source']
            self.record('instance-session', metadata=metadata[INSTANCE])
            revision = int((self.home / 'revision').read_text()) if (self.home / 'revision').exists() else 7
            result.field_meta = {INSTANCE: {**metadata[INSTANCE], 'expectedInterruptionRevision': revision}}
            if self.profile == 'instance-malformed':
                result.field_meta[INSTANCE].pop('expectedInterruptionRevision')
        return result

    async def new_session(self, cwd, mcp_servers=None, **kwargs):
        result = self.instance_session(await super().new_session(cwd, mcp_servers), kwargs)
        if self.profile == 'picker':
            result.config_options = self.picker(result.session_id)
        return result

    async def resume_session(self, session_id, cwd, mcp_servers=None, **kwargs):
        result = self.instance_session(await super().resume_session(session_id, cwd, mcp_servers), kwargs)
        if self.profile == 'picker':
            result.config_options = self.picker(session_id)
        return result

    def picker(self, session_id):
        saved = self.home / (session_id + '.settings')
        values = json.loads(saved.read_text()) if saved.exists() else dict(provider='one', model='draft', effort='low')
        from acp.schema import SessionConfigOptionSelect
        return [SessionConfigOptionSelect(id=key, name=name, category=category, current_value=values[key],
                    options=[{'value': v, 'name': v} for v in choices])
                for key, name, category, choices in [('provider','Connection','model_config',['one','two']),
                    ('model','Model','model',['draft','review']), ('effort','Reasoning effort','thought_level',['low','high'])]]

    async def set_config_option(self, session_id, config_id, value, **kwargs):
        from acp.schema import SetSessionConfigOptionResponse
        options = self.picker(session_id)
        selected = next(row for row in options if row.id == config_id)
        assert value in {choice.value for choice in selected.options}
        settings = {row.id: (value if row.id == config_id else row.current_value) for row in options}
        (self.home / (session_id + '.settings')).write_text(json.dumps(settings))
        self.record('configure', sessionId=session_id, settings=settings)
        return SetSessionConfigOptionResponse(config_options=self.picker(session_id))

    async def cancel(self, session_id, **kwargs):
        if self.profile.startswith('instance'):
            (self.home / 'revision').write_text('8')
        await super().cancel(session_id, **kwargs)

    def speaks(self, trigger):
        if trigger['sender'] in {'alpha', 'beta'}:
            return self.identity == 'beta' and trigger['text'] == 'Question for Beta: check 42'
        return not (trigger['text'] == 'drop-after-effect' and self.identity == 'beta')

    async def ext_method(self, method, params):
        assert self.profile.startswith('attention') and method == 'amplifier/attention'
        context = params['context']
        assert set(context) == {'conversation_id', 'thread_id', 'agent', 'participants', 'messages', 'trigger_id', 'responsibilities'}
        assert set(context['agent']) == {'id', 'name', 'is_agent'}
        assert all(set(row) <= {'id', 'name', 'is_agent'} for row in context['participants'])
        assert all(set(row) <= {'id', 'sender', 'text', 'recipients', 'reply_to'} for row in context['messages'])
        assert len(context['participants']) <= 64 and 1 <= len(context['messages']) <= 40
        assert len(json.dumps(context, ensure_ascii=False, separators=(',', ':')).encode()) <= 32768
        self.record('offer', context=context, occurredAt=params['occurredAt'])
        if self.profile == 'attention-error':
            raise RequestError(-32002, 'Source authorization changed.')
        if self.profile == 'attention-malformed':
            return {'action': 'engage', 'id': 'offer'}
        trigger = next(row for row in context['messages'] if row['id'] == context['trigger_id'])
        if not self.speaks(trigger):
            return {'action': 'hold', 'id': 'offer'}
        return {'action': 'engage', 'id': 'offer', 'ticket': 'fixture-ticket', 'expiresAt': 4102444800000}

    async def emit(self, session_id, text):
        await self.client.session_update(session_id=session_id,
            update=AgentMessageChunk(content=TextContentBlock(text=text)))

    async def prompt(self, session_id, prompt, **kwargs):
        text = prompt[-1].text
        context = json.loads(text.split('\n', 1)[1])
        trigger = next(row for row in context['messages'] if row['id'] == context['trigger_id'])
        source = trigger['text']
        self.record('prompt', sessionId=session_id, sourceId=trigger['id'],
                    source=source, sender=trigger['sender'], context=context)
        if self.profile.startswith('instance'):
            assert kwargs[INSTANCE] == {'agentId': 'public:' + self.identity, 'source': self.source,
                'expectedInterruptionRevision': (int((self.home / 'revision').read_text())
                    if (self.home / 'revision').exists() else 7), 'input': {
                    'id': kwargs[INSTANCE]['input']['id'], 'revision': 0,
                    'actor': trigger['sender'], 'audience': ['conversation'],
                    'replyTo': context['conversation_id']}}
            self.record('instance-input', metadata=kwargs[INSTANCE])
        if not self.speaks(trigger):
            await self.emit(session_id, '[[PASS]]')
            return PromptResponse(stop_reason='end_turn')
        self.record('work', sessionId=session_id, sourceId=trigger['id'], source=source, sender=trigger['sender'])
        if self.profile.startswith('attention'):
            assert kwargs['com.microsoft.amplifier.attention'] == {'id': 'offer', 'ticket': 'fixture-ticket'}
        self.cancelled.clear()
        if source == 'drop-after-effect':
            os._exit(7)
        if source == 'silent-after-decision':
            assert 'return exactly [[PASS]]' in text
            for chunk in ('[[PA', 'SS]]'):
                await self.emit(session_id, chunk)
            return PromptResponse(stop_reason='end_turn')
        if source == 'silent-split-wait':
            await self.emit(session_id, '[[PA')
            while not (self.home / 'release').exists() and not self.cancelled.is_set():
                await asyncio.sleep(.01)
            await self.emit(session_id, 'SS]]')
            return PromptResponse(stop_reason='end_turn')
        if source == 'pass-prefix-error':
            await self.emit(session_id, '[[PA')
            raise RequestError(-32002, 'Failed before silence marker completed.')
        if source == 'silent-empty':
            return PromptResponse(stop_reason='end_turn')
        await self.client.session_update(session_id=session_id,
            update=AgentThoughtChunk(content=TextContentBlock(text='Private reasoning')))
        if source == 'permission':
            request = asyncio.create_task(self.client.request_permission(session_id=session_id,
                tool_call=ToolCallUpdate(tool_call_id='synthetic', title='Synthetic permission'),
                options=[PermissionOption(option_id='allow-this', name='Allow once', kind='allow_once'),
                         PermissionOption(option_id='deny-this', name='Deny', kind='reject_once')]))
            cancelled = asyncio.create_task(self.cancelled.wait())
            try:
                await asyncio.wait([request, cancelled], return_when=asyncio.FIRST_COMPLETED)
                if self.cancelled.is_set():
                    request.cancel()
                    await asyncio.gather(request, return_exceptions=True)
                    return PromptResponse(stop_reason='cancelled')
                result = request.result()
            finally:
                cancelled.cancel()
                await asyncio.gather(cancelled, return_exceptions=True)
            self.record('permission', outcome=result.outcome.model_dump(mode='json', by_alias=True, exclude_none=True))
        if source == 'wait-for-stop':
            await self.emit(session_id, self.identity + ' unfinished draft')
            await self.cancelled.wait()
            return PromptResponse(stop_reason='cancelled')
        if source == 'stream-error':
            await self.emit(session_id, self.identity + ' interrupted draft')
            raise RequestError(-32002, 'Failed after partial output.')
        if source == 'busy exchange':
            if self.identity == 'alpha':
                result = 'Question for Beta: check 42'
            else:
                await self.emit(session_id, 'Beta working')
                while not (self.home / 'release').exists() and not self.cancelled.is_set():
                    await asyncio.sleep(.01)
                result = 'Beta finished first work'
        elif source == 'Question for Beta: check 42':
            result = 'Beta answer: 42'
        else:
            result = self.identity + ' public answer: ' + source
        await self.emit(session_id, result)
        return PromptResponse(stop_reason='end_turn')


if __name__ == '__main__':
    asyncio.run(run_agent(SharedFixture(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else 'plain'), use_unstable_protocol=True))
