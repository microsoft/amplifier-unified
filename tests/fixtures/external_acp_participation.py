"""Synthetic participation peer using the public ACP SDK; no model or tools."""
import asyncio
import json
import sys

from acp import RequestError, run_agent
from acp.schema import (AgentMessageChunk, AgentThoughtChunk, PermissionOption,
                        PromptResponse, TextContentBlock, ToolCallUpdate)
from external_acp_agent import Fixture

ATTENTION = 'com.microsoft.amplifier.attention'


class ParticipationFixture(Fixture):
    def __init__(self, home, profile):
        super().__init__(home, True)
        self.profile = profile
        self.active_id = None

    def wire(self, event):
        if event.direction == 'incoming' and event.message.get('method') == 'session/prompt':
            self.active_id = event.message['id']

    async def initialize(self, **kwargs):
        result = await super().initialize(**kwargs)
        if self.profile != 'plain':
            result.field_meta = {
                ATTENTION: {'version': 1, 'enabled': self.profile != 'disabled',
                            'method': '_amplifier/attention'},
                'steering': {'supported': True},
                'io.github.bkrabach.agent-instance': {'inputContinuity': {
                    'awareness': {'version': 1, 'activeOnly': True, 'promptIdRequired': True}}}}
        if self.profile == 'wrong-version':
            result.field_meta[ATTENTION]['version'] = 2
        return result

    async def prompt(self, session_id, prompt, **kwargs):
        if ATTENTION in kwargs:
            self.record('metadata', metadata=kwargs, sessionId=session_id)
            if kwargs[ATTENTION]['ticket'] == 'expired':
                raise RequestError(-32002, 'Offer expired; no input was forwarded.',
                                   {ATTENTION: {'version': 1, 'outcome': 'not_forwarded'}})
        text = prompt[-1].text
        if text.startswith('Decide quietly whether'):
            projection = json.loads(text.split('Latest conversation projection:\n', 1)[1])
            self.record('decision', sessionId=session_id, context=projection)
            permission = await self.client.request_permission(session_id=session_id,
                tool_call=ToolCallUpdate(tool_call_id='quiet', title='Must remain private'),
                options=[PermissionOption(option_id='allow', name='Allow', kind='allow_once')])
            self.record('permission', outcome=permission.outcome.model_dump(mode='json', by_alias=True))
            await self.client.session_update(session_id=session_id,
                update=AgentThoughtChunk(content=TextContentBlock(text='Private decision reasoning')))
            if projection.get('choice') == 'wait':
                await self.cancelled.wait()
                return PromptResponse(stop_reason='cancelled')
            output = ('not a decision' if projection.get('choice') == 'malformed'
                      else json.dumps({'decision': projection.get('choice', 'speak'), 'reason': 'fixture'}))
            if projection.get('choice') == 'oversized':
                output = json.dumps({'decision': 'speak', 'reason': 'x' * 65536})
            for chunk in (output[:10], output[10:]):
                await self.client.session_update(session_id=session_id,
                    update=AgentMessageChunk(content=TextContentBlock(text=chunk)))
            return PromptResponse(stop_reason='end_turn')
        return await super().prompt(session_id, prompt, **kwargs)

    async def ext_method(self, method, params):
        self.record('extension', method=method, params=params)
        if method == 'amplifier/attention':
            choice = params['context'].get('choice', 'engage')
            if choice == 'error':
                raise RequestError(-32002, 'Source authorization changed.')
            if choice == 'malformed':
                return {'action': 'engage', 'id': 'offer'}
            if choice == 'hold':
                return {'action': 'hold', 'id': 'offer', 'reason': 'No useful contribution'}
            return {'action': 'engage', 'id': 'offer', 'ticket': 'private-ticket', 'expiresAt': 4102444800000}
        if method == 'session/steering':
            steering = params['_meta']['steering']
            assert steering['activePromptId'] == self.active_id
            assert steering['idleBehavior'] == 'promptRequired' and steering['intent'] == 'awareness'
            if params['prompt'][0]['text'] == 'invalid-receipt':
                return {'status': 'injected', '_meta': {'steering': {'inputId': 'wrong'}}}
            if params['prompt'][0]['text'] == 'idle-refusal':
                return {'status': 'promptRequired', '_meta': {'steering': {'inputId': steering['inputId']}}}
            return {'status': 'injected', '_meta': {'steering': {'inputId': steering['inputId']}}}
        raise RequestError(-32601, 'Unsupported extension')


if __name__ == '__main__':
    peer = ParticipationFixture(sys.argv[1], sys.argv[2])
    asyncio.run(run_agent(peer, observers=[peer.wire], use_unstable_protocol=True))
