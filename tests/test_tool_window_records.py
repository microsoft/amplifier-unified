"""Real lifecycle observations must reconnect to saved fields without replay."""
import copy
import json

import pytest

from test_work_windows import fixture


def tool_records(call='call', sid='s', name='bash', *, arguments=None, result=None):
    common = {'session_id': sid, 'tool_call_id': call, 'tool_name': name}
    return [
        {'event': 'tool:pre', 'timestamp': 1, 'data': {**common, 'tool_input': arguments or {'command': 'printf saved-command'}}},
        {'event': 'tool:post', 'timestamp': 2, 'data': {**common, 'result': result or {'success': True, 'output': {'stdout': 'saved-output', 'returncode': 0}}}},
    ]


def live_tool(session, *, sid='s', call='call', name='bash'):
    # The exact public ExecutionEvents observer strips input/results on purpose.
    from amplifier_web.execution_events import ExecutionEvents
    emitted = []
    events = ExecutionEvents(sid, emitted.append)
    for event in tool_records(call, sid, name):
        events.hook(sid, event['event'], event['data'])
    node = next(row['event'] for row in reversed(emitted) if row.get('type') == 'execution.event')
    node.update(turnId='t', startedAt=1, endedAt=2)
    session['execution'].update(nodes=[node], turns=[{'id': 't', 'anchorMessageId': 'user', 'phase': 'completed', 'startedAt': 1, 'endedAt': 2}])
    return node


def write(path, events):
    path.write_text(''.join(json.dumps(row) + '\n' for row in events))


@pytest.mark.asyncio
async def test_real_observer_tool_fields_arrive_without_another_execution_and_keep_authority(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    live_tool(session)
    original = copy.deepcopy(session)
    first = owner.live_pages.read(session, 't@user')
    before = owner.project(session)['execution']['segments'][0]['detailRevision']
    assert 'input' not in first['items'][0] and 'inputDetail' not in first['items'][0]
    write(path, tool_records())
    saved = await owner.read('s')
    after = owner.project(session)['execution']['segments'][0]['detailRevision']
    assert before != after
    known = {row['id']: row['detailVersion'] for row in first['items']}
    rows = owner.live_pages.sync(session, 't@user', known)['items']
    assert len(rows) == 1
    row = rows[0]
    assert row['contentAvailability']['status'] == 'linked'
    assert row['inputSummary']['command'] == 'printf saved-command'
    for field, expected in [('input', 'saved-command'), ('output', 'saved-output')]:
        ref = row[field + 'Detail']
        assert ref['windowKey'] == saved['items'][0]['id']
        assert ref['id'] != row['id']
        assert expected in (await owner.read_text('s', ref))['value']
    assert session == original
    assert row['startedAt'] == 1 and row['endedAt'] == 2 and row['phase'] == 'completed'
    await owner.close()


@pytest.mark.asyncio
async def test_delegate_request_response_selectors_keep_complete_saved_content(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    live_tool(session, name='delegate')
    instruction = 'A full saved request ' * 100 + 'REQUEST-END'
    response = 'A full saved response ' * 100 + 'RESPONSE-END'
    write(path, tool_records(name='delegate', arguments={'agent':'reviewer', 'instruction':instruction}, result={'success':True, 'output':response}))
    await owner.read('s')
    row = owner.live_pages.read(session, 't@user')['items'][0]
    exchange = row['exchange']
    for field, value in [('delegateRequest', instruction), ('delegateResponse', response)]:
        assert exchange[field] == value[:512]
        ref = exchange[field + 'Detail']
        assert ref['windowKey'].startswith('event-window:')
        assert (await owner.read_text('s', ref))['value'] == value
    assert len(row['inputSummary']['instruction']) <= 180
    assert row['inputSummary']['agent'] == 'reviewer'
    assert row['inputSummary']['instruction'].endswith('…')
    await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['duplicate-pre', 'duplicate-post', 'retry', 'name-mismatch', 'child', 'post-only', 'reversed'])
async def test_ambiguous_or_unpaired_fields_do_not_borrow_another_attempt(tmp_path, monkeypatch, case):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    live_tool(session)
    records = tool_records()
    if case == 'duplicate-pre': records.insert(1, copy.deepcopy(records[0]))
    elif case == 'duplicate-post': records.append(copy.deepcopy(records[-1]))
    elif case == 'retry': records += tool_records(arguments={'command':'other attempt'})
    elif case == 'name-mismatch': records[-1]['data']['tool_name'] = 'other'
    elif case == 'child':
        for event in records: event['data']['session_id'] = 'child'
    elif case == 'post-only': records = records[1:]
    elif case == 'reversed': records.reverse()
    write(path, records)
    await owner.read('s')
    row = owner.live_pages.read(session, 't@user')['items'][0]
    assert not any(key in row for key in ('inputDetail','outputDetail','errorDetail'))
    assert row['phase'] == 'completed'
    await owner.close()


@pytest.mark.asyncio
async def test_public_root_alias_and_exact_native_source_rebinding(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    session['id'] = 'public'
    live_tool(session, sid='public')
    write(path, tool_records())
    await owner.read('public')
    row = owner.live_pages.read(session, 't@user')['items'][0]
    assert 'saved-command' in (await owner.read_text('public', row['inputDetail']))['value']
    session['nativeIdentity'] = 'other'
    with pytest.raises(ValueError, match='expired'):
        await owner.read_text('public', row['inputDetail'])
    assert 'inputDetail' not in owner.live_pages.read(session, 't@user')['items'][0]
    await owner.close()


@pytest.mark.asyncio
async def test_queued_delegate_receipt_is_not_a_response(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    live_tool(session, name='delegate')
    write(path, tool_records(name='delegate', arguments={'instruction':'Do the work'}, result={'success':True,'output':{'status':'queued','receipt':'accepted'}}))
    await owner.read('s')
    row = owner.live_pages.read(session, 't@user')['items'][0]
    assert row['exchange']['delegateRequest'] == 'Do the work'
    assert 'delegateResponse' not in row['exchange'] and 'delegateResponseDetail' not in row['exchange']
    assert 'queued' in (await owner.read_text('s', row['exchange']['outputDetail']))['value']
    await owner.close()


@pytest.mark.asyncio
async def test_exact_metadata_only_pair_never_claims_linked_payload(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    live_tool(session)
    records = tool_records()
    records[0]['data'].pop('tool_input')
    records[1]['data'].pop('result')
    write(path, records)
    await owner.read('s')
    row = owner.live_pages.read(session, 't@user')['items'][0]
    assert row['contentAvailability']['status'] == 'unrecorded'
    assert not any(field + 'Detail' in row for field in ('input', 'output', 'error'))
    await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('reason,wording', [('source_changed','changed'), ('timeout','longer'), ('queue_full','longer'), ('worker_failed','could not be read')])
async def test_saved_content_io_failure_does_not_claim_source_changed(tmp_path, monkeypatch, reason, wording):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    live_tool(session)
    write(path, tool_records())
    await owner.read('s')
    row = owner.live_pages.read(session, 't@user')['items'][0]
    async def refused(*args, **kwargs):
        return {'status':'retry', 'reason':reason}
    monkeypatch.setattr(owner.projector, 'project', refused)
    with pytest.raises(ValueError, match=wording):
        await owner.read_text('s', row['inputDetail'])
    await owner.close()
