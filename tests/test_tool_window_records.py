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
        assert ref['windowKey'].startswith('open-work:') and ref['id'] != row['id']
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

def test_native_index_keeps_bounded_hints_when_raw_previews_cut_before_code(tmp_path):
    from amplifier_web.event_log_view import EventIndex
    path=tmp_path/'events.jsonl'
    events=tool_records(name='tool_exec',arguments={'description':'leading detail '*100,'code':'print("visible code")'},
        result={'success':False,'output':{'stdout':'long output '*100,'stderr':'Saved validation error','returncode':7}})
    events[-1]['data'].pop('tool_input') # Legacy terminal retains original native input.
    write(path,events)
    index=EventIndex(path,'s');index.refresh()
    node=next(iter(index.nodes.values()))
    assert 'visible code' not in node['input']
    assert node['inputSummary']['code']=='print("visible code")'
    assert len(node['inputSummary']['description'])==180
    assert node['outcomeSummary']=={'exitCode':7,'error':'Saved validation error'}
    assert node['inputDetail'] and node['outputDetail']
    with path.open('a') as stream:
        stream.write(json.dumps({'event':'tool:pre','timestamp':3,'data':{'session_id':'s',
            'tool_call_id':'call','tool_name':'tool_exec','tool_input':{'code':'print("next")'}}})+'\n')
    index.refresh()
    node=next(iter(index.nodes.values()))
    assert node['inputSummary']=={'code':'print("next")'} and not node['outcomeSummary']

def test_old_native_index_checkpoint_cannot_hide_new_tool_hints(tmp_path):
    import gzip
    from amplifier_web.event_log_view import EventIndex
    from amplifier_web.event_index_checkpoint import Checkpoints, encode
    path=tmp_path/'events.jsonl'
    write(path,tool_records(name='tool_exec',arguments={'description':'prefix '*100,'code':'print("retained")'}))
    original=EventIndex(path,'s');original.refresh()
    cache=Checkpoints(tmp_path/'host')
    cache.directory.mkdir(parents=True)
    old=encode(original);old['schema']=2
    for _,node in old['nodes']:
        node.pop('inputSummary',None);node.pop('outcomeSummary',None)
    cache.path(path,'s').write_bytes(gzip.compress(json.dumps(old).encode()))
    restored=EventIndex(path,'s')
    assert not cache.load(restored)
    restored.refresh()
    assert next(iter(restored.nodes.values()))['inputSummary']['code']=='print("retained")'
    cache.save(restored)
    current=EventIndex(path,'s')
    assert cache.load(current)
    assert next(iter(current.nodes.values()))['inputSummary']['code']=='print("retained")'


# Independent persistence contract: this is not imported from the implementation.
# Bodies and even bounded input/output hints belong to canonical field readers.
_SAVED_TOOL_PLACEMENT_FIELDS = {
    'id', 'revision', 'producerId', 'parentId', 'turnId', 'sessionId',
    'rootSessionId', 'kind', 'phase', 'label', 'tool', 'toolCallId',
    'toolObservationId', 'startedAt', 'endedAt', 'lifecycle', 'liveObservation',
}


def _host_placed_tool(session=None, *, arguments=None, result=None):
    from amplifier_web import execution
    from amplifier_web.execution_events import ExecutionEvents

    if session is None:
        session = {'id': 's', 'messages': [
            {'id': 'user', 'inputId': 'original', 'role': 'user',
             'text': 'Inspect the saved fixture', 'createdAt': 0}]}
    else:
        session['messages'][0]['inputId'] = 'original'
    execution.ensure_turn(session, 'original', 'Inspect the saved fixture')

    def emit(event):
        if event.get('type') == 'execution.event':
            execution.ingest(session, event['event'])

    observer = ExecutionEvents(session['id'], emit)
    observer.lifecycle({'type': 'input.delivered', 'source': 'user',
                        'input_id': 'original'})
    events = tool_records(sid=session['id'], arguments=arguments, result=result)
    for event in events:
        # Preserve the real hook's exact observation UUID in canonical data.
        event['data'] = observer.hook(session['id'], event['event'], event['data'])
    execution.finish(session)
    node, = session['execution']['nodes']
    assert node['turnId'] == 'original'
    assert session['execution']['turns'][0]['anchorMessageId'] == 'user'
    assert node['toolObservationId'] == events[-1]['data']['unified_tool_observation_id']
    return session, events, node


def test_stored_execution_retains_host_tool_placement_without_payloads():
    from amplifier_web.session_projection import stored_execution
    from amplifier_web.tool_observations import identity

    session, _, node = _host_placed_tool()
    assert identity(node['toolObservationId']) == node['toolObservationId']
    expected = {key: value for key, value in node.items()
                if key in _SAVED_TOOL_PLACEMENT_FIELDS}
    # Poison every body/hint/ref family, plus an unknown scalar, to distinguish
    # a metadata allowlist from a denylist of today's known payload fields.
    for field in ('input', 'output', 'error', 'summary', 'body', 'request',
                  'response', 'inputPreview', 'outputPreview', 'preview',
                  'actionPath', 'delegateRequest', 'delegateResponse',
                  'unknownScalar'):
        node[field] = 'BODY-MUST-NOT-BE-SAVED'
    for field in ('inputSummary', 'outcomeSummary', '_eventFields', 'inputDetail',
                  'outputDetail', 'errorDetail', 'delegateRequestDetail',
                  'delegateResponseDetail', 'toolDetail', 'exchange', 'failure',
                  'usage', 'routing', 'customMetadata'):
        node[field] = {'nested': ['BODY-MUST-NOT-BE-SAVED']}
    node['savedObservations'] = [{'input': 'BODY-MUST-NOT-BE-SAVED'}]
    original = copy.deepcopy(session['execution'])

    saved = stored_execution(session['execution'])

    assert saved['nodes'] == [expected]
    assert saved['turns'] == original['turns']
    assert saved['nodes'][0]['phase'] == 'completed'
    assert all(type(value) in (str, int, float, bool, type(None))
               for value in saved['nodes'][0].values())
    assert 'BODY-MUST-NOT-BE-SAVED' not in json.dumps(saved)
    assert session['execution'] == original
    assert stored_execution(saved) == saved


@pytest.mark.parametrize('case', [
    'missing-token', 'invalid-token', 'noncanonical-uuid', 'native-row',
    'canonical-row', 'raw-observation', 'missing-turn-id', 'unknown-turn-id',
    'missing-turn', 'native-turn', 'canonical-turn', 'missing-session-id',
    'missing-root-id', 'missing-call-id',
])
def test_stored_execution_rejects_unqualified_tool_placement(case):
    from amplifier_web.session_projection import stored_execution

    session, _, node = _host_placed_tool()
    tree = session['execution']
    if case == 'missing-token':
        node.pop('toolObservationId')
    elif case == 'invalid-token':
        node['toolObservationId'] = 'not-an-observation-uuid'
    elif case == 'noncanonical-uuid':
        node['toolObservationId'] = '{' + node['toolObservationId'] + '}'
    elif case == 'native-row':
        node['nativeHistory'] = True
    elif case == 'canonical-row':
        node['canonicalHistory'] = True
    elif case == 'raw-observation':
        node.update(observation=True, association='unverified', phase='recorded')
    elif case == 'missing-turn-id':
        node.pop('turnId')
    elif case == 'unknown-turn-id':
        node['turnId'] = 'not-a-host-turn'
    elif case == 'missing-turn':
        tree['turns'] = []
    elif case == 'native-turn':
        tree['turns'][0]['nativeHistory'] = True
    elif case == 'canonical-turn':
        tree['turns'][0]['canonicalHistory'] = True
    elif case == 'missing-session-id':
        node.pop('sessionId')
    elif case == 'missing-root-id':
        node.pop('rootSessionId')
    else:
        node.pop('toolCallId')
    original = copy.deepcopy(tree)

    saved = stored_execution(tree)

    assert saved['nodes'] == []
    assert tree == original


@pytest.mark.parametrize('field', ['parentId', 'label', 'startedAt', 'endedAt'])
def test_stored_tool_placement_never_persists_nested_metadata(field):
    from amplifier_web.session_projection import stored_execution

    session, _, node = _host_placed_tool()
    node[field] = {'hiddenBody': ['NESTED-BODY-MUST-NOT-BE-SAVED']}
    original = copy.deepcopy(session['execution'])

    saved = stored_execution(session['execution'])

    # Invalid metadata may be omitted or reject the row, but cannot become a
    # side channel for nested data under an otherwise permitted field name.
    assert all(set(row) <= _SAVED_TOOL_PLACEMENT_FIELDS
               and all(type(value) in (str, int, float, bool, type(None))
                       for value in row.values())
               for row in saved['nodes'])
    assert 'NESTED-BODY-MUST-NOT-BE-SAVED' not in json.dumps(saved)
    assert session['execution'] == original


@pytest.mark.asyncio
async def test_raw_saved_observations_never_mint_host_tool_placement(tmp_path, monkeypatch):
    from amplifier_web.session_projection import stored_execution

    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    try:
        # No host observer or accepted input exists for these raw records.
        write(path, tool_records())
        original = copy.deepcopy(session)
        saved = await owner.read(session['id'])
        group, = saved['items']
        assert group['observationWindow'] and group['phase'] == 'recorded'
        rows = owner.nodes(session['id'], group['id'])['items']
        assert len(rows) == 2
        assert all(row['observationWindow'] and row['association'] == 'unverified'
                   and row['phase'] == 'recorded' and not row.get('liveObservation')
                   for row in rows)
        assert session == original
        stored = stored_execution(session['execution'])
        assert stored['nodes'] == [] and stored['turns'] == []
    finally:
        await owner.close()


async def _read_exact_placed_fields(owner, session, node, expected, later_events):
    groups = owner.project(session)['execution']['segments']
    group, = [row for row in groups if row['turnId'] == 'original']
    assert group['anchorMessageId'] == 'user'
    page = owner.live_pages.read(session, group['id'])
    row, = page['items']
    assert row['id'] == node['id'] and row['turnId'] == 'original'
    assert row['toolObservationId'] == node['toolObservationId']
    assert row['phase'] == node['phase']
    ref = row['toolDetail']
    first = await owner.read_tool(session['id'], ref)
    result = first
    if later_events:
        assert first['before'] is not None
        assert first['item']['contentAvailability']['status'] == 'not-loaded'
        assert all(field + 'Detail' not in first['item'] for field in expected)
        result = await owner.read_tool(session['id'], {
            **ref, 'lookupKey': first['lookupKey'], 'before': first['before']})
    assert result['before'] is None
    assert result['item']['contentAvailability']['status'] == 'linked'
    assert result['item']['toolDetail'] == ref
    assert result['item']['id'] == node['id']
    assert result['item']['turnId'] == 'original'
    assert result['item']['toolObservationId'] == node['toolObservationId']
    assert result['item']['startedAt'] == node['startedAt']
    assert result['item']['endedAt'] == node['endedAt']
    window_reads = [call for call in owner.projector.inputs
                    if call['operation'] == 'window']
    assert len(window_reads) == (2 if later_events else 1)

    refs = {}
    for field, full_text in expected.items():
        detail = result['item'][field + 'Detail']
        refs[field] = copy.deepcopy(detail)
        assert detail['id'] != node['id']
        assert detail['windowKey'].startswith('open-work:')
        assert owner._page(session['id'], detail['windowKey'])['_openScope']['id'] == node['id']
        assert detail['sessionId'] == session['id']
        assert detail['field'] == field and detail['digestKind'] == 'event-field-v1'
        assert detail['digest']
        assert len(full_text) > 16384 and len(full_text.encode('utf-8')) > 16384
        chunks, offset = [], 0
        for _ in range(8):
            response = await owner.read_text(session['id'], {
                **detail, 'offset': offset, 'limit': 16384})
            assert response['total'] == len(full_text)
            assert 0 < len(response['value']) <= 16384
            assert response['value'] == full_text[offset:offset + 16384]
            chunks.append(response['value'])
            next_offset = response['nextOffset']
            if next_offset is None:
                break
            assert next_offset == offset + len(response['value']) > offset
            offset = next_offset
        else:
            pytest.fail('The bounded full-field read did not reach its end')
        assert len(chunks) > 1
        assert ''.join(chunks) == full_text
        assert detail == refs[field], 'Paging must preserve the complete issued selector'
    return refs


@pytest.mark.asyncio
@pytest.mark.parametrize('later_events', [0, 140])
async def test_stored_tool_placement_reopens_exact_paged_unicode_fields(
        tmp_path, monkeypatch, later_events):
    """JSON saved-view transform plus new owners, not a full AppService restart."""
    from amplifier_web import execution
    from amplifier_web.execution_events import ExecutionEvents
    from amplifier_web.session_projection import stored_execution
    from amplifier_web.work_windows import WorkWindows, source_identity
    from test_work_windows import FakeProjector

    owner, session, path, service = fixture(tmp_path, monkeypatch, count=0)
    reopened_owner = None
    try:
        arguments = {'command': '入力-é-𝄞\n' * 4096 + 'INPUT-END'}
        output = {'success': True, 'output': {
            'stdout': '結果-ø-𐍈\n' * 4096 + 'OUTPUT-END', 'returncode': 0}}
        _, events, node = _host_placed_tool(session, arguments=arguments, result=output)
        original_node = copy.deepcopy(node)
        # A later real host turn must not pull the original observed tool forward.
        session['messages'].append({
            'id': 'later-user', 'inputId': 'later', 'role': 'user',
            'text': 'A later fixture request',
            'createdAt': session['execution']['turns'][0]['endedAt']})
        execution.ensure_turn(session, 'later', 'A later fixture request')
        execution.finish(session)
        assert session['execution']['currentTurnId'] == 'later'
        # Later capture-only observations force an older lookup page. They are
        # deliberately not admitted as resident host rows or bound to any turn.
        later_observer = ExecutionEvents(session['id'], lambda event: None)
        later_observer.lifecycle({'type': 'input.delivered', 'source': 'user',
                                  'input_id': 'later'})
        for index in range(later_events):
            event = tool_records(call='later-' + str(index), sid=session['id'],
                                 result='unrelated saved result')[-1]
            event['timestamp'] = 10 + index
            event['data'] = later_observer.hook(session['id'], event['event'], event['data'])
            events.append(event)
        write(path, events)
        canonical_bytes = path.read_bytes()
        original = copy.deepcopy(session)
        expected = {field: json.dumps(value, ensure_ascii=False, indent=2)
                    for field, value in [('input', arguments), ('output', output)]}

        saved_tree = stored_execution(session['execution'])
        saved_node, = saved_tree['nodes']
        assert saved_node == {key: value for key, value in original_node.items()
                              if key in _SAVED_TOOL_PLACEMENT_FIELDS}
        assert saved_tree['turns'] == original['execution']['turns']
        warm_refs = await _read_exact_placed_fields(
            owner, session, original_node, expected, later_events)
        assert session == original
        await owner.close()

        # Recreate both the session owner and WorkWindows without reusing a
        # resident page, field cache, selector lease or projector instance.
        reopened = json.loads(json.dumps({**session, 'execution': saved_tree}))
        reopened_service = copy.copy(service)
        reopened_service.instance_id = 'reopened-host'
        reopened_service.projections = copy.copy(service.projections)
        reopened_service.projections.detail_bodies = {}
        def reopened_session(identity, hydrate=False):
            assert identity == reopened['id']
            return reopened
        reopened_service._session = reopened_session
        reopened_owner = WorkWindows(reopened_service)
        reopened_owner.projector = FakeProjector(service.data_dir)
        assert source_identity(reopened) == source_identity(session)
        assert reopened['execution']['nodes'][0]['turnId'] == 'original'
        assert reopened['execution']['currentTurnId'] == 'later'
        before_reads = copy.deepcopy(reopened)
        with pytest.raises(ValueError, match='expired'):
            await reopened_owner.read_text(reopened['id'], warm_refs['input'])
        fresh_refs = await _read_exact_placed_fields(
            reopened_owner, reopened, original_node, expected, later_events)
        for field in expected:
            assert fresh_refs[field]['windowKey'] != warm_refs[field]['windowKey']
            assert fresh_refs[field]['id'] == warm_refs[field]['id']
            assert fresh_refs[field]['digest'] == warm_refs[field]['digest']
        assert reopened == before_reads
        assert session == original
        assert path.read_bytes() == canonical_bytes
    finally:
        await owner.close()
        if reopened_owner is not None:
            await reopened_owner.close()
