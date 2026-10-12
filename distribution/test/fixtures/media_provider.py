"""Offline provider exercising the normal saved-commentary media workflow."""
import json
from pathlib import Path

from amplifier_core.models import ProviderInfo
from amplifier_core.message_models import ChatResponse, TextBlock, ToolCall


class Provider:
    name = 'media-fixture'

    def get_info(self):
        return ProviderInfo(id=self.name, display_name='Offline media workflow',
                            defaults={'model': 'fixture'}, capabilities=['tools'])

    async def list_models(self):
        return [{'id': 'fixture'}]

    def parse_tool_calls(self, response):
        return response.tool_calls or []

    async def complete(self, request, **kwargs):
        root = Path(__file__).parent
        # Naming requests are ancillary and cannot drive the acceptance sequence.
        if 'You generate names and descriptions for conversation sessions.' in str(request.messages):
            return ChatResponse(content=[TextBlock(text='{"action":"defer"}')], finish_reason='stop')
        stage = getattr(self, 'stage', 0)
        self.stage = stage + 1
        with (root / 'calls.txt').open('a') as stream:
            stream.write(str(stage) + '\n')
        if stage == 0:
            return ChatResponse(content=[TextBlock(text='Here is the saved animation.')],
                tool_calls=[ToolCall(id='discover', name='app_control',
                    arguments={'operation': 'get_state', 'args': {'path': '/messages'}})],
                finish_reason='tool_calls')
        result = json.loads(next(message.content for message in reversed(request.messages)
                                 if message.role == 'tool' and message.name == 'app_control'))
        (root / f'result-{stage}.json').write_text(json.dumps(result))
        if stage == 1:
            assert result['success'], result
            state = result['output']
            message = next(item for item in state['messages'] if item['text'] == 'Here is the saved animation.')
            assert message['saved'] is True and state['execution'] is False
            fixture = json.loads((root / 'media.json').read_text())
            return ChatResponse(content=[], tool_calls=[ToolCall(id='attach', name='app_control',
                arguments={'operation': 'dispatch', 'args': {'action': 'outputs.attachMedia',
                    'id': 'fixture-media-attachment', 'args': {
                        'sessionId': state['sessionId'], 'messageId': message['messageId'], 'path': fixture['path'],
                        'expectedSha256': fixture['sha256'], 'title': 'Saved animation'}}})],
                finish_reason='tool_calls')
        assert stage == 2 and result['success'], result
        return ChatResponse(content=[TextBlock(text='Saved animation attached. Browser playback is unverified.')],
                            finish_reason='stop')


async def mount(coordinator, config=None):
    await coordinator.mount('providers', Provider(), name='media-fixture')
