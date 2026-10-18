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


@pytest.mark.parametrize('variant', ['cold','parked','parked-remount','when-idle','skip-preservation'])
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
    assert report['variant']==variant and report['workerReadyAtSettlement']
    assert len(report['workerSources'])==len(report['processes'])
    methods={x['method'] for x in report['workerEvents'] if x['stage']=='production-method-entry'}
    assert {'start','prepare_manager','acquire_for_mutation'} <= methods
    mutant = variant=='skip-preservation'
    assert all(s['mutant'] is mutant for s in report['workerSources'])
    assert all((s['originalConstructorSha256']!=s['executedConstructorSha256']) is mutant for s in report['workerSources'])
    if mutant:
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
