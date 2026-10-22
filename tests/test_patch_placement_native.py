"""Production placement qualification: finite subprocesses, no synthetic worker boot.

Executed only in the existing prepared native CI/environment. A missing runtime
fails the check; no importorskip, installs, personal credentials or live homes.
"""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys

import pytest

from test_queued_configuration_boundary import boundary


async def cleanup_workers(root, probe_pid, events):
    """Last-resort cleanup uses exact owned PID/start/argv/group evidence."""
    for path in root.glob('worker-owned-*.json'):
        row = json.loads(path.read_text())
        assert row['probePid'] == probe_pid
        pid = row['pid']
        def alive():
            try:
                fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                assert fields[19] == row['startTicks'], 'PID reused: do not signal'
                if fields[0] == 'Z': return False
                argv = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
                assert str(root).encode() in argv and b'--worker' in argv
                assert os.getpgid(pid) == row['processGroup'] == pid
                return True
            except FileNotFoundError:
                return False
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if not alive(): break
            events.append({'pid':pid,'startTicks':row['startTicks'],'signal':sig.name})
            try: os.killpg(pid,sig)
            except ProcessLookupError: break
            try:
                async with asyncio.timeout(6):
                    while alive(): await asyncio.sleep(.05)
            except TimeoutError:
                continue
        assert not alive(), 'Owned native worker exit not confirmed'


async def wait_probe(process, root, report):
    primary = None
    try:
        await asyncio.wait_for(process.wait(), 240)
    except BaseException as exc:
        primary = exc
        report['primaryFailure'] = {'type':type(exc).__name__,'message':str(exc)}
    finally:
        try:
            if process.returncode is None:
                report['probeSignals'].append('SIGTERM')
                process.terminate()
                try: await asyncio.wait_for(process.wait(),65)
                except BaseException as exc:
                    report['cleanupErrors'].append('probe-grace: '+type(exc).__name__)
            try: await cleanup_workers(root,process.pid,report['workerFallbackSignals'])
            except BaseException as exc:
                report['cleanupErrors'].append('workers: '+type(exc).__name__+': '+str(exc))
        finally:
            if process.returncode is None:
                report['probeSignals'].append('SIGKILL')
                process.kill()
                try: await asyncio.wait_for(process.wait(),5)
                except BaseException as exc:
                    report['cleanupErrors'].append('probe-reap: '+type(exc).__name__)
            report['returncode'] = process.returncode
    if primary is not None:
        primary.add_note(json.dumps(report))
        raise primary
    assert not report['cleanupErrors'], report


def require_normal_cleanup(report, parent):
    assert not parent['probeSignals'] and not parent['workerFallbackSignals'] and not parent['cleanupErrors'], parent
    assert not report['signals'] and not report['cleanupErrors'], report
    assert report['runtimeRowsAfterClose'] == report['preparationsAfterClose'] == 0
    assert report['processes'] and all(p['returncode'] == 0 for p in report['processes'])
    assert report['writerAvailableAfterClose'] is True


@pytest.mark.parametrize('variant', ['cold','parked','parked-remount','when-idle','skip-preservation','retired-control'])
async def test_production_patch_placement_lifecycle(tmp_path, variant):
    fixture = Path(__file__).parent / 'fixtures/patch_placement_native_probe.py'
    root = tmp_path / variant
    output = (Path(os.environ['GITHUB_WORKSPACE']) / 'output/python-checks'
        if os.environ.get('GITHUB_ACTIONS')=='true' else tmp_path) / ('placement-native-'+variant)
    output.mkdir(parents=True,exist_ok=False)
    cleanup = {'probeSignals':[],'workerFallbackSignals':[],'cleanupErrors':[]}
    with (output/'stdout.log').open('wb') as stdout, (output/'stderr.log').open('wb') as stderr:
        process = await asyncio.create_subprocess_exec(sys.executable,'-u',str(fixture),str(root),variant,
            stdout=stdout,stderr=stderr)
        try:
            await wait_probe(process,root,cleanup)
        finally:
            try:
                if (root/'evidence.json').exists():
                    (output/'evidence.json').write_bytes((root/'evidence.json').read_bytes())
            finally:
                (output/'parent-cleanup.json').write_text(json.dumps(cleanup,indent=2)+'\n')
    assert (output/'evidence.json').is_file(), (output/'stderr.log').read_text()[-3000:]
    report = json.loads((output/'evidence.json').read_text())
    require_normal_cleanup(report,cleanup)
    assert report['variant']==variant
    assert report.get('workerReadyAtSettlement') is True, json.dumps({
        'failure':report.get('failure'), 'state':report.get('state'),
        'stages':report.get('stages'), 'stderr':(output/'stderr.log').read_text()[-3000:]})
    assert len(report['workerSources'])==len(report['processes'])
    methods={x['method'] for x in report['workerEvents'] if x['stage']=='production-method-entry'}
    assert {'start','prepare_manager','acquire_for_mutation'} <= methods
    mutant = variant=='skip-preservation'
    assert all(s['mutant'] is mutant for s in report['workerSources'])
    assert all((s['originalConstructorSha256']!=s['executedConstructorSha256']) is mutant for s in report['workerSources'])
    if variant == 'retired-control':
        proof = report['retiredControl']
        assert process.returncode == 0, (output/'stderr.log').read_text()[-3000:]
        assert report['controlHashBefore'] == report['controlHashAfter']
        assert proof['controlHashBeforeRestart'] == proof['controlHashAfterRestart']
        assert proof['oldReturncode'] == 0 and proof['oldPid'] != proof['newPid']
        assert proof['attemptWorkerPid'] == proof['oldPid']
        assert proof['guardPresent'] and proof['writerAvailableAfterRetirement']
        assert proof['writerAvailableAfterRefusal'] and proof['originalRecordPreserved'] and proof['noReplay']
        assert report['state']['effect'] == 'unknown' and report['attempts'] == 1
        assert proof['effectRead']['nativeEffect'] == 'unconfirmed'
        assert 'Native evidence is absent' in proof['reconcileRefusal']
        assert 'retired-start-preserved-original-unknown-held' in report['stages']
    elif mutant:
        assert process.returncode != 0, 'Skipped preservation escaped the same byte oracle'
        assert report['negativeWitness']=='real-startup-preservation-skipped'
        assert report['controlHashBefore']!=report['controlHashAfter']
        assert report['state']['phase']=='error' and report['state']['effect']!='mounted'
        assert 'Placement startup changed original control-state bytes' in (output/'stderr.log').read_text()
    else:
        assert process.returncode == 0, (output/'stderr.log').read_text()[-3000:]
        assert report['state']['phase']=='ready' and report['state']['effect']=='mounted'
        assert report['controlHashBefore']==report['controlHashAfter']
        assert report['attempts']==1 and len(report['processes'])==2
        assert {'binding-settled','field-only-and-actual-replacement-verified','exact-duplicate-no-effect',
                'fresh-read-write-and-park','next-generation-and-park-usable'} <= set(report['stages'])
        assert 'park' in methods
        if variant in {'parked','parked-remount','when-idle'}:
            assert 'real-initial-startup-and-park' in report['stages']
            assert any(x['stage']=='production-method-entry' and x['method']=='acquire_for_mutation'
                and x['parked'] for x in report['workerEvents'])
        if variant == 'parked-remount':
            assert report['staleRemountRequested'] and report['staleRemountObserved']
            assert 'remount' in methods
            recovery = report['explicitRecovery']
            assert recovery['originalState']['effect'] == 'unknown'
            assert recovery['effectRead']['nativeEffect'] == 'none'
            assert recovery['reconciled']['state']['effect'] == 'none'
            assert recovery['reconciled']['state']['stage'] == 'reconciled'
            assert recovery['cancelledState']['phase'] == 'cancelled'
            assert recovery['cancelledHistory'][:len(recovery['originalHistory'])] == recovery['originalHistory']
            assert recovery['freshOperationId'] != recovery['originalOperationId']
            assert recovery['freshCommandId'] != recovery['originalCommandId']
            assert recovery['remountedObjectId'] != recovery['originalMountedObjectId']
            assert recovery['originalRecordPreserved'] and recovery['noOriginalReplay']
            assert 'explicit-none-cancel-fresh-binding' in report['stages']
        if variant != 'when-idle':
            assert report['noBindingProviderReplay']
            assert report['nextSuccessfulResponse']['inputId'] == 'placement-next-input'
            assert report['nextSuccessfulResponse']['outcome'] == 'generation.finished'
            assert report['nextSuccessfulResponse']['text'] == 'placement-finite-turn-complete'
        if variant=='when-idle':
            assert 'actual-generation-queued-binding' in report['stages']
            assert report['originalInputCount']==report['originalBindToolCount']==1
            assert report['controlHashAtRead']==report['controlHashAtBind']==report['controlHashAfter']


@pytest.mark.parametrize('source', ['parent-signal','parent-worker','child-signal','child-error'])
def test_native_placement_cleanup_fallback_is_not_qualification(source):
    parent={'probeSignals':[],'workerFallbackSignals':[],'cleanupErrors':[]}
    report={'signals':[],'cleanupErrors':[],'runtimeRowsAfterClose':0,'preparationsAfterClose':0,
        'processes':[{'pid':700001,'returncode':0}],'writerAvailableAfterClose':True}
    if source=='parent-signal': parent['probeSignals'].append('SIGTERM')
    if source=='parent-worker': parent['workerFallbackSignals'].append({'pid':700001,'signal':'SIGKILL'})
    if source=='child-signal': report['signals'].append({'pid':700001,'signal':'SIGTERM'})
    if source=='child-error': report['cleanupErrors'].append('Synthetic ordinary close failure')
    with pytest.raises(AssertionError):
        require_normal_cleanup(report,parent)


@pytest.mark.parametrize('case', ['success','failed-but-parked','previous-input','wrong-response'])
def test_native_placement_next_response_requires_bound_success(case):
    from fixtures.patch_placement_native_probe import require_next_success
    owner = {'id':'synthetic-root', 'status':'idle',
        'collaborationGeneration':{'id':'next-generation','terminal':True},
        'generations':[{'event':'generation.finished','generation_id':'next-generation',
            'sessionId':'synthetic-root','rootSessionId':'synthetic-root','input_ids':['placement-next-input']}],
        'messages':[{'id':'next-final','role':'assistant','inputId':'placement-next-input',
            'generationId':'next-generation','text':'placement-finite-turn-complete',
            'response':{'status':'completed','phase':'final'}}]}
    parked = True  # The old terminal/input/park predicate permits every case.
    if case == 'failed-but-parked':
        owner['generations'][0]['event'] = 'generation.failed'
    elif case == 'previous-input':
        owner['messages'][0]['inputId'] = 'placement-warm-input'
    elif case == 'wrong-response':
        owner['messages'][0]['text'] = 'unrelated response'
    assert parked and owner['collaborationGeneration']['terminal']
    if case == 'success':
        assert require_next_success(owner)['messageId'] == 'next-final'
    else:
        with pytest.raises(AssertionError, match='Next generation did not succeed|Missing exact successful final response'):
            require_next_success(owner)


def placement_provider_module():
    from types import ModuleType
    from fixtures.patch_placement_native_probe import PROVIDER
    module = ModuleType('placement_metadata_contract')
    exec(compile(PROVIDER, '<placement-provider-fixture>', 'exec'), module.__dict__)
    return module


async def test_placement_provider_supports_actual_metadata_constructor():
    from amplifier_web.provider_environment import construct_schema_provider, config_schema, close_provider
    module = placement_provider_module()
    supplied = {'scenario':'synthetic-not-read', 'default_model':'offline-placement'}
    provider = construct_schema_provider(module.PlacementProvider, supplied)
    try:
        assert provider.coordinator is None
        assert provider.config == {} and provider.config is not supplied
        assert await config_schema(provider) == {'fields':[]}
        assert provider.get_info().defaults['model'] == 'offline-placement'
        assert supplied == {'scenario':'synthetic-not-read', 'default_model':'offline-placement'}
    finally:
        await close_provider(provider)


async def test_placement_provider_real_mount_preserves_arguments():
    from amplifier_core import ModuleCoordinator
    module = placement_provider_module()
    coordinator = ModuleCoordinator()
    config = {'scenario':'synthetic-not-read', 'default_model':'offline-placement'}
    try:
        await module.mount(coordinator, config)
        provider = coordinator.get('providers')['placement']
        assert isinstance(provider, module.PlacementProvider)
        assert provider.coordinator is coordinator
        assert provider.config is config
        assert config == {'scenario':'synthetic-not-read', 'default_model':'offline-placement'}
    finally:
        try:
            await coordinator.unmount('providers', name='placement')
        finally:
            await coordinator.cleanup()


@pytest.mark.parametrize('evidence_kind', ['none','missing','unknown','saved','nonterminal','conflicting'])
async def test_explicit_rebind_requires_authenticated_terminal_none(boundary, evidence_kind):
    """Real public recovery/cancel; only signed effect faults and transport are synthetic."""
    import copy
    from amplifier_web.configuration_recovery import receipt_path, seal
    from amplifier_web.service import AppError
    from fixtures.patch_placement_native_probe import reconcile_proven_none
    from test_configuration_authority import finished
    from test_queued_configuration_boundary import native_refusal, operation
    b = boundary
    original = await native_refusal(b)
    attempt = original['attempts'][-1]
    receipt = receipt_path(attempt)
    if evidence_kind == 'missing':
        receipt.unlink()
    elif evidence_kind in {'unknown','saved','nonterminal','conflicting'}:
        effect = {'unknown':'unknown','saved':'saved_not_mounted',
                  'nonterminal':'none','conflicting':'none'}[evidence_kind]
        value = seal(attempt, effect, **({} if evidence_kind == 'nonterminal' else {'terminal':True}))
        if evidence_kind == 'conflicting':
            value['body']['attemptId'] = 'not-the-original-attempt'
        receipt.write_text(json.dumps(value))
    calls_before = list(b.runtime.calls)
    controls_before = b.runtime.controls.state_path().read_bytes()
    commands = []
    async def action(name, args, identity):
        commands.append(name)
        return await b.app.dispatch(name, args, command_id=identity, include_state=False)
    # Disposal BEFORE classification must refuse even when a valid none receipt
    # exists. The original public state is still unknown.
    await action('configuration.cancel', {'id':b.owner['id']}, 'premature-cancel')
    refused = await finished(b.app, 'premature-cancel')
    assert refused['phase'] == 'error' and 'unresolved' in refused['error']
    assert operation(b) == original and b.owner['configurationBusy']
    retained = {}
    if evidence_kind == 'none':
        await reconcile_proven_none(action, b.owner['id'], original['id'], retained)
        reconciled = copy.deepcopy(operation(b))
        assert reconciled['request'] == original['request']
        assert reconciled['attempts'] == original['attempts']
        assert reconciled['history'][:-1] == original['history']
        await action('configuration.cancel', {'id':b.owner['id']}, 'proven-none-cancel')
        assert (await finished(b.app, 'proven-none-cancel'))['phase'] == 'ready'
        cancelled = b.app.management.configuration_record(b.owner['id'], original['id'])
        assert cancelled['state']['phase'] == 'cancelled' and cancelled['state']['effect'] == 'none'
        assert cancelled['request'] == original['request'] and cancelled['attempts'] == original['attempts']
        assert cancelled['history'][:-1] == reconciled['history']
        assert not b.owner.get('pendingConfiguration') and not b.owner.get('configurationBusy')
    else:
        expected = {'missing':'Authenticated native no-effect is unproven',
                    'unknown':'Authenticated native no-effect is unproven',
                    'saved':'Authenticated native no-effect is unproven',
                    'nonterminal':'not terminal', 'conflicting':'different binding'}[evidence_kind]
        with pytest.raises((AssertionError, AppError), match=expected):
            await reconcile_proven_none(action, b.owner['id'], original['id'], retained)
        assert operation(b) == original and b.owner['configurationBusy']
        assert commands.count('configuration.cancel') == 1  # No disposal after the failed proof.
        assert 'configuration.patchPlacement.bind' not in commands
    assert b.runtime.calls == calls_before
    assert b.runtime.controls.state_path().read_bytes() == controls_before


@pytest.mark.parametrize(('op', 'operation', 'preserved'), [
    ('control', 'configuration.patchPlacement.bind', True),
    ('control', 'configuration.apply', False),
    ('control', 'tool.invoke', False),
    ('send', '', False), ('retry', '', False), ('resume', '', False)])
async def test_retired_request_preservation_is_exactly_placement(monkeypatch, op, operation, preserved):
    from amplifier_web.runtime import RuntimeManager
    manager = RuntimeManager()
    session = {'id':'retired-fixture'}
    async def emit(*args): pass
    manager._retired[session['id']] = (session, emit)
    seen, checks = [], []
    def guard(): checks.append('authority')
    class StartupObserved(Exception): pass
    async def start(saved, callback, **kwargs):
        seen.append((saved, callback, kwargs))
        raise StartupObserved()  # No process or transport needed for forwarding contract.
    monkeypatch.setattr(manager, '_start_locked', start)
    try:
        # A caller-supplied lookalike must not opt ordinary operations into
        # preservation. The host decision uses only the exact operation.
        with pytest.raises(StartupObserved):
            await manager._request(session['id'], op, guard=guard,
                **({'operation':operation, 'arguments':{'preserve_configuration':True,
                    '_preserveConfiguration':True}} if op == 'control' else {}))
        assert len(seen) == 1 and seen[0][:2] == (session, emit) and checks
        assert seen[0][2] == {'guard':guard, **({'preserve_configuration':True} if preserved else {})}
    finally:
        await manager.close()


@pytest.mark.parametrize('boundary', ['missing-guard','revoked-before-start','revoked-after-start'])
async def test_retired_placement_requires_original_host_guard(monkeypatch, boundary):
    from amplifier_web.runtime import RuntimeManager
    manager = RuntimeManager()
    async def emit(*args): pass
    manager._retired['retired-fixture'] = ({'id':'retired-fixture'}, emit)
    starts, admissions, checks = [], [], []
    def guard():
        checks.append('check')
        if boundary == 'revoked-before-start' or len(checks) > 1:
            raise ValueError('Synthetic original authority revoked')
    async def start(*args, **kwargs): starts.append(kwargs)
    async def admit(*args, **kwargs):
        admissions.append(True)
        raise AssertionError('Denied placement reached native dispatch')
    monkeypatch.setattr(manager, '_admit', admit)
    if boundary != 'missing-guard':
        monkeypatch.setattr(manager, '_start_locked', start)
    else:
        def no_preparation(*args, **kwargs):
            raise AssertionError('Unguarded placement reached startup preparation')
        monkeypatch.setattr(manager, '_check_execution', no_preparation)
    try:
        match = 'retained host operation guard' if boundary == 'missing-guard' else 'original authority revoked'
        with pytest.raises(ValueError, match=match):
            await manager.control('retired-fixture', 'configuration.patchPlacement.bind',
                {}, **({'guard':guard} if boundary != 'missing-guard' else {}))
        assert not admissions
        assert len(starts) == (1 if boundary == 'revoked-after-start' else 0)
        if starts:
            assert starts[0] == {'guard':guard, 'preserve_configuration':True}
    finally:
        await manager.close()
