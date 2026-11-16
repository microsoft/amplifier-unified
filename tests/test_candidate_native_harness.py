"""Pure phase-one controls; no Docker, candidate builds, installs or native loads."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import candidate_native_protocol as p
sys.path.insert(0, str(SCRIPTS.parent / 'tests/fixtures'))
import candidate_native_adapter as adapter
import candidate_native_control as control


@pytest.fixture
def nomination():
    return {'repository': p.REPOSITORY, 'harness': 'a' * 40, 'harnessTree': 'b' * 40,
            'candidate': 'c' * 40, 'candidateTree': 'd' * 40, 'pr': 2134}


@pytest.fixture
def facts(nomination):
    n = nomination
    return {'repository': {'full_name': p.REPOSITORY, 'default_branch': 'main'},
            'run': {'id': 123, 'run_attempt': 1, 'event': 'workflow_dispatch',
                    'head_sha': n['harness'], 'path': p.WORKFLOW, 'status': 'in_progress',
                    'conclusion': None, 'head_repository': {'full_name': p.REPOSITORY}},
            'harness': {'sha': n['harness'], 'tree': {'sha': n['harnessTree']}},
            'candidate': {'sha': n['candidate'], 'tree': {'sha': n['candidateTree']}},
            'ancestry': {'status': 'behind'},
            'rules': [{'type': 'required_status_checks', 'parameters': {'required_status_checks': [{'context': 'Fast release checks'}]}},
                      {'type': 'pull_request', 'parameters': {'required_approving_review_count': 1}}],
            'pr': {'number': n['pr'], 'state': 'open',
                   'base': {'ref': 'main', 'repo': {'full_name': p.REPOSITORY}},
                   'head': {'sha': n['candidate'], 'repo': {'full_name': p.REPOSITORY}}}}


def test_only_independent_nominated_facts_issue_test_selection(nomination, facts):
    v = p.select(nomination, facts, now=100)
    assert v['kind'] == p.KIND and v['profiles'] == ['python-project', 'browser-test']
    assert v['run'] == 123 and v['attempt'] == 1 and v['expires'] == 3700
    assert 'artifact' not in v and 'serving' not in v and 'delivery' not in v


@pytest.mark.parametrize('change', ['missing-h', 'fork', 'workflow', 'h', 'h-tree', 'c-tree', 'moved-c',
                                   'cancelled', 'wrong-event', 'unprotected', 'unaccepted-h'])
def test_nonnominated_or_changed_independent_facts_refuse(nomination, facts, change):
    if change == 'missing-h': nomination['harness'] = ''
    elif change == 'fork': facts['pr']['head']['repo']['full_name'] = 'fork/candidate'
    elif change == 'workflow': facts['run']['path'] = '.github/workflows/other.yml'
    elif change == 'h': facts['run']['head_sha'] = 'e' * 40
    elif change == 'h-tree': facts['harness']['tree']['sha'] = 'e' * 40
    elif change == 'c-tree': facts['candidate']['tree']['sha'] = 'e' * 40
    elif change == 'moved-c': facts['pr']['head']['sha'] = 'e' * 40
    elif change == 'cancelled': facts['run']['status'] = 'completed'; facts['run']['conclusion'] = 'cancelled'
    elif change == 'wrong-event': facts['run']['event'] = 'pull_request_target'
    elif change == 'unprotected': facts['rules'] = []
    else: facts['ancestry']['status'] = 'ahead'
    with pytest.raises(ValueError): p.select(nomination, facts, now=100)


@pytest.fixture
def selection(nomination, facts):
    return p.select(nomination, facts, now=100)


@pytest.fixture
def plan(selection):
    return p.registration_plan(selection, profile='python-project', owner='owner', root='/tmp/owned',
        source='/tmp/source', backend={'backend': 'docker', 'daemon': 'daemon'},
        resource='resource', launch='launch', universe='dtu-op-owned', source_digest='f' * 64,
        nonce='1' * 64)


def test_registration_is_exact_single_use_before_effect(selection, plan, tmp_path):
    ledger = p.Registry(selection, tmp_path / 'ledger.json', clock=lambda: 200)
    ack = ledger.register(plan)
    ledger.consume(plan, ack, action='start')
    with pytest.raises(ValueError, match='consumed'): ledger.consume(plan, ack, action='start')
    assert json.loads((tmp_path / 'ledger.json').read_text())['entries'][0]['state'] == 'consumed'


@pytest.mark.parametrize('change', ['run', 'attempt', 'owner', 'resource', 'action', 'nonce', 'digest', 'extra'])
def test_registration_ack_cannot_cross_scope(selection, plan, tmp_path, change):
    ledger = p.Registry(selection, tmp_path / 'ledger.json', clock=lambda: 200)
    ack = ledger.register(plan)
    changed = copy.deepcopy(plan)
    if change == 'digest': ack['planDigest'] = '0' * 64
    elif change == 'extra': ack['untrusted'] = True
    elif change in ('run', 'attempt'): changed[change] += 1
    elif change == 'action': changed['action'] = 'remove'
    else: changed[change] = 'other'
    with pytest.raises(ValueError): ledger.consume(changed, ack, action='start')


def test_expiry_cancellation_and_unknown_never_admit_next_owner(selection, plan, tmp_path):
    now = [200]
    ledger = p.Registry(selection, tmp_path / 'ledger.json', clock=lambda: now[0])
    ack = ledger.register(plan)
    now[0] = selection['expires'] + 1
    with pytest.raises(ValueError): ledger.consume(plan, ack, action='start')
    now[0] = 200
    ledger.cancel()
    with pytest.raises(ValueError): ledger.consume(plan, ack, action='start')
    other = p.Registry(selection, tmp_path / 'other.json', clock=lambda: 200)
    other.consume(plan, other.register(plan), action='start')
    other.settle('resource', cleanup='unknown', absent=False)
    with pytest.raises(ValueError): other.register({**plan, 'resource': 'next', 'nonce': '2' * 64})


def test_arbitrary_file_is_not_a_registration_ack(selection, plan, tmp_path):
    marker = tmp_path / 'registered.json'; marker.write_text('{}')
    ledger = p.Registry(selection, tmp_path / 'ledger.json', clock=lambda: 200)
    with pytest.raises(ValueError): ledger.consume(plan, json.loads(marker.read_text()), action='start')


def test_failed_cleanup_and_missing_duplicate_or_skipped_cases_cannot_qualify(selection):
    good = p.empty_report(selection, wheel='f' * 64)
    good['cases'] = [dict(profile=profile, passed=True, nativeMap=True, tmpNoexec=True,
                          cleanup='completed', absent=True, sourceUnchanged=True,
                          artifactSha256='e' * 64) for profile in p.PROFILES]
    xml = '<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0">' + ''.join(
        '<testcase classname="test_candidate_native_runtime" name="test_prepared_runtime[' + x + ']"/>' for x in p.PROFILES) + '</testsuite></testsuites>'
    p.verify_cases(good, xml.encode(), selection)
    for damage in ('missing', 'duplicate', 'skip', 'cleanup', 'wheel', 'kind'):
        v = copy.deepcopy(good); raw = xml
        if damage == 'missing': v['cases'].pop()
        elif damage == 'duplicate': v['cases'][1] = v['cases'][0]
        elif damage == 'skip': raw = raw.replace('/>', '><skipped/></testcase>', 1)
        elif damage == 'cleanup': v['cases'][0]['cleanup'] = 'unknown'
        elif damage == 'wheel': v['wheelSha256'] = ''
        else: v['kind'] = 'official-release'
        with pytest.raises(ValueError): p.verify_cases(v, raw.encode(), selection)


def test_candidate_test_record_is_rejected_by_unchanged_production_validators(selection, tmp_path):
    from amplifier_web import app_release_artifacts as a, app_component_graph as g
    with pytest.raises(ValueError): a.validate_descriptor(selection, '0.97.1')
    with pytest.raises(ValueError): g.validate_graph([selection])
    from amplifier_web.environment_cleanup_execution import Gate
    from types import SimpleNamespace
    with pytest.raises(ValueError):
        Gate(SimpleNamespace(update_manager=SimpleNamespace(_running_artifact=None), instance_id='test'))


def test_no_environment_flag_or_actions_url_issues_authority(monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    for fake in ({}, {'harness': 'a' * 40}, {'url': 'https://github.com/microsoft/amplifier-unified/actions/runs/1',
                  'sha256': 'b' * 64, 'stamp': {}, 'RECORD': 'x'}):
        with pytest.raises(ValueError): p.select(fake, {}, now=100)


@pytest.mark.parametrize('wrong', ['CANDIDATE', 'CANDIDATE_TREE', 'CANDIDATE_PR', 'missing'])
def test_dispatch_inputs_cannot_nominate_their_own_candidate(wrong):
    nominated = {'CANDIDATE': 'a' * 40, 'CANDIDATE_TREE': 'b' * 40, 'CANDIDATE_PR': '2134'}
    requested = {'REQUESTED_C': 'a' * 40, 'REQUESTED_C_TREE': 'b' * 40, 'REQUESTED_PR': '2134'}
    p.validate_request(nominated, requested)
    if wrong == 'missing': nominated = {}
    else: nominated[wrong] = 'other'
    with pytest.raises(ValueError): p.validate_request(nominated, requested)


@pytest.fixture
def test_inputs():
    raw = {'amplifier_web/' + n: ('# synthetic ' + n).encode() for n in adapter.MEMBERS}
    return SimpleNamespace(raw=raw, sha256='f' * 64, installation={'prefix': sys.prefix},
                           recheck=lambda: None)


@pytest.mark.parametrize('damage', ['pid', 'start', 'dead', 'interpreter', 'prefix', 'nonce', 'members'])
def test_test_gate_reuses_real_lifecycle_refusals(test_inputs, damage):
    registry = SimpleNamespace(recheck=lambda *a, **k: None, selection={})
    gate = adapter.CandidateTestGate(test_inputs, registry, 'resource')
    child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(20)'])
    try:
        start = Path('/proc', str(child.pid), 'stat').read_text().rsplit(')', 1)[1].split()[19]
        ready = {'schema': 1, 'phase': 'loaded', 'nonce': gate.nonce, 'pid': child.pid,
                 'start': start, 'interpreter': sys.executable,
                 'prefix': str(Path(sys.executable).parent.parent), 'members': gate.members}
        assert gate.validate(ready, child, sys.executable) == (str(child.pid), start)
        if damage == 'dead':
            child.terminate(); child.wait(timeout=5)
        elif damage == 'pid': ready['pid'] += 1
        elif damage == 'start': ready['start'] = 'wrong'
        elif damage == 'members': ready['members'] = []
        else: ready[damage] = 'wrong'
        with pytest.raises(ValueError): gate.validate(ready, child, sys.executable)
    finally:
        if child.poll() is None: child.kill()
        child.wait(timeout=5)


def test_test_gate_rechecks_stop_after_wait(test_inputs):
    stopped = [False]
    def recheck(*args, **kwargs):
        p.require(not stopped[0], 'Stop after wait')
    gate = adapter.CandidateTestGate(test_inputs, SimpleNamespace(recheck=recheck), 'resource')
    stopped[0] = True
    with pytest.raises(ValueError, match='Stop'): gate.recheck()


def test_adapter_is_scoped_and_restores_original_without_changing_permit(monkeypatch, test_inputs, plan):
    from amplifier_web import task_environments as owner
    app, another = object(), object()
    events = []
    original = lambda *a: 'original-other-app'
    monkeypatch.setattr(owner, 'invoke_cleanup', original)
    monkeypatch.setattr(owner.storage, 'directory', lambda *a: Path(plan['root']))
    def invoke(config, home, row, action, args, **kwargs):
        events.append(kwargs)
        return 'test-invoke'
    monkeypatch.setattr(owner, 'invoke', invoke)
    app = SimpleNamespace(data_dir=Path('/test'))
    snapshot = {'archive_sha256': 'a' * 64}
    registry = SimpleNamespace(recheck=lambda *a, **k: None, selection={},
        value={'entries': [{'plan': plan, 'snapshot': snapshot}]})
    row = {'id': plan['resource'], 'sessionId': plan['owner'], 'launchOperationId': plan['launch'],
           'universeId': plan['universe'], 'source': plan['source'], 'backendIdentity': plan['backend'],
           'snapshot': snapshot}
    permit = object()
    with adapter.candidate_test_cleanup(app, test_inputs, registry, row['id']):
        assert owner.invoke_cleanup(another, {}, row, 'remove', {}, permit) == 'original-other-app'
        assert owner.invoke_cleanup(app, {'backend': 'docker'}, row, 'remove', {}, permit) == 'test-invoke'
        assert events[0]['_cleanup_permit'] is permit
        assert isinstance(events[0]['_cleanup_gate'], adapter.CandidateTestGate)
        with pytest.raises(ValueError):
            owner.invoke_cleanup(app, {'backend': 'docker'}, {**row, 'id': 'other'}, 'remove', {}, permit)
    assert owner.invoke_cleanup is original


@pytest.mark.parametrize('late_stop', [False, True])
def test_same_invoke_retains_durable_permit_and_uncertain_delivery_without_replay(
        tmp_path, monkeypatch, test_inputs, late_stop):
    """Real pipe/PID/commit boundary, synthetic child instead of Docker/backend."""
    from amplifier_web import task_environments as owner
    from amplifier_web.environment_cleanup_execution import digest
    registry = SimpleNamespace(recheck=lambda *a, **k: None, selection={})
    gate = adapter.CandidateTestGate(test_inputs, registry, 'resource')
    real_popen = subprocess.Popen
    sentinel = tmp_path / 'effect'
    calls = []
    script = """import json,os,sys
first=json.loads(sys.stdin.readline()); second=json.loads(sys.stdin.readline())
ready=json.loads(sys.argv[1]);ready['pid']=os.getpid()
ready['start']=open('/proc/self/stat').read().rsplit(')',1)[1].split()[19]
print(json.dumps(ready),flush=True)
line=sys.stdin.readline()
if line:
    permit=json.loads(line)
    assert permit['nonce']==ready['nonce']
    open(sys.argv[2],'w').write('possibly-delivered')
raise SystemExit(1)
"""
    ready = {'schema': 1, 'phase': 'loaded', 'nonce': gate.nonce, 'pid': 1, 'start': '',
             'interpreter': sys.executable, 'prefix': str(Path(sys.executable).parent.parent),
             'members': gate.members}
    def popen(*args, **kwargs):
        calls.append(args)
        return real_popen([sys.executable, '-I', '-c', script, json.dumps(ready), str(sentinel)], **kwargs)
    monkeypatch.setattr(owner.subprocess, 'Popen', popen)
    monkeypatch.setattr(owner.storage, 'directory', lambda *a: tmp_path)
    def permit(proof, lifecycle):
        assert proof['kind'] == p.KIND and 'serving' not in proof
        assert proof['ready']['pid'] == int(lifecycle[0])
        p.save(tmp_path / 'permit.json', proof)
        def final_guard():
            assert json.loads((tmp_path / 'permit.json').read_text()) == proof
            p.require(not late_stop, 'Stop after durable permit')
        return final_guard
    with pytest.raises(ValueError):
        owner.invoke({'python': sys.executable, 'dockerHost': 'unix:///synthetic'}, tmp_path,
            {'snapshot': {}, 'source': str(tmp_path), 'id': 'resource'}, 'remove',
            {'requestId': 'original-unknown', 'timeoutSeconds': 5}, _cleanup_gate=gate, _cleanup_permit=permit)
    assert len(calls) == 1 and (tmp_path / 'permit.json').exists()
    assert sentinel.exists() is (not late_stop)


def test_wheel_capture_refuses_changed_digest_or_source_and_record(tmp_path, monkeypatch):
    import base64, csv, io
    path = tmp_path / 'candidate.whl'
    records, blobs, data = [], {}, {}
    for name in adapter.SOURCES:
        member = 'amplifier_web/' + name
        raw = ('# synthetic ' + name).encode(); data[member] = raw
        records.append([member, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip('='), str(len(raw))])
        blobs[member] = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        dest = tmp_path / 'installed' / member; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(raw)
    text = io.StringIO(); csv.writer(text).writerows(records)
    def build(record_text):
        with zipfile.ZipFile(path, 'w') as z:
            for member, raw in data.items(): z.writestr(member, raw)
            z.writestr('synthetic.dist-info/RECORD', record_text)
        return hashlib.sha256(path.read_bytes()).hexdigest()
    selected = build(text.getvalue())
    # Isolate archive byte-join unit tests from installation/import assertions;
    # this is not used by the real native test or claimed installed evidence.
    monkeypatch.setattr(adapter.WheelInputs, 'recheck', lambda self: None)
    assert len(adapter.WheelInputs(path, selected, blobs, tmp_path / 'installed').raw) == len(adapter.SOURCES)
    with pytest.raises(ValueError): adapter.WheelInputs(path, '0' * 64, blobs, tmp_path / 'installed')
    with pytest.raises(ValueError): adapter.WheelInputs(path, selected, {**blobs, next(iter(blobs)): '0' * 40}, tmp_path / 'installed')
    bad = build(text.getvalue().replace('sha256=', 'wrong=', 1))
    with pytest.raises(ValueError): adapter.WheelInputs(path, bad, blobs, tmp_path / 'installed')


def test_workflow_isolation_and_phase1_never_enable_native_on_pr():
    text = (SCRIPTS.parent / p.WORKFLOW).read_text()
    assert 'pull_request_target' not in text
    assert 'CANDIDATE_NATIVE_HARNESS_SHA' in text and 'CANDIDATE_NATIVE_HARNESS_TREE' in text
    assert 'persist-credentials: true' not in text and '${{ secrets.' not in text
    execution = text.split('  execution:', 1)[1].split('  verification:', 1)[0]
    assert 'GH_TOKEN' not in execution and 'GITHUB_TOKEN' not in execution
    assert 'needs: control' in execution and "name: Candidate Docker execution" in execution
    unit = text.split('  unit:', 1)[1].split('  control:', 1)[0]
    assert 'test_candidate_native_harness.py' in unit and 'test_candidate_native_runtime.py' not in unit


def test_production_sources_are_not_changed_by_harness_work():
    root = SCRIPTS.parent
    base = os.environ.get('CANDIDATE_HARNESS_PR_BASE') or subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    p.production_preserved(root, base)


@pytest.fixture
def provenance(selection, facts, nomination):
    import io, base64, csv
    wheel_stream = io.BytesIO()
    blobs, origins, records = {}, {}, []
    prefix = '/synthetic/installed'
    for name in control.SOURCES:
        body = ('# synthetic source ' + name).encode()
        checksum = hashlib.sha256(body).hexdigest()
        blobs[name] = hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()
        origins[name] = {'path': prefix + '/lib/python3.13/site-packages/' + name, 'sha256': checksum}
        records.append([name, 'sha256=' + base64.urlsafe_b64encode(bytes.fromhex(checksum)).decode().rstrip('='), str(len(body))])
    record = io.StringIO(); csv.writer(record).writerows(records)
    with zipfile.ZipFile(wheel_stream, 'w') as package:
        for name in control.SOURCES: package.writestr(name, ('# synthetic source ' + name).encode())
        package.writestr('synthetic.dist-info/RECORD', record.getvalue())
    wheel = wheel_stream.getvalue()
    proof = {'selection': selection, 'qualifiers': {'H-file': 'e' * 64}, 'sourceBlobs': blobs}
    report = p.empty_report(selection, wheel=hashlib.sha256(wheel).hexdigest())
    report['wheelFile'] = 'synthetic.whl'
    payloads = {'synthetic.whl': wheel}
    registry = {'kind': p.KIND, 'selectionDigest': p.digest(selection), 'cancelled': False, 'entries': []}
    for index, profile in enumerate(p.PROFILES):
        plan = p.registration_plan(selection, profile=profile, owner='owner-' + profile, root='/synthetic/owner',
            source='/synthetic/source', backend={'backend': 'docker', 'daemon': 'daemon'}, resource='resource-' + profile,
            launch='launch-' + profile, universe='universe-' + profile, source_digest='f' * 64, nonce=str(index + 1) * 64)
        ack = {'kind': p.KIND, 'planDigest': p.digest(plan), 'selectionDigest': p.digest(selection),
               'run': plan['run'], 'attempt': plan['attempt'], 'nonce': plan['nonce']}
        entry = {'plan': plan, 'ack': ack, 'state': 'absent'}
        registry['entries'].append(entry)
        ready = {'phase': 'loaded', 'nonce': '3' * 64, 'members': [
            {'name': name, 'capture': 'executed-source-buffer', 'sha256': origins[name]['sha256']}
            for name in ('amplifier_web/environment_retention.py', 'amplifier_web/test_environment_worker.py')]}
        loaded = {'kind': p.KIND, 'status': 'permit-recorded', 'selectionDigest': p.digest(selection),
                  'resource': plan['resource'], 'wheelSha256': report['wheelSha256'],
                  'ready': ready, 'readySha256': p.digest(ready)}
        calls = [{'action': action, 'result': {'request': {
            'state': 'completed', 'resourceId': plan['resource'], 'sessionId': plan['owner'],
            **({'cleanupExecution': loaded} if action == 'remove' else {})}}}
            for action in ('start', 'execute', 'stop', 'remove')]
        archive = b'synthetic retained bytes: not a native result'
        payloads[profile + '-artifacts.tar'] = archive
        payloads[profile + '-absence.json'] = json.dumps({'ok': True, 'observation': {
            'absenceConfirmed': True, 'rootOwner': plan['owner'], 'universeId': plan['universe'],
            'operationId': plan['launch'], 'backendIdentity': plan['backend']}})
        report['cases'].append(dict(profile=profile, passed=True, nativeMap=True, tmpNoexec=True, cleanup='completed',
            absent=True, sourceUnchanged=True, artifactSha256=hashlib.sha256(archive).hexdigest(),
            registration={'plan': plan, 'ack': ack}, calls=calls,
            dependencyAfterRetirement={'complete': True, 'candidates': [], 'protectedIds': []}))
    payloads.update({'registry.json': json.dumps(registry),
        'module-origins.json': json.dumps({'kind': p.KIND, 'installation': {'prefix': prefix}, 'members': origins}),
        'installed.json': json.dumps({'kind': p.KIND, 'wheelSha256': report['wheelSha256'], 'wheelFile': 'synthetic.whl',
                                     'sourceBlobs': blobs, 'prefix': prefix, 'python': prefix + '/bin/python'})})
    xml = '<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0">' + ''.join(
        '<testcase classname="test_candidate_native_runtime" name="test_prepared_runtime[' + x + ']"/>' for x in p.PROFILES
    ) + '</testsuite></testsuites>'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, body in [('admission.json', json.dumps(proof)), ('report.json', json.dumps(report)),
                           ('junit.xml', xml), *payloads.items()]:
            archive.writestr(name, body)
    raw = stream.getvalue()
    artifact = {'id': 55, 'name': 'candidate-native-123-1', 'expired': False,
                'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(),
                'workflow_run': {'id': 123, 'head_sha': nomination['harness']}}
    jobs = [{'name': name, 'run_id': 123, 'run_attempt': 1, 'conclusion': 'success',
             'steps': [{'name': s, 'status': 'completed', 'conclusion': 'success'} for s in control.STEPS]}
            for name in ('Trusted candidate selection', 'Candidate Docker execution', 'External candidate evidence verification')]
    facts['run'].update(status='completed', conclusion='success')
    return proof, facts, jobs, artifact, raw


def test_external_provenance_and_internal_status_are_distinct(provenance, nomination):
    proof, facts, jobs, artifact, raw = provenance
    result = control.verify_job(proof, facts, jobs, artifact, raw, nomination=nomination, run_id=123, attempt=1)
    assert result['stage'] == 'external-final' and result['kind'] == p.KIND
    with pytest.raises(ValueError):
        control.verify_job(proof, facts, jobs, artifact, raw, nomination=nomination, run_id=123, attempt=1, external=False)
    facts['run'].update(status='in_progress', conclusion=None)
    assert control.verify_job(proof, facts, jobs, artifact, raw, nomination=nomination, run_id=123, attempt=1,
                              external=False)['stage'] == 'workflow-check-only'


@pytest.mark.parametrize('damage', ['run', 'attempt', 'artifact-head', 'artifact-digest', 'artifact-name',
                                    'expired', 'failed-job', 'skipped-step', 'duplicate-job', 'no-control', 'no-verifier'])
def test_provenance_cannot_bless_claimed_success(provenance, nomination, damage):
    proof, facts, jobs, artifact, raw = provenance
    if damage == 'run': artifact['workflow_run']['id'] += 1
    elif damage == 'attempt': jobs[1]['run_attempt'] += 1
    elif damage == 'artifact-head': artifact['workflow_run']['head_sha'] = 'e' * 40
    elif damage == 'artifact-digest': artifact['digest'] = 'sha256:' + '0' * 64
    elif damage == 'artifact-name': artifact['name'] = 'candidate-native-123-2'
    elif damage == 'expired': artifact['expired'] = True
    elif damage == 'failed-job': jobs[1]['conclusion'] = 'failure'
    elif damage == 'skipped-step': jobs[1]['steps'][0]['conclusion'] = 'skipped'
    elif damage == 'duplicate-job': jobs.append(copy.deepcopy(jobs[1]))
    elif damage == 'no-control': jobs.pop(0)
    else: jobs.pop()
    with pytest.raises(ValueError):
        control.verify_job(proof, facts, jobs, artifact, raw, nomination=nomination, run_id=123, attempt=1)


def test_current_cancellation_after_registration_blocks_effect(selection, plan, tmp_path):
    stopped = [False]
    def current(): p.require(not stopped[0], 'Cancelled after registration')
    registry = p.Registry(selection, tmp_path / 'ledger.json', clock=lambda: 200, current=current)
    ack = registry.register(plan)
    stopped[0] = True
    with pytest.raises(ValueError, match='Cancelled'): registry.consume(plan, ack, action='start')
    assert registry.value['entries'][0]['state'] == 'registered'


def test_snapshot_is_bound_once_to_consumed_resource(selection, plan, tmp_path):
    ledger = p.Registry(selection, tmp_path / 'ledger.json', clock=lambda: 200)
    ledger.consume(plan, ledger.register(plan), action='start')
    ledger.bind_snapshot(plan['resource'], {'archive_sha256': 'e' * 64})
    with pytest.raises(ValueError): ledger.bind_snapshot(plan['resource'], {'archive_sha256': 'f' * 64})
    with pytest.raises(ValueError): ledger.recheck('other', action='remove')
    with pytest.raises(ValueError): ledger.recheck(plan['resource'], action='arbitrary-command')


def test_expiry_during_blocking_observation_is_rechecked(selection, tmp_path):
    now = [selection['expires'] - 1]
    def observation(): now[0] = selection['expires'] + 1
    ledger = p.Registry(selection, tmp_path / 'ledger.json', clock=lambda: now[0], current=observation)
    with pytest.raises(ValueError, match='Expired'): ledger.check()


@pytest.mark.parametrize('inspection', [
    {'complete': False, 'candidates': [], 'protectedIds': []},
    {'complete': True, 'candidates': [], 'protectedIds': ['unresolved']},
    {'complete': True, 'candidates': [{'id': 'still-present'}], 'protectedIds': []},
])
def test_dependency_unknown_or_incomplete_inventory_cannot_settle_absent(inspection):
    with pytest.raises(ValueError): p.dependencies_absent(inspection)


def test_empty_complete_dependency_inventory_is_the_only_absence():
    p.dependencies_absent({'complete': True, 'candidates': [], 'protectedIds': []})


def test_installed_origin_cannot_be_selected_by_wheel_record_only(tmp_path):
    item = object.__new__(adapter.WheelInputs)
    item.installed = tmp_path / 'not-installed'
    item.raw = {}
    # Real distribution observation rejects this root before module imports.
    with pytest.raises(ValueError, match='distribution origin'): item.recheck()


@pytest.mark.parametrize('damage', ['source-blob', 'origin', 'registration', 'absence', 'loaded', 'dependency', 'archive'])
def test_payload_evidence_requires_raw_joins_not_success_flags(provenance, nomination, damage):
    import io
    proof, facts, jobs, artifact, raw = provenance
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    if damage == 'source-blob':
        proof['sourceBlobs'][control.SOURCES[0]] = '0' * 40
        files['admission.json'] = json.dumps(proof).encode()
        installed = json.loads(files['installed.json']); installed['sourceBlobs'] = proof['sourceBlobs']
        files['installed.json'] = json.dumps(installed).encode()
    elif damage == 'origin':
        value = json.loads(files['module-origins.json'])
        value['members'][control.SOURCES[0]]['path'] = '/checkout/' + control.SOURCES[0]
        files['module-origins.json'] = json.dumps(value).encode()
    elif damage == 'absence':
        value = json.loads(files['python-project-absence.json'])
        value['observation']['rootOwner'] = 'other'
        files['python-project-absence.json'] = json.dumps(value).encode()
    elif damage == 'archive':
        files['python-project-artifacts.tar'] = b'changed'
    else:
        value = json.loads(files['report.json'])
        if damage == 'registration': value['cases'][0]['registration']['ack']['attempt'] += 1
        elif damage == 'loaded': value['cases'][0]['calls'][-1]['result']['request']['cleanupExecution']['ready']['nonce'] = 'changed'
        else: value['cases'][0]['dependencyAfterRetirement']['protectedIds'] = ['unsettled']
        files['report.json'] = json.dumps(value).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, value in files.items(): archive.writestr(name, value)
    raw = stream.getvalue(); artifact['digest'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        control.verify_job(proof, facts, jobs, artifact, raw, nomination=nomination, run_id=123, attempt=1)


def test_bounded_build_logs_and_reaps_synthetic_children_only(tmp_path):
    from candidate_native_execution import bounded_build
    out, err = tmp_path / 'stdout', tmp_path / 'stderr'
    result = bounded_build([sys.executable, '-I', '-c', "import sys;print('out');print('err',file=sys.stderr)"],
        cwd=tmp_path, env={'PATH': os.defpath}, timeout=5, stdout_path=out, stderr_path=err)
    assert result == out.read_text() == 'out\n' and err.read_text() == 'err\n'
    with pytest.raises(ValueError, match='deadline'):
        bounded_build([sys.executable, '-I', '-c', "import time;print('before',flush=True);time.sleep(20)"],
            cwd=tmp_path, env={'PATH': os.defpath}, timeout=.2, stdout_path=out, stderr_path=err)
    assert out.read_text() == 'before\n'


def test_dead_build_leader_does_not_leave_pipe_holding_descendant(tmp_path):
    import time
    from candidate_native_execution import bounded_build
    pid = tmp_path / 'child.pid'
    child = "import os,time;open(" + repr(str(pid)) + ",'w').write(str(os.getpid()));time.sleep(20)"
    parent = "import subprocess,sys;subprocess.Popen([sys.executable,'-I','-c'," + repr(child) + "])"
    with pytest.raises(ValueError, match='deadline'):
        bounded_build([sys.executable, '-I', '-c', parent], cwd=tmp_path, env={'PATH': os.defpath},
                      timeout=.5, stdout_path=tmp_path / 'stdout', stderr_path=tmp_path / 'stderr')
    child_pid = int(pid.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try: status = Path('/proc', str(child_pid), 'stat').read_text().rsplit(')', 1)[1].split()[0]
        except FileNotFoundError: break
        if status == 'Z': break
        time.sleep(.02)
    else: pytest.fail('Owned descendant survived failed build deadline')


def test_pr_base_check_reports_missing_shallow_history_then_accepts_fetched_base(tmp_path):
    origin, clone = tmp_path / 'source', tmp_path / 'shallow'
    origin.mkdir()
    def git(*args, cwd=origin):
        return subprocess.check_output(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                                        *args], cwd=cwd, text=True, stderr=subprocess.PIPE).strip()
    git('init', '-q')
    (origin / 'amplifier_web').mkdir()
    (origin / 'amplifier_web/source.py').write_text('original')
    git('add', '.'); git('commit', '-qm', 'base')
    base = git('rev-parse', 'HEAD')
    (origin / 'test-only.txt').write_text('test')
    git('add', '.'); git('commit', '-qm', 'test only')
    git('clone', '--quiet', '--depth=1', origin.as_uri(), str(clone))
    with pytest.raises(ValueError, match='not fetched'): p.production_preserved(clone, base)
    git('fetch', '--quiet', '--depth=1', 'origin', base, cwd=clone)
    p.production_preserved(clone, base)
    (clone / 'amplifier_web/source.py').write_text('changed')
    with pytest.raises(ValueError, match='production'): p.production_preserved(clone, base)
