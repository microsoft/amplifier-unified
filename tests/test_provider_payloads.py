import json
from types import SimpleNamespace

import pytest

from amplifier_core import HookRegistry, HookResult
from amplifier_web.provider_recording import install_request_redaction
from amplifier_web.provider_payloads import save, open_record, DIRECTORY
from amplifier_web.event_log_view import EventIndex
from amplifier_web.browser_detail import compact


async def capture(directory, event, raw, field='raw'):
    hooks, capabilities, records = HookRegistry(), {}, []
    coordinator = SimpleNamespace(hooks=hooks, get_capability=capabilities.get,
        register_capability=lambda key, value: capabilities.update({key: value}))
    async def record(event, data):
        records.append(data)
        return HookResult()
    hooks.register(event, record, priority=100)
    install_request_redaction(coordinator, directory=directory)
    await hooks.emit(event, {'session_id': 'child', 'request_id': 'call',
        'provider': 'fixture', 'model': 'fixture-model', field: raw,
        'provider_payloads': {'forged': {'sha256': 'a' * 64}}})
    return records[0]


def index(directory, event, data):
    path = directory / 'events.jsonl'
    path.write_text(json.dumps({'event': event, 'data': data}) + '\n')
    result = EventIndex(path, 'child')
    result.refresh()
    return next(iter(result.nodes.values()))


@pytest.mark.parametrize('event,field,raw_key', [('llm:request', 'request', 'raw_request'),
                                              ('llm:response', 'response', 'raw_response')])
async def test_large_capture_preserves_tail_and_secret_redaction_with_bounded_projection(tmp_path, event, field, raw_key):
    raw = {'messages': ['é' * 1_500_000, 'AFTER OLD LIMIT'], 'api_key': 'never-save-me'}
    data = await capture(tmp_path, event, raw, raw_key)
    info = data['provider_payloads'][raw_key]
    assert data['request_capture']['truncated'] is True
    assert info['truncated'] is False
    assert set(data['provider_payloads']) == {raw_key}
    node = index(tmp_path, event, data)
    projected = compact(node, 'root', 'nodes', 512)
    assert len(json.dumps(projected)) < 4000
    assert projected[field + 'Recording']['sha256'] == info['sha256']
    assert projected['sessionId'] == 'child'
    with open_record(node['_eventFields'][field], info['sha256'])[0] as stream:
        value = json.load(stream)
    assert value['messages'] == raw['messages']
    assert value['api_key'] == '[REDACTED]'
    assert raw['api_key'] == 'never-save-me'
    path = tmp_path / DIRECTORY / (info['sha256'] + '.json')
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'never-save-me' not in path.read_text()
    path.write_bytes(b'x' * info['bytes'])
    with pytest.raises(ValueError, match='changed'):
        open_record(node['_eventFields'][field], info['sha256'])


async def test_capture_io_failure_keeps_safe_preview_and_call_alive(tmp_path, monkeypatch):
    def unavailable(*args):
        raise OSError('disk full')
    monkeypatch.setattr('amplifier_web.provider_payloads.save', unavailable)
    data = await capture(tmp_path, 'llm:request', {'input': 'x' * 1_100_000, 'api_key': 'private'})
    assert data['request_capture']['truncated']
    assert data['provider_payload_unavailable']
    assert 'provider_payloads' not in data
    assert 'private' not in json.dumps(data)


def test_archive_limit_is_honest_and_temporary_files_are_removed(tmp_path, monkeypatch):
    monkeypatch.setattr('amplifier_web.provider_payloads.MAX_CHARS', 300)
    info = save(tmp_path, {'input': 'x' * 1000})
    assert info['truncated']
    assert len(list((tmp_path / DIRECTORY).iterdir())) == 1
    monkeypatch.setattr('amplifier_web.provider_payloads.MAX_BYTES', 1)
    with pytest.raises(ValueError, match='storage limit'):
        save(tmp_path, {'input': 'x' * 1000})
    assert len(list((tmp_path / DIRECTORY).iterdir())) == 1


async def test_download_is_authenticated_exact_and_attributed_to_child(aiohttp_client, tmp_path):
    from amplifier_web.server import create_app
    from test_service import Runtime
    app = await create_app(tmp_path / 'app', workspace=tmp_path, runtime=Runtime(),
        preload_providers=False, voice=False, background_updates=False)
    client = await aiohttp_client(app)
    data = await capture(tmp_path, 'llm:request', {'input': 'x' * 1_100_000, 'end': 'kept'})
    node = index(tmp_path, 'llm:request', data)
    session = {'id': 'root', 'execution': {'nodes': [node]}}
    app['service']._session = lambda identity: session if identity == 'root' else {'id': 'unrelated'}
    info = node['requestRecording']
    args = {'sessionId': 'root', 'id': node['id'], 'field': 'request', 'digest': info['sha256']}
    url = '/api/conversation/provider-recording'
    assert (await client.get(url, params=args)).status == 401
    client.session.headers['Authorization'] = 'Bearer ' + app['control_token']
    response = await client.get(url, params=args)
    assert response.status == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Content-Disposition'].startswith('attachment;')
    result = await response.json()
    assert result['sessionId'] == 'child' and result['rootSessionId'] == 'root'
    assert result['callId'] == node['id']
    assert result['payload']['end'] == 'kept'
    assert len(result['payload']['input']) == 1_100_000
    for changes in ({'sessionId': 'unrelated'}, {'digest': 'b' * 64}, {'id': 'other'}, {'field': '../../secret'}):
        assert (await client.get(url, params={**args, **changes})).status == 400
    reference = node['_eventFields']['request']
    (tmp_path / DIRECTORY / (info['sha256'] + '.json')).unlink()
    with pytest.raises(ValueError, match='unavailable'):
        open_record(reference, info['sha256'])


@pytest.mark.parametrize('replacement', ['link', 'fifo'])
async def test_download_rejects_links_and_non_regular_files(tmp_path, replacement):
    import os
    data = await capture(tmp_path, 'llm:request', {'input': 'x' * 1_100_000})
    node = index(tmp_path, 'llm:request', data)
    info = node['requestRecording']
    path = tmp_path / DIRECTORY / (info['sha256'] + '.json')
    original = path.read_bytes()
    path.unlink()
    if replacement == 'link':
        target = tmp_path / 'other.json'
        target.write_bytes(original)
        path.symlink_to(target)
    else:
        os.mkfifo(path)
    with pytest.raises(ValueError):
        open_record(node['_eventFields']['request'], info['sha256'])
