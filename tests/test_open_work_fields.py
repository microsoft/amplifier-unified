"""An open exact detail survives bounded recent-window turnover, not identity changes."""
import asyncio
import copy
import json

import pytest

from test_work_windows import fixture
from test_tool_window_records import live_tool, tool_records
from test_provider_window_records import live_call, recorded_call


def append(path, count, start=0):
    with path.open('a') as stream:
        for i in range(start, start + count):
            stream.write(json.dumps({'event': 'tool:post', 'timestamp': 3+i,
                'data': {'tool_call_id': f'new-{i}', 'tool_name': 'other', 'result': 'newer work'}}) + '\n')


async def opened(tmp_path, monkeypatch):
    windows, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    native = tool_records()
    tool = live_tool(session, events=native)
    live_call(session)
    session['execution']['nodes'].insert(0, tool)
    path.write_text(''.join(json.dumps(row) + '\n' for row in native + recorded_call()))
    await windows.read('s')
    page = windows.live_pages.read(session, 't@user')
    return windows, session, path, {row['id']: row for row in page['items']}


@pytest.mark.asyncio
async def test_open_tool_and_model_fields_survive_cutoff_and_latest_page_eviction(tmp_path, monkeypatch):
    windows, session, path, first = await opened(tmp_path, monkeypatch)
    original = copy.deepcopy(session)
    tool, model = first['tool:s:call'], first['llm:host']
    refs = [tool['inputDetail'], tool['outputDetail'], model['requestDetail'], model['responseDetail']]
    expected = [await windows.read_text('s', ref) for ref in refs]
    current = first
    for i, count in enumerate([99, 101, *([1] * 10)]):
        append(path, count, start=i*1000)
        await windows.read('s')
        result = windows.live_pages.sync(session, 't@user', {key: row['detailVersion'] for key,row in current.items()})
        current.update((row['id'], row) for row in result['items'])
        assert current['tool:s:call']['inputDetail'] == refs[0]
        assert current['tool:s:call']['outputDetail'] == refs[1]
        assert current['llm:host']['requestDetail'] == refs[2]
        assert current['llm:host']['responseDetail'] == refs[3]
    assert [await windows.read_text('s', ref) for ref in refs] == expected
    assert session == original and len(windows.pages) <= 8
    await windows.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['token', 'source', 'position', 'client', 'group', 'owner'])
async def test_retained_field_refuses_changed_exact_admission(tmp_path, monkeypatch, change):
    windows, session, path, rows = await opened(tmp_path, monkeypatch)
    ref = rows['tool:s:call']['inputDetail']
    if change == 'token': session['execution']['nodes'][0]['toolObservationId'] = '11111111-1111-4111-8111-111111111111'
    elif change == 'source': session['nativeIdentity'] = 'other'
    elif change == 'position': session['execution']['nodes'].reverse()
    elif change == 'client': monkeypatch.setattr(windows.live_pages, '_client', lambda: 'other-client')
    elif change == 'group': windows.live_pages.groups.clear()
    else:
        replacement = copy.deepcopy(session)
        monkeypatch.setattr(windows, '_owner', lambda identity: replacement)
    with pytest.raises(ValueError): await windows.read_text('s', ref)
    await windows.close()


@pytest.mark.asyncio
async def test_retained_field_rechecks_bytes_and_does_not_authorize_forged_ref(tmp_path, monkeypatch):
    windows, session, path, rows = await opened(tmp_path, monkeypatch)
    ref = rows['tool:s:call']['inputDetail']
    with pytest.raises(ValueError): await windows.read_text('s', {**ref, 'digest': 'forged'})
    with pytest.raises(ValueError): await windows.read_text('s', {**ref, 'id': rows['llm:host']['requestDetail']['id']})
    path.write_bytes(path.read_bytes().replace(b'printf saved-command', b'printf other-command'))
    with pytest.raises(ValueError): await windows.read_text('s', ref)
    await windows.close()


@pytest.mark.asyncio
async def test_changed_provider_attempt_cannot_inherit_earlier_response(tmp_path, monkeypatch):
    windows, session, path, rows = await opened(tmp_path, monkeypatch)
    old = rows['llm:host']['responseDetail']
    path.write_text(json.dumps(recorded_call(request='new-attempt')[0]) + '\n')
    await windows.read('s')
    result = windows.live_pages.sync(session, 't@user', {key: row['detailVersion'] for key,row in rows.items()})
    model = next(row for row in result['items'] if row['id'] == 'llm:host')
    assert 'requestDetail' in model and 'responseDetail' not in model
    with pytest.raises(ValueError): await windows.read_text('s', old)
    await windows.close()


@pytest.mark.asyncio
async def test_delegate_fields_keep_the_same_open_receipt(tmp_path, monkeypatch):
    windows, session, path, _ = fixture(tmp_path, monkeypatch, count=0)
    native = tool_records(name='delegate', arguments={'instruction':'Inspect this exact request '*40},
                          result={'success':True,'output':'Exact saved answer '*40})
    live_tool(session, name='delegate', events=native)
    path.write_text(''.join(json.dumps(row)+'\n' for row in native))
    await windows.read('s')
    first=windows.live_pages.read(session,'t@user')['items'][0]
    refs=[first['exchange'][field+'Detail'] for field in ('delegateRequest','delegateResponse')]
    append(path,201)
    await windows.read('s')
    result=windows.live_pages.sync(session,'t@user',{first['id']:first['detailVersion']})
    item=next(iter(result['items']),first)
    assert [item['exchange'][field+'Detail'] for field in ('delegateRequest','delegateResponse')]==refs
    assert 'exact request' in (await windows.read_text('s',refs[0]))['value']
    assert 'saved answer' in (await windows.read_text('s',refs[1]))['value']
    await windows.close()


@pytest.mark.asyncio
async def test_group_bound_expires_its_retained_fields(tmp_path, monkeypatch):
    from amplifier_web import open_work_fields, resident_work_pages
    windows,session,path,rows=await opened(tmp_path,monkeypatch)
    entry=next(iter(windows.live_pages.groups.values()))
    assert len(entry['fields'])==4<=open_work_fields.MAX_FIELDS
    assert 'PRIVATE REQUEST' in (await windows.read_text('s',rows['llm:host']['requestDetail']))['value']
    original=rows['llm:host']['requestDetail']
    monkeypatch.setattr(resident_work_pages,'MAX_GROUPS',1)
    # A different admitted group retires the first group's entire tiny lease.
    session['messages'].append({'id':'next','role':'user','text':'Other work','createdAt':10})
    session['execution']['turns'].append({'id':'next','anchorMessageId':'next','phase':'completed','startedAt':10})
    session['execution']['nodes'].append({'id':'other','kind':'tool','turnId':'next','startedAt':11,'phase':'completed'})
    windows.live_pages.read(session,'next@next')
    assert len(windows.live_pages.groups)==1
    with pytest.raises(ValueError,match='expired'): await windows.read_text('s',original)
    await windows.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['tool','provider'])
async def test_new_ambiguity_invalidates_retained_fields(tmp_path,monkeypatch,kind):
    windows,session,path,rows=await opened(tmp_path,monkeypatch)
    records=[json.loads(line) for line in path.read_text().splitlines()]
    duplicate=records[1 if kind=='tool' else 2]
    with path.open('a') as stream:stream.write(json.dumps(duplicate)+'\n')
    await windows.read('s')
    windows.live_pages.sync(session,'t@user',{key:row['detailVersion'] for key,row in rows.items()})
    ref=rows['tool:s:call']['inputDetail'] if kind=='tool' else rows['llm:host']['requestDetail']
    with pytest.raises(ValueError):await windows.read_text('s',ref)
    await windows.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['token','owner','evict'])
async def test_changed_admission_during_native_field_read_is_rechecked(tmp_path,monkeypatch,change):
    windows,session,path,rows=await opened(tmp_path,monkeypatch)
    ref=rows['tool:s:call']['inputDetail']
    windows.projector.gate=asyncio.Event()
    pending=asyncio.create_task(windows.read_text('s',ref))
    await asyncio.sleep(0)
    if change=='token':session['execution']['nodes'][0]['toolObservationId']='11111111-1111-4111-8111-111111111111'
    elif change=='owner':
        other=copy.deepcopy(session)
        windows.service._session=lambda identity,hydrate=False:other
    else:windows.live_pages.groups.clear()
    windows.projector.gate.set()
    with pytest.raises(ValueError):await pending
    await windows.close()


@pytest.mark.asyncio
async def test_more_than_200_fields_keep_stable_leases_without_revision_thrash(tmp_path,monkeypatch):
    from amplifier_web.open_work_fields import MAX_FIELDS
    windows,session,path,_=fixture(tmp_path,monkeypatch,count=0)
    nodes,records=[],[]
    for index in range(60):
        native=tool_records(call=f'delegate-{index}',name='delegate',
            arguments={'instruction':f'Request {index}'},result={'success':True,'output':f'Answer {index}'})
        nodes.append(live_tool(session,name='delegate',events=native))
        records.extend(native[-1:])
    session['execution']['nodes']=nodes
    path.write_text(''.join(json.dumps(row)+'\n' for row in records))
    await windows.read('s')
    first=windows.live_pages.read(session,'t@user')['items']
    entry=next(iter(windows.live_pages.groups.values()))
    assert 200<len(entry['fields'])<=MAX_FIELDS
    known={row['id']:row['detailVersion'] for row in first}
    refs=[row['exchange']['delegateResponseDetail'] for row in first]
    for count in [201,1,1]:
        append(path,count,start=count)
        await windows.read('s')
        result=windows.live_pages.sync(session,'t@user',known)
        for row in result['items']:
            before=next(value for value in first if value['id']==row['id'])
            assert row['exchange']['delegateResponseDetail']==before['exchange']['delegateResponseDetail']
            known[row['id']]=row['detailVersion']
        # A second reconciliation of identical exact row facts does no work.
        assert windows.live_pages.sync(session,'t@user',known)['items']==[]
    for index,ref in enumerate(refs):
        assert (await windows.read_text('s',ref))['value']==f'Answer {index}'
    assert all(key[0] in entry['ids'] for key in entry['fields'])
    await windows.close()
