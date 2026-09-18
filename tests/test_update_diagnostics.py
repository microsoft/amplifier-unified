import json
import sys
from types import SimpleNamespace
import pytest
from amplifier_web import app_updates
from amplifier_web.updates import process,UpdateManager
from amplifier_web.update_diagnostics import CommandFailure,CommandTimeout,PROBE_PREFIX,probe_record
from amplifier_web.service import AppService
from test_service import Runtime
from test_app_updates import prepared_activation


async def test_subprocess_failure_retains_safe_classification_not_output(tmp_path):
    secret='fixture-sensitive-credential'
    code="import sys; print('"+secret+"'); sys.stderr.write('ImportError: '+ '"+secret+"'); raise SystemExit(17)"
    with pytest.raises(CommandFailure) as caught:await process(sys.executable,'-c',code)
    facts=caught.value.diagnostic_facts
    assert facts['exitCode']==17 and facts['errorType']=='ImportError'
    assert facts['stdoutBytes']>0 and facts['stderrBytes']>0
    assert secret not in json.dumps(facts)+str(caught.value)


async def test_command_timeout_has_distinct_diagnostic():
    with pytest.raises(CommandTimeout) as caught:
        await process(sys.executable,'-c','import time; time.sleep(30)',timeout=.05)
    assert caught.value.diagnostic_facts['timedOut'] is True
    assert caught.value.diagnostic_facts['errorType']=='TimeoutError'


def test_probe_frame_filters_private_fields_and_nonversion_output():
    raw=PROBE_PREFIX+json.dumps({'ok':True,'version':'0.6.4','stage':'complete','private':'secret','path':'/private/path','errorType':'secret'})
    assert probe_record('provider warning\n'+raw)=={'ok':True,'version':'0.6.4','stage':'complete'}
    assert probe_record(PROBE_PREFIX+'not-json') is None
    assert probe_record(PROBE_PREFIX+json.dumps({'ok':False,'stage':[],'errorType':{},'version':[]}))=={'ok':False}


async def test_failed_replacement_probe_has_phase_and_correlation_without_termination(tmp_path,monkeypatch):
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    async def command(*args,**kwargs):
        if '-c' in args:
            raise CommandFailure({'exitCode':1,'durationMs':42,'stdoutBytes':93,'stderrBytes':0,
                'probe':{'ok':False,'stage':'imports','errorType':'ModuleNotFoundError','private':'secret'}})
        return ''
    monkeypatch.setattr(app_updates,'process',command)
    monkeypatch.setattr(app_updates.os,'kill',lambda *args:pytest.fail('no termination'))
    await app_updates.activate(manager)
    updates=service.state['updates'];failure=updates['diagnostics']['lastFailure']
    assert failure['phase']=='replacement-probe' and failure['exitCode']==1
    assert failure['attemptId'] and failure['commandId']
    assert failure['probe']['errorType']=='ModuleNotFoundError'
    assert 'replacement probe' in updates['error']
    await service.diagnostics.flush()
    text=json.dumps(service.diagnostics.read(stream='updates'))
    assert 'secret' not in text and '/fixture' not in text
    assert service.diagnostics.path.stat().st_mode&0o777==0o600
    assert updates['pendingApp'] is None
    await service.close()


async def test_framed_probe_accepts_warnings_and_reports_actual_mismatch(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    manager.diagnostics.begin('application','a'*40)
    output='warning: third-party startup message\n'+PROBE_PREFIX+json.dumps({'ok':True,'version':'99.0.0','stage':'complete'})
    assert app_updates.verified_version(manager,output,'v99.0.0','candidate-version')=='99.0.0'
    with pytest.raises(ValueError):app_updates.verified_version(manager,output,'98.0.0','candidate-version')
    assert manager.diagnostics.state['lastFailure']['observedVersion']=='99.0.0'
    assert manager.diagnostics.state['lastFailure']['expectedVersion']=='98.0.0'
    await service.close()


async def test_restart_success_clears_current_failure_and_preserves_history(tmp_path):
    from amplifier_web import __version__
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    failure={'phase':'replacement-probe','status':'failed'}
    service.state['updates']={'error':'prior failure','diagnostics':{'lastFailure':failure,'events':[failure]},
        'pendingRestart':{'version':__version__,'revision':'a'*40,'attemptId':'b'*32}}
    manager=UpdateManager(service);service.update_manager=manager
    updates=service.state['updates']
    assert updates['error'] is None and 'lastFailure' not in updates['diagnostics']
    assert updates['diagnostics']['events'][0]==failure
    assert updates['diagnostics']['latest']['phase']=='restart-ack'
    assert updates['diagnostics']['latest']['attemptId']=='b'*32
    await service.close()


async def test_collector_receives_only_structured_event_and_owns_durable_sink(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path);received=[]
    service.diagnostics.record=lambda *args:received.append(args)
    manager=UpdateManager(service);service.update_manager=manager
    manager.diagnostics.begin('application','a'*40)
    manager.diagnostics.record('candidate-install','failed',errorType='RuntimeError',stdout='secret')
    assert received[0][0]=='updates' and received[0][1]['event']=='update:candidate-install'
    assert 'secret' not in json.dumps(received)
    assert not (manager.directory/'diagnostics.jsonl').exists()
    await service.close()


async def test_candidate_install_failure_cannot_replace_host_or_become_generic(tmp_path,monkeypatch):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    service.state['updates']['application']={'status':'update','revision':'a'*40,'latest':'99.0.0'}
    monkeypatch.setattr(app_updates.shutil,'which',lambda name:'/fixture/tool')
    async def command(*args,**kwargs):raise CommandFailure({'exitCode':2,'durationMs':1,'stdoutBytes':0,'stderrBytes':7})
    monkeypatch.setattr(app_updates,'process',command)
    monkeypatch.setattr(app_updates,'installed_target',lambda:pytest.fail('no replacement after failed staging'))
    await manager.command('app')
    assert service.state['updates']['diagnostics']['lastFailure']['phase']=='candidate-install'
    assert 'candidate install' in service.state['updates']['error']
    assert not (manager.directory/'previous-app.json').exists()
    await service.close()


async def test_restart_helper_failure_keeps_host_and_distinguishes_installed_package(tmp_path,monkeypatch):
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    async def command(*args,**kwargs):return '99.0.0' if '-c' in args else ''
    async def spawn(*args,**kwargs):raise PermissionError('fixture sensitive launch detail')
    monkeypatch.setattr(app_updates,'process',command)
    monkeypatch.setattr(app_updates.asyncio,'create_subprocess_exec',spawn)
    monkeypatch.setattr(app_updates.os,'kill',lambda *args:pytest.fail('no termination'))
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed',lambda home:False)
    await app_updates.activate(manager)
    failure=service.state['updates']['diagnostics']['lastFailure']
    assert failure['phase']=='restart-helper' and failure['errorType']=='PermissionError'
    assert 'installed' in service.state['updates']['error']
    assert service.state['updates']['pendingRestart'] is None
    assert 'sensitive' not in json.dumps(service.state['updates'])
    await service.close()


async def test_cancelled_application_replacement_clears_automatic_pending_retry(tmp_path,monkeypatch):
    import asyncio
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    async def command(*args,**kwargs):raise asyncio.CancelledError()
    monkeypatch.setattr(app_updates,'process',command)
    with pytest.raises(asyncio.CancelledError):await app_updates.activate(manager)
    updates=service.state['updates']
    assert updates['phase']=='interrupted' and updates['pendingApp'] is None
    assert updates['diagnostics']['lastFailure']['phase']=='replacement-install'
    assert updates['diagnostics']['lastFailure']['status']=='interrupted'
    await service.close()
