import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppService, AppError
from amplifier_web.smart_tools import SmartToolsManager
from amplifier_web.observation_contract import validate_result, local_schema
from amplifier_web.observation_store import ObservationStore
from amplifier_web.runtime_controls import override_path

FIXTURE = Path(__file__).parent / 'fixtures' / 'mcp_observation_server.py'


def record(path, status='pending', revision='r1'):
    path.write_text(json.dumps({'status': status, 'revision': revision, 'summary': 'Controlled exact record',
        'evidence': [{'uri': 'fixture://one/result', 'revision': revision, 'digest': 'a'*64}] if status == 'actionable' else []}))


async def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    runtime = SimpleNamespace(close=AsyncMock(), observation_input=AsyncMock(return_value={'accepted': True, 'inputId': 'receipt'}))
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    await app.dispatch('session.create', {'title': 'Observer target'})
    sid = app._session()['id']
    controls = override_path(sid).with_name('control-state.json')
    controls.parent.mkdir(parents=True, exist_ok=True)
    controls.write_text(json.dumps({'task': {'id': 'task', 'revision': 1, 'status': 'active', 'questionIds': []}}))
    app.smart_tools = SmartToolsManager(app)
    manager = app.smart_tools
    installed = manager.root / 'installs' / ('a'*24)
    installed.mkdir(parents=True)
    code = installed / 'observer.py'; code.write_bytes(FIXTURE.read_bytes())
    manager.state['installations'].append({'id': 'a'*24, 'status': 'installed', 'commit': 'qualified-revision'})
    data = tmp_path / 'source'; data.mkdir(); record(data / 'record.json')
    await manager.configure({'id': 'observer', 'name': 'Fixture', 'command': sys.executable, 'args': [str(code), str(data)], 'installationId': 'a'*24})
    await manager.connect('observer')
    descriptor = {'installationId': 'a'*24, 'connectionId': 'observer', 'toolName': 'fixture_observe', 'implementationRevision': 'qualified-revision',
        'requestArgument': 'observation', 'targetSchema': {'const': {'record': 'one', 'owner': 'fixture'}}, 'scopeSchema': {'const': {'read': 'one'}},
        'artifacts': [{'path': 'observer.py', 'sha256': hashlib.sha256(code.read_bytes()).hexdigest()}],
        'qualification': {'kind': 'reviewed-trusted-read', 'providerFree': True, 'concurrentReadSafe': True, 'evidence': [{'uri': 'test://review', 'digest': 'a'*64}]}}
    result = (await app.dispatch('observation.qualify', {'sessionId': sid, 'descriptor': descriptor}))['result']
    now = [1800000000.0]; app.observations.clock = lambda: now[0]
    args = {'sessionId': sid, 'observerId': result['observer']['id'], 'target': {'record': 'one', 'owner': 'fixture'}, 'scope': {'read': 'one'}, 'sourceId': 'fixture://one',
        'args': {}, 'intervalSeconds': 15, 'durationSeconds': 120, 'readTimeoutSeconds': 3}
    return app, runtime, now, sid, args, data, controls, descriptor


async def create(app, args, request='arm', origin='ui', **extra):
    preview = (await app.dispatch('observation.preview', args, origin=origin))['result']
    return (await app.dispatch('observation.create', {**args, 'previewHash': preview['previewHash'], 'requestId': request, **extra}, origin=origin))['result']['watch']


async def test_actual_stdio_three_pending_reads_are_quiet_and_terminal_one_shot(tmp_path, monkeypatch):
    app, runtime, now, sid, args, data, _, _ = await fixture(tmp_path, monkeypatch)
    try:
        app.history.ensure_loaded = AsyncMock(side_effect=AssertionError('Pending must not load history'))
        app.management = SimpleNamespace(ensure_runtime=AsyncMock(side_effect=AssertionError('No provider prep')), provider_catalog=SimpleNamespace(close=AsyncMock()), setup_manager=None)
        watch = await create(app, args)
        before = copy.deepcopy(app.state)
        for _ in range(3):
            await app.observations.tick(); now[0] += 16
        assert app.state == before
        assert len(app.observations.store.rows('run', sid)) == 3
        runtime.observation_input.assert_not_awaited()
        app.history.ensure_loaded.assert_not_awaited()
        record(data / 'record.json', 'actionable', 'result-1')
        await app.observations.tick(); now[0] += 16; await app.observations.tick()
        assert runtime.observation_input.await_count == 1
        assert len(app.observations.store.rows('outbox')) == 1
        assert app.observations.store.get('watch', watch['id'])['status'] == 'ended'
        assert app.state == before  # Fake terminal handoff emits no events in this test.
    finally: await app.close()


async def test_exact_retry_before_preview_does_not_rearm_and_conflict_rejected(tmp_path, monkeypatch):
    app, _, now, sid, args, *_ = await fixture(tmp_path, monkeypatch)
    try:
        preview = (await app.dispatch('observation.preview', args))['result']
        payload = {**args, 'previewHash': preview['previewHash'], 'requestId': 'same'}
        watch = (await app.dispatch('observation.create', payload))['result']['watch']
        await app.dispatch('observation.pause', {'sessionId': sid, 'id': watch['id'], 'expectedRevision': 1, 'requestId': 'pause'})
        now[0] += 10000
        result = (await app.dispatch('observation.create', {**payload, 'previewHash': 'stale-ignored-on-exact-retry'}))['result']
        assert result['duplicate'] and result['watch']['status'] == 'paused'
        assert result['watch']['expiresAt'] == watch['expiresAt']
        with pytest.raises(AppError, match='different intent'):
            await app.dispatch('observation.create', {**payload, 'durationSeconds': 300})
    finally: await app.close()


@pytest.mark.parametrize('change', ['task', 'stop', 'execution', 'pause', 'cancel', 'expiry'])
async def test_read_return_race_cannot_restore_authority(tmp_path, monkeypatch, change):
    app, runtime, now, sid, args, data, control, _ = await fixture(tmp_path, monkeypatch)
    try:
        if change == 'expiry': args['durationSeconds'] = 30
        watch = await create(app, args)
        record(data / 'record.json', 'actionable')
        original = app.smart_tools.call_tool
        async def delayed(*a, **kw):
            result = await original(*a, **kw)
            if change == 'task': control.write_text(json.dumps({'task': {'id': 'task', 'revision': 2, 'status': 'active'}}))
            elif change == 'stop': app._session(sid)['interruptionRevision'] = 1
            elif change == 'execution': app._session(sid)['executionRevision'] = 1
            elif change == 'expiry': now[0] += 31
            else:
                await app.dispatch('observation.'+change, {'sessionId': sid, 'id': watch['id'], 'expectedRevision': 1, 'requestId': change})
            return result
        app.smart_tools.call_tool = delayed
        await app.observations.tick()
        if change == 'expiry':
            assert app.observations.store.rows('outbox')[0]['outcome']['status'] == 'expired'
            assert runtime.observation_input.await_count == 1
        else: runtime.observation_input.assert_not_awaited()
        assert app.observations.store.rows('run')[0]['phase'] == 'discarded'
    finally: await app.close()


async def test_operator_boundary_human_provenance_and_cross_session_scope(tmp_path, monkeypatch):
    app, _, _, sid, args, _, _, descriptor = await fixture(tmp_path, monkeypatch)
    try:
        with pytest.raises(AppError, match='operator'):
            await app.app_bridge('dispatch', {'action': 'observation.qualify', 'args': {'descriptor': descriptor}}, sid)
        with pytest.raises(AppError, match='human request'):
            await create(app, args, origin='agent', sourceMessageId='invented')
        app._session(sid)['messages'].append({'id': 'human', 'role': 'user', 'inputOrigin': 'ui', 'text': 'Watch this exact record for two minutes'})
        watch = await create(app, args, origin='agent', sourceMessageId='human')
        assert watch['authorization']['messageId'] == 'human'
        with pytest.raises(AppError, match='calling conversation'):
            await app.app_bridge('dispatch', {'action': 'observation.read', 'args': {'sessionId': 'other', 'id': watch['id']}}, sid)
        with pytest.raises(AppError, match='internal'):
            await app.dispatch('runtime.control', {'sessionId': sid, 'operation': 'observation.submit', 'args': {}})
    finally: await app.close()


@pytest.mark.parametrize('field', ['target', 'source', 'schema', 'code', 'account', 'configuration'])
async def test_wrong_bindings_fail_before_or_after_read_without_handoff(tmp_path, monkeypatch, field):
    app, runtime, now, sid, args, data, _, _ = await fixture(tmp_path, monkeypatch)
    try:
        await create(app, args)
        manager = app.smart_tools
        if field == 'schema': manager.schemas['observer'][0]['inputSchema'] = {'type': 'object'}
        elif field == 'code': (manager.root / 'installs' / ('a'*24) / 'observer.py').write_text('changed')
        elif field == 'configuration': manager._server('observer')['args'].append('changed')
        elif field == 'account': manager._server('observer')['transport'] = 'streamable-http'
        else:
            original = manager.call_tool
            async def bad(*a, **kw):
                result = await original(*a, **kw)
                result['structuredContent'][field] = {} if field == 'target' else {'id': 'other', 'revision': 'r1'}
                return result
            manager.call_tool = bad
        await app.observations.tick()
        if field in {'target', 'source'}:
            outcome = app.observations.store.rows('outbox')[0]['outcome']
            assert outcome['status'] == 'observation_failed' and outcome['source']['revision'] is None
        else: runtime.observation_input.assert_not_awaited()
    finally: await app.close()


def test_schema_is_local_only_and_failure_does_not_claim_fresh_revision():
    for schema in ({'$ref':'https://example.test/schema'}, {'$id':'https://example.test/schema'}, {'$dynamicRef':'#/a'}):
        with pytest.raises(ValueError): local_schema(schema)
    value = {'contract':'amplifier.observation.v1', 'status':'observation_failed', 'target':{}, 'source':{'id':'source','revision':None}, 'semanticKey':None,'observedAt':1,'summary':'Read failed','evidence':[]}
    assert validate_result(value, {'target':{}, 'sourceId':'source'}) == value
    value['status'] = 'pending'
    with pytest.raises(ValueError): validate_result(value, {'target':{}, 'sourceId':'source'})


def test_owner_loss_read_retry_but_unknown_admission_never_replays(tmp_path):
    path = tmp_path / 'observation.db'
    first = ObservationStore(path); second = ObservationStore(path)
    try:
        first.put('watch', {'id':'w','sessionId':'s','revision':1,'status':'active','nextDue':0,'sequence':0,'intervalSeconds':15,'expiresAt':10000})
        assert first.acquire(0) and not second.acquire(1)
        run = first.claim('w', 0)
        assert second.acquire(61)
        assert second.get('run', run['id'])['phase'] == 'abandoned_read'
        resumed = second.claim('w', 61)
        assert resumed['id'] == run['id']  # A read retry retains its occurrence identity.
        second.commit(resumed, {'status':'actionable','semanticKey':'same'}, 62)
        out = second.rows('outbox')[0]
        second.outbox_phase(out['id'], ['pending'], 'submitting', 63)
        assert first.acquire(122)
        assert first.get('outbox', out['id'])['phase'] == 'unknown'
        with first.transaction(): first.terminal(first.get('watch','w'), {'status':'actionable','semanticKey':'same'}, 123)
        assert len(first.rows('outbox')) == 1
    finally: first.close(); second.close()


async def test_forbidden_server_roots_request_never_becomes_pending_or_host_assistance(tmp_path,monkeypatch):
    app,runtime,_,sid,args,data,_,_ = await fixture(tmp_path,monkeypatch)
    try:
        watch = await create(app,args)
        value = json.loads((data/'record.json').read_text()); value['request']='roots'; (data/'record.json').write_text(json.dumps(value))
        await app.observations.tick()
        assert app.observations.store.get('watch',watch['id'])['status'] in {'needs_review','ended'}
        if runtime.observation_input.await_count:
            assert app.observations.store.rows('outbox')[0]['outcome']['status']=='observation_failed'
        assert all(r['phase'] != 'pending' for r in app.observations.store.rows('run'))
    finally: await app.close()


async def test_authentication_http_scope_and_pending_use_real_sdk(tmp_path,monkeypatch):
    from test_connector_lifecycle import authorize, wait_for
    from mcp_oauth_server import Fixture
    from typing import Any
    app,runtime,now,sid,args,_,_,descriptor = await fixture(tmp_path,monkeypatch)
    remote = Fixture(identity=True)
    @remote.mcp.tool(structured_output=True)
    def exact_observe(observation: dict[str,Any]) -> dict[str,Any]:
        assert observation['target'] == {'record':'one','owner':'fixture'}
        assert observation['scope'] == {'read':'one'}
        remote.calls += 1
        return {'contract':'amplifier.observation.v1','status':'pending','target':observation['target'],
            'source':{'id':'fixture://one','revision':'record-1'},'observedAt':1800000000,'semanticKey':'record-1','summary':'One permitted exact record remains pending','evidence':[]}
    await remote.start()
    try:
        await app.smart_tools.configure({'id':'remote','name':'Qualified HTTP fixture','transport':'streamable-http','url':remote.origin+'/mcp','auth':'oauth','installationId':'a'*24})
        await authorize(app.smart_tools,remote)
        await wait_for(lambda: app.smart_tools.oauth.status('remote')['phase']=='ready')
        descriptor.update(connectionId='remote',toolName='exact_observe')
        qualified = (await app.dispatch('observation.qualify',{'sessionId':sid,'descriptor':descriptor}))['result']['observer']
        assert qualified['account']['mode']=='attested'
        args['observerId']=qualified['id']; await create(app,args)
        before=copy.deepcopy(app.state)
        for _ in range(3): await app.observations.tick(); now[0]+=16
        assert app.state == before and remote.calls==3
        runtime.observation_input.assert_not_awaited()
        # Source-side identity changed; the existing per-dispatch account check
        # must prevent a fourth read rather than silently adopting that identity.
        remote.identity_override={'schemaVersion':1,'issuer':remote.origin,'subject':'other-user','displayName':'Other'}
        await app.observations.tick()
        assert remote.calls==3
    finally: await app.close(); await remote.close()


def test_schema_maps_literal_data_and_local_references_never_retrieve(monkeypatch):
    import socket
    from amplifier_web.observation_contract import validate_schema
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('Schema must never resolve network addresses')))
    schema={'type':'object','properties':{'id':{'type':'string'},'exact':{'const':{'id':'a','$ref':'https://literal.invalid'}}},
            '$defs':{'row':{'type':'string'}},'additionalProperties':{'$ref':'#/$defs/row'},'examples':[{'id':'literal'}]}
    validate_schema({'id':'one','exact':{'id':'a','$ref':'https://literal.invalid'}},schema)
    invalid=[{'$schema':'http://json-schema.org/draft-07/schema#','id':'https://bad.invalid'},
        {'properties':{'id':{'$ref':'https://bad.invalid'}}}, {'$recursiveRef':'https://bad.invalid'},
        {'$ref':'#/examples/0','examples':[{'$ref':'https://bad.invalid'}]}]
    for item in invalid:
        with pytest.raises(ValueError): validate_schema({},item)


async def test_cold_host_retains_result_until_explicit_worker_available(tmp_path,monkeypatch):
    app,runtime,now,sid,args,data,*_=await fixture(tmp_path,monkeypatch)
    try:
        available=[False]; runtime.observation_available=lambda _: available[0]
        await create(app,args); record(data/'record.json','actionable')
        await app.observations.tick()
        assert app.observations.store.rows('outbox')[0]['phase']=='waiting_worker'
        report=app.observations.operation_records(sid)[0]
        assert report['state']=='waiting_input' and report['evidence']['handoffs'][0]['detail'].startswith('Result retained')
        runtime.observation_input.assert_not_awaited()
        now[0]+=16; await app.observations.tick(); runtime.observation_input.assert_not_awaited()
        available[0]=True; now[0]+=16; await app.observations.tick()
        assert runtime.observation_input.await_count==1
    finally: await app.close()


async def test_sdk_queue_rechecks_watch_revocation_immediately_before_dispatch(tmp_path,monkeypatch):
    import asyncio
    app,runtime,_,sid,args,data,*_=await fixture(tmp_path,monkeypatch)
    try:
        watch=await create(app,args)
        connection=app.smart_tools.connections['observer']
        entered=asyncio.Event(); released=asyncio.Event()
        async def account_guard(_): entered.set(); await released.wait()
        connection.account_guard=account_guard
        pending=asyncio.create_task(app.observations.tick())
        await entered.wait()
        await app.dispatch('observation.pause',{'sessionId':sid,'id':watch['id'],'expectedRevision':1,'requestId':'pause'})
        # If the queued observer executes it returns a forbidden malformed result;
        # the watch guard must reject before SDK call_tool.
        (data/'record.json').unlink()
        released.set(); await pending
        assert app.observations.store.rows('run')[0]['phase']=='discarded'
        runtime.observation_input.assert_not_awaited()
        assert app.observations.store.get('watch',watch['id'])['status']=='paused'
    finally: await app.close()


def test_audit_pruning_preserves_exact_terminal_tombstone(tmp_path):
    store=ObservationStore(tmp_path/'observations.db')
    try:
        store.put('watch',{'id':'w','sessionId':'s','revision':1,'status':'active','nextDue':0,'sequence':0,'intervalSeconds':15,'expiresAt':100000})
        for n in range(105):
            now=n*16; store.acquire(now); run=store.claim('w',now)
            store.commit(run,{'status':'pending','semanticKey':'unchanged'},now+1)
        store.acquire(1700); run=store.claim('w',1700)
        store.commit(run,{'status':'actionable','semanticKey':'done'},1701)
        assert len(store.rows('run'))==100 and len(store.rows('outbox'))==1
        with store.transaction(): store.terminal(store.get('watch','w'),{'status':'actionable','semanticKey':'done'},1702)
        assert len(store.rows('outbox'))==1
    finally: store.close()
