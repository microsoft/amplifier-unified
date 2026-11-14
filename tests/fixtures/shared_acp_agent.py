"""Source-aware public participation with the real SDK, no model or network."""
import asyncio
import json
import os
import sys

from acp import run_agent
from acp.schema import AgentMessageChunk, AgentThoughtChunk, TextContentBlock, PromptResponse, PermissionOption, ToolCallUpdate
from external_acp_agent import Fixture


class SharedFixture(Fixture):
    def __init__(self, home, identity, profile="plain"):
        super().__init__(home, True)
        self.identity, self.profile = identity, profile

    async def initialize(self, **kwargs):
        result = await super().initialize(**kwargs)
        if self.profile == 'attention':
            result.field_meta = {'com.microsoft.amplifier.attention': {
                'version': 1, 'enabled': True, 'method': '_amplifier/attention'}}
        return result

    def speaks(self, trigger):
        if trigger['sender'] in {'alpha', 'beta'}:
            return self.identity == 'beta' and trigger['text'] == 'Question for Beta: check 42'
        return not (trigger['text'] == 'drop-after-effect' and self.identity == 'beta')

    async def ext_method(self, method, params):
        assert self.profile == 'attention' and method == 'amplifier/attention'
        context = params['context']
        assert set(context) == {'conversation_id', 'thread_id', 'agent', 'participants', 'messages', 'trigger_id', 'responsibilities'}
        assert set(context['agent']) == {'id', 'name', 'is_agent'}
        assert all(set(row) <= {'id', 'name', 'is_agent'} for row in context['participants'])
        assert all(set(row) <= {'id', 'sender', 'text', 'recipients', 'reply_to'} for row in context['messages'])
        assert len(context['participants']) <= 64 and 1 <= len(context['messages']) <= 40
        assert len(json.dumps(context, ensure_ascii=False, separators=(',', ':')).encode()) <= 32768
        self.record('offer', context=context, occurredAt=params['occurredAt'])
        trigger = next(row for row in context['messages'] if row['id'] == context['trigger_id'])
        if not self.speaks(trigger):
            return {'action': 'hold', 'id': 'offer'}
        return {'action': 'engage', 'id': 'offer', 'ticket': 'fixture-ticket', 'expiresAt': 4102444800000}

    async def emit(self, session_id, text):
        await self.client.session_update(session_id=session_id,
            update=AgentMessageChunk(content=TextContentBlock(text=text)))

    async def prompt(self, session_id, prompt, **kwargs):
        text = prompt[-1].text
        quiet = text.startswith('Decide quietly')
        context = json.loads(text.split('Latest conversation projection:\n', 1)[1] if quiet else text.split('\n', 1)[1])
        trigger = next(row for row in context['messages'] if row['id'] == context['trigger_id'])
        source = trigger['text']
        self.record('decision' if quiet else 'work', sessionId=session_id,
                    sourceId=trigger['id'], source=source, sender=trigger['sender'])
        if quiet:
            speak = self.speaks(trigger)
            await self.emit(session_id, json.dumps({'decision': 'speak' if speak else 'pass', 'reason': 'fixture'}))
            return PromptResponse(stop_reason='end_turn')
        if self.profile == 'attention':
            assert kwargs['com.microsoft.amplifier.attention'] == {'id': 'offer', 'ticket': 'fixture-ticket'}
        self.cancelled.clear()
        if source == 'drop-after-effect':
            os._exit(7)
        if source == 'silent-after-decision':
            assert 'return exactly [[PASS]]' in text
            await self.emit(session_id, '[[PASS]]')
            return PromptResponse(stop_reason='end_turn')
        await self.client.session_update(session_id=session_id,
            update=AgentThoughtChunk(content=TextContentBlock(text='Private reasoning')))
        if source == 'permission':
            result = await self.client.request_permission(session_id=session_id,
                tool_call=ToolCallUpdate(tool_call_id='synthetic', title='Synthetic permission'),
                options=[PermissionOption(option_id='allow-this', name='Allow', kind='allow_once')])
            self.record('permission', outcome=result.outcome.model_dump(mode='json', by_alias=True, exclude_none=True))
        if source == 'wait-for-stop':
            await self.emit(session_id, self.identity + ' unfinished draft')
            await self.cancelled.wait()
            return PromptResponse(stop_reason='cancelled')
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
