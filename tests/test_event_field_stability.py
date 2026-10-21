"""Exact accepted field reads survive unrelated log growth, never changed evidence."""
import hashlib
import json

import pytest

from amplifier_web import event_window as windows
from test_event_window import event, owner, read, write


def mutate_after_decode(monkeypatch, action):
    decode = windows._bounded_json
    def changed(*args):
        value = decode(*args)
        action()
        return value
    monkeypatch.setattr(windows, '_bounded_json', changed)


@pytest.mark.parametrize('field,name,key', [('request', 'llm:request', 'raw'),
                                          ('output', 'tool:post', 'result')])
def test_exact_field_survives_append_during_read(owner, monkeypatch, field, name, key):
    session, path = owner
    value = {'body': 'saved content ' * 1000}
    write(path, [event(1, name, **{key: value})])
    reference = read(session)['items'][0]['_eventFields'][field]
    def append():
        with path.open('ab') as stream:
            stream.write(json.dumps(event(2)).encode() + b'\n')
    mutate_after_decode(monkeypatch, append)
    result = windows.read_field(session, reference, offset=7, limit=128)
    expected = json.dumps(value, ensure_ascii=False, indent=2)
    assert result['text'] == expected[7:135]
    assert result['total'] == len(expected)
    assert result['digest'] == hashlib.sha256(expected.encode()).hexdigest()


@pytest.mark.parametrize('change', ['edit', 'edit-and-append', 'truncate', 'replace', 'link'])
def test_changed_field_is_rejected_even_with_log_growth(owner, monkeypatch, change):
    session, path = owner
    write(path, [event(1, 'llm:request', raw={'body': 'original'})])
    reference = read(session)['items'][0]['_eventFields']['request']
    def alter():
        raw = path.read_bytes()
        if change in {'edit', 'edit-and-append'}:
            path.write_bytes(raw.replace(b'original', b'modified') +
                (b'{}\n' if change.endswith('append') else b''))
        elif change == 'truncate':
            path.write_bytes(b'{}\n')
        else:
            other = path.with_suffix('.new')
            other.write_bytes(raw)
            if change == 'link':
                path.unlink()
                path.symlink_to(other)
            else:
                other.replace(path)
    mutate_after_decode(monkeypatch, alter)
    with pytest.raises(windows.StaleWindow):
        windows.read_field(session, reference)


@pytest.mark.asyncio
async def test_actual_worker_reads_redacted_preview_and_keeps_partial_recording_honest(tmp_path, monkeypatch):
    from amplifier_web.provider_payloads import MAX_CHARS, open_record
    from test_provider_payloads import capture
    from test_work_windows import fixture
    windows_owner, session, path, service = fixture(tmp_path, monkeypatch, count=0)
    raw = {'api_key': 'private-never-return', 'messages': ['x' * (MAX_CHARS + 1000)], 'tail': 'not captured'}
    data = await capture(path.parent, 'llm:request', raw)
    path.write_text(json.dumps({'event': 'llm:request', 'data': data}) + '\n')
    # Real disposable worker, not FakeProjector. No runtime or provider starts.
    windows_owner.projector = None
    try:
        group = (await windows_owner.read('s'))['items'][0]['id']
        node = windows_owner.nodes('s', group)['items'][0]
        assert node['requestCapture']['truncated'] and node['requestRecording']['truncated']
        assert node['requestRecording']['limit_chars'] == MAX_CHARS
        result = await windows_owner.read_text('s', {**node['requestDetail'], 'limit': 16000})
        assert '[REDACTED]' in result['value'] and 'private-never-return' not in result['value']
        assert result['nextOffset'] and result['total'] > 16000
        page = windows_owner.pages[group]
        reference = page['items'][0]['_eventFields']['request']
        with open_record(reference, node['requestRecording']['sha256'])[0] as stream:
            saved = json.load(stream)
        assert saved['api_key'] == '[REDACTED]' and 'tail' not in saved
        assert data['provider_payloads']['raw']['truncated'] is True
        assert raw['api_key'] == 'private-never-return', 'Capture cannot edit the provider request'
    finally:
        await windows_owner.close()


@pytest.mark.parametrize('field,key,name,value', [
    ('delegateRequest', 'tool_input', 'tool:pre', {'instruction': 'Work instruction ' * 200}),
    ('delegateResponse', 'result', 'tool:post', {'success': True, 'output': {'response': 'Work result ' * 200}}),
])
def test_delegate_field_is_selected_from_full_exact_envelope(owner, field, key, name, value):
    from amplifier_web.delegate_exchange import select
    session, path = owner
    write(path, [event(1, name, tool_name='delegate', **{key: value})])
    row = read(session)['items'][0]
    assert len(row[field]) == windows.PREVIEW_CHARS
    reference = row['_eventFields'][field]
    assert reference['delegateField'] == field
    raw_field = 'input' if field == 'delegateRequest' else 'output'
    assert row[field + 'Detail']['digest'] != row[raw_field + 'Detail']['digest']
    expected = select(value, field)
    result = windows.read_field(session, reference, offset=500, limit=16384)
    assert result['text'] == expected[500:] and result['total'] == len(expected)
    write(path, [event(1, name, tool_name='delegate', **{key: {'instruction': 'changed'}})])
    with pytest.raises(windows.StaleWindow):
        windows.read_field(session, reference)


@pytest.mark.parametrize('name,value', [
    ('tool:post', {'status': 'queued', 'response': 'not a completed answer'}),
    ('tool:post', {'success': False, 'error': 'failed', 'response': 'not an answer'}),
    ('tool:error', 'failed'),
    ('tool:post', {'job_id': 'receipt-only'}),
])
def test_delegate_receipts_and_failures_are_not_completed_responses(owner, name, value):
    session, path = owner
    write(path, [event(1, name, tool_name='delegate', result=value)])
    row = read(session)['items'][0]
    assert 'outputDetail' in row
    assert not any(key.startswith('delegateResponse') for key in row)
    assert 'delegateResponse' not in row['_eventFields']


@pytest.mark.parametrize('selector,key', [('arbitrary', 'result'), ('delegateRequest', 'result'),
                                        ('delegateResponse', 'tool_input'), ({}, 'result')])
def test_delegate_selector_is_a_fixed_owned_transform(owner, selector, key):
    session, path = owner
    write(path, [event(1, result='saved')])
    reference = read(session)['items'][0]['_eventFields']['output']
    with pytest.raises(ValueError, match='reference'):
        windows.read_field(session, {**reference, 'delegateField': selector, 'key': key})


def test_header_summary_uses_only_bounded_named_strings_from_accepted_input(owner):
    session, path = owner
    value = {'padding': 'large unrelated argument ' * 1000, 'command': 'printf saved ' * 1000,
             'action': 'workspace.inspect', 'path': '/work/readme', 'nested': {'command': 'not used'},
             'operation': {'arbitrary': 'object'}, 'password': 'not a header'}
    write(path, [event(1, 'tool:pre', tool_input=value)])
    row = read(session)['items'][0]
    assert 'command' not in row['input'], 'The ordinary bounded JSON preview ended before command'
    assert row['inputSummary'] == {'command': value['command'][:180],
                                   'action': value['action'], 'path': value['path']}
    complete = windows.read_field(session, row['_eventFields']['input'], limit=16384)
    assert complete['total'] == len(json.dumps(value, ensure_ascii=False, indent=2))


def test_missing_tool_payload_never_gets_invented_header_or_detail(owner):
    session, path = owner
    write(path, [{'event': 'tool:pre', 'data': {'tool_name': 'app_control', 'tool_call_id': 'c'}}])
    row = read(session)['items'][0]
    assert not row['_eventFields']
    assert 'inputSummary' not in row and 'inputDetail' not in row
