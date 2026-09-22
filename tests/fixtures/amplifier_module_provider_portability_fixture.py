"""Local-only inference fixture; never records or forwards credential values."""
import json
import hashlib
import os
from pathlib import Path

from amplifier_core.message_models import ChatResponse, TextBlock


class PortabilityFixtureProvider:
    def __init__(self, *, api_key=None, config=None):
        self.api_key = api_key
        self.config = config or {}

    def get_info(self):
        return {'id': 'portability-fixture', 'name': 'Local portability fixture',
                'capabilities': ['completion:single_attempt:v1'],
                'config_fields': [{'id': 'api_key', 'field_type': 'secret', 'required': True}]}

    async def complete(self, request, **kwargs):
        assert self.api_key == os.environ['PORTABILITY_FIXTURE_KEY']
        assert self.config['api_key'] == self.api_key
        assert request.model == 'fixture-model'
        assert request.max_output_tokens == 1024 and request.reasoning_effort == 'high'
        assert kwargs == {'request_options': {'single_attempt': True}}
        assert request.tools is None and request.stream is False
        assert request.metadata == {'purpose': 'destination-execution-probe'}
        assert len(request.messages) == 1 and request.messages[0].content == 'Reply with OK.'
        audit = Path(os.environ['PORTABILITY_FIXTURE_AUDIT'])
        fd = os.open(audit, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'a') as stream:
            stream.write(json.dumps({'host': os.environ['PORTABILITY_FIXTURE_HOST'],
                'pid': os.getpid(), 'model': request.model, 'maxOutputTokens': request.max_output_tokens,
                'destinationCredentialVerified': True, 'purpose': request.metadata['purpose']}) + '\n')
        receipt = {'version': 1, 'model': request.model, 'reasoning_effort': request.reasoning_effort,
            'max_output_tokens': request.max_output_tokens, 'timeout_seconds': request.timeout,
            'native_count_requests': 1, 'generation_requests': 1, 'native_input_tokens': 6,
            'retries': 0, 'continuations': 0, 'closed': True,
            'input_sha256': hashlib.sha256(json.dumps(
                [{'role':'user','content':[{'type':'input_text','text':'Reply with OK.'}]}],
                sort_keys=True,separators=(',',':')).encode()).hexdigest(), 'request_sha256': 'b' * 64}
        return ChatResponse(content=[TextBlock(text='OK')], finish_reason='stop',
                            metadata={'openai:status': 'completed', 'openai:single_attempt': receipt})

    async def close(self):
        pass
