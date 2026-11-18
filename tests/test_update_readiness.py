import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace
import uuid

import aiohttp
from aiohttp import web
import pytest

from amplifier_web import __version__, update_readiness
from amplifier_web.auth import data_identity
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager
from test_service import Runtime


REVISION='a'*40
ATTEMPT='b'*32
INSTANCE='c'*32
TARGET={'version':__version__,'revision':REVISION,'attemptId':ATTEMPT,
        'sourceInstanceId':'d'*32,'requestedAt':1.0}
IDENTITY={'version':__version__,'revision':REVISION,'instanceId':INSTANCE}


def make_manager(tmp_path,monkeypatch,*,state=None,identity=None):
    monkeypatch.setattr(update_readiness,'running_identity',lambda:dict(identity or IDENTITY))
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    service.state['updates']=deepcopy(state if state is not None else {'phase':'activating','pendingRestart':TARGET})
    manager=UpdateManager(service)
    service.update_manager=manager
    return service,manager


def health(manager,**changes):
    return {'ok':True,'app':'amplifier-unified','dataIdentity':data_identity(manager.home),
            **manager.running_identity,**changes}


def fake_distribution(monkeypatch,*,located=None,payload=None):
    location=located or Path(update_readiness.__file__).parent
    distribution=SimpleNamespace(locate_file=lambda _:location,
        read_text=lambda _:json.dumps(payload if payload is not None else {'vcs_info':{'commit_id':REVISION}}))
    monkeypatch.setattr(update_readiness.metadata,'distribution',lambda _:distribution)
    return distribution


@pytest.mark.parametrize('scope', [[], {}, 1, None, 'other'])
def test_invalid_saved_update_intent_refuses_without_throwing(scope):
    assert not update_readiness.valid_update_intent({'id': 'request', 'scope': scope})
    assert not update_readiness.valid_target({**TARGET, 'updateIntent': {'id': 'request', 'scope': scope}})


def test_running_identity_attests_only_own_installed_revision(monkeypatch):
    fake_distribution(monkeypatch)
    first=update_readiness.running_identity()
    second=update_readiness.running_identity()
    assert first['version']==__version__ and first['revision']==REVISION
    assert len(first['instanceId'])==32 and first['instanceId']!=second['instanceId']


@pytest.mark.parametrize('payload',[
    {},{'vcs_info':{}},{'vcs_info':{'commit_id':'bad'}},{'vcs_info':{'commit_id':'A'*40}},
    {'vcs_info':{'commit_id':None}},{'vcs_info':{'commit_id':'a'*39}},
])
def test_missing_or_invalid_installed_revision_is_not_attested(monkeypatch,payload):
    fake_distribution(monkeypatch,payload=payload)
    assert update_readiness.running_identity()['revision'] is None


def test_checkout_does_not_attest_unrelated_installed_distribution(tmp_path,monkeypatch):
    fake_distribution(monkeypatch,located=tmp_path/'other-install/amplifier_web')
    assert update_readiness.running_identity()['revision'] is None


async def test_running_identity_remains_captured_when_installation_is_replaced(tmp_path,monkeypatch):
    distribution=fake_distribution(monkeypatch)
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    try:
        old=deepcopy(manager.running_identity)
        distribution.read_text=lambda _:json.dumps({'vcs_info':{'commit_id':'e'*40}})
        assert update_readiness.running_identity()['revision']=='e'*40
        assert manager.running_identity==old
        assert old['revision']==REVISION
    finally:
        await service.close()


async def test_constructor_waits_for_real_listener_readiness(tmp_path,monkeypatch):
    service,manager=make_manager(tmp_path,monkeypatch)
    try:
        assert service.state['updates']['phase']=='activating'
        assert service.state['updates']['pendingRestart']==TARGET
        assert not manager.diagnostics.state['events']
        with pytest.raises(Exception,match='update is activating'):
            await service.dispatch('conversation.send',{'text':'Must wait for readiness'})
        assert await manager.confirm_readiness(health(manager))
        state=service.state['updates']
        assert state['phase']=='installed' and state['pendingRestart'] is None
        receipt=state['diagnostics']['latest']
        assert receipt['phase']=='restart-ack' and receipt['attemptId']==ATTEMPT
        assert receipt['observedRevision']==REVISION
        assert not await manager.confirm_readiness(health(manager))
        assert sum(event['phase']=='restart-ack' for event in manager.diagnostics.state['events'])==1
    finally:
        await service.close()


@pytest.mark.parametrize('scope', ['app', 'whole'])
async def test_exact_restart_intent_controls_component_continuation(tmp_path, monkeypatch, scope):
    intent = {'id': 'explicit-request', 'scope': scope}
    target = {**TARGET, 'updateIntent': intent}
    service, manager = make_manager(tmp_path, monkeypatch, state={
        'phase': 'activating', 'pendingRestart': target,
        'applicationIntent': intent, 'sequence': {'stage': 'application', 'install': True, 'nextStage': 'other'}})
    try:
        assert await manager.confirm_readiness(health(manager))
        sequence = service.state['updates']['sequence']
        if scope == 'app':
            assert sequence['stage'] == 'complete' and sequence['install'] is False
            assert 'nextStage' not in sequence
            assert service.state['updates']['applicationIntent'] == intent
        else:
            assert sequence['stage'] == 'included' and sequence['nextStage'] == 'included'
            assert sequence['install'] is True
        assert not await manager.confirm_readiness(health(manager))
    finally:
        await service.close()


@pytest.mark.parametrize('outcome', ['pass','failed','changed-target'])
async def test_successor_of_old_updater_proves_browser_recovery_before_clearing_attention(tmp_path,monkeypatch,outcome):
    from amplifier_web import app_updates, app_replacement
    from test_ui_use_packaging import QUALIFIED_BROWSER
    service,manager=make_manager(tmp_path,monkeypatch)
    diagnostics=manager.diagnostics
    try:
        diagnostics.begin('application', 'e'*40, application_extras=['managed-browser'])
        failure=diagnostics.record('managed-browser-install','failed',errorType='CommandFailure')
        diagnostics.begin('application', REVISION, ATTEMPT, application_extras=['managed-browser'])
        # This is the persisted shape produced by 0.47.1, before this repair.
        diagnostics.state['batch'].pop('browserFailures')
        diagnostics.state['batch'].pop('verifiedBrowserFailures')
        service.state['updates']['pendingRestart']={**TARGET,'qualification':{'extras':['managed-browser']}}
        monkeypatch.setattr(app_replacement,'matches_running',lambda *_:True)
        calls=[]
        async def process(*args,**kwargs):
            calls.append(args)
            if outcome=='failed':raise RuntimeError('Synthetic browser launch failure')
            if outcome=='changed-target':service.state['updates']['pendingRestart']['attemptId']='f'*32
            return json.dumps(QUALIFIED_BROWSER)
        monkeypatch.setattr(app_updates,'process',process)
        assert await manager.confirm_readiness(health(manager)) is (outcome!='changed-target')
        assert len(calls)==1 and 'qualify_managed_browser' in calls[0][-1]
        failures=list(diagnostics.state['activeFailures'].values())
        if outcome=='pass':assert failures==[]
        else:assert any(group['latest']['id']==failure['id'] for group in failures)
        assert failure['id'] in diagnostics.path.read_text(), 'Recovery retains historical failure'
    finally:
        await service.close()


@pytest.mark.parametrize('healthy', [False, True])
async def test_first_updated_process_settles_old_storage_failure_only_after_verified_ack(tmp_path,monkeypatch,healthy):
    from amplifier_web import app_replacement
    service,manager=make_manager(tmp_path,monkeypatch)
    diagnostics=manager.diagnostics
    try:
        diagnostics.begin('application','e'*40,application_extras=[])
        failure=diagnostics.record('candidate-install','failed',errorType='OSError',reason='disk-full')
        diagnostics.begin('application',REVISION,ATTEMPT,application_extras=[])
        diagnostics.record('stage','started')
        diagnostics.record('stage','succeeded')
        diagnostics.begin('application',REVISION,ATTEMPT,application_extras=[])
        diagnostics.state['batch'].pop('storageFailures')
        diagnostics.state['batch'].pop('verifiedStorageFailures')
        service.state['updates']['pendingRestart']={**TARGET,'qualification':{'extras':[]}}
        verified=[]
        def matches(*_):
            verified.append(True)
            return healthy
        monkeypatch.setattr(app_replacement,'matches_running',matches)
        assert await manager.confirm_readiness(health(manager)) is healthy
        assert verified==[True]
        assert bool(diagnostics.state['activeFailures']) is not healthy
        assert failure['id'] in diagnostics.path.read_text()
    finally:
        await service.close()


async def test_application_result_survives_component_failure_and_service_reload(tmp_path,monkeypatch):
    service,manager=make_manager(tmp_path,monkeypatch)
    try:
        assert not await manager.confirm_readiness(health(manager,revision='e'*40))
        assert 'applicationResult' not in service.state['updates']
        assert await manager.confirm_readiness(health(manager))
        result=deepcopy(service.state['updates']['applicationResult'])
        assert result['attemptId']==ATTEMPT and result['phase']=='restart-ack'
        assert result['version']==__version__ and result['revision']==REVISION
        assert result['verifiedAt']>0
        # This result is not a diagnostic ring entry: later component batches
        # must not erase it or turn it into a claim about the failed component.
        for index in range(9):
            manager.diagnostics.begin('ecosystem',format(index,'032x'),tier='included')
            manager.diagnostics.record('ecosystem-activation','succeeded')
        manager.diagnostics.begin('ecosystem','f'*32,tier='other',components=['foundation'])
        manager.diagnostics.record('ecosystem-validation','failed',reason='runtime-source-changed')
        await manager.publish(phase='error',error='Component sources changed.',sequence={'stage':'other'})
        assert service.state['updates']['applicationResult']==result
    finally:
        await service.close()
    restored=AppService(tmp_path,Runtime(),workspace=tmp_path)
    try:
        restored.update_manager=UpdateManager(restored)
        assert restored.state['updates']['applicationResult']==result
        assert restored.state['updates']['phase']=='error'
        assert restored.state['updates']['diagnostics']['lastFailure']['reason']=='runtime-source-changed'
    finally:
        await restored.close()


async def test_legacy_application_state_preserves_restart_readiness(tmp_path,monkeypatch):
    from amplifier_web import app_updates
    monkeypatch.setattr(app_updates,'application_state',lambda:{'current':__version__,'status':'current'})
    service,manager=make_manager(tmp_path,monkeypatch)
    try:
        assert service.state['updates']['application']['canInstall'] is True
        assert service.state['updates']['pendingRestart']==TARGET
        assert service.state['updates']['phase']=='activating'
        assert await manager.confirm_readiness(health(manager))
        assert service.state['updates']['pendingRestart'] is None
    finally:
        await service.close()


@pytest.mark.parametrize('changes',[
    {'ok':False},{'ok':1},{'app':'another-app'},{'version':'0.0.0'},{'revision':'e'*40},
    {'revision':None},{'instanceId':'e'*32},{'dataIdentity':'another-data-directory'},
])
async def test_incorrect_health_never_acknowledges(tmp_path,monkeypatch,changes):
    service,manager=make_manager(tmp_path,monkeypatch)
    try:
        assert not await manager.confirm_readiness(health(manager,**changes))
        assert service.state['updates']['pendingRestart']==TARGET
        assert service.state['updates']['phase']=='activating'
        assert not manager.diagnostics.state['events']
    finally:
        await service.close()


@pytest.mark.parametrize('identity',[
    {**IDENTITY,'version':'0.0.0'},{**IDENTITY,'revision':'e'*40},
    {**IDENTITY,'revision':None},{**IDENTITY,'instanceId':TARGET['sourceInstanceId']},
])
async def test_wrong_install_or_outgoing_instance_cannot_self_acknowledge(tmp_path,monkeypatch,identity):
    service,manager=make_manager(tmp_path,monkeypatch,identity=identity)
    try:
        assert not await manager.confirm_readiness(health(manager))
        assert service.state['updates']['pendingRestart']==TARGET
        assert not manager.diagnostics.state['events']
    finally:
        await service.close()


async def test_probe_from_superseded_attempt_cannot_acknowledge_current_marker(tmp_path,monkeypatch):
    service,manager=make_manager(tmp_path,monkeypatch)
    newer={**TARGET,'attemptId':'e'*32}
    service.state['updates']['pendingRestart']=newer
    try:
        assert not await manager.confirm_readiness(health(manager),expected=TARGET)
        assert service.state['updates']['pendingRestart']==newer
        assert not manager.diagnostics.state['events']
        assert await manager.confirm_readiness(health(manager),expected=newer)
        assert manager.diagnostics.state['latest']['attemptId']==newer['attemptId']
    finally:
        await service.close()


def legacy_state():
    failure={'kind':'application','phase':'service-restart','status':'failed',
             'attemptId':ATTEMPT,'revision':REVISION,'exitCode':-15}
    probe={'kind':'application','phase':'replacement-probe','status':'succeeded',
           'attemptId':ATTEMPT,'revision':REVISION,'probe':{'ok':True,'version':__version__}}
    return {'phase':'error','error':update_readiness.LEGACY_RESTART_ERROR,'pendingRestart':None,
            'diagnostics':{'kind':'application','attemptId':ATTEMPT,'revision':REVISION,
                           'lastFailure':failure,'events':[probe,failure]}}


def validated_record(manager,value=None):
    path=manager.directory/'applications'/REVISION/'validated.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value or {'version':__version__,'revision':REVISION,'attemptId':ATTEMPT}))
    return path


async def test_legacy_erased_marker_reconciles_current_health_without_fake_ack(tmp_path,monkeypatch):
    original=legacy_state()
    service,manager=make_manager(tmp_path,monkeypatch,state=original)
    validated_record(manager)
    try:
        assert service.state['updates']['phase']=='error'
        candidate=update_readiness.recovery_candidate(manager)
        assert candidate['legacy'] is True and candidate['attemptId']==ATTEMPT
        assert await manager.confirm_readiness(health(manager))
        state=service.state['updates']
        assert state['phase']=='installed' and state['error'] is None
        assert state['diagnostics']['lastFailure']==original['diagnostics']['lastFailure']
        assert state['diagnostics']['events'][:2]==original['diagnostics']['events']
        assert state['diagnostics']['latest']['phase']=='restart-reconcile'
        assert not any(row['phase']=='restart-ack' for row in state['diagnostics']['events'])
        assert state['reconciliation']['attemptId']==ATTEMPT
        assert state['reconciliation']['verifiedAt']>0
        assert 'no additional restart' in state['detail']
    finally:
        await service.close()


@pytest.mark.parametrize('broken',[
    'different-error','different-phase','different-operation','different-kind',
    'failure-attempt','failure-revision','probe-attempt','probe-revision','probe-version',
    'probe-failed','probe-not-ok','probe-missing','validated-missing','validated-invalid',
    'validated-attempt','validated-revision','validated-version',
])
async def test_legacy_recovery_requires_all_correlated_evidence(tmp_path,monkeypatch,broken):
    state=legacy_state()
    diagnostic=state['diagnostics'];failure=diagnostic['lastFailure'];probe=diagnostic['events'][0]
    validation={'version':__version__,'revision':REVISION,'attemptId':ATTEMPT}
    if broken=='different-error':state['error']='Other failure'
    elif broken=='different-phase':state['phase']='idle'
    elif broken=='different-operation':failure['phase']='replacement-install'
    elif broken=='different-kind':failure['kind']='ecosystem'
    elif broken=='failure-attempt':failure['attemptId']='e'*32
    elif broken=='failure-revision':failure['revision']='e'*40
    elif broken=='probe-attempt':probe['attemptId']='e'*32
    elif broken=='probe-revision':probe['revision']='e'*40
    elif broken=='probe-version':probe['probe']['version']='0.0.0'
    elif broken=='probe-failed':probe['status']='failed'
    elif broken=='probe-not-ok':probe['probe']['ok']=False
    elif broken=='probe-missing':diagnostic['events']=[failure]
    elif broken=='validated-attempt':validation['attemptId']='e'*32
    elif broken=='validated-revision':validation['revision']='e'*40
    elif broken=='validated-version':validation['version']='0.0.0'
    service,manager=make_manager(tmp_path,monkeypatch,state=state)
    if broken!='validated-missing':
        path=validated_record(manager,validation)
        if broken=='validated-invalid':path.write_text('{')
    try:
        assert update_readiness.recovery_candidate(manager) is None
        assert not await manager.confirm_readiness(health(manager))
        assert service.state['updates']['error']==state['error']
        assert service.state['updates']['diagnostics']['lastFailure']==state['diagnostics']['lastFailure']
        assert not any(row['phase'] in {'restart-ack','restart-reconcile'} for row in manager.diagnostics.state['events'])
    finally:
        await service.close()


async def listener(manager,handler):
    sock=socket.socket()
    sock.bind(('127.0.0.1',0))
    sock.setblocking(False)
    manager.service.port=sock.getsockname()[1]
    manager.service.server_config={'bind':['127.0.0.1'],'tls':{'method':'none'}}
    app=web.Application()
    app.router.add_get('/api/health',handler)
    runner=web.AppRunner(app)
    await runner.setup()
    return runner,web.SockSite(runner,sock),sock


async def test_waiter_requires_listener_then_authenticated_health(tmp_path,monkeypatch):
    service,manager=make_manager(tmp_path,monkeypatch)
    requests=[]
    async def handle(request):
        requests.append(dict(request.headers))
        if request.headers.get('Authorization')!='Bearer fixture-control-token':
            return web.json_response({},status=401)
        return web.json_response(health(manager))
    runner,site,sock=await listener(manager,handle)
    task=asyncio.create_task(update_readiness.wait_for_readiness(manager,'fixture-control-token',timeout=1,interval=.01))
    try:
        await asyncio.sleep(.04)
        assert service.state['updates']['phase']=='activating' and not requests
        assert not task.done()
        await site.start()
        await asyncio.wait_for(task,2)
        assert service.state['updates']['phase']=='installed'
        assert requests[0]['Authorization']=='Bearer fixture-control-token'
        assert requests[0]['Host']==f'localhost:{service.port}'
        assert manager.diagnostics.state['latest']['phase']=='restart-ack'
        assert 'fixture-control-token' not in json.dumps(manager.diagnostics.state)
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        await runner.cleanup();sock.close();await service.close()


@pytest.mark.parametrize('response_kind',['unauthorized','wrong-revision','redirect'])
async def test_listener_without_verified_health_times_out_and_keeps_gate(tmp_path,monkeypatch,response_kind):
    service,manager=make_manager(tmp_path,monkeypatch)
    redirected=[]
    async def handle(request):
        if request.query.get('redirected'):
            redirected.append(request)
            return web.json_response(health(manager))
        if response_kind=='unauthorized':return web.json_response({},status=401)
        if response_kind=='redirect':raise web.HTTPFound('/api/health?redirected=true')
        return web.json_response(health(manager,revision='e'*40))
    runner,site,sock=await listener(manager,handle)
    try:
        await site.start()
        await update_readiness.wait_for_readiness(manager,'fixture-token',timeout=.06,interval=.01)
        state=service.state['updates']
        assert state['phase']=='activating' and state['pendingRestart']==TARGET
        assert 'not been confirmed' in state['error']
        failure=manager.diagnostics.state['lastFailure']
        assert failure['phase']=='restart-readiness' and failure['errorType']=='TimeoutError'
        assert not redirected
        assert not any(row['phase']=='restart-ack' for row in manager.diagnostics.state['events'])
        with pytest.raises(Exception,match='update is activating'):
            await service.dispatch('conversation.send',{'text':'Still waiting for readiness'})
    finally:
        await runner.cleanup();sock.close();await service.close()


@pytest.mark.parametrize('finish', ['timeout', 'cancel'])
async def test_slow_target_preparation_cannot_stall_loop_or_reset_budget(tmp_path, monkeypatch, finish):
    import threading
    service, manager = make_manager(tmp_path, monkeypatch)
    started, release = threading.Event(), threading.Event()
    def slow_targets(manager):
        started.set()
        release.wait(3)
        return [], None
    monkeypatch.setattr(update_readiness, 'probe_targets', slow_targets)
    task = asyncio.create_task(update_readiness.wait_for_readiness(
        manager, 'fixture-token', timeout=.15 if finish == 'timeout' else 2))
    try:
        deadline = asyncio.get_running_loop().time() + 1
        while not started.is_set():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(.005)
        # The resolver thread is blocked, but the event loop remains usable.
        await asyncio.wait_for(asyncio.sleep(.01), .1)
        assert not release.is_set() and not task.done()
        if finish == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await asyncio.wait_for(task, .5)
        failure = manager.diagnostics.state['lastFailure']
        assert failure['errorType'] == ('TimeoutError' if finish == 'timeout' else 'CancelledError')
        assert service.state['updates']['pendingRestart'] == TARGET
        assert not any(row['phase'] == 'restart-ack' for row in manager.diagnostics.state['events'])
    finally:
        release.set()
        await service.close()


@pytest.mark.parametrize('slow_phase', ['response', 'confirmation'])
async def test_total_readiness_deadline_covers_multiple_targets_and_confirmation(tmp_path, monkeypatch, slow_phase):
    service, manager = make_manager(tmp_path, monkeypatch)
    service.port = 8941
    requests, confirmed = [], []
    class Response:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def json(self):
            await asyncio.sleep(.04 if slow_phase == 'response' else .001)
            return health(manager)
    class Client:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def get(self, url, **kwargs):
            requests.append(url)
            return Response()
    async def confirm(*args, **kwargs):
        await asyncio.sleep(.04)
        confirmed.append(True)
        return True
    urls = ['https://127.0.0.1:8941/api/health', 'https://127.0.0.2:8941/api/health']
    monkeypatch.setattr(update_readiness, 'probe_targets', lambda manager: (urls, None))
    monkeypatch.setattr(update_readiness.aiohttp, 'ClientSession', Client)
    monkeypatch.setattr(update_readiness, 'confirm_readiness', confirm)
    try:
        await update_readiness.wait_for_readiness(manager, 'fixture-token', timeout=.02, interval=.001)
        assert requests == urls[:1]  # Never start target two after total budget.
        assert not confirmed  # No late confirmation, including an in-flight one.
        assert service.state['updates']['pendingRestart'] == TARGET
        assert manager.diagnostics.state['lastFailure']['errorType'] == 'TimeoutError'
        assert not any(row['phase'] == 'restart-ack' for row in manager.diagnostics.state['events'])
    finally:
        await service.close()


async def test_cancelled_readiness_preserves_marker_and_reports_interruption(tmp_path,monkeypatch):
    service,manager=make_manager(tmp_path,monkeypatch)
    async def handle(request):return web.json_response(health(manager))
    runner,site,sock=await listener(manager,handle)
    task=asyncio.create_task(update_readiness.wait_for_readiness(manager,'fixture-token',timeout=2,interval=.01))
    try:
        await asyncio.sleep(.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert service.state['updates']['pendingRestart']==TARGET
        assert service.state['updates']['phase']=='activating'
        failure=manager.diagnostics.state['lastFailure']
        assert failure['status']=='interrupted' and failure['errorType']=='CancelledError'
        assert not any(row['phase']=='restart-ack' for row in manager.diagnostics.state['events'])
    finally:
        await runner.cleanup();sock.close();await service.close()


@pytest.mark.parametrize('finish',['timeout','cancel'])
async def test_waiter_for_superseded_attempt_does_not_ack_or_overwrite_new_attempt(tmp_path,monkeypatch,finish):
    service,manager=make_manager(tmp_path,monkeypatch)
    requested=asyncio.Event();release=asyncio.Event()
    async def handle(request):
        requested.set()
        await release.wait()
        return web.json_response(health(manager))
    runner,site,sock=await listener(manager,handle)
    await site.start()
    task=asyncio.create_task(update_readiness.wait_for_readiness(manager,'fixture-token',timeout=.1,interval=.01))
    try:
        await asyncio.wait_for(requested.wait(),1)
        newer={**TARGET,'attemptId':'e'*32}
        await manager.publish(pendingRestart=newer,error='Current attempt detail')
        manager.diagnostics.begin('application',REVISION,newer['attemptId'])
        latest=deepcopy(manager.diagnostics.record('current-attempt','started'))
        release.set()
        if finish=='cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        else:
            await asyncio.wait_for(task,1)
        state=service.state['updates']
        assert state['pendingRestart']==newer and state['error']=='Current attempt detail'
        assert manager.diagnostics.state['latest']==latest
        assert not any(row['phase']=='restart-ack' for row in manager.diagnostics.state['events'])
    finally:
        release.set();task.cancel();await asyncio.gather(task,return_exceptions=True)
        await runner.cleanup();sock.close();await service.close()


async def test_failed_legacy_health_probe_retains_original_failure(tmp_path,monkeypatch):
    original=legacy_state()
    service,manager=make_manager(tmp_path,monkeypatch,state=original)
    validated_record(manager)
    async def handle(request):return web.json_response(health(manager,revision='e'*40))
    runner,site,sock=await listener(manager,handle)
    try:
        await site.start()
        await update_readiness.wait_for_readiness(manager,'fixture-token',timeout=.05,interval=.01)
        state=service.state['updates']
        assert state['phase']=='error' and state['error']==update_readiness.LEGACY_RESTART_ERROR
        assert state['diagnostics']['lastFailure']==original['diagnostics']['lastFailure']
        assert state['diagnostics']['latest']['phase']=='restart-readiness'
        assert state['diagnostics']['latest']['status']=='failed'
        assert not any(row['phase'] in {'restart-ack','restart-reconcile'} for row in manager.diagnostics.state['events'])
    finally:
        await runner.cleanup();sock.close();await service.close()


@pytest.mark.parametrize('operation',['check','app','install','activate','rollback','tick'])
async def test_pending_restart_blocks_later_updates_from_erasing_receipt(tmp_path,monkeypatch,operation):
    service,manager=make_manager(tmp_path,monkeypatch)
    service.state['updates']['error']='Pending restart needs review'
    manager.diagnostics.begin('application',REVISION,ATTEMPT)
    manager.diagnostics.record('service-restart-request','uncertain',exitCode=-15)
    before=deepcopy(service.state['updates'])
    async def unexpected(*args,**kwargs):pytest.fail('no update may start during pending handoff')
    monkeypatch.setattr(manager,'inventory_sources',unexpected)
    monkeypatch.setattr('amplifier_web.app_updates.stage',unexpected)
    monkeypatch.setattr('amplifier_web.app_updates.activate',unexpected)
    try:
        await getattr(manager,operation)()
        assert service.state['updates']==before
        assert manager.awaiting_restart()
    finally:
        await service.close()


@pytest.mark.parametrize('identity',[{**IDENTITY,'revision':'e'*40},{**IDENTITY,'version':'0.0.0'}])
async def test_old_legacy_failure_does_not_permanently_block_repairs(tmp_path,monkeypatch,identity):
    service,manager=make_manager(tmp_path,monkeypatch,state=legacy_state(),identity=identity)
    validated_record(manager)
    async def handle(request):return web.json_response(health(manager))
    runner,site,sock=await listener(manager,handle)
    calls=[]
    async def inventory(**kwargs):calls.append('inventory');return []
    async def application():return {'id':'application','current':__version__,'status':'current'}
    monkeypatch.setattr(manager,'inventory_sources',inventory)
    monkeypatch.setattr('amplifier_web.app_updates.check',application)
    await site.start()
    manager.readiness_task=asyncio.create_task(update_readiness.wait_for_readiness(manager,'fixture-token',timeout=.06,interval=.01))
    try:
        assert manager.awaiting_restart()
        await manager.check()
        assert not calls
        await manager.readiness_task
        assert not manager.awaiting_restart()
        assert service.state['updates']['phase']=='error'
        await manager.check()
        assert calls==['inventory']
        assert service.state['updates']['phase']=='checked'
        assert service.state['updates']['diagnostics']['lastFailure']['phase']=='service-restart'
        assert not any(row['phase'] in {'restart-ack','restart-reconcile'} for row in manager.diagnostics.state['events'])
    finally:
        await runner.cleanup();sock.close();await service.close()


async def test_verified_app_restart_continues_manual_component_install(tmp_path, monkeypatch):
    service,manager=make_manager(tmp_path,monkeypatch,state={
        'phase':'activating','pendingRestart':TARGET,
        'sequence':{'stage':'application','install':True},
    })
    service.state['settings']['updates']['autoInstall']=False
    try:
        assert not service.state['settings']['updates']['autoInstall']
        assert 'nextStage' not in service.state['updates']['sequence']
        assert await manager.confirm_readiness(health(manager))
        sequence=service.state['updates']['sequence']
        assert sequence['nextStage']=='included' and sequence['install'] is True
        assert sequence['included']['status']=='waiting'
        assert sequence['other']['status']=='waiting'
    finally:
        await service.close()


# A host that an app update started confirms its start after its first health answer. On Windows that
# confirmation asks the Task Scheduler twice who runs this host (windows_service.update_owner): once
# for the check of a rollback (windows_app_recovery.confirm) and once before the acknowledgement. Each
# question is a PowerShell process that takes seconds on some PCs. The cases below hold a stand-in for
# that question in its thread and require that the host goes on answering.

GENERATION = 'e' * 32
WINDOWS_TARGET = {**TARGET, 'generation': GENERATION}


class WindowsSys:
    """The module's ``sys`` with the platform name of Windows. Every other attribute is the real one."""
    platform = 'win32'

    def __getattr__(self, name):
        return getattr(sys, name)


class HeldOwner:
    """A stand-in for the Task Scheduler question, with the product's answer type (the task's spec).

    In a thread that is not the event loop's it waits until the case lets it go. On the loop's own
    thread it returns at once: nothing else can run there, and the case fails on ``started``.
    """

    def __init__(self, answer, questions):
        self.answer, self.loop_thread = answer, threading.get_ident()
        self.gates = [(threading.Event(), threading.Event()) for _ in range(questions)]
        self.on_loop = []

    def __call__(self, home):
        index = len(self.on_loop)
        self.on_loop.append(threading.get_ident() == self.loop_thread)
        if self.on_loop[-1] or index >= len(self.gates):
            return self.answer
        started, go = self.gates[index]
        started.set()
        assert go.wait(30), 'The case did not let the owner question go'
        return self.answer

    async def started(self, index):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        while not self.gates[index][0].is_set():
            assert not any(self.on_loop), 'The owner question ran on the event loop: the host answers nothing during it'
            assert loop.time() < deadline, 'The owner question did not start'
            await asyncio.sleep(.005)

    def go(self, index=None):
        for _, go in self.gates if index is None else [self.gates[index]]:
            go.set()


def handed_over(monkeypatch, home, *, status='prepared'):
    """What an app update leaves for the new host: the task's spec and the launcher's hand-over record.

    The record is kept in memory, with the product's own validation of each write.
    """
    from amplifier_web import windows_app_recovery as recovery
    spec = SimpleNamespace(python=str(Path(home) / 'bootstrap/Scripts/pythonw.exe'), data_dir=str(home),
                           registration_id='a' * 32)
    launcher = {'prefix': str(Path(home) / 'bootstrap'), 'digest': 'b' * 64}
    earlier_host = {'schema': 1, 'registration': spec.registration_id, 'run': '7' * 32, 'pid': 123,
                    'created': '134000000000000001', 'image': r'C:\Python\pythonw.exe', 'phase': 'listening'}
    saved = {'writes': [], 'record': recovery._validate({
        'schema': 1, 'registration': spec.registration_id,
        'restart': {'schema': 1, 'run': earlier_host,
                    'target': {'revision': REVISION, 'generation': GENERATION, 'attemptId': ATTEMPT}},
        'promoted': {'schema': 1, 'bootstrap': launcher, 'revision': REVISION, 'generation': GENERATION,
                     'version': __version__, 'componentDigest': '2' * 64},
        'previous': None, 'bootstrap': launcher, 'installation': launcher,
        'identity': {'version': '1.0.0', 'revision': '1' * 40, 'instanceId': '2' * 32},
        'graph': [{'name': 'amplifier-unified', 'version': '1.0.0',
                   'url': 'https://github.com/microsoft/amplifier-unified', 'revision': '1' * 40}],
        'extras': [], 'artifact': None, 'status': status}, spec)}

    def read(given):
        assert given is spec
        return deepcopy(saved['record'])

    def write(given, value):
        assert given is spec
        saved['record'] = recovery._validate(value, spec)
        saved['writes'].append(value['status'])
        return deepcopy(saved['record'])

    monkeypatch.setattr(recovery, 'read', read)
    monkeypatch.setattr(recovery, '_write', write)
    return spec, saved


def windows_host(tmp_path, monkeypatch, questions):
    """A host on Windows that an app update started, with a held owner question."""
    from amplifier_web import windows_service
    service, manager = make_manager(tmp_path, monkeypatch,
                                    state={'phase': 'activating', 'pendingRestart': WINDOWS_TARGET})
    spec, saved = handed_over(monkeypatch, manager.home)
    monkeypatch.setattr(update_readiness, 'sys', WindowsSys())
    owner = HeldOwner(spec, questions)
    monkeypatch.setattr(windows_service, 'update_owner', owner)
    return service, manager, saved, owner


@pytest.mark.parametrize('held', [0, 2.3])
async def test_the_host_answers_while_the_two_owner_questions_of_a_restart_confirmation_run(tmp_path, monkeypatch, held):
    """``held``: the waiter's own request has a time limit of 2 seconds, counted from its start. An owner
    question that is still open after that time is not cut by it: the limit ends when the health answer
    is read."""
    service, manager, saved, owner = windows_host(tmp_path, monkeypatch, questions=2)

    async def handle(request):
        return web.json_response(health(manager))
    runner, site, sock = await listener(manager, handle)
    await site.start()
    task = asyncio.create_task(update_readiness.wait_for_readiness(manager, 'fixture-token', timeout=30, interval=.01))
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5), trust_env=False) as outside:
            # 0: the check of a rollback (windows_app_recovery.confirm). 1: before the acknowledgement.
            for question in (0, 1):
                await owner.started(question)
                async with outside.get(f'http://127.0.0.1:{service.port}/api/health') as response:
                    assert response.status == 200 and (await response.json())['ok'] is True
                if question == 0:
                    await asyncio.sleep(held)
                assert not task.done()
                assert service.state['updates']['pendingRestart'] == WINDOWS_TARGET
                assert saved['record']['status'] == 'prepared' and not saved['writes']
                owner.go(question)
        await asyncio.wait_for(task, 10)
        assert owner.on_loop == [False, False]
        state = service.state['updates']
        assert state['phase'] == 'installed' and state['pendingRestart'] is None
        assert state['applicationResult']['phase'] == 'restart-ack'
        assert saved['writes'] == ['acknowledged']
        assert manager.diagnostics.state['latest']['phase'] == 'restart-ack'
    finally:
        owner.go()
        task.cancel();await asyncio.gather(task, return_exceptions=True)
        await runner.cleanup();sock.close();await service.close()


async def test_a_restart_target_that_changes_during_the_owner_question_is_not_acknowledged(tmp_path, monkeypatch):
    service, manager, saved, owner = windows_host(tmp_path, monkeypatch, questions=1)
    task = asyncio.create_task(manager.confirm_readiness(health(manager), expected=WINDOWS_TARGET))
    try:
        await owner.started(0)
        # Another task of the host, while the question's thread runs: a later attempt is now pending.
        later = {**WINDOWS_TARGET, 'attemptId': 'f' * 32}
        service.state['updates']['pendingRestart'] = later
        owner.go(0)
        assert await asyncio.wait_for(task, 10) is False
        assert owner.on_loop == [False]
        # The hand-over record names the first attempt. The answer for it must not retire that record.
        assert saved['record']['status'] == 'prepared' and not saved['writes']
        state = service.state['updates']
        assert state['pendingRestart'] == later and state['phase'] == 'activating'
        assert 'applicationResult' not in state
        assert not any(row['phase'] == 'restart-ack' for row in manager.diagnostics.state['events'])
    finally:
        owner.go()
        task.cancel();await asyncio.gather(task, return_exceptions=True)
        await service.close()


async def test_an_owner_that_is_not_this_host_is_not_acknowledged(tmp_path, monkeypatch):
    service, manager, saved, owner = windows_host(tmp_path, monkeypatch, questions=1)
    owner.answer = None  # The Task Scheduler does not run this host for this folder.
    task = asyncio.create_task(manager.confirm_readiness(health(manager), expected=WINDOWS_TARGET))
    try:
        await owner.started(0)
        owner.go(0)
        assert await asyncio.wait_for(task, 10) is False
        assert saved['record']['status'] == 'prepared' and not saved['writes']
        state = service.state['updates']
        assert state['pendingRestart'] == WINDOWS_TARGET and state['phase'] == 'activating'
        assert not any(row['phase'] == 'restart-ack' for row in manager.diagnostics.state['events'])
    finally:
        owner.go()
        task.cancel();await asyncio.gather(task, return_exceptions=True)
        await service.close()


async def test_a_restart_without_a_generation_asks_no_owner_question(tmp_path, monkeypatch):
    """A host on Windows that no app generation started (a start by hand): no question, no hand-over record."""
    service, manager, saved, owner = windows_host(tmp_path, monkeypatch, questions=0)
    service.state['updates']['pendingRestart'] = dict(TARGET)
    try:
        assert await asyncio.wait_for(manager.confirm_readiness(health(manager), expected=TARGET), 10) is True
        assert owner.on_loop == [] and not saved['writes']
        assert service.state['updates']['applicationResult']['phase'] == 'restart-ack'
    finally:
        await service.close()


async def test_a_newer_running_version_asks_no_owner_question_and_leaves_the_hand_over_record(tmp_path, monkeypatch):
    """A running release that is newer than the pending restart supersedes it. That path never asked
    for the owner and never retired the launcher's record; it still does neither."""
    from amplifier_web import app_replacement
    proof = {'extras': []}
    older = {**WINDOWS_TARGET, 'version': '0.0.1', 'qualification': proof}
    running = {**IDENTITY, 'revision': 'f' * 40}
    service, manager = make_manager(tmp_path, monkeypatch, identity=running,
                                    state={'phase': 'activating', 'pendingRestart': older})
    spec, saved = handed_over(monkeypatch, manager.home)
    monkeypatch.setattr(update_readiness, 'sys', WindowsSys())
    owner = HeldOwner(spec, questions=0)
    monkeypatch.setattr('amplifier_web.windows_service.update_owner', owner)
    # The installed packages of this test process are not the release's. That comparison has its own cases.
    monkeypatch.setattr(app_replacement, 'matches_running', lambda manager, target: True)
    successor = {**older, 'version': running['version'], 'revision': running['revision']}
    try:
        assert await asyncio.wait_for(update_readiness.confirm_readiness(
            manager, health(manager), expected=older, successor=successor), 10) is True
        assert owner.on_loop == [] and not saved['writes'] and saved['record']['status'] == 'prepared'
        state = service.state['updates']
        assert state['applicationResult']['phase'] == 'restart-superseded' and state['pendingRestart'] is None
    finally:
        await service.close()


@pytest.mark.parametrize('question', [0, 1])
async def test_a_confirmation_that_is_cancelled_during_an_owner_question_changes_nothing(tmp_path, monkeypatch, question):
    service, manager, saved, owner = windows_host(tmp_path, monkeypatch, questions=2)

    async def handle(request):
        return web.json_response(health(manager))
    runner, site, sock = await listener(manager, handle)
    await site.start()
    task = asyncio.create_task(update_readiness.wait_for_readiness(manager, 'fixture-token', timeout=30, interval=.01))
    try:
        for earlier in range(question):
            await owner.started(earlier)
            owner.go(earlier)
        await owner.started(question)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not manager.lock.locked() and not service.lock.locked()

        def unchanged():
            state = service.state['updates']
            assert state['pendingRestart'] == WINDOWS_TARGET and state['phase'] == 'activating'
            assert 'applicationResult' not in state
            assert saved['record']['status'] == 'prepared' and not saved['writes']
            assert not any(row['phase'] == 'restart-ack' for row in manager.diagnostics.state['events'])
        unchanged()
        failure = manager.diagnostics.state['lastFailure']
        assert failure['phase'] == 'restart-readiness' and failure['errorType'] == 'CancelledError'
        # The thread ends later. Nothing uses its answer.
        owner.go()
        await asyncio.sleep(.05)
        unchanged()
    finally:
        owner.go()
        task.cancel();await asyncio.gather(task, return_exceptions=True)
        await runner.cleanup();sock.close();await service.close()


@pytest.mark.skipif(sys.platform != 'win32', reason='needs the real Task Scheduler question of native Windows')
async def test_native_windows_the_loop_runs_during_the_real_owner_questions_of_a_restart_confirmation(monkeypatch):
    """A measurement on a hosted Windows runner, and the proof with the real question.

    Real: windows_service.update_owner (a PowerShell process that asks the Task Scheduler), the whole
    confirmation (wait_for_readiness, windows_app_recovery.confirm, confirm_readiness), the app
    service with its SQLite file, the update manager, the hand-over record and this process's run
    record as private files, and a listener on 127.0.0.1.
    The case's own part: the runner's account has no Amplifier task, so the real question answers
    "no task"; the case then gives the confirmation the spec of its own folder in the answer's place.
    The identity of the running app is a stand-in, as in every case of this file. So the comparison
    of the installed packages with the update's record is not made; the case makes that check's two
    reads of the installed packages (of this test environment) in its place and answers yes. The
    line says for each of the two whether it read to its end ("installedPackagesRead"). The listener
    is a test application with one route and no TLS, not the product's server.
    The line HOST-LOOP-MEASUREMENT in the output holds the numbers. They are printed before the
    checks, so a run without the change prints them too.
    """
    import http.client
    import shutil
    from fixtures.manual_app_handoff_native import token_user_default_owner
    from amplifier_web import app_component_graph as components, app_replacement, app_updates
    from amplifier_web import windows_app_recovery as recovery, windows_files, windows_service, windows_task_runtime

    clock = time.perf_counter
    loop_thread = threading.get_ident()
    home = windows_files.address(Path.home() / ('au-host-loop-' + uuid.uuid4().hex[:10]))
    report, questions, spans, ticks, answers = {}, [], [], [], []
    stop = threading.Event()
    real_owner = windows_service.update_owner

    def timed(name, function):
        def call(*args, **kwargs):
            began = clock()
            try:
                return function(*args, **kwargs)
            finally:
                spans.append((name, began, clock()))
        return call

    async def tick():
        while True:
            ticks.append(clock())
            await asyncio.sleep(.005)

    def seconds(value):
        return round(value, 3)

    # A hosted runner's account is elevated: without this a new folder belongs to the Administrators
    # group, and the private-folder check refuses that owner. On a person's PC the account owns it.
    with token_user_default_owner():
        service = runner = sock = ticker = reader = None
        try:
            with windows_files.directory(home / 'config/services', owned_root=home, create=True):
                pass
            pending = {**WINDOWS_TARGET, 'qualification': {'extras': []}}
            service, manager = make_manager(home, monkeypatch, state={'phase': 'activating', 'pendingRestart': pending})
            spec = SimpleNamespace(python=sys.executable, data_dir=str(home), registration_id='a' * 32)
            launcher = {'prefix': str(home / 'bootstrap'), 'digest': 'b' * 64}
            # This process's own run record, written by the product's runner as a host writes it.
            host_run = windows_task_runtime.Runner(spec)
            host_run.phase('listening')
            earlier_host = {**host_run.record, 'run': '7' * 32}
            record = {
                'schema': 1, 'registration': spec.registration_id,
                'restart': {'schema': 1, 'run': earlier_host,
                            'target': {'revision': REVISION, 'generation': GENERATION, 'attemptId': ATTEMPT}},
                'promoted': {'schema': 1, 'bootstrap': launcher, 'revision': REVISION, 'generation': GENERATION,
                             'version': __version__, 'componentDigest': '2' * 64},
                'previous': None, 'bootstrap': launcher, 'installation': launcher,
                'identity': {'version': '1.0.0', 'revision': '1' * 40, 'instanceId': '2' * 32},
                'graph': [{'name': 'amplifier-unified', 'version': '1.0.0',
                           'url': 'https://github.com/microsoft/amplifier-unified', 'revision': '1' * 40}],
                'extras': [], 'artifact': None, 'status': 'prepared'}
            recovery._write(spec, record)
            assert recovery.read(spec)['status'] == 'prepared'

            def owner(given):
                began = clock()
                answer = real_owner(given)  # The real PowerShell process and the real Task Scheduler question.
                questions.append({'began': began, 'ended': clock(), 'answer': answer,
                                  'onLoopThread': threading.get_ident() == loop_thread})
                return spec
            monkeypatch.setattr(windows_service, 'update_owner', owner)

            package_reads = {}

            def installed_packages(manager, target):
                # Both reads are made, also when the first one refuses a package of the test environment
                # (a source of its own). The line names a read that did not reach its end.
                for name, read in (('graph', components.installed_graph), ('extras', app_updates.installed_extras)):
                    try:
                        read()
                    except ValueError as error:
                        package_reads[name] = type(error).__name__
                    else:
                        package_reads[name] = 'complete'
                return True
            monkeypatch.setattr(app_replacement, 'matches_running', timed('installed packages read', installed_packages))
            monkeypatch.setattr(recovery, 'acknowledge', timed('acknowledge', recovery.acknowledge))
            monkeypatch.setattr(manager.diagnostics, 'record', timed('diagnostic record', manager.diagnostics.record))
            monkeypatch.setattr(service, '_publish_changes', timed('publish', service._publish_changes))

            async def handle(request):
                return web.json_response(health(manager))
            runner, site, sock = await listener(manager, handle)
            await site.start()

            def read_health():
                # Another program's view: one request after the other, each on a new connection.
                while not stop.is_set():
                    began, status = clock(), None
                    try:
                        connection = http.client.HTTPConnection('127.0.0.1', service.port, timeout=60)
                        try:
                            connection.request('GET', '/api/health')
                            response = connection.getresponse()
                            response.read()
                            status = response.status
                        finally:
                            connection.close()
                    except OSError as error:
                        status = type(error).__name__
                    answers.append((began, clock(), status))
                    stop.wait(.02)
            reader = threading.Thread(target=read_health, daemon=True)
            reader.start()
            ticker = asyncio.create_task(tick())
            await asyncio.sleep(.3)

            began = clock()
            await asyncio.wait_for(update_readiness.wait_for_readiness(
                manager, 'fixture-token', timeout=120, interval=.05), 150)
            ended = clock()
            await asyncio.sleep(.3)
            stop.set()
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
            await asyncio.to_thread(reader.join, 70)

            inside = [(first, second) for first, second in zip(ticks, ticks[1:]) if second >= began and first <= ended]
            quiet = [(first, second) for first, second in zip(ticks, ticks[1:]) if second < began]
            waits = [row for row in answers if row[1] >= began and row[0] <= ended]
            for row in questions:
                row['ticksDuring'] = sum(row['began'] < at < row['ended'] for at in ticks)
                row['answersDuring'] = sum(row['began'] <= first and second <= row['ended'] and status == 200
                                           for first, second, status in answers)
            report['confirmation'] = {
                'seconds': seconds(ended - began),
                'longestLoopPause': seconds(max(second - first for first, second in inside)),
                'longestLoopPauseBeforeIt': seconds(max(second - first for first, second in quiet)),
                'longestHttpWait': seconds(max(second - first for first, second, _ in waits)),
                'httpRequests': len(waits), 'httpNot200': sum(status != 200 for _, _, status in waits),
                'ownerQuestions': [{'seconds': seconds(row['ended'] - row['began']),
                                    'startedAfter': seconds(row['began'] - began),
                                    'onLoopThread': row['onLoopThread'],
                                    'realAnswer': 'no task' if row['answer'] is None else 'a task',
                                    'loopTicksDuring': row['ticksDuring'],
                                    'httpAnswersDuring': row['answersDuring']} for row in questions],
                'otherWorkOnTheLoop': [{'what': name, 'seconds': seconds(second - first),
                                        'startedAfter': seconds(first - began)} for name, first, second in spans],
                'installedPackagesRead': dict(package_reads),
            }
            state = service.state['updates']
            report['result'] = {'phase': state.get('phase'), 'pendingRestart': state.get('pendingRestart'),
                                'record': recovery.read(spec)['status']}

            # Each candidate alone, on this runner, after the confirmation.
            def alone(function, times=3):
                rows = []
                for _ in range(times):
                    first = clock()
                    try:
                        function()
                    except Exception as error:  # A measurement: the time counts, the error is named.
                        rows.append({'seconds': seconds(clock() - first), 'error': type(error).__name__})
                    else:
                        rows.append(seconds(clock() - first))
                return rows
            report['alone'] = {
                'ownerQuestion': alone(lambda: real_owner(manager.home)),
                'saveToSqlite': alone(lambda: service._save_changes(globals={'updates'})),
                'installedGraphAndExtras': alone(lambda: (components.installed_graph(), app_updates.installed_extras())),
                'handOverRecordWrite': alone(lambda: recovery._write(spec, {**record, 'status': 'acknowledged'})),
                'runRecordRead': alone(lambda: windows_task_runtime.observe(spec)),
            }
            report['runner'] = {'python': sys.version.split()[0], 'cpus': os.cpu_count()}
            print('HOST-LOOP-MEASUREMENT ' + json.dumps(report, sort_keys=True))

            assert report['result'] == {'phase': 'installed', 'pendingRestart': None, 'record': 'acknowledged'}
            assert len(questions) == 2, questions
            assert any(name == 'installed packages read' for name, _, _ in spans)
            assert set(package_reads) == {'graph', 'extras'}, package_reads
            assert not any(row['onLoopThread'] for row in questions), 'An owner question ran on the event loop'
            assert all(row['ticksDuring'] >= 1 for row in questions), 'The event loop did not run during an owner question'
            assert all(row['answersDuring'] >= 1 for row in questions), 'No HTTP answer during an owner question'
            assert report['confirmation']['httpNot200'] == 0
        finally:
            stop.set()
            if ticker is not None:
                ticker.cancel()
            if runner is not None:
                await runner.cleanup()
            if sock is not None:
                sock.close()
            if service is not None:
                await service.close()
            shutil.rmtree(home, ignore_errors=True)
