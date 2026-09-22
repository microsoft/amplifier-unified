"""Local-only inference fixture; never records or forwards credential values."""
import json
import os
from pathlib import Path

from amplifier_core.message_models import ChatResponse, TextBlock


class PortabilityFixtureProvider:
    def __init__(self, *, api_key=None, config=None):
        self.api_key = api_key
        self.config = config or {}

    def get_info(self):
        return {'id': 'portability-fixture', 'name': 'Local portability fixture',
                'config_fields': [{'id': 'api_key', 'field_type': 'secret', 'required': True}]}

    async def complete(self, request, **kwargs):
        assert self.api_key == os.environ['PORTABILITY_FIXTURE_KEY']
        assert self.config['api_key'] == self.api_key
        assert request.model == 'fixture-model'
        assert request.max_output_tokens == 16
        assert request.tools is None and request.stream is False
        assert request.metadata == {'purpose': 'destination-execution-probe'}
        assert len(request.messages) == 1 and request.messages[0].content == 'Reply with OK.'
        audit = Path(os.environ['PORTABILITY_FIXTURE_AUDIT'])
        fd = os.open(audit, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'a') as stream:
            stream.write(json.dumps({'host': os.environ['PORTABILITY_FIXTURE_HOST'],
                'pid': os.getpid(), 'model': request.model, 'maxOutputTokens': request.max_output_tokens,
                'destinationCredentialVerified': True, 'purpose': request.metadata['purpose']}) + '\n')
        return ChatResponse(content=[TextBlock(text='OK')], finish_reason='stop')

    async def close(self):
        pass
