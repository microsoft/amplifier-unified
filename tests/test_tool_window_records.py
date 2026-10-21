"""Real lifecycle observations reconnect to saved fields without replay."""
import copy
import json
import pytest
from test_work_windows import fixture

def tool_records(call='call', sid='s', name='bash', *, arguments=None, result=None):
    common = {'session_id': sid, 'tool_call_id': call, 'tool_name': name}
    arguments = arguments if arguments is not None else {'command': 'printf saved-command'}
    return [
        {'event': 'tool:pre', 'timestamp': 1, 'data': {**common, 'tool_input': arguments}},
        {'event': 'tool:post', 'timestamp': 2, 'data': {**common, 'tool_input': arguments,
            'result': result if result is not None else {'success': True, 'output': {'stdout': 'saved-output', 'returncode': 0}}}},
    ]

def live_tool(session, *, sid='s', call='call', name='bash', events=None):
    from amplifier_web.execution_events import ExecutionEvents
    emitted = []
    observer = ExecutionEvents(sid, emitted.append)
    for event in events if events is not None else tool_records(call, sid, name):
        event['data'] = observer.hook(sid, event['event'], event['data']) or event['data']
    node = next(row['event'] for row in reversed(emitted) if row.get('type') == 'execution.event')
    node.update(turnId='t', startedAt=1, endedAt=2)
    session['execution'].update(nodes=[node], turns=[{'id': 't', 'anchorMessageId': 'user', 'phase': 'completed', 'startedAt': 1, 'endedAt': 2}])
    return node

def write(path, events):
    path.write_text(''.join(json.dumps(row) + '\n' for row in events))

def observed(session, path, *, events=None, **kwargs):
    events = events if events is not None else tool_records(**kwargs)
    node = live_tool(session, events=events, **{key:value for key,value in kwargs.items() if key in {'sid','call','name'}})
    write(path, events)
    return node

@pytest.mark.asyncio
async def test_real_observer_tool_fields_arrive_without_another_execution_and_keep_authority(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    events = tool_records()
    live_tool(session, events=events)
    original = copy.deepcopy(session)
    first = owner.live_pages.read(session, 't@user')
    before = owner.project(session)['execution']['segments'][0]['detailRevision']
    assert 'input' not in first['items'][0] and 'inputDetail' not in first['items'][0]
    write(path, events)
    saved = await owner.read('s')
    after = owner.project(session)['execution']['segments'][0]['detailRevision']
    assert before != after
    known = {row['id']: row['detailVersion'] for row in first['items']}
    row = owner.live_pages.sync(session, 't@user', known)['items'][0]
    assert row['toolObservationId'] == session['execution']['nodes'][0]['toolObservationId']
    assert row['contentAvailability']['status'] == 'linked'
    assert row['inputSummary']['command'] == 'printf saved-command'
    for field, expected in [('input', 'saved-command'), ('output', 'saved-output')]:
        ref = row[field + 'Detail']
        assert ref['windowKey'] == saved['items'][0]['id'] and ref['id'] != row['id']
        assert expected in (await owner.read_text('s', ref))['value']
    assert session == original
    assert row['startedAt'] == 1 and row['endedAt'] == 2 and row['phase'] == 'completed'
    await owner.close()

@pytest.mark.asyncio
async def test_delegate_selectors_keep_complete_saved_content(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    instruction = 'A full saved request ' * 100 + 'REQUEST-END'
    response = 'A full saved response ' * 100 + 'RESPONSE-END'
    observed(session,path,name='delegate',arguments={'agent':'reviewer','instruction':instruction},result={'success':True,'output':response})
    session['execution']['nodes'][0]['label']='Delegate · reviewer'
    await owner.read('s')
    row = owner.live_pages.read(session, 't@user')['items'][0]
    assert row['exchange']['toolObservationId'] == row['toolObservationId']
    for field, value in [('delegateRequest', instruction), ('delegateResponse', response)]:
        assert row['exchange'][field] == value[:512]
        assert (await owner.read_text('s', row['exchange'][field + 'Detail']))['value'] == value
    assert row['inputSummary']['instruction'].endswith('…')
    await owner.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['duplicate-token','wrong-name','different-namespace','wrong-token'])
async def test_exact_observation_refuses_wrong_or_ambiguous_fields(tmp_path,monkeypatch,case):
    owner,session,path,_=fixture(tmp_path,monkeypatch,count=0)
    events=tool_records()
    live_tool(session,events=events)
    if case=='duplicate-token':events.append(copy.deepcopy(events[-1]))
    elif case=='wrong-name':events[-1]['data']['tool_name']='other'
    elif case=='different-namespace':events[-1]['data']['session_id']='child'
    else:events[-1]['data']['unified_tool_observation_id']='11111111-1111-4111-8111-111111111111'
    write(path,events)
    await owner.read('s')
    row=owner.live_pages.read(session,'t@user')['items'][0]
    assert not any(field+'Detail' in row for field in ('input','output','error'))
    assert row['phase']=='completed'
    await owner.close()

@pytest.mark.asyncio
async def test_reused_call_id_never_borrows_older_attempt_even_when_only_older_pair_is_loaded(tmp_path,monkeypatch):
    owner,session,path,_=fixture(tmp_path,monkeypatch,count=0)
    old=tool_records(arguments={'command':'wrong old attempt'})
    live_tool(session,events=old)
    current=tool_records(arguments={'command':'current attempt'})
    live_tool(session,events=current)
    write(path,old)
    await owner.read('s')
    row=owner.live_pages.read(session,'t@user')['items'][0]
    assert row['contentAvailability']['status']=='not-loaded' and 'inputDetail' not in row
    assert row['inputSummary']['command']=='current attempt'
    await owner.close()

@pytest.mark.asyncio
async def test_exact_pre_only_stays_partial_instead_of_suppressing_future_lookup(tmp_path,monkeypatch):
    owner,session,path,_=fixture(tmp_path,monkeypatch,count=0)
    events=tool_records()[:1]
    live_tool(session,events=events)
    write(path,events)
    await owner.read('s')
    row=owner.live_pages.read(session,'t@user')['items'][0]
    assert row['contentAvailability']['status']=='partial' and 'inputDetail' in row and 'outputDetail' not in row
    await owner.close()

@pytest.mark.asyncio
async def test_legacy_records_remain_independent_and_exclude_clearly_outside_attempt(tmp_path,monkeypatch):
    owner,session,path,_=fixture(tmp_path,monkeypatch,count=0)
    node=live_tool(session)
    node.pop('toolObservationId',None)
    older=tool_records(arguments={'command':'wrong older command'})
    for event in older:event['timestamp']-=10
    events=tool_records()
    write(path,older+events+copy.deepcopy(events))
    await owner.read('s')
    row=owner.live_pages.read(session,'t@user')['items'][0]
    assert row['contentAvailability']['status']=='unverified'
    assert not any(field+'Detail' in row for field in ('input','output','error'))
    observations=row['savedObservations']
    assert len(observations)==4 and len({item['id'] for item in observations})==4
    assert all(item['phase']=='recorded' and item['association']=='unverified' and 'endedAt' not in item for item in observations)
    assert 'wrong older' not in json.dumps(observations)
    assert 'saved-command' in (await owner.read_text('s',observations[0]['inputDetail']))['value']
    await owner.close()

@pytest.mark.asyncio
async def test_public_root_alias_and_exact_native_source_rebinding(tmp_path,monkeypatch):
    owner,session,path,_=fixture(tmp_path,monkeypatch,count=0)
    session['id']='public'
    events=tool_records(sid='public')
    live_tool(session,sid='public',events=events)
    for event in events:event['data']['session_id']='s'
    session['execution']['nodes'][0]['sessionId']='public'
    write(path,events)
    await owner.read('public')
    row=owner.live_pages.read(session,'t@user')['items'][0]
    assert 'saved-command' in (await owner.read_text('public',row['inputDetail']))['value']
    session['nativeIdentity']='other'
    with pytest.raises(ValueError,match='expired'):await owner.read_text('public',row['inputDetail'])
    assert 'inputDetail' not in owner.live_pages.read(session,'t@user')['items'][0]
    await owner.close()

@pytest.mark.asyncio
async def test_queued_delegate_receipt_is_not_a_response(tmp_path,monkeypatch):
    owner,session,path,_=fixture(tmp_path,monkeypatch,count=0)
    observed(session,path,name='delegate',arguments={'instruction':'Do the work'},result={'success':True,'output':{'status':'queued','receipt':'accepted'}})
    await owner.read('s')
    row=owner.live_pages.read(session,'t@user')['items'][0]
    assert row['exchange']['delegateRequest']=='Do the work'
    assert 'delegateResponse' not in row['exchange']
    assert 'queued' in (await owner.read_text('s',row['exchange']['outputDetail']))['value']
    await owner.close()

@pytest.mark.asyncio
async def test_exact_metadata_only_event_never_claims_linked_payload(tmp_path,monkeypatch):
    owner,session,path,_=fixture(tmp_path,monkeypatch,count=0)
    events=tool_records()
    live_tool(session,events=events)
    events[-1]['data'].pop('tool_input');events[-1]['data'].pop('result')
    write(path,events)
    await owner.read('s')
    row=owner.live_pages.read(session,'t@user')['items'][0]
    assert row['contentAvailability']['status']=='unrecorded'
    await owner.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('reason,wording',[('source_changed','changed'),('timeout','longer'),('queue_full','longer'),('worker_failed','could not be read')])
async def test_saved_content_io_failure_does_not_claim_source_changed(tmp_path,monkeypatch,reason,wording):
    owner,session,path,_=fixture(tmp_path,monkeypatch,count=0)
    observed(session,path)
    await owner.read('s')
    row=owner.live_pages.read(session,'t@user')['items'][0]
    async def refused(*args,**kwargs):return {'status':'retry','reason':reason}
    monkeypatch.setattr(owner.projector,'project',refused)
    with pytest.raises(ValueError,match=wording):await owner.read_text('s',row['inputDetail'])
    await owner.close()
