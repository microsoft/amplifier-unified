import asyncio
import copy
import json

import pytest

from test_work_windows import fixture
from test_tool_window_records import live_tool, tool_records, write, observed


def unrelated(count=140):
    return [{'event':'tool:post', 'timestamp':10 + i, 'data':{'session_id':'s','tool_call_id':'other-'+str(i),'tool_name':'other','result':'unrelated'}} for i in range(count)]


def opened(owner, session):
    return owner.live_pages.read(session, 't@user')['items'][0]['toolDetail']


@pytest.mark.asyncio
async def test_one_bounded_older_page_per_user_request_without_hydrating_history(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    events=tool_records()
    live_tool(session,events=events)
    write(path,events+unrelated())
    original = copy.deepcopy(session)
    ref = opened(owner, session)
    first = await owner.read_tool('s', ref)
    assert first['before'] and first['item']['contentAvailability']['status'] == 'not-loaded'
    assert len(owner.projector.inputs) == 1
    assert first['coverage']['scannedBytes'] <= 4*1024*1024
    result = await owner.read_tool('s', {**ref, 'lookupKey':first['lookupKey'], 'before':first['before']})
    assert result['before'] is None and len(owner.projector.inputs) == 2
    assert result['item']['toolDetail'] == ref
    assert 'saved-command' in (await owner.read_text('s', result['item']['inputDetail']))['value']
    assert session == original
    await owner.close()


@pytest.mark.asyncio
async def test_one_long_running_call_can_join_exact_records_across_two_requested_pages(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    node=live_tool(session)
    node.pop('toolObservationId',None)
    start,end=tool_records()
    write(path,[start,*unrelated(),end])
    ref = opened(owner, session)
    first = await owner.read_tool('s', ref)
    assert first['before'] and 'outputDetail' not in first['item']
    assert first['item']['contentAvailability']['status']=='unverified'
    assert len(first['item']['savedObservations'])==1
    second = await owner.read_tool('s', {**ref, 'lookupKey':first['lookupKey'], 'before':first['before']})
    assert len(second['item']['savedObservations'])==2
    assert 'outputDetail' not in second['item']
    assert 'saved-output' in (await owner.read_text('s', second['item']['savedObservations'][-1]['outputDetail']))['value']
    await owner.close()


@pytest.mark.asyncio
async def test_exact_admitted_child_log_lookup_never_enumerates_other_children(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    events=tool_records(sid='child')
    live_tool(session,sid='child',events=events)
    child = path.parents[2] / 'child/context-intelligence/events.jsonl'
    child.parent.mkdir(parents=True)
    write(child,events)
    original = copy.deepcopy(session)
    def forbidden(*args, **kwargs):
        raise AssertionError('No child directory search is allowed')
    monkeypatch.setattr(type(path), 'iterdir', forbidden)
    result = await owner.read_tool('s', opened(owner, session))
    assert owner.projector.inputs[0]['session']['nativeIdentity'] == 'child'
    assert 'saved-command' in (await owner.read_text('s', result['item']['inputDetail']))['value']
    assert session == original
    await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['row-replaced','row-reordered','call-changed','native-rebind','owner-replaced','group-reanchored','observation-changed'])
async def test_changed_scope_during_lookup_never_publishes_wrong_saved_fields(tmp_path, monkeypatch, change):
    owner, session, path, service = fixture(tmp_path, monkeypatch, count=0)
    node=observed(session,path)
    ref = opened(owner, session)
    owner.projector.gate = asyncio.Event()
    pending = asyncio.create_task(owner.read_tool('s', ref))
    await asyncio.sleep(0)
    if change == 'row-replaced': session['execution']['nodes'][0] = {**node,'id':'other'}
    elif change == 'row-reordered': session['execution']['nodes'].insert(0, {'id':'other'})
    elif change == 'call-changed': node['toolCallId'] = 'other'
    elif change == 'native-rebind': session['nativeIdentity'] = 'other'
    elif change == 'group-reanchored': node['anchorMessageId']='other'
    elif change == 'observation-changed': node['toolObservationId']='11111111-1111-4111-8111-111111111111'
    else: service._session = lambda *args, **kwargs: copy.deepcopy(session)
    owner.projector.gate.set()
    with pytest.raises(ValueError): await pending
    assert not owner.pages
    await owner.close()


@pytest.mark.asyncio
async def test_legacy_duplicate_terminal_in_another_page_remains_a_separate_observation(tmp_path, monkeypatch):
    owner, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    node=live_tool(session)
    node.pop('toolObservationId',None)
    start,end=tool_records()
    write(path,[start,end,*unrelated(),end])
    ref = opened(owner, session)
    first = await owner.read_tool('s', ref)
    second = await owner.read_tool('s', {**ref,'lookupKey':first['lookupKey'],'before':first['before']})
    assert second['item']['contentAvailability']['status']=='unverified'
    assert len(second['item']['savedObservations'])==3
    assert 'outputDetail' not in second['item']
    await owner.close()


@pytest.mark.asyncio
async def test_saved_lookup_fields_expire_on_rebind_eviction_and_cross_client(tmp_path, monkeypatch):
    from contextvars import ContextVar
    from types import SimpleNamespace
    owner, session, path, service = fixture(tmp_path, monkeypatch, count=0)
    service.clients = SimpleNamespace(current=ContextVar('client',default='one'))
    observed(session,path)
    result = await owner.read_tool('s', opened(owner, session))
    field = result['item']['inputDetail']
    service.clients.current.set('two')
    with pytest.raises(ValueError): await owner.read_text('s', field)
    service.clients.current.set('one')
    assert 'saved-command' in (await owner.read_text('s', field))['value']
    owner.pages.clear()
    assert 'saved-command' in (await owner.read_text('s', field))['value']
    owner.live_pages.groups.clear()
    with pytest.raises(ValueError, match='expired'): await owner.read_text('s', field)
    await owner.close()


@pytest.mark.asyncio
async def test_older_lookup_never_claims_a_previous_same_call_is_the_exact_current_observation(tmp_path,monkeypatch):
    owner,session,path,_=fixture(tmp_path,monkeypatch,count=0)
    old=tool_records(arguments={'command':'old command'})
    live_tool(session,events=old)
    live_tool(session,events=tool_records(arguments={'command':'new command'}))
    write(path,old+unrelated())
    ref=opened(owner,session)
    first=await owner.read_tool('s',ref)
    result=await owner.read_tool('s',{**ref,'lookupKey':first['lookupKey'],'before':first['before']})
    assert result['item']['contentAvailability']['status']=='unrecorded'
    assert 'inputDetail' not in result['item'] and 'savedObservations' not in result['item']
    await owner.close()

@pytest.mark.asyncio
async def test_nested_delegate_header_keeps_exact_admitted_parent_lookup(tmp_path,monkeypatch):
    owner,session,path,_=fixture(tmp_path,monkeypatch,count=0)
    parent=observed(session,path,name='delegate',arguments={'instruction':'Nested saved request'},
        result={'success':True,'output':'Nested saved response'})
    worker={'id':'worker:child:call','kind':'worker','sessionId':'child','parentId':parent['id'],
        'turnId':'t','phase':'completed','label':'reviewer','startedAt':1,'endedAt':2,'liveObservation':True}
    child={'id':'tool:child:nested','kind':'tool','tool':'bash','label':'bash',
        'toolCallId':'nested','sessionId':'child','parentId':worker['id'],'turnId':'t',
        'phase':'completed','startedAt':1,'endedAt':2,'liveObservation':True}
    session['execution']['nodes']=[parent,worker,child]
    rows=owner.live_pages.read(session,'t@user')['items']
    nested=next(row for row in rows if row['id']==child['id'])
    direct=next(row for row in rows if row['id']==worker['id'])
    assert nested['delegate']['exchange']['toolDetail']==direct['exchange']['toolDetail']
    result=await owner.read_tool('s',nested['delegate']['exchange']['toolDetail'])
    assert result['item']['exchange']['delegateRequest']=='Nested saved request'
    assert result['item']['exchange']['delegateResponse']=='Nested saved response'
    await owner.close()

@pytest.mark.asyncio
async def test_existing_canonical_tool_keeps_its_own_saved_payloads_and_reader(tmp_path,monkeypatch):
    from amplifier_web.event_log_view import EventIndex
    owner,session,path,_=fixture(tmp_path,monkeypatch,count=0)
    events=tool_records(arguments={'command':'legacy paired input '*80+'LEGACY-END'})
    events[-1]['data'].pop('tool_input')  # Supported old producer: input only in pre.
    write(path,events)
    index=EventIndex(path,'s');index.refresh()
    node=next(iter(index.nodes.values()))
    assert node['canonicalHistory']
    node.update(liveObservation=True,turnId='t')
    session['execution'].update(nodes=[node],turns=[{'id':'t','anchorMessageId':'user','phase':'completed','startedAt':1,'endedAt':2}])
    original=copy.deepcopy(session)
    await owner.read('s')
    row=owner.live_pages.read(session,'t@user')['items'][0]
    assert row['input'].startswith('{')
    assert row['inputDetail']['windowKey'].startswith('resident-work:')
    assert 'LEGACY-END' in owner.live_pages.read_text(session,row['inputDetail'])['value']
    assert 'toolDetail' not in row and 'savedObservations' not in row
    assert session==original
    await owner.close()
