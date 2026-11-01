"""Image diagnostics retain evidence without copying image transport or requests."""
import copy
import hashlib
import json

import pytest

from amplifier_web.provider_recording import redact_request
from amplifier_web.provider_payloads import DIRECTORY, open_record
from test_provider_payloads import capture, index


ENCODED = 'aW1hZ2UtZml4dHVyZQ=='
URL = 'data:image/png;base64,' + ENCODED


def image_block():
    return {'type': 'input_image', 'image_url': URL, 'detail': 'high'}


@pytest.mark.parametrize('raw,transport', [
    ({'input': [{'role': 'user', 'content': [image_block()]}]}, URL),
    ({'messages': [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': URL, 'detail': 'high'}}]}]}, URL),
    ({'messages': [{'role': 'user', 'content': [
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': ENCODED}}]}]}, ENCODED),
    ({'messages': [{'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'call', 'content': [
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': ENCODED}}]}]}]}, ENCODED),
    ({'contents': [{'role': 'user', 'parts': [{'inline_data': {'mime_type': 'image/png', 'data': ENCODED}}]}]}, ENCODED),
    ({'contents': [{'role': 'user', 'parts': [{'inlineData': {'mimeType': 'image/png', 'data': ENCODED}}]}]}, ENCODED),
    ({'contents': [{'role': 'user', 'parts': [{'inline_data': {'mime_type': 'image/png', 'data': b'image-fixture'}}]}]}, b'image-fixture'),
    ({'messages': [{'role': 'user', 'content': 'Describe this', 'images': [ENCODED]}]}, ENCODED),
    ({'output': [{'type': 'image_generation_call', 'result': ENCODED}]}, ENCODED),
    ({'content': [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': ENCODED}}]}, ENCODED),
])
def test_known_wire_image_fields_become_fingerprints_without_mutation(raw, transport):
    before = copy.deepcopy(raw)
    safe, truncated = redact_request(raw)
    serialized = json.dumps(safe)
    assert raw == before and not truncated
    assert '__unified_image_transport__' in serialized
    assert (transport.decode() if isinstance(transport, bytes) else transport) not in serialized
    assert hashlib.sha256(transport.encode() if isinstance(transport, str) else transport).hexdigest() in serialized
    assert str(len(transport)) in serialized
    assert redact_request(safe) == (safe, False), 'Fingerprint copies remain stable on repeated redaction'


@pytest.mark.parametrize('raw', [
    {'input': URL},
    {'messages': [{'role': 'user', 'content': URL}]},
    {'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': URL}]}]},
    {'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': json.dumps(image_block())}]}]},
    {'tools': [{'name': 'fixture', 'input_schema': {'example': image_block()}}]},
    {'messages': [{'role': 'assistant', 'tool_calls': [{'function': {'arguments': image_block()}}]}]},
    {'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': image_block()}]}]},
    {'metadata': {'messages': [{'role': 'user', 'content': [image_block()]}]}},
    {'messages': [{'role': 'user', 'content': [{'type': 'input_image', 'image_url': 'https://example.test/image.png'}]}]},
    {'contents': [{'role': 'user', 'parts': [{'inline_data': {'mime_type': 'application/pdf', 'data': ENCODED}}]}]},
])
def test_ordinary_text_schema_arguments_urls_and_other_media_keep_capture_policy(raw):
    assert redact_request(raw) == (raw, False)


def test_image_shaped_text_still_uses_the_existing_text_limit():
    raw = {'input': [{'role': 'user', 'content': [
        {'type': 'input_text', 'text': URL * 10000}]}]}
    safe, truncated = redact_request(raw, limit=500)
    assert truncated and '__unified_image_transport__' not in json.dumps(safe)
    assert '[capture limit]' in json.dumps(safe)


async def test_real_hook_captures_image_fingerprints_without_consuming_text_or_options(tmp_path):
    # Match the triggering three-image input without copying private user data.
    encoded = 'a' * 4_300_000
    url = 'data:image/png;base64,' + encoded
    raw = {'model': 'fixture', 'input': [{'role': 'user', 'content': [
        *({'type': 'input_image', 'image_url': url, 'detail': 'high'} for _ in range(3)),
        {'type': 'input_text', 'text': 'Useful instructions after the images'},
    ]}], 'tools': [{'name': 'read', 'description': 'Keep the real tool schema'}],
        'prompt_cache_key': 'fixture-cache', 'max_output_tokens': 4096}
    original = json.dumps(raw).encode()
    data = await capture(tmp_path, 'llm:request', raw)
    assert json.dumps(raw).encode() == original, 'The exact body sent by the provider stays intact'
    safe = json.dumps(data)
    assert len(safe) < 3000 and encoded[:1000] not in safe
    assert 'Useful instructions after the images' in safe
    assert data['raw']['tools'] == raw['tools']
    assert data['raw']['prompt_cache_key'] == 'fixture-cache'
    assert data['raw']['max_output_tokens'] == 4096
    assert data['request_capture']['truncated'] is False
    assert 'provider_payloads' not in data and not (tmp_path / DIRECTORY).exists()
    node = index(tmp_path, 'llm:request', data)
    assert node['requestInfo']['message_count'] == 1
    assert (tmp_path / 'events.jsonl').stat().st_size < 3000


@pytest.mark.parametrize('event,field', [('llm:request', 'raw'), ('llm:response', 'raw_response')])
async def test_full_recording_and_event_copy_omit_transport_but_preserve_large_text(tmp_path, event, field):
    encoded = 'a' * 4_300_000
    image = {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': encoded}}
    raw = {'content': [image, {'type': 'text', 'text': 't' * 1_100_000},
                       {'type': 'text', 'text': 'Tail after long text'}],
           'usage': {'input_tokens': 120, 'cache_read_input_tokens': 100}}
    original = json.dumps(raw).encode()
    data = await capture(tmp_path, event, raw, field)
    info = data['provider_payloads'][field]
    assert data['request_capture']['truncated'] and not info['truncated']
    node = index(tmp_path, event, data)
    reference = node['_eventFields']['request' if event == 'llm:request' else 'response']
    with open_record(reference, info['sha256'])[0] as stream:
        recorded = json.load(stream)
    assert recorded['content'][1:] == raw['content'][1:]
    assert recorded['usage'] == raw['usage']
    assert encoded[:1000] not in json.dumps(recorded)
    assert encoded[:1000] not in (tmp_path / 'events.jsonl').read_text()
    assert info['bytes'] < 1_102_000
    assert json.dumps(raw).encode() == original
