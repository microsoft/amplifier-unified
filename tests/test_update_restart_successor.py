from copy import deepcopy
import json

import pytest

from amplifier_web import __version__, app_updates, update_readiness
from test_update_readiness import ATTEMPT, REVISION, TARGET, health, listener, make_manager


def successor_manager(tmp_path, monkeypatch):
    old_app={'name':'amplifier-unified','version':'0.0.1','url':app_updates.SOURCE,'revision':'e'*40}
    target={**TARGET,'version':old_app['version'],'revision':old_app['revision'],
        'qualification':{'app':old_app,'extras':['native-desktop'],'dependencyDigest':'f'*64}}
    failure={'kind':'application','phase':'restart-readiness','status':'failed',
        'attemptId':ATTEMPT,'revision':old_app['revision'],'errorType':'TimeoutError'}
    service,manager=make_manager(tmp_path,monkeypatch,state={'phase':'activating',
        'pendingRestart':target,'diagnostics':{'lastFailure':failure,'events':[failure]},
        'sequence':{'stage':'application','install':True}})
    app={**old_app,'version':__version__,'revision':REVISION}
    monkeypatch.setattr('amplifier_web.app_features.running_application',lambda _:deepcopy(app))
    monkeypatch.setattr(app_updates,'installed_extras',lambda:['native-desktop'])
    monkeypatch.setattr(app_updates.components,'installed_graph',lambda:[deepcopy(app)])
    calls=[]
    async def process(*args,**kwargs):
        calls.append(args)
        if args[0]=='gh':return json.dumps({'tag_name':'v'+__version__,'draft':False,'prerelease':False})
        if args[0]=='git':return REVISION+'\trefs/tags/v'+__version__
        assert args[1:4]==('-I','-c',app_updates.PROBE) and args[4:]==('native-desktop',)
        return 'AMPLIFIER_UPDATE_PROBE='+json.dumps({'ok':True,'isolated':True,'stage':'complete','version':__version__})
    monkeypatch.setattr(app_updates,'process',process)
    return service,manager,target,failure,calls,process


async def test_healthy_published_successor_retires_old_restart_and_continues_components(tmp_path,monkeypatch):
    service,manager,target,failure,calls,_=successor_manager(tmp_path,monkeypatch)
    try:
        successor=await update_readiness.qualify_successor(manager,target)
        assert successor and service.state['updates']['pendingRestart']==target
        assert await update_readiness.confirm_readiness(manager,health(manager),expected=target,successor=successor)
        state=service.state['updates']
        assert not manager.awaiting_restart() and state['pendingRestart'] is None and state['phase']=='installed'
        assert state['diagnostics']['lastFailure']==failure
        assert state['diagnostics']['latest']['phase']=='restart-superseded'
        assert not any(e['phase']=='restart-ack' for e in state['diagnostics']['events'])
        assert state['reconciliation']['version']==__version__
        assert state['reconciliation']['revision']==REVISION
        assert state['reconciliation']['supersedes']['version']=='0.0.1'
        assert state['application']['latest']=='v'+__version__
        assert state['sequence']['nextStage']=='included' and state['sequence']['install'] is True
        assert len(calls)==3
    finally:
        await service.close()


@pytest.mark.parametrize('broken',['unpublished','wrong_tag','draft','prerelease','wrong_revision','bad_probe','lost_extra','changed_graph','replacement','feature'])
async def test_unverified_or_ineligible_successor_keeps_pending_restart(tmp_path,monkeypatch,broken):
    service,manager,target,failure,calls,original=successor_manager(tmp_path,monkeypatch)
    if broken=='lost_extra':monkeypatch.setattr(app_updates,'installed_extras',lambda:[])
    if broken=='replacement':service.state['updates']['pendingReplacement']={'unqualified':True}
    if broken=='feature':target['featureSelection']={'kind':'add-feature'}
    async def process(*args,**kwargs):
        if args[0]=='gh':
            if broken=='unpublished':raise RuntimeError('release unavailable')
            if broken in {'wrong_tag','draft','prerelease'}:
                return json.dumps({'tag_name':'v0.0.2' if broken=='wrong_tag' else 'v'+__version__,
                    'draft':broken=='draft','prerelease':broken=='prerelease'})
        if args[0]=='git' and broken=='wrong_revision':return 'f'*40+'\trefs/tags/v'+__version__
        if '-I' in args:
            if broken=='bad_probe':return 'AMPLIFIER_UPDATE_PROBE={"ok":false}'
            if broken=='changed_graph':monkeypatch.setattr(app_updates.components,'installed_graph',lambda:[])
        return await original(*args,**kwargs)
    monkeypatch.setattr(app_updates,'process',process)
    try:
        assert await update_readiness.qualify_successor(manager,target) is None
        assert service.state['updates']['pendingRestart']['attemptId']==ATTEMPT
        assert manager.awaiting_restart()
        assert not any(e['phase'] in {'restart-ack','restart-superseded'} for e in manager.diagnostics.state['events'])
    finally:
        await service.close()


@pytest.mark.parametrize('changed',['health','attempt','graph'])
async def test_qualification_cannot_clear_changed_receipt_packages_or_another_listener(tmp_path,monkeypatch,changed):
    service,manager,target,_,_,_=successor_manager(tmp_path,monkeypatch)
    try:
        successor=await update_readiness.qualify_successor(manager,target)
        response=health(manager)
        if changed=='health':response['instanceId']='f'*32
        if changed=='attempt':service.state['updates']['pendingRestart']['attemptId']='f'*32
        if changed=='graph':monkeypatch.setattr(app_updates.components,'installed_graph',lambda:[])
        assert not await update_readiness.confirm_readiness(manager,response,expected=target,successor=successor)
        assert manager.awaiting_restart()
    finally:
        await service.close()


async def test_startup_qualifies_once_and_confirms_the_actual_listener(tmp_path,monkeypatch):
    from aiohttp import web
    service,manager,target,failure,calls,_=successor_manager(tmp_path,monkeypatch)
    async def handle(request):return web.json_response(health(manager))
    runner,site,sock=await listener(manager,handle)
    await site.start()
    try:
        await update_readiness.wait_for_readiness(manager,'fixture-token',timeout=.2,interval=.01)
        assert not manager.awaiting_restart()
        assert len(calls)==3
        assert manager.diagnostics.state['lastFailure']==failure
        assert manager.diagnostics.state['latest']['phase']=='restart-superseded'
    finally:
        await runner.cleanup();sock.close();await service.close()
