"""Current installation evidence is distinct from immutable attempt history."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import update_health as health
from amplifier_web.attention import snapshot
from amplifier_web.update_diagnostics import UpdateDiagnostics
from test_update_core_lookup_failure import checked_install
from test_update_health_first_install import core_row
from test_update_health_setup_record import INCOMPLETE, UP_TO_DATE, setup_record  # noqa: F401
from test_updates import app, prepare, repo  # noqa: F401 -- existing real manager fixture
from test_windows_launcher_follow import rig  # noqa: F401 -- stand-ins for the Windows parts of a launcher refresh

CAPTURE = Path(__file__).parent / 'fixtures/update-current-health-captured060.json'


def captured():
    value = json.loads(CAPTURE.read_text())
    observed = value['observedState']
    get = lambda path: observed[path]['value']
    state = {'phase': 'checked', 'error': None, 'available': 0,
             'release': value['active']['current'], 'sequence': get('/updates/sequence'),
             'applicationResult': get('/updates/applicationResult'),
             'diagnostics': {'events': [], 'activeFailures': get('/updates/diagnostics/activeFailures'),
                             'completedBatches': list(get('/updates/diagnostics/completedBatches').values())},
             'application': {'current': '0.60.0', 'runningRevision': get('/updates/applicationResult')['revision'], 'status': 'current'},
             'items': []}
    return value, state


def acknowledged_app_with_old_component_history():
    """Sanitized 0.100.2 shape: app receipts outlive component activation history."""
    app_attempt, revision = '5' * 32, '2' * 40
    phases = ['ecosystem-source-policy', 'ecosystem-source-preflight', 'ecosystem-prepare',
              'ecosystem-runtime-lock', 'ecosystem-validation', 'ecosystem-source-preflight']
    packages = [None, 'amplifier-module-context-managed', 'amplifier-module-hook-computer-use',
                None, 'amplifier-module-tool-image', 'amplifier-bundle-context-intelligence']
    rows = [{'id': f'{i + 10:032x}', 'attemptId': f'{i + 20:032x}', 'kind': 'ecosystem',
             'status': 'failed', 'phase': phase, 'at': 1794230000 + i * 1000,
             **({'package': packages[i]} if packages[i] else {})} for i, phase in enumerate(phases)]
    state = {'phase': 'checked', 'error': None, 'available': 0, 'items': [],
             'sequence': {'stage': 'complete', 'install': False},
             'application': {'status': 'current', 'current': '0.100.2', 'runningRevision': revision},
             'applicationResult': {'phase': 'restart-ack', 'attemptId': app_attempt,
                 'revision': revision, 'version': '0.100.2', 'verifiedAt': 1794821240.24},
             'diagnostics': {'lastFailure': {}, 'activeFailures': {
                 str(i): {'latest': row} for i, row in enumerate(rows)},
                 'completedBatches': [{'kind': 'application', 'phase': 'restart-ack',
                     'attemptId': app_attempt, 'revision': revision, 'startedAt': 1794821240.2,
                     'completedAt': 1794821240.3, 'applicationExtras': []}],
                 'events': [{'kind': 'application', 'phase': 'restart-ack', 'status': 'succeeded',
                     'attemptId': app_attempt, 'at': 1794821240.3} for _ in range(50)]}}
    proof = {'generation': '1' * 32, 'activationUnknown': True, 'qualifiedAt': 1794470895,
             'packages': set(filter(None, packages)), 'components': set(filter(None, packages))}
    return state, proof


@pytest.mark.parametrize('case', ['old', 'newer', 'activation', 'invalid-ack', 'graph', 'config'])
def test_app_success_and_selected_component_attention_are_distinct(case):
    state, proof = acknowledged_app_with_old_component_history()
    selected = state['diagnostics']['activeFailures']['3']['latest']
    if case == 'newer':
        selected['at'] = 1794822000
    elif case == 'activation':
        selected['phase'] = 'ecosystem-activation'
    elif case == 'invalid-ack':
        state['applicationResult']['attemptId'] = '7' * 32
    elif case == 'graph':
        proof = {}
    elif case == 'config':
        proof['configurationChanged'] = True
    before = copy.deepcopy(state)
    result = health.current(state, proof)
    assert result['actionRequired'] and result['status'] != 'current'
    if case in {'old', 'activation', 'config'}:
        assert result['failureAttemptId'] == selected['attemptId']
        assert result['failurePhase'] == selected['phase']
        assert result['failureAt'] == selected['at']
        assert result['failureKind'] == 'ecosystem'
        assert result['selectedFailure'] == selected
        assert result['title'] == 'Application updated; earlier component issue needs review'
        assert '0.100.2' in result['detail'] and '2026-11-' in result['detail']
    else:
        assert result['title'] != 'Application updated; earlier component issue needs review'
    if proof:
        assert result['proofLimitation']['activationUnknown'] is True
    assert state == before


def test_activation_retention_limit_alone_is_neutral_and_not_reinstallation():
    state, proof = acknowledged_app_with_old_component_history()
    state['diagnostics']['activeFailures'] = {}
    result = health.current(state, proof)
    assert result['status'] == 'unchecked' and not result['actionRequired']
    assert result['title'] == 'Application is up to date'
    assert result['proofLimitation'] == {'activationUnknown': True}
    assert 'No reinstall is requested' in result['detail']
    assert 'selectedFailure' not in result


def mounted_fixture(tmp_path, monkeypatch):
    value, state = captured()
    manager = SimpleNamespace(home=tmp_path, directory=tmp_path/'updates',
        service=SimpleNamespace(state={'updates': state, 'configurationRevision': 1}, _save_changes=lambda **kw: None))
    manager.directory.mkdir()
    (manager.directory/'active.json').write_text(json.dumps(value['active']))
    generation = value['active']['current'];source = value['generations'][generation]
    stage = manager.directory/'releases'/generation;stage.mkdir(parents=True)
    for name in ('plan.json','validated.json','profiles-qualified.json','runtime-installed.json'):
        (stage/name).write_text(json.dumps(source[name]))
    project = tmp_path/'runtime';project.mkdir();(project/'uv.lock').write_text('exact graph')
    (stage/'runtime.lock').write_text('exact graph')
    from amplifier_web import runtime_environment, runtime_qualification, runtime_profiles
    monkeypatch.setattr(runtime_environment, 'project_path', lambda *a: project)
    # Sanitized reference has names/versions, not host-private paths. The actual
    # recorded-graph comparator is exercised against the same synthetic graph.
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda p: source['runtime-installed.json'])
    monkeypatch.setattr(health, 'configuration', lambda m: ('fixture', SimpleNamespace(settings={})))
    monkeypatch.setattr(runtime_profiles, 'configuration_key', lambda *a: source['profiles-qualified.json']['configuration'])
    manager.diagnostics=UpdateDiagnostics(manager)
    return manager, stage, project, source


def test_a_generation_record_removed_during_verification_cannot_skip_the_package_comparison(tmp_path, monkeypatch):
    from amplifier_web import runtime_qualification
    manager, stage, project, source = mounted_fixture(tmp_path, monkeypatch)
    assert health.active_evidence(manager)
    changed = copy.deepcopy(source['runtime-installed.json'])
    changed[0]['version'] = '0.0.0+changed'
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda p: changed)
    assert health.active_evidence(manager) == {}  # Control: a changed package gives no proof.
    real = runtime_qualification.verify_recorded

    def verify(project, receipt, **options):
        (Path(receipt) / 'runtime-installed.json').unlink()  # Gone after the proof read it.
        return real(project, receipt, **options)
    monkeypatch.setattr(runtime_qualification, 'verify_recorded', verify)
    assert health.active_evidence(manager) == {}


def test_captured060_current_graph_removes_old_health_cards_not_history(tmp_path, monkeypatch):
    manager,stage,project,source=mounted_fixture(tmp_path,monkeypatch)
    original=copy.deepcopy(manager.diagnostics.state)
    evidence=health.active_evidence(manager)
    assert evidence and 'amplifier-foundation' in evidence['packages']
    manager._active_update_evidence=evidence
    health.publish(manager)
    status=manager.service.state['updates']['currentStatus']
    assert status['status']=='current' and not status['actionRequired']
    assert snapshot(manager.service.state)['pages'].get('updates',0)==0
    assert manager.diagnostics.state==original  # No resolve/delete/dismissal.
    manager.diagnostics=UpdateDiagnostics(manager)
    assert manager.diagnostics.state==original
    assert health.current(manager.service.state['updates'],health.active_evidence(manager))['status']=='current'


@pytest.mark.parametrize('case',['newer','missing-package','unrelated-domain','unknown-activation','config','graph','lock','partial','staged','app-unverified','rollback'])
def test_current_unknown_or_new_problem_is_not_hidden_by_old_success(tmp_path,monkeypatch,case):
    m,stage,project,source=mounted_fixture(tmp_path,monkeypatch);u=m.service.state['updates']
    if case=='newer':next(iter(u['diagnostics']['activeFailures'].values()))['latest']['at']=10**11
    elif case=='missing-package':source['runtime-installed.json'][:]=[{'name':'other','version':'1'}];(stage/'runtime-installed.json').write_text(json.dumps(source['runtime-installed.json']))
    elif case=='unrelated-domain':next(iter(u['diagnostics']['activeFailures'].values()))['latest']['kind']='application'
    elif case=='unknown-activation':u['diagnostics']['completedBatches']=[]
    elif case=='config':(stage/'profiles-qualified.json').write_text(json.dumps({'profiles':['work'],'configuration':'different'}))
    elif case=='graph':source['runtime-installed.json'][0]['version']='changed'
    elif case=='lock':(project/'uv.lock').write_text('changed')
    elif case=='partial':u['sequence']={'stage':'included'}
    elif case=='staged':u.update(phase='staged',pendingRelease='b'*32)
    elif case=='app-unverified':u['pendingReplacement']={'unqualified':True}
    elif case=='rollback':u['pendingRollback']='c'*32;u['phase']='error';u['error']='Rollback not confirmed'
    status=health.current(u,health.active_evidence(m))
    assert status['status']!='current'
    if case not in {'partial','staged','unknown-activation'}:assert status['actionRequired']


def test_render_projection_does_no_filesystem_work_and_config_revision_invalidates(tmp_path,monkeypatch):
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch);m._active_update_evidence=health.active_evidence(m)
    m._update_health_configuration_revision=1
    monkeypatch.setattr(Path,'read_bytes',lambda *a:pytest.fail('render read'))
    monkeypatch.setattr(Path,'read_text',lambda *a:pytest.fail('render read'))
    for _ in range(100):health.publish(m)
    assert m.service.state['updates']['currentStatus']['status']=='current'
    m.service.state['configurationRevision']=2;health.publish(m)
    assert not m.service.state['updates']['currentStatus']['actionRequired']
    assert m.service.state['updates']['currentStatus']['title']=='Checking current configuration'


@pytest.mark.parametrize('blocker', [
    {'kind': 'checkout-handoff'},
    {'kind': 'configuration', 'reasonCode': 'management-tasks-unavailable'},
    {'kind': 'configuration', 'reasonCode': 'management-lock-unavailable'},
    {'kind': 'runtime', 'reasonCode': 'runtime-observer-unavailable'},
])
def test_unverified_activity_is_attention_not_an_endless_progress_wait(blocker):
    state = {'phase': 'app-staged', 'pendingApp': {'revision': 'a' * 40}, 'blockers': [blocker]}
    before = copy.deepcopy(state)
    projected = health.current(state)
    assert projected == {
        'status': 'action-required', 'actionRequired': True, 'title': 'Update needs attention',
        'detail': 'Automatic restart is paused because Amplifier cannot confirm earlier work has finished. The current app is kept.',
        'action': 'diagnostics', 'component': None}
    assert state == before
    state['blockers'] = [{'kind': 'configuration', 'reasonCode': 'configuration-task-pending'}]
    assert health.current(state)['status'] == 'waiting'
    state['blockers'] = []
    assert not health.current(state)['actionRequired']


def test_retry_witness_preserves_manual_and_unknown_boundaries():
    batch={};failure={'reason':'runtime-source-constraint','status':'failed'}
    health.record_retry(batch,'A',failure)
    assert health.retry_decision(batch,'A')=='changed-inputs'
    assert health.retry_decision(batch,'B') is None
    health.record_retry(batch,'A',{'status':'interrupted'})
    assert health.retry_decision(batch,'B')=='inspect'
    health.record_retry(batch,'A',{'status':'failed','errorType':'TimeoutError'})
    assert health.retry_decision(batch,'A')=='changed-inputs'
    assert batch['retry']=={'inputKey':'A','mode':'changed-inputs'}
    legacy={'retry':{'mode':'backoff','inputKey':'A','attempts':100,'retryAt':1}}
    assert health.retry_decision(legacy,'A')=='changed-inputs'
    assert health.retry_decision(legacy,'B') is None


def test_unknown_component_failure_keeps_app_ack_and_history_without_transient_claim():
    failure={'kind':'ecosystem','status':'failed','phase':'ecosystem-runtime-resolve',
             'at':200,'errorType':'CommandFailure','exitCode':1}
    state={'phase':'error','error':'Component preparation failed.',
           'application':{'status':'current','current':'0.80.1','runningRevision':'a'*40},
           'applicationResult':{'phase':'restart-ack','revision':'a'*40,'version':'0.80.1','verifiedAt':100},
           'diagnostics':{'lastFailure':failure,'activeFailures':{'component':{'latest':failure}},
                          'batch':{'kind':'ecosystem','retry':{'mode':'backoff','retryAt':10**12}}}}
    before=copy.deepcopy(state)
    current=health.current(state,{})
    assert current['status']=='action-required'
    assert current['title']=='Update could not finish'
    assert 'transient' not in current['detail'].lower()
    assert state==before


@pytest.mark.parametrize('phase,pending,status', [
    ('validating', {}, 'working'),
    ('staged', {'pendingRelease': 'b'*32}, 'waiting'),
    ('available', {'available': 1}, 'available'),
    ('installed', {}, 'action-required'),
])
def test_failed_application_check_does_not_replace_component_progress(phase, pending, status):
    app = {'id': 'application', 'kind': 'app', 'status': 'check_failed',
           'detail': 'Could not read published-release metadata.',
           'checkStage': 'release_metadata', 'checkReason': 'unavailable'}
    state = {'phase': phase, 'application': app, 'items': [app], 'available': 0,
             'sequence': {'stage': 'complete'}, **pending}
    before = copy.deepcopy(state)
    result = health.current(state, {'packages': ['component'], 'startedAt': 100})
    assert result['status'] == status
    assert state == before
    if status == 'action-required':
        assert result['title'] == 'Application check needs attention'
        assert result['action'] == 'check'
        assert result['component'] == 'Amplifier Unified'
    if status == 'available':
        assert 'independently' in result['detail']


def test_failed_application_check_cannot_mask_component_failure_or_replace_its_retry():
    app = {'id': 'application', 'kind': 'app', 'status': 'check_failed'}
    failure = {'kind': 'ecosystem', 'status': 'failed', 'at': 200,
               'reason': 'runtime-source-constraint', 'package': 'protected-provider'}
    state = {'application': app, 'items': [app], 'phase': 'error',
             'diagnostics': {'lastFailure': failure, 'batch': {'retry': {'mode': 'changed-inputs'}}}}
    before = copy.deepcopy(state)
    result = health.current(state, {'packages': ['protected-provider'], 'startedAt': 100})
    assert result['status'] == 'protected'
    assert result['component'] == 'protected-provider'
    assert state == before


@pytest.mark.parametrize('failure_kind',['protected','command'])
@pytest.mark.parametrize('unrelated',[None,'application','smart-tools','rollback'])
async def test_automatic_unchanged_conflict_does_zero_duplicate_stage_work(tmp_path,monkeypatch,failure_kind,unrelated):
    from amplifier_web.updates import UpdateManager
    from amplifier_web.runtime_environment import ProtectedRuntimeSource
    from amplifier_web import update_plan
    from amplifier_web.update_sequence import app_selection
    from amplifier_web.host import config as host_config
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch)
    m.lock=asyncio.Lock();m.closed=False;m.awaiting_restart=lambda:False
    m.independent_tool_results=lambda s:False
    m.inventory=[{'id':'fixture','kind':'runtime environment','status':'update','eligible':True,'latest':'A'}]
    m.service.lock=asyncio.Lock();u=m.service.state['updates'];u['sequence']={'stage':'included'}
    calls={'validate':0}
    releases=m.directory/'releases';existing=set(releases.iterdir())
    async def publish(**kw):u.update(kw)
    async def build(*a):return {'mode':'worker','reason':'fixture','activation':'per-worker'}
    async def validate(stage,release):
        assert stage.is_dir() and stage.name==release
        calls['validate']+=1
        if failure_kind=='command':
            from amplifier_web.update_diagnostics import CommandFailure
            raise CommandFailure({'exitCode':1,'stderrBytes':10611})
        raise ProtectedRuntimeSource('amplifier-foundation','runtime-source-constraint')
    m.publish=publish;m.validate=validate
    monkeypatch.setattr(update_plan,'build',build)
    monkeypatch.setattr(health,'preparation_key',lambda m,rows:rows[0]['latest'])
    # Avoid any real shared configuration; the full install orchestration owns
    # stage creation, the preflight guard and failure recording in this fixture.
    from amplifier_web import updates,session_files
    monkeypatch.setattr(updates,'foundation_home',lambda h:tmp_path/'absent')
    monkeypatch.setattr(session_files,'amplifier_home',lambda:tmp_path/'empty-shared')
    monkeypatch.setattr(host_config,'amplifier_home',lambda:tmp_path/'empty-shared')
    # Supply the real checked witness after isolating configuration. Live and
    # staged selection verification still run in UpdateManager.install.
    m.inventory[0]['appSelection']=app_selection(m.home)['witness']
    await UpdateManager.install(m,automatic=True)
    assert calls=={'validate':1}
    first=set(releases.iterdir())
    assert len(first-existing)==1
    failed=copy.deepcopy(m.diagnostics.state['lastFailure'])
    if unrelated:
        m.diagnostics.begin(unrelated,'d'*40 if unrelated=='application' else 'd'*32)
        m.diagnostics.record('unrelated-check','succeeded')
        m.diagnostics=UpdateDiagnostics(m)
    for _ in range(10):
        u['phase']='available'
        await UpdateManager.install(m,automatic=True)
    assert calls=={'validate':1}
    assert set(releases.iterdir())==first
    assert m.diagnostics.state['lastFailure']==failed
    m.inventory[0]['latest']='B'
    await UpdateManager.install(m,automatic=True)
    assert calls=={'validate':2}
    assert len(set(releases.iterdir())-existing)==2
    await UpdateManager.install(m)  # Deliberate fresh install intent.
    assert calls=={'validate':3}
    assert len(set(releases.iterdir())-existing)==3


@pytest.mark.parametrize('reason',[None,'disk-full','bundle-load-failed','runtime-source-constraint'])
def test_later_complete_verified_graph_is_current_for_older_preparation_failures(tmp_path,monkeypatch,reason):
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch)
    for group in m.diagnostics.state['activeFailures'].values():
        group['latest'].pop('reason',None)
        if reason:group['latest']['reason']=reason
    before=copy.deepcopy(m.diagnostics.state)
    assert health.current(m.service.state['updates'],health.active_evidence(m))['status']=='current'
    assert m.diagnostics.state==before


@pytest.mark.parametrize('case',['covered','unknown-features','missing-feature','newer','wrong-running'])
def test_application_result_covers_old_generic_candidate_failure_only_with_current_scope(case):
    u={'phase':'checked','sequence':{'stage':'complete'},'application':{'status':'current','current':'0.60.1','runningRevision':'a'*40},
       'applicationResult':{'phase':'restart-ack','attemptId':'a'*32,'revision':'a'*40,'version':'0.60.1','verifiedAt':30},
       'diagnostics':{'completedBatches':[{'kind':'application','phase':'restart-ack','attemptId':'a'*32,'revision':'a'*40,'startedAt':20,'completedAt':31,'applicationExtras':['managed-browser']}],
         'activeFailures':{'old':{'latest':{'kind':'application','status':'failed','phase':'candidate-install','at':10,'applicationExtras':[]}}}}}
    failure=u['diagnostics']['activeFailures']['old']['latest']
    if case=='unknown-features':failure.pop('applicationExtras')
    elif case=='missing-feature':failure['applicationExtras']=['tui']
    elif case=='newer':failure['at']=32
    elif case=='wrong-running':u['application']['runningRevision']='b'*40
    value=health.current(u,{'generation':'c'*32,'startedAt':20,'verifiedAt':31,'packages':{'example'}})
    assert (value['status']=='current')==(case=='covered')


@pytest.mark.parametrize('case', [
    'discovery', 'restart-capability', 'missing-proof', 'wrong-proof',
    'wrong-revision', 'wrong-version', 'unknown-extras', 'missing-feature',
    'later', 'unknown-time', 'invalid-refusal-time', 'interrupted',
    'candidate-install', 'activation', 'pending-replacement', 'pending-restart',
])
def test_verified_manual_payload_covers_only_older_pre_effect_same_scope_refusals(case):
    failure = {'kind': 'application', 'status': 'failed', 'phase': 'target-discovery',
               'at': 10, 'applicationExtras': []}
    refusal = {'kind': 'application', 'status': 'failed', 'phase': 'restart-capability',
               'at': 20, 'applicationExtras': ['managed-browser']}
    manual = {'phase': 'manual-handoff-reconciled', 'requestId': 'manual-current',
              'revision': 'a' * 40, 'version': '0.76.3', 'verifiedAt': 30,
              'refusal': refusal}
    state = {'phase': 'installed', 'error': None, 'available': 0,
             'application': {'status': 'current', 'current': '0.76.3', 'runningRevision': 'a' * 40},
             'sequence': {'stage': 'complete'}, 'manualHandoff': manual,
             'diagnostics': {'activeFailures': {'old': {'latest': failure},
                                                 'manual': {'latest': refusal}}}}
    verified = 'manual-current'
    if case == 'restart-capability':failure['phase'] = 'restart-capability'
    elif case == 'missing-proof':verified = None
    elif case == 'wrong-proof':verified = 'other-request'
    elif case == 'wrong-revision':manual['revision'] = 'b' * 40
    elif case == 'wrong-version':manual['version'] = '0.76.2'
    elif case == 'unknown-extras':failure.pop('applicationExtras')
    elif case == 'missing-feature':failure['applicationExtras'] = ['tui']
    elif case == 'later':failure['at'] = 21
    elif case == 'unknown-time':failure.pop('at')
    elif case == 'invalid-refusal-time':refusal['at'] = 31
    elif case == 'interrupted':failure['status'] = 'interrupted'
    elif case == 'candidate-install':failure['phase'] = 'candidate-install'
    elif case == 'activation':failure['phase'] = 'activation'
    elif case == 'pending-replacement':state['pendingReplacement'] = {'unconfirmed': True}
    elif case == 'pending-restart':state['pendingRestart'] = {'requestStatus': 'rejected'}
    original = copy.deepcopy(state)
    evidence = {'generation': 'c' * 32, 'startedAt': 40, 'verifiedAt': 50, 'packages': {'example'}}
    result = health.current(state, evidence, manual_verified=verified)
    assert (result['status'] == 'current') == (case in {'discovery', 'restart-capability'})
    assert state == original  # Historical failures and exact manual receipt stay byte-equivalent.


@pytest.mark.parametrize('automatic',[True,False])
async def test_interrupted_preparation_cannot_be_replayed_by_manual_intent(tmp_path,monkeypatch,automatic):
    from amplifier_web.updates import UpdateManager
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch)
    m.lock=asyncio.Lock();m.awaiting_restart=lambda:False
    m.diagnostics.state['batch']={'retry':{'mode':'inspect','inputKey':'old'}}
    before=copy.deepcopy(m.diagnostics.state)
    if automatic:
        await UpdateManager.install(m,automatic=True)
    else:
        with pytest.raises(ValueError,match='interrupted'):
            await UpdateManager.install(m)
    assert m.diagnostics.state==before


@pytest.mark.parametrize('case',['local-only','mixed-check-failure','unknown-issue','new-conflict'])
def test_custom_source_is_information_without_hiding_current_conflicts(tmp_path,monkeypatch,case):
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch);u=m.service.state['updates']
    u['items']=[{'id':'custom','label':'Custom module','status':'local_changes','updateTier':'included'}]
    u['sequence']={'stage':'included','included':{'issues':1}}
    if case=='mixed-check-failure':
        u['items'].append({'id':'broken','label':'Unavailable source','status':'check_failed'})
    elif case=='unknown-issue':u['sequence']['included']['issues']=2
    elif case=='new-conflict':
        next(iter(u['diagnostics']['activeFailures'].values()))['latest']['at']=10**11
    m._active_update_evidence=health.active_evidence(m);health.publish(m)
    assert u['currentStatus']['actionRequired']==(case!='local-only')
    if case=='local-only':
        assert u['currentStatus']['status']=='unchecked'
        notices=snapshot(m.service.state)
        assert notices['pages'].get('updates',0)==0
        assert [row['id'] for row in notices['items']]==['source:custom']
        assert notices['items'][0]['informational']


@pytest.mark.parametrize('case',['evicted','legacy-event','legacy-missing','newer','activation-failure','outside-graph','changed-graph'])
def test_history_retention_is_not_activation_authority(tmp_path,monkeypatch,case):
    m,stage,_,source=mounted_fixture(tmp_path,monkeypatch);u=m.service.state['updates']
    batches=u['diagnostics']['completedBatches']
    activation=next(row for row in batches if row.get('revision')==u['release'])
    if case.startswith('legacy'):
        activation.pop('revision')
        if case=='legacy-event':
            u['diagnostics']['events']=[{'revision':u['release'],'phase':'ecosystem-activation',
                'status':'succeeded','at':activation['completedAt'],'attemptId':activation['attemptId']}]
    else:
        # More than the retained eight later batches, none for this generation.
        batches[:]=[{'revision':str(i),'phase':'check','completedAt':activation['completedAt']+i} for i in range(10)]
        batches[:]=batches[-8:]
    failure=next(iter(u['diagnostics']['activeFailures'].values()))['latest']
    if case=='newer':failure['at']=10**11
    elif case=='activation-failure':failure['phase']='ecosystem-activation'
    elif case=='outside-graph':failure['package']='not-in-qualified-graph'
    elif case=='changed-graph':source['runtime-installed.json'][0]['version']='different'
    before=copy.deepcopy(m.diagnostics.state)
    evidence=health.active_evidence(m)
    value=health.current(u,evidence)
    if case=='legacy-event':
        assert value['status']=='current'
    elif case in {'evicted','legacy-missing'}:
        assert value['status']=='unchecked' and not value['actionRequired']
        assert 'activation receipt' in value['detail']
        assert evidence['activationUnknown']
    else:
        assert value['actionRequired'] and value['status']!='current'
    assert m.diagnostics.state==before


async def test_configuration_revalidation_gap_is_neutral_but_failed_proof_is_not(tmp_path,monkeypatch):
    m,stage,_,_=mounted_fixture(tmp_path,monkeypatch)
    m._active_update_evidence=health.active_evidence(m);m._update_health_configuration_revision=1
    m.service.state['configurationRevision']=2
    health.publish(m)
    assert not m.service.state['updates']['currentStatus']['actionRequired']
    (stage/'profiles-qualified.json').write_text('{}')
    await health.refresh(m);health.publish(m)
    assert m.service.state['updates']['currentStatus']['actionRequired']


@pytest.mark.parametrize('case', ['changed', 'restored', 'newer', 'pending',
                                  'graph', 'lock', 'unknown', 'outside-graph'])
async def test_changed_configuration_does_not_resurrect_a_covered_source_conflict(tmp_path, monkeypatch, case):
    from amplifier_web import runtime_profiles
    from amplifier_web.builtin_behaviors import CONTEXT_INTELLIGENCE_BEHAVIOR_URI
    real_key = runtime_profiles.configuration_key
    m, stage, project, source = mounted_fixture(tmp_path, monkeypatch)
    u = m.service.state['updates']
    settings = {}
    monkeypatch.setattr(runtime_profiles, 'configuration_key', real_key)
    monkeypatch.setattr(health, 'configuration', lambda manager: (
        real_key(settings, manager.home), SimpleNamespace(settings=settings)))
    qualified = real_key(settings, m.home, stage)
    (stage / 'profiles-qualified.json').write_text(json.dumps({
        'profiles': ['work'], 'configuration': qualified}))
    failure = next(iter(u['diagnostics']['activeFailures'].values()))['latest']
    failure.update(reason='runtime-source-constraint', package='amplifier-foundation',
                   phase='ecosystem-source-preflight')
    settings['web_bundles'] = {'excluded': [CONTEXT_INTELLIGENCE_BEHAVIOR_URI]}
    assert real_key(settings, m.home, stage) != qualified
    if case == 'restored':
        settings.clear()
    elif case == 'newer':
        failure['at'] = 10**11
    elif case == 'pending':
        u['pendingRelease'] = 'b' * 32
    elif case == 'graph':
        source['runtime-installed.json'][0]['version'] = 'changed'
    elif case == 'lock':
        (project / 'uv.lock').write_text('changed')
    elif case == 'unknown':
        u['diagnostics']['componentPreparation'] = {'retry': {'mode': 'inspect'}}
    elif case == 'outside-graph':
        failure['package'] = 'not-in-qualified-graph'
    before = copy.deepcopy(m.diagnostics.state)
    # Preparation's existing strict evidence contract must not acquire a new
    # baseline merely because the display can explain an old installation.
    if case == 'changed':
        assert health.active_evidence(m) == {}
        from amplifier_web.update_partial import Preparation
        with pytest.raises(ValueError, match='verified working component generation'):
            await Preparation(m, stage, []).verify_baseline()
    await health.refresh(m)
    health.publish(m)
    status = u['currentStatus']
    if case == 'changed':
        # The covered conflict stays history, and the changed settings are no alarm.
        assert (status['status'], status['title'], status['detail']) == SETTINGS_CHANGED
        assert status['component'] is None
        assert not status['actionRequired'] and status['action'] == 'check'
        assert status['proofLimitation'] == {'configurationChanged': True}
    elif case == 'restored':
        assert status['status'] == 'current'
    elif case == 'pending':
        assert status['status'] == 'waiting'
    else:
        assert status['actionRequired'] and status['status'] != 'current'
        assert status['title'] != 'Component verification is incomplete'
    assert m.diagnostics.state == before


@pytest.mark.parametrize('race', ['configuration', 'pointer'])
def test_configuration_explanation_rejects_racing_observations(tmp_path, monkeypatch, race):
    from amplifier_web import runtime_profiles, runtime_qualification
    m, _, _, source = mounted_fixture(tmp_path, monkeypatch)
    calls = 0
    def key(*args):
        nonlocal calls
        calls += 1
        return 'changed' if calls == 1 or race == 'pointer' else 'changed-again'
    monkeypatch.setattr(runtime_profiles, 'configuration_key', key)
    if race == 'pointer':
        def graph(project):
            (m.directory / 'active.json').write_text(json.dumps({'current': 'b' * 32}))
            return source['runtime-installed.json']
        monkeypatch.setattr(runtime_qualification, 'installed_graph', graph)
    assert health.active_evidence(m, explain_configuration=True) == {}


@pytest.mark.parametrize('key', [None, {}, [], '', 'different', 'a' * 63, 'G' * 64])
def test_malformed_configuration_receipt_is_not_an_explained_change(tmp_path, monkeypatch, key):
    m, stage, _, _ = mounted_fixture(tmp_path, monkeypatch)
    (stage / 'profiles-qualified.json').write_text(json.dumps({
        'profiles': ['work'], 'configuration': key}))
    before = copy.deepcopy(m.diagnostics.state)
    assert health.active_evidence(m, explain_configuration=True) == {}
    assert m.diagnostics.state == before


@pytest.mark.parametrize('case', ['quiet', 'newer', 'inspection', 'replacement'])
def test_explained_configuration_change_stays_neutral_during_next_revalidation(tmp_path, monkeypatch, case):
    from amplifier_web import runtime_profiles
    m, _, _, source = mounted_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_profiles, 'configuration_key', lambda *args: 'b' * 64)
    m._active_update_evidence = health.active_evidence(m, explain_configuration=True)
    m._update_health_configuration_revision = 1
    assert m._active_update_evidence['configurationChanged']
    u = m.service.state['updates']
    if case == 'newer':
        next(iter(u['diagnostics']['activeFailures'].values()))['latest']['at'] = 10**11
    elif case == 'inspection':
        u['diagnostics']['componentPreparation'] = {'retry': {'mode': 'inspect'}}
    elif case == 'replacement':
        u['pendingReplacement'] = {'unconfirmed': True}
    before = copy.deepcopy(m.diagnostics.state)
    m.service.state['configurationRevision'] = 2
    health.publish(m)
    assert (u['currentStatus']['title'] == 'Checking current configuration') == (case == 'quiet')
    assert u['currentStatus']['actionRequired'] == (case != 'quiet')
    assert m.diagnostics.state == before


async def test_real_global_configuration_change_explains_retained_failure(tmp_path, monkeypatch):
    from amplifier_web import runtime_profiles
    from amplifier_web.host.config import read_config
    from amplifier_web.builtin_behaviors import CONTEXT_INTELLIGENCE_BEHAVIOR_URI
    real_configuration, real_key = health.configuration, runtime_profiles.configuration_key
    m, stage, _, _ = mounted_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(health, 'configuration', real_configuration)
    monkeypatch.setattr(runtime_profiles, 'configuration_key', real_key)
    shared = tmp_path / 'canonical-shared'
    shared.mkdir()
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
    staged_shared = stage / 'shared-config'
    staged_shared.mkdir()
    (staged_shared / 'settings.yaml').write_text('{}')
    workspace = stage / 'qualification-workspace'
    workspace.mkdir()
    config = read_config(workspace, home=stage, shared_home=staged_shared, global_only=True)
    (stage / 'profiles-qualified.json').write_text(json.dumps({
        'profiles': ['work'], 'configuration': real_key(config.settings, m.home, stage)}))
    assert health.active_evidence(m)
    settings = shared / 'settings.yaml'
    settings.write_text(json.dumps({'web_bundles': {'excluded': [CONTEXT_INTELLIGENCE_BEHAVIOR_URI]}}))
    before = settings.read_bytes()
    await health.refresh(m)
    health.publish(m)
    assert (m.service.state['updates']['currentStatus']['status'],
            m.service.state['updates']['currentStatus']['title']) == SETTINGS_CHANGED[:2]
    assert m.service.state['updates']['currentStatus']['component'] is None
    assert settings.read_bytes() == before
    # An ordinary qualification writes the new key, so this state is not
    # permanent. Exercise the real qualification/configuration owner;
    # subprocess mounts are the same explicit offline seam as its unit tests.
    from amplifier_web import updates, runtime_environment, runtime_qualification
    from unittest.mock import AsyncMock
    import shutil
    old_files = {p.name: p.read_bytes() for p in stage.iterdir() if p.is_file()}
    new_stage = stage.parent / ('d' * 32)
    shutil.copytree(stage, new_stage)
    (new_stage / 'shared-config/settings.yaml').write_bytes(before)
    m.inventory = []
    async def prepared(*args, **kwargs):
        return tmp_path / 'runtime'
    async def overrides(project, target, **kwargs):
        return target
    probes = []
    async def process(*args, **kwargs):
        assert '--read-only' in args and '--global-only' in args
        probes.append(args)
    monkeypatch.setattr(runtime_environment, 'stage', prepared)
    monkeypatch.setattr(runtime_qualification, 'prepare_overrides', overrides)
    monkeypatch.setattr(updates, 'process', process)
    m.publish = AsyncMock()
    await updates.UpdateManager.validate(m, new_stage, new_stage.name)
    assert len(probes) == 4
    qualified = json.loads((new_stage / 'profiles-qualified.json').read_text())
    assert qualified['configuration'] == real_key(health.configuration(m)[1].settings, m.home, new_stage)
    # A synthetic activation receipt is explicit: no real resolver/install or
    # worker promotion is claimed by this temporary-home health regression.
    pointer = json.loads((m.directory / 'active.json').read_text())
    (m.directory / 'active.json').write_text(json.dumps({**pointer, 'current': new_stage.name}))
    activation = next(row for row in m.diagnostics.state['completedBatches']
                      if row.get('revision') == stage.name)
    m.diagnostics.state['completedBatches'].append({**activation, 'revision': new_stage.name})
    await health.refresh(m)
    health.publish(m)
    assert m.service.state['updates']['currentStatus']['status'] == 'current'
    assert old_files == {p.name: p.read_bytes() for p in stage.iterdir() if p.is_file()}
    assert settings.read_bytes() == before
    (m.directory / 'active.json').write_text(json.dumps(pointer))
    settings.write_text('{}')
    await health.refresh(m)
    health.publish(m)
    assert m.service.state['updates']['currentStatus']['status'] == 'current'


# The installation matches its record, and the settings are not the ones that the
# components were checked with. A saved AI connection gives this state. Nothing
# failed, and nothing is required of the person.
SETTINGS_CHANGED = ('unchecked', 'Your settings changed since the components were checked',
                    'The installed components are as recorded. Your AI connections or other settings that choose '
                    'components changed after Amplifier checked these components. The next update check looks for '
                    'the components that the changed settings need. To check now, select Check for updates.')
CHECKING_SETTINGS = {'status': 'unchecked', 'title': 'Checking current configuration',
                     'detail': 'Update health will refresh against the changed settings. Existing components remain active.',
                     'actionRequired': False, 'action': None, 'component': None}
LOCAL_SOURCE = {'id': 'custom', 'label': 'Custom source', 'status': 'local_changes', 'updateTier': 'included'}


def settled(**changes):
    """A complete check that found nothing newer, on an app that is current."""
    return {'phase': 'checked', 'error': None, 'available': 0, 'items': [], 'sequence': {'stage': 'complete'},
            'application': {'status': 'current'}, 'diagnostics': {}, **changes}


def generation_proof(**limits):
    """What a health read holds when the selected generation's lock and recorded packages match."""
    return {'generation': 'a' * 32, 'verifiedAt': 20, 'startedAt': 10, 'configuration': 'b' * 64,
            'attemptId': 'c' * 32, 'packages': {'recorded'}, 'components': {'recorded'}, **limits}


def receipt_gone(**limits):
    """The same, when the bounded history no longer holds the activation of that generation."""
    return {'generation': 'a' * 32, 'activationUnknown': True, 'qualifiedAt': 10, 'configuration': 'b' * 64,
            'packages': {'recorded'}, 'components': {'recorded'}, **limits}


def component_failure(at):
    return {'activeFailures': {'one': {'latest': {
        'id': 'd' * 32, 'attemptId': 'e' * 32, 'kind': 'ecosystem', 'status': 'failed',
        'phase': 'ecosystem-stage', 'package': 'recorded', 'at': at}}}}


def settings_entry(state):
    """What the Settings entry "Updates" counts, and the item that an alarm adds."""
    attention = snapshot(state)
    return (attention['pages'].get('updates', 0), attention['settingsUnread'],
            [item['id'] for item in attention['items'] if item['id'] == 'updates:current'])


@pytest.mark.parametrize('also', ['nothing', 'receipt-gone', 'local-source', 'covered-failure'])
def test_changed_settings_on_an_installation_that_matches_its_record_are_no_alarm(also):
    state, proof = settled(), generation_proof(configurationChanged=True)
    if also == 'receipt-gone':
        proof = receipt_gone(configurationChanged=True)
    elif also == 'local-source':
        state = settled(items=[dict(LOCAL_SOURCE)])
    elif also == 'covered-failure':
        state = settled(diagnostics=component_failure(at=5))  # Before the activation of this generation.
    before = copy.deepcopy(state)
    value = health.current(state, proof)
    assert (value['status'], value['title'], value['detail']) == SETTINGS_CHANGED
    assert value['actionRequired'] is False
    assert value['action'] == 'check' and value['component'] is None
    # The support report still gets the limit of the proof.
    assert value['proofLimitation'] == {'configurationChanged': True,
                                        **({'activationUnknown': True} if also == 'receipt-gone' else {})}
    assert 'selectedFailure' not in value and 'failureKind' not in value
    text = (value['title'] + ' ' + value['detail']).lower()
    assert not any(word in text for word in ('qualif', 'graph', 'source configuration', 'verif', 'incomplete'))
    assert settings_entry({'updates': {**state, 'currentStatus': value}}) == (0, 0, [])
    assert state == before


@pytest.mark.parametrize('neighbour', ['no-proof', 'receipt-gone', 'local-source', 'setup-record', 'settings-equal',
                                       'after-the-next-check', 'not-checked', 'later-failure', 'later-failure-no-proof'])
def test_the_states_beside_changed_settings_keep_their_text_and_their_mark(neighbour):
    state, proof, expected, mark, action = {
        'no-proof': (settled(), {}, ('unverified', 'Component verification is incomplete',
            'The active component graph or configuration could not be verified. Current components are kept. '
            'Check for updates and inspect diagnostics if this remains.'), True, 'check'),
        'receipt-gone': (settled(), receipt_gone(), ('unchecked', 'Current components kept',
            'The installed graph matches its recorded qualification. Its older activation receipt is no longer '
            'retained, so full activation verification is unavailable. No reinstall is requested.'), False, None),
        'local-source': (settled(items=[dict(LOCAL_SOURCE)]), generation_proof(), ('unchecked', 'Custom sources kept',
            'Local source changes are kept as configured and are not replaced automatically. Source details remain '
            'available; no repair is required for intentional customization.'), False, None),
        # A new installation never reaches the state: setup's record carries no settings key.
        'setup-record': (settled(), {'setupRecord': True, 'packageCount': 3}, ('current', 'You\u2019re up to date',
            'No newer version of the app or its components was found. The components that setup recorded are '
            'installed as recorded.'), False, None),
        'settings-equal': (settled(), generation_proof(), ('current', 'You\u2019re up to date',
            'The current application and qualified components are ready. Earlier attempts remain in history; '
            'configured fixed versions are kept.'), False, None),
        # The next check lists the row "Qualify current component configuration", which ends the state.
        'after-the-next-check': (settled(available=1), generation_proof(configurationChanged=True),
            ('available', 'Updates available',
             'The app and eligible components can update while preserving configured pins and local sources.'),
            False, 'install'),
        'not-checked': (settled(sequence={}), generation_proof(configurationChanged=True), ('unchecked',
            'Check for updates', 'The full desired component state has not been checked.'), False, 'check'),
        # A component failure after the activation is not covered by the proof, with changed settings or without.
        'later-failure': (settled(diagnostics=component_failure(at=30)), generation_proof(configurationChanged=True),
            ('action-required', 'Update could not finish', 'The prior attempt has no applicable verified outcome. '
             'Current installation is retained; inspect diagnostics before retrying.'), True, 'diagnostics'),
        'later-failure-no-proof': (settled(diagnostics=component_failure(at=30)), {},
            ('action-required', 'Update could not finish', 'The prior attempt has no applicable verified outcome. '
             'Current installation is retained; inspect diagnostics before retrying.'), True, 'diagnostics'),
    }[neighbour]
    value = health.current(state, proof)
    assert (value['status'], value['title'], value['detail']) == expected
    assert (value['actionRequired'], value['action']) == (mark, action)
    count, unread, item = settings_entry({'updates': {**state, 'currentStatus': value}})
    assert item == (['updates:current'] if mark else [])
    # A local source is information and counts nothing. With automatic installation
    # (the default) a listed update is progress and counts nothing either.
    assert (count, unread) == ((1, 1) if mark else (0, 0))


def test_an_earlier_component_issue_keeps_its_own_sentence_about_changed_settings():
    """The other place that reads the limit. Its text has a mark for the earlier issue, and stays."""
    state, proof = acknowledged_app_with_old_component_history()
    value = health.current(state, {**proof, 'configurationChanged': True})
    assert value['title'] == 'Application updated; earlier component issue needs review'
    assert value['actionRequired'] and value['status'] != 'unchecked'
    assert 'The current source configuration also differs from that qualification. ' in value['detail']
    assert SETTINGS_CHANGED[1] not in value['title'] and SETTINGS_CHANGED[2] not in value['detail']


async def test_a_second_ai_connection_gives_the_quiet_text_until_the_next_check(tmp_path, monkeypatch):
    """What a person meets: a component update ran once; then a second AI connection is saved for all folders.

    The real settings read and the real key. The proof is of the fixture's generation.
    """
    from amplifier_web import runtime_profiles
    real_configuration, real_key = health.configuration, runtime_profiles.configuration_key
    m, stage, _, _ = mounted_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(health, 'configuration', real_configuration)
    monkeypatch.setattr(runtime_profiles, 'configuration_key', real_key)
    shared = tmp_path / 'canonical-shared'
    shared.mkdir()
    monkeypatch.setenv('AMPLIFIER_HOME', str(shared))
    settings = shared / 'settings.yaml'
    first = {'id': 'provider-openai-11111111', 'module': 'provider-openai', 'config': {'default_model': 'one'}}
    second = {'id': 'provider-openai-22222222', 'module': 'provider-openai', 'config': {'default_model': 'two'}}
    settings.write_text(json.dumps({'config': {'providers': [first]}}))
    # The component update qualified the settings with the one connection.
    qualified = real_key(health.configuration(m)[1].settings, m.home, stage)
    (stage / 'profiles-qualified.json').write_text(json.dumps({'profiles': ['work'], 'configuration': qualified}))
    u = m.service.state['updates']
    await health.refresh(m)
    health.publish(m)
    assert (u['currentStatus']['status'], u['currentStatus']['title']) == ('current', 'You\u2019re up to date')
    assert settings_entry(m.service.state) == (0, 0, [])
    # Another model for the same connection is no change of the key.
    settings.write_text(json.dumps({'config': {'providers': [{**first, 'config': {'default_model': 'three'}}]}}))
    assert real_key(health.configuration(m)[1].settings, m.home, stage) == qualified
    # A connection of another kind changes the key, and so does the other order of two kinds.
    # Two connections of one kind have one key in both orders.
    other = {'id': 'provider-anthropic-33333333', 'module': 'provider-anthropic', 'config': {'default_model': 'one'}}
    keys = []
    for rows in ([first, other], [other, first], [second, first], [first, second]):
        settings.write_text(json.dumps({'config': {'providers': rows}}))
        keys.append(real_key(health.configuration(m)[1].settings, m.home, stage))
    assert keys[2] == keys[3] and len({qualified, *keys}) == 4
    # "Save and check connection" for a second connection: the settings change, and the owner is told.
    assert json.loads(settings.read_text()) == {'config': {'providers': [first, second]}}
    m.service.state['configurationRevision'] = 2
    health.publish(m)
    assert u['currentStatus'] == CHECKING_SETTINGS
    before = copy.deepcopy(m.diagnostics.state)
    # The owner's pass reads the proof again. No check runs.
    await health.refresh(m)
    health.publish(m)
    status = u['currentStatus']
    assert (status['status'], status['title'], status['detail']) == SETTINGS_CHANGED
    assert status['actionRequired'] is False and status['action'] == 'check'
    assert status['proofLimitation'] == {'configurationChanged': True}
    assert settings_entry(m.service.state) == (0, 0, [])
    assert m._active_update_evidence['configurationChanged'] is True
    # Only the page's words changed: a preparation still gets no proof for these settings,
    # and no failure record was removed or settled.
    assert health.active_evidence(m) == {}
    assert m.diagnostics.state == before
    # The next check lists the changed settings as an update, and the state ends.
    u['available'] = 1
    health.publish(m)
    assert (u['currentStatus']['status'], u['currentStatus']['title']) == ('available', 'Updates available')
    # The connection is removed again: the settings are the checked ones.
    u['available'] = 0
    settings.write_text(json.dumps({'config': {'providers': [first]}}))
    await health.refresh(m)
    health.publish(m)
    assert (u['currentStatus']['status'], u['currentStatus']['title']) == ('current', 'You\u2019re up to date')


@pytest.mark.parametrize('automatic', [True, False])
@pytest.mark.parametrize('phase', [None, 'waiting', 'ready', 'failed', 'failed-unchanged', 'done'])
def test_the_launcher_step_reads_changed_settings_as_a_quiet_state(rig, phase, automatic):  # noqa: F811
    """On Windows the launcher's text shows over a quiet health state, and in three phases its button.

    The real `current()` and the real `windows_launcher_follow.project`. With the alarm status that this
    state had, the step only added one sentence to the alarm text and kept no generation for the button.
    """
    from amplifier_web import windows_launcher_follow as follow
    from test_windows_launcher_follow import TEXTS
    value = health.current(settled(), generation_proof(configurationChanged=True))
    m = rig.m
    m.service.state['settings']['updates']['autoInstall'] = automatic
    m._launcher_follow.phase = phase
    m.state['launcherRefresh'] = {'phase': phase, 'launcher': {'version': '1.0.0'}, 'app': follow._target(rig.target)}
    m.state['currentStatus'] = copy.deepcopy(value)
    follow.project(m)
    row, button = m.state['currentStatus'], m._launcher_follow.button_generation
    if phase in {None, 'done'}:
        # No launcher text for this state: its own text, with nothing added.
        assert row == value and button is None
    else:
        status, title, detail, action = TEXTS[phase]
        if phase in {'failed', 'failed-unchanged'}:
            detail += (' Amplifier tries again after the next app update. To try now, select Retry installation.'
                       if automatic else ' To try again, select Retry installation.')
        assert row == {'status': status, 'title': title, 'detail': detail, 'action': action,
                       'actionRequired': False, 'component': None}
        # A click on that button asks for a launcher refresh of this app generation.
        assert button == (rig.target['generation'] if phase in {'ready', 'failed', 'failed-unchanged'} else None)
    assert settings_entry(m.service.state) == (0, 0, [])
    follow.project(m)
    assert m.state['currentStatus'] == row


@pytest.mark.parametrize('case',['available','invalid-graph','local-only'])
def test_local_customization_does_not_mask_available_updates_or_invalid_graph(tmp_path,monkeypatch,case):
    m,stage,_,_=mounted_fixture(tmp_path,monkeypatch);u=m.service.state['updates']
    u['diagnostics']['activeFailures']={}
    u['items']=[{'id':'custom','label':'Custom source','status':'local_changes','updateTier':'included'}]
    if case=='available':u['available']=1
    elif case=='invalid-graph':(stage/'runtime-installed.json').write_text('[]')
    value=health.current(u,health.active_evidence(m))
    assert value['status']=={'available':'available','invalid-graph':'unverified','local-only':'unchecked'}[case]
    assert value['actionRequired']==(case=='invalid-graph')


async def test_inspection_fence_quietly_waits_through_ten_actual_automatic_ticks(tmp_path,monkeypatch):
    from amplifier_web.updates import UpdateManager
    import time
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch)
    m.service.state['settings']={'updates':{'autoInstall':True,'autoCheck':True}}
    m.service.state['updates'].update(phase='available',available=1,lastCheck=time.time(),
                                    adoption={'activeWorkers':0,'pendingWorkers':0})
    m.diagnostics.state['batch']={'retry':{'mode':'inspect'}}
    m.awaiting_restart=lambda:False
    m.lock=asyncio.Lock();m.cleanup_task=None;m._storage_last_pass=time.time()
    m.service.runtime=SimpleNamespace(generation_usage=lambda:[])
    m.requires_idle=lambda state:False
    # Any further work is a defect: tick must wait before checks/staging or its
    # exception handler can create another background-update failure.
    async def forbidden(*a,**kw):pytest.fail('automatic retry work')
    m.check=m.publish=forbidden
    m.install=lambda **kw:UpdateManager.install(m,**kw)
    # The owner's real rule for a check that time causes. The last check was just
    # now, so it names no reason. A rule that named one would run the forbidden check.
    m.check_due=lambda:UpdateManager.check_due(m)
    before=copy.deepcopy(m.service.state)
    for _ in range(10):await UpdateManager.tick(m)
    assert m.service.state==before


async def interrupted_copy_fixture(tmp_path,monkeypatch):
    """Cancel a real copy thread in the actual install orchestration."""
    import threading
    from amplifier_web import updates, update_storage, update_plan, session_files
    from amplifier_web.update_sequence import app_selection
    from amplifier_web.host import config as host_config
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch)
    m.lock=asyncio.Lock();m.closed=False;m.awaiting_restart=lambda:False
    m.independent_tool_results=lambda s:False
    m.service.lock=asyncio.Lock()
    u=m.service.state['updates'];u.update(phase='available',available=1,sequence={'stage':'included'})
    m.inventory=[{'id':'fixture','kind':'runtime environment','status':'update','eligible':True,'latest':'A'}]
    calls={'validate':0}
    async def publish(**kw):u.update(kw)
    async def build(*a):return {'mode':'worker','reason':'fixture','activation':'per-worker'}
    async def validate(stage,release):
        assert stage.is_dir() and stage.name==release
        calls['validate']+=1
        raise RuntimeError('fresh candidate stopped during validation')
    m.publish=publish;m.validate=validate
    monkeypatch.setattr(update_plan,'build',build)
    monkeypatch.setattr(health,'preparation_key',lambda m,rows:'A')
    source=tmp_path/'copy-source';source.mkdir();(source/'keep').write_text('unchanged')
    monkeypatch.setattr(updates,'foundation_home',lambda h:source)
    monkeypatch.setattr(session_files,'amplifier_home',lambda:tmp_path/'empty-shared')
    monkeypatch.setattr(host_config,'amplifier_home',lambda:tmp_path/'empty-shared')
    m.inventory[0]['appSelection']=app_selection(m.home)['witness']
    releases=m.directory/'releases';existing=set(releases.iterdir())
    started=threading.Event();release=threading.Event();finished=threading.Event()
    copy_snapshot=update_storage.copy_snapshot
    def copy_with_gate(source,target):
        started.set()
        assert release.wait(3), 'test did not release its owned copy'
        result=copy_snapshot(source,target);finished.set()
        return result
    monkeypatch.setattr(update_storage,'copy_snapshot',copy_with_gate)
    task=asyncio.create_task(updates.UpdateManager.install(m))
    assert await asyncio.to_thread(started.wait,3)
    task.cancel()
    await asyncio.sleep(0)  # Give cancellation the blocked owned copy to settle.
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert finished.is_set()
    assert calls=={'validate':0}
    proof=m.diagnostics.state['batch']['preparation']
    assert proof['boundary']=='copy-only' and proof['copySettled']
    assert set(releases.iterdir())-existing=={releases/proof['candidate']}
    assert (source/'keep').read_text()=='unchanged'
    return m,calls,copy.deepcopy(proof),copy.deepcopy(m.diagnostics.state['lastFailure'])


@pytest.mark.parametrize('case',['safe','pointer-drift','promotion','rollback','unsettled','validation','legacy','stage-replaced'])
async def test_manual_fresh_candidate_requires_actual_settled_copy_and_unchanged_pointer(tmp_path,monkeypatch,case):
    from amplifier_web.updates import UpdateManager
    m,calls,proof,failure=await interrupted_copy_fixture(tmp_path,monkeypatch)
    releases=m.directory/'releases';existing=set(releases.iterdir())
    u=m.service.state['updates'];batch=m.diagnostics.state.get('componentPreparation',m.diagnostics.state['batch']);old=m.diagnostics.state['attemptId']
    if case=='pointer-drift':
        (m.directory/'active.json').write_text(json.dumps({'current':'b'*32}))
    elif case=='promotion':u['pendingRelease']=proof['candidate']
    elif case=='rollback':u['pendingRollback']='c'*32
    elif case=='unsettled':batch['preparation']['copySettled']=False
    elif case=='validation':batch['preparation']['boundary']='preparing'
    elif case=='legacy':batch.pop('preparation')
    elif case=='stage-replaced':batch['preparation']['directoryIdentity']=[0,0]
    if case=='safe':
        await UpdateManager.install(m)
        assert calls=={'validate':1}
        assert m.diagnostics.state['attemptId']!=old
        assert m.diagnostics.state['revision']!=proof['candidate']
        assert set(releases.iterdir())-existing=={releases/m.diagnostics.state['revision']}
        assert any(row['phase']=='ecosystem-preparation-reconciled' for row in m.diagnostics.state['events'])
        assert any(group.get('latest')==failure for group in m.diagnostics.state['activeFailures'].values())
    else:
        before=copy.deepcopy(m.diagnostics.state)
        with pytest.raises(ValueError,match='no proof'):await UpdateManager.install(m)
        assert m.diagnostics.state==before
        assert calls=={'validate':0}
        assert set(releases.iterdir())==existing


@pytest.mark.parametrize('kind',['application','smart-tools','rollback'])
@pytest.mark.parametrize('safe',[True,False])
async def test_unrelated_batch_preserves_exact_component_fence_and_copy_recovery(tmp_path,monkeypatch,kind,safe):
    from amplifier_web.updates import UpdateManager
    m,calls,proof,_=await interrupted_copy_fixture(tmp_path,monkeypatch)
    releases=m.directory/'releases';existing=set(releases.iterdir())
    if not safe:
        m.diagnostics.state.get('componentPreparation',m.diagnostics.state['batch'])['preparation']['boundary']='preparing'
    m.diagnostics.begin(kind,'d'*40 if kind=='application' else 'd'*32)
    m.diagnostics.record('unrelated-check','succeeded')
    # Reload the same retained host state. The display batch and latest event
    # are now unrelated; neither may discard the unresolved component operation.
    m.diagnostics=UpdateDiagnostics(m)
    if safe:
        await UpdateManager.install(m)
        assert calls['validate']==1
        assert m.diagnostics.state['revision']!=proof['candidate']
        assert set(releases.iterdir())-existing=={releases/m.diagnostics.state['revision']}
        assert not health.pending_preparation(m.diagnostics.state)
    else:
        with pytest.raises(ValueError,match='interrupted'):await UpdateManager.install(m)
        assert calls['validate']==0
        assert set(releases.iterdir())==existing
        assert health.pending_preparation(m.diagnostics.state)


async def test_component_fence_allows_health_storage_adoption_and_independent_app(tmp_path,monkeypatch):
    from amplifier_web.updates import UpdateManager
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch)
    m.service.state['settings']={'updates':{'autoCheck':False,'autoInstall':True}}
    m.diagnostics.state['batch']={'kind':'ecosystem','attemptId':'a'*32,'retry':{'mode':'inspect'}}
    m.awaiting_restart=lambda:False;m.requires_idle=lambda u:False
    m.service.runtime=SimpleNamespace(generation_usage=lambda:[])
    m.cleanup_task=None;m._storage_policy_due=True;m._update_health_initial=True
    calls={'health':0,'storage':0,'app':0}
    async def refresh(manager):calls['health']+=1
    async def retire():calls['storage']+=1
    async def app():
        calls['app']+=1
        m.diagnostics.begin('application','e'*40)
    async def publish(**kw):m.service.state['updates'].update(kw)
    m.publish=publish;m.retire_storage=retire;m.app=app
    # The owner's real rule for a check that time causes. Automatic checks are off,
    # so it names no reason. A rule that named one would run this check.
    async def forbidden(*a,**kw):pytest.fail('a check although automatic checks are off')
    m.check=forbidden
    m.check_due=lambda:UpdateManager.check_due(m)
    m.service.state['updates']['appAvailable']=True
    monkeypatch.setattr(health,'refresh',refresh)
    await UpdateManager.tick(m)
    await m.cleanup_task
    assert calls=={'health':1,'storage':1,'app':1}
    assert m.service.state['updates']['adoption']=={'activeWorkers':0,'pendingWorkers':0}
    assert health.pending_preparation(m.diagnostics.state)


# Follow-up A35: "Update could not finish" for a component update that did not fail.

# What the page gets while the proof of a new selection is not read yet.
CHECKING_COMPONENTS = {'status': 'working', 'title': 'Checking components',
                       'detail': 'Amplifier is checking the components that it uses now.',
                       'actionRequired': False, 'action': None, 'component': None}
# The record of mounted_fixture already names the package of the PC
# (amplifier-module-provider-chat-completions), so another provider is the added one.
ADDED_PACKAGE = {'name': 'amplifier-module-provider-ollama', 'version': '1.0.0'}


def command_failures(updates):
    """The record of the PC: a command of an earlier attempt failed; no reason, no package."""
    for group in updates['diagnostics']['activeFailures'].values():
        for key in ('reason', 'package'):
            group['latest'].pop(key, None)
        group['latest'].update(phase='ecosystem-runtime-lock', errorType='CommandFailure')
    return max((group['latest'] for group in updates['diagnostics']['activeFailures'].values()),
               key=lambda row: row['at'])


@pytest.mark.parametrize('record', ['command', 'source'])
async def test_a_package_that_the_record_does_not_name_keeps_the_health_proof(tmp_path, monkeypatch, record):
    """Second form: a connection check installed one more package into the selected environment."""
    from amplifier_web import runtime_qualification
    from amplifier_web.update_partial import Preparation
    m, stage, _, source = mounted_fixture(tmp_path, monkeypatch)
    u = m.service.state['updates']
    if record == 'command':
        command_failures(u)  # Else as captured: a source conflict, "Component source needs review".
    recorded = source['runtime-installed.json']
    installed = copy.deepcopy(recorded)
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda p: copy.deepcopy(installed))
    await health.refresh(m)
    health.publish(m)
    assert u['currentStatus']['title'] == 'You’re up to date'
    installed.insert(4, dict(ADDED_PACKAGE))
    assert [row['name'] for row in installed] == sorted(row['name'] for row in installed)
    before = copy.deepcopy(m.diagnostics.state)
    await health.refresh(m)
    health.publish(m)
    status = u['currentStatus']
    assert status['title'] not in {'Update could not finish', 'Component source needs review'}
    assert 'failureAttemptId' not in status and 'selectedFailure' not in status
    assert (status['status'], status['title'], status['actionRequired']) == ('current', 'You’re up to date', False)
    attention = snapshot(m.service.state)
    assert not any(item.get('id') == 'updates:current' for item in attention['items'])
    assert attention['pages'].get('updates', 0) == 0
    # The proof names the recorded packages only.
    assert m._active_update_evidence['packages'] == {row['name'] for row in recorded}
    # The default compares exactly, and a preparation asks with the default.
    assert health.active_evidence(m) == {}
    assert health.active_evidence(m, explain_configuration=True) == {}
    with pytest.raises(ValueError, match='verified working component generation'):
        await Preparation(m, stage, []).verify_baseline()
    assert m.diagnostics.state == before  # No failure record was removed or settled.


@pytest.mark.parametrize('recorded_package', ['changed', 'missing'])
async def test_a_changed_or_missing_recorded_package_ends_the_health_proof_beside_an_added_one(
        tmp_path, monkeypatch, recorded_package):
    from amplifier_web import runtime_qualification
    m, _, _, source = mounted_fixture(tmp_path, monkeypatch)
    u = m.service.state['updates']
    newest = command_failures(u)
    installed = copy.deepcopy(source['runtime-installed.json'])
    installed.insert(4, dict(ADDED_PACKAGE))
    if recorded_package == 'changed':
        installed[0]['version'] = '0.0.0+changed'
    else:
        del installed[1]
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda p: copy.deepcopy(installed))
    await health.refresh(m)
    health.publish(m)
    status = u['currentStatus']
    assert m._active_update_evidence == {}
    assert status['status'] != 'current' and status['actionRequired']
    assert status['failureAttemptId'] == newest['attemptId']
    assert any(item.get('id') == 'updates:current' for item in snapshot(m.service.state)['items'])


async def test_a_switch_publishes_no_earlier_failure_before_the_proof_of_the_new_selection_is_read(
        app, repo, monkeypatch):
    """First form: the owner's real switch, with an earlier failed component attempt in the record."""
    from amplifier_web.updates import active_release
    manager, _ = await prepare(app, repo)

    async def validate(stage, release):
        pass
    manager.validate = validate
    # As after a complete check: the sequence is at its last stage and the app is current.
    app.state['updates']['sequence'] = {'stage': 'other', 'install': True}
    app.state['updates']['application']['status'] = 'current'
    manager.diagnostics.begin('ecosystem', components=['repo'])
    failure = manager.diagnostics.record('ecosystem-runtime-lock', 'failed', errorType='CommandFailure')
    await manager.publish()
    assert app.state['updates']['currentStatus']['title'] == 'Update could not finish'
    assert app.state['updates']['currentStatus']['failureAttemptId'] == failure['attemptId']
    reads = []

    def evidence(m, **options):
        # This staging fixture stubs qualification, so the real read finds no
        # package record. The stand-in keeps the real rule for the time: the
        # proof is the completed activation of the generation that the pointer names.
        pointer = active_release(m.home)
        batch = next((row for row in reversed(m.diagnostics.state.get('completedBatches', []))
                      if row.get('revision') == pointer.get('current')
                      and row.get('phase') == 'ecosystem-activation'), None)
        reads.append((app.state['updates']['phase'], getattr(m, '_update_proof_unread', None),
                      bool(batch), options.get('allow_additions')))
        if batch is None:
            return {}
        return {'generation': pointer['current'], 'verifiedAt': batch['completedAt'],
                'startedAt': batch['startedAt'], 'configuration': 'a' * 64,
                'attemptId': batch['attemptId'], 'packages': set(), 'components': set()}
    monkeypatch.setattr(health, 'active_evidence', evidence)
    published = []
    publish = app._publish_changes

    def observe(**kwargs):
        published.append({'phase': app.state['updates']['phase'],
                          'status': copy.deepcopy(app.state['updates']['currentStatus']),
                          'attention': [item['id'] for item in snapshot(app.state)['items']]})
        return publish(**kwargs)
    app._publish_changes = observe
    await manager.install()
    installed = [row for row in published if row['phase'] == 'installed']
    assert installed
    # No publication of the new selection tells of the earlier attempt.
    assert [row['status']['title'] for row in installed
            if row['status']['title'] == 'Update could not finish' or 'failureAttemptId' in row['status']] == []
    assert not any('updates:current' in row['attention'] for row in installed)
    assert installed[0]['status'] == CHECKING_COMPONENTS
    # The health read came after that publication, found the new proof, and ended the mark.
    assert [row for row in reads if row[0] == 'installed'] == [('installed', True, True, True)]
    assert manager._update_proof_unread is False
    last = app.state['updates']['currentStatus']
    assert installed[-1]['status'] == last
    assert (last['status'], last['title'], last['actionRequired']) == ('current', 'You’re up to date', False)
    # The earlier attempt stays in the record.
    assert failure in manager.diagnostics.state['events']
    assert [group['latest'] for group in manager.diagnostics.state['activeFailures'].values()] == [failure]


@pytest.mark.parametrize('case', ['record', 'source-record', 'after-app-update', 'error', 'phase-error',
                                  'phase-interrupted', 'application-record', 'no-record'])
def test_an_unread_proof_holds_back_only_a_result_from_an_earlier_component_record(case):
    failure = {'id': '1' * 32, 'attemptId': '2' * 32, 'kind': 'ecosystem', 'status': 'failed',
               'phase': 'ecosystem-runtime-lock', 'at': 10}
    state = {'phase': 'installed', 'error': None, 'release': 'a' * 32, 'available': 0, 'items': [],
             'sequence': {'stage': 'complete', 'install': False}, 'application': {'status': 'current'},
             'diagnostics': {'activeFailures': {'ecosystem:' + 'f' * 64: {'latest': failure}}}}
    evidence = {}
    if case == 'source-record':
        failure['reason'] = 'runtime-source-conflict'
    elif case == 'after-app-update':
        state, evidence = acknowledged_app_with_old_component_history()
    elif case == 'error':
        state['error'] = 'Could not activate the ecosystem update; review its diagnostic receipt before retrying.'
    elif case == 'phase-error':
        state['phase'] = 'error'
    elif case == 'phase-interrupted':
        state['phase'] = 'interrupted'
    elif case == 'application-record':
        failure['kind'] = 'application'
    elif case == 'no-record':
        state['diagnostics']['activeFailures'] = {}
        state['available'] = 1
    manager = SimpleNamespace(service=SimpleNamespace(state={'updates': state}), _active_update_evidence=evidence)
    health.publish(manager)
    plain = copy.deepcopy(state['currentStatus'])
    assert plain['title'] == {'source-record': 'Component source needs review', 'no-record': 'Updates available',
        'after-app-update': 'Application updated; earlier component issue needs review'}.get(
            case, 'Update could not finish')
    assert plain.get('failureKind') == {'no-record': None, 'application-record': 'application'}.get(case, 'ecosystem')
    manager._update_proof_unread = True
    health.publish(manager)
    if case in {'record', 'source-record', 'after-app-update'}:
        assert state['currentStatus'] == CHECKING_COMPONENTS
    else:
        # A failure of the operation itself, and a result from no component record, stay.
        assert state['currentStatus'] == plain
    # The support report's selection is the same with the mark.
    assert health.projection(manager) == plain


@pytest.mark.parametrize('read', ['proof', 'nothing', 'raises'])
async def test_a_health_read_does_not_end_the_unread_mark(tmp_path, monkeypatch, read):
    """Only the switch ends the mark. Another read (the owner's pass) did not read the new selection."""
    m, _, project, _ = mounted_fixture(tmp_path, monkeypatch)
    u = m.service.state['updates']
    command_failures(u)
    m._update_proof_unread = True
    health.publish(m)  # No proof is held, and none is read yet.
    assert u['currentStatus'] == CHECKING_COMPONENTS
    if read == 'raises':
        def broken(manager, **options):
            raise RuntimeError('the read ended with an error')
        monkeypatch.setattr(health, 'active_evidence', broken)
        with pytest.raises(RuntimeError):
            await health.refresh(m)
    else:
        if read == 'nothing':
            (project / 'uv.lock').write_text('changed')
        await health.refresh(m)
    assert m._update_proof_unread is True
    health.publish(m)
    status = u['currentStatus']
    if read == 'proof':
        # A result that comes from no failure record is published as it is.
        assert (status['status'], status['title']) == ('current', 'You’re up to date')
    else:
        assert status == CHECKING_COMPONENTS


async def earlier_failed_attempt(app, repo):
    """The real owner with a prepared update, and one earlier failed component attempt in its record."""
    manager, _ = await prepare(app, repo)

    async def validate(stage, release):
        pass
    manager.validate = validate
    app.state['updates']['sequence'] = {'stage': 'other', 'install': True}
    app.state['updates']['application']['status'] = 'current'
    manager.diagnostics.begin('ecosystem', components=['repo'])
    failure = manager.diagnostics.record('ecosystem-runtime-lock', 'failed', errorType='CommandFailure')
    await manager.publish()
    assert app.state['updates']['currentStatus']['title'] == 'Update could not finish'
    return manager, failure


async def test_a_switch_whose_own_read_finds_no_proof_ends_the_mark_and_shows_the_earlier_record(
        app, repo, monkeypatch):
    """The mark holds a record back only until the switch's own read ends, also when that read finds nothing."""
    manager, failure = await earlier_failed_attempt(app, repo)
    reads = []

    def evidence(m, **options):
        reads.append((app.state['updates']['phase'], getattr(m, '_update_proof_unread', None)))
        return {}
    monkeypatch.setattr(health, 'active_evidence', evidence)
    await manager.install()
    assert ('installed', True) in reads  # The switch's own read ran with the mark set.
    assert manager._update_proof_unread is False
    state = app.state['updates']
    assert state['phase'] == 'installed' and not state.get('error')
    status = state['currentStatus']
    assert status['title'] == 'Update could not finish' and status['actionRequired']
    assert status['failureAttemptId'] == failure['attemptId']


async def test_a_health_read_that_began_before_the_switch_shows_no_earlier_failure(app, repo, monkeypatch):
    """The owner's pass reads the proof without the owner's lock; a person's Install runs the switch meanwhile.

    The pass's read began before the switch, so it read no new selection. It ends after the switch's first
    publication, and the pass publishes. That publication must not tell of the earlier attempt.
    """
    import threading
    from amplifier_web.updates import active_release
    manager, _ = await earlier_failed_attempt(app, repo)
    early_started, early_may_end, late_may_end = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def evidence(m, **options):
        calls.append(len(calls))
        if calls[-1] == 0:
            # The pass's read. It began before the switch: no generation is selected.
            assert not active_release(m.home).get('current')
            early_started.set()
            assert early_may_end.wait(20)
            return {}
        assert late_may_end.wait(20)  # The switch's own read ends after the pass's publication.
        pointer = active_release(m.home)
        batch = next(row for row in reversed(m.diagnostics.state['completedBatches'])
                     if row.get('revision') == pointer['current'])
        return {'generation': pointer['current'], 'verifiedAt': batch['completedAt'],
                'startedAt': batch['startedAt'], 'configuration': 'a' * 64,
                'attemptId': batch['attemptId'], 'packages': set(), 'components': set()}
    monkeypatch.setattr(health, 'active_evidence', evidence)

    async def owner_pass():
        # The two lines of the owner's pass after a host start or a settings change.
        await health.refresh(manager)
        asyncio.get_running_loop().call_later(0, late_may_end.set)
        await manager.publish()
    owner = asyncio.create_task(owner_pass())
    while not early_started.is_set():
        await asyncio.sleep(0.01)
    published = []
    publish = app._publish_changes

    def observe(**kwargs):
        updates = app.state['updates']
        published.append((updates['phase'], getattr(manager, '_update_proof_unread', None),
                          updates['currentStatus']['title']))
        if updates['phase'] == 'installed':
            early_may_end.set()  # The pass's read ends after the switch published its selection.
        return publish(**kwargs)
    app._publish_changes = observe
    try:
        await manager.install()
        await owner
    finally:
        early_may_end.set()
        late_may_end.set()
    rows = [row for row in published if row[0] == 'installed']
    # The switch's first publication, the pass's publication, the switch's last publication.
    assert rows == [('installed', True, 'Checking components'), ('installed', True, 'Checking components'),
                    ('installed', False, 'You’re up to date')]
    assert manager._update_proof_unread is False


@pytest.mark.parametrize('where,error', [('read', RuntimeError), ('publication', RuntimeError)])
async def test_a_switch_that_ends_with_an_error_leaves_no_unread_mark_and_shows_its_failure(
        app, repo, monkeypatch, where, error):
    manager, _ = await prepare(app, repo)

    async def validate(stage, release):
        pass
    manager.validate = validate
    seen = []
    if where == 'read':
        async def refresh(m):
            seen.append(getattr(m, '_update_proof_unread', None))
            raise error('the read ended with an error')
        monkeypatch.setattr(health, 'refresh', refresh)
    else:
        publish = app._publish_changes

        def refuse(**kwargs):
            if app.state['updates']['phase'] == 'installed':
                seen.append(getattr(manager, '_update_proof_unread', None))
                raise error('the publication ended with an error')
            return publish(**kwargs)
        app._publish_changes = refuse
    await manager.install()  # It raises nothing.
    assert seen == [True]  # The mark was set when the error came.
    assert manager._update_proof_unread is False
    state = app.state['updates']
    assert state['phase'] == 'error'
    status = state['currentStatus']
    assert status['title'] == 'Update could not finish' and status['actionRequired']
    assert status['failurePhase'] == 'ecosystem-activation'


@pytest.mark.parametrize('where', ['its own read', 'the publication after its own read'])
@pytest.mark.parametrize('found', ['a proof', 'no proof'])
async def test_a_switch_that_is_cancelled_before_it_published_the_result_of_its_own_read_leaves_that_to_the_owner(
        app, repo, monkeypatch, where, found):
    """The task of the switch is cancelled in its own read, or in the publication after that read.

    An earlier failed attempt is in the record. At the cancellation the page
    holds "Checking components". In the first place nothing read the proof of
    the new selection. In the second place the read ended and the mark ended,
    and the publication of its result waits for the service's lock. The
    cancelled switch publishes nothing (the case records every publication
    from before the switch). It ends the mark, it asks the update owner for
    the read, and it wakes the owner. The real pass then reads and publishes
    what it found as its first publication: "You're up to date" with a proof
    of the new selection, the earlier attempt without one. That pass is quiet
    otherwise (no check is due, the sequence is complete): the same pass with
    no such request publishes nothing, so without the request the page would
    keep "Checking components".
    """
    import time
    from amplifier_web.updates import active_release
    manager, failure = await earlier_failed_attempt(app, repo)

    def evidence(m, **options):
        # The stand-in of the first case of the switch: this staging fixture stubs qualification.
        pointer = active_release(m.home)
        batch = next((row for row in reversed(m.diagnostics.state.get('completedBatches', []))
                      if row.get('revision') == pointer.get('current')
                      and row.get('phase') == 'ecosystem-activation'), None)
        if batch is None or found == 'no proof':
            return {}
        return {'generation': pointer['current'], 'verifiedAt': batch['completedAt'],
                'startedAt': batch['startedAt'], 'configuration': 'a' * 64,
                'attemptId': batch['attemptId'], 'packages': set(), 'components': set()}
    monkeypatch.setattr(health, 'active_evidence', evidence)
    read, reads, reached, never = health.refresh, [], asyncio.Event(), asyncio.Event()

    async def refresh(m):
        reads.append(getattr(m, '_update_proof_unread', None))
        if len(reads) > 1:
            return await read(m)   # The owner's pass: the real read.
        if where == 'its own read':
            reached.set()
            await never.wait()     # The cancellation comes here, inside the switch's read.
        await read(m)              # The switch's read ends.
        await app.lock.acquire()   # Its publication must now wait for the service's lock.
        reached.set()
    monkeypatch.setattr(health, 'refresh', refresh)
    published, publish = [], app._publish_changes

    def observe(**kwargs):
        published.append((app.state['updates']['phase'], copy.deepcopy(app.state['updates']['currentStatus']),
                          [item['id'] for item in snapshot(app.state)['items']]))
        return publish(**kwargs)
    app._publish_changes = observe
    manager._update_health_initial = False   # The first read of this host is over.
    switch = asyncio.create_task(manager.install())
    try:
        await asyncio.wait_for(reached.wait(), 60)
        for _ in range(10):
            await asyncio.sleep(0)   # The switch is at its wait now.
        # What the page holds when the cancellation comes, and where the switch is.
        at_cancel = len(published)
        assert published[-1][:2] == ('installed', CHECKING_COMPONENTS) and not switch.done()
        assert manager._update_proof_unread is (where == 'its own read')
        assert app.lock.locked() is (where != 'its own read')
        manager.wakeup.clear()
        switch.cancel()
        await asyncio.wait({switch}, timeout=60)
    finally:
        if app.lock.locked():
            app.lock.release()
        never.set()
    assert switch.cancelled()
    # The cancelled switch published nothing: the page still holds the working value.
    assert len(published) == at_cancel and app.state['updates']['currentStatus'] == CHECKING_COMPONENTS
    assert manager._update_proof_unread is False
    # It asked the owner for the read, and it woke the owner.
    assert manager._update_health_initial is True and manager.wakeup.is_set()
    # An otherwise quiet pass: no check is due, the sequence is complete, no storage pass is due.
    app.state['updates'].update(lastCheck=time.time(), sequence={'stage': 'complete', 'install': False})
    manager._start_check_open = False
    manager._storage_last_pass, manager._storage_policy_due = time.time(), False
    await manager.tick()   # The real pass.
    after = published[at_cancel:]
    assert reads[1:] == [False] and manager._update_health_initial is False
    # Its first publication is the result of its read, and no publication of it is the working value.
    assert after and CHECKING_COMPONENTS not in [row[1] for row in after]
    _, status, attention = after[0]
    if found == 'a proof':
        assert (status['status'], status['title'], status['actionRequired']) == ('current', 'You\u2019re up to date', False)
        assert 'updates:current' not in attention
    else:
        assert status['title'] == 'Update could not finish' and status['actionRequired']
        assert status['failureAttemptId'] == failure['attemptId']
    assert app.state['updates']['currentStatus'] == status
    # The earlier attempt stays in the record.
    assert failure in manager.diagnostics.state['events']
    # The same pass with no request for a read: it reads nothing and publishes nothing.
    count = len(published)
    await manager.tick()
    assert len(reads) == 2 and len(published) == count


async def test_a_switch_that_is_cancelled_before_it_publishes_its_selection_asks_for_no_read(app, repo, monkeypatch):
    """The cancellation comes inside the switch, at the write of the pointer: before the mark and its first publication.

    The page holds no working value then. The cancelled switch asks the owner for no read and does not wake it.
    """
    from amplifier_web import updates
    manager, _ = await earlier_failed_attempt(app, repo)
    write, seen = updates.write_private, []

    def cancelled_at_the_pointer(path, *args, **settings):
        if Path(path).name == 'active.json':
            seen.append(app.state['updates']['phase'])
            raise asyncio.CancelledError('the switch was cancelled before it published its selection')
        return write(path, *args, **settings)
    monkeypatch.setattr(updates, 'write_private', cancelled_at_the_pointer)
    manager._update_health_initial = False
    manager.wakeup.clear()
    with pytest.raises(asyncio.CancelledError):
        await manager.install()
    assert seen == ['activating']   # Inside the switch, after it published that it switches.
    assert getattr(manager, '_update_proof_unread', False) is False
    assert manager._update_health_initial is False and not manager.wakeup.is_set()
    assert app.state['updates']['currentStatus'] != CHECKING_COMPONENTS



# The first read of a host's proof, when a hold kept the owner's passes from it.

def new_host(c, monkeypatch, *, hold_returns=False, hold_stays=False, real_waits=1):
    """A second manager for the same saved state, as a host start makes it, with a hold on its passes.

    The loop and the pass are the real ones. Three stand-ins: the hold (on
    Windows an open launcher request; ``fenced`` is false elsewhere), the step
    after a pass that ends it (``consider``), and the clock. ``wait_for_work``
    records each wait and whether the wake was set, and it does not wait. The
    case ends at the wait number ``real_waits`` that would take its 60 seconds.
    """
    from amplifier_web import windows_launcher_follow as follow
    from amplifier_web.updates import UpdateManager
    import time
    manager = UpdateManager(c.service)
    manager._storage_last_pass = time.time()  # No storage pass: it is not the subject.
    hold, waits, passes, tick = {'on': True}, [], [], manager.tick
    monkeypatch.setattr(follow, 'fenced', lambda manager: hold['on'])

    async def consider(manager):
        hold['on'] = hold_stays

    async def held_pass():
        passes.append(len(waits))
        if hold_returns and len(passes) > 1:
            hold['on'] = True  # The hold is on again inside the next pass.
        await tick()

    async def wait(delay):
        woken = manager.wakeup.is_set()
        waits.append((delay, woken))
        if delay == 60 and not woken:
            real = sum(1 for seconds, wake in waits if seconds == 60 and not wake)
            manager.closed = real >= real_waits
        manager.closed = manager.closed or len(waits) > 12  # A loop that never waits ends here.

    monkeypatch.setattr(follow, 'consider', consider)
    monkeypatch.setattr(manager, 'tick', held_pass)
    monkeypatch.setattr(manager, 'wait_for_work', wait)
    return manager, waits, passes


async def checked_new_installation(c):
    await c.manager.check()
    assert c.state['currentStatus']['detail'] == UP_TO_DATE


def incomplete(c):
    status = c.state['currentStatus']
    return (status['status'], status['title'], status['detail'], status['actionRequired']) == (
        'unverified', 'Component verification is incomplete', INCOMPLETE, True)


async def test_the_first_read_of_a_host_follows_at_once_when_its_hold_ends_after_a_pass(
        setup_record, monkeypatch):  # noqa: F811
    """A new installation that is up to date. A host starts with a hold on its passes.

    Seen on a PC after a launcher refresh: for the 60 seconds between two
    passes the page said "Component verification is incomplete", with the
    unread mark. The hold ends in the step after the first pass. The next pass
    comes with no wait, reads the proof and publishes what the check found.
    """
    async with checked_install(setup_record.home, core_row(setup_record), monkeypatch) as c:
        await checked_new_installation(c)
        manager, waits, passes = new_host(c, monkeypatch)
        assert incomplete(c) and manager._update_health_initial is True  # A host that has read no proof.
        await asyncio.wait_for(manager.loop(), 60)
        # The start, the wait after the held pass (the wake is set), the wait after the pass that read.
        assert waits == [(3, False), (60, True), (60, False)] and len(passes) == 2
        assert manager._update_health_initial is False and health.cached_evidence(manager).get('setupRecord')
        status = c.state['currentStatus']
        assert (status['status'], status['detail'], status['actionRequired']) == ('current', UP_TO_DATE, False)


async def test_a_hold_that_is_on_again_in_the_next_pass_gets_no_second_pass_without_a_wait(
        setup_record, monkeypatch):  # noqa: F811
    """The hold ends after each pass and is on again inside the next one.

    The wake starts one pass. That pass asks for no other: the loop waits its
    60 seconds. So at most one pass without a wait follows each pass that
    waited, and the loop cannot run without a wait.
    """
    async with checked_install(setup_record.home, core_row(setup_record), monkeypatch) as c:
        await checked_new_installation(c)
        manager, waits, passes = new_host(c, monkeypatch, hold_returns=True, real_waits=2)
        await asyncio.wait_for(manager.loop(), 60)
        assert waits == [(3, False), (60, True), (60, False), (60, True), (60, False)] and len(passes) == 4
        assert manager._update_health_initial is True and incomplete(c)  # No pass came to the read.


async def test_a_hold_that_stays_gets_no_wake(setup_record, monkeypatch):  # noqa: F811
    async with checked_install(setup_record.home, core_row(setup_record), monkeypatch) as c:
        await checked_new_installation(c)
        manager, waits, passes = new_host(c, monkeypatch, hold_stays=True, real_waits=2)
        await asyncio.wait_for(manager.loop(), 60)
        assert waits == [(3, False), (60, False), (60, False)] and len(passes) == 2
        assert manager._update_health_initial is True and incomplete(c)


async def test_no_launcher_request_begins_at_the_pass_without_a_wait_or_at_once_after_it(
        rig, setup_record, monkeypatch):  # noqa: F811
    """The real loop, the real pass and the real step after a pass (``windows_launcher_follow.consider``).

    A launcher refresh begins only when the update state is the same at two
    passes in a row. Between two passes the loop waits its 60 seconds, so
    that rule gives a minute of unchanged state before a refresh. The wake
    for the first read adds one pass without a wait. It must add no more.

    A host starts with an open launcher request of an earlier host. The step
    after the first pass withdraws that request, which ends the hold. The
    pass without a wait follows and makes the first read; it is the first
    look at the update state, so no request begins there. The loop then
    waits. The request begins at the pass after that wait.

    Not Windows: the Windows parts of a refresh (its files, the Scheduler
    task) are the stand-ins of ``test_windows_launcher_follow.rig``. The
    clock of the launcher code is the stand-in's, and the case moves it past
    the 300 seconds of a withdrawn request, so that only the two equal looks
    hold a new request back.
    """
    from amplifier_web import windows_launcher_follow as follow
    from amplifier_web.updates import UpdateManager
    import time
    async with checked_install(setup_record.home, core_row(setup_record), monkeypatch) as c:
        await checked_new_installation(c)
        rig.records['request'] = {
            'schema': 1, 'id': 'c' * 32, 'registration': rig.spec.registration_id,
            'launcher': {'prefix': 'launcher', 'digest': 'old'}, 'target': follow._target(rig.target),
            'status': 'open', 'result': None, 'hold': None, 'repair': False, 'at': rig.now[0]}
        rig.m.state['launcherRefresh'] = {'attempt': True}   # The stand-in for the task reads it from its own owner.
        manager = UpdateManager(c.service)
        manager._storage_last_pass = time.time()   # No storage pass: it is not the subject.
        follow.at_start(manager)   # As the server does it after it made the owner.
        assert follow.fenced(manager) and manager.awaiting_restart() and manager._update_health_initial is True
        waits = []

        async def wait(delay):
            begun = sum(1 for call in rig.calls if call[0] == 'plan')
            waits.append((delay, manager.wakeup.is_set(), begun))
            if len(waits) == 2:
                rig.now[0] += 301   # The 300 seconds after a withdrawn request are over.
            manager.closed = bool(begun) or len(waits) > 8   # A loop that begins no request ends here.

        monkeypatch.setattr(manager, 'wait_for_work', wait)
        await asyncio.wait_for(manager.loop(), 60)
        # Each wait: its seconds, whether the wake was set (no wait then), and the requests begun before it.
        assert waits == [
            (3, False, 0),    # The start of the loop. Then the pass that the hold stops; the step after it ends the hold.
            (60, True, 0),    # The wake for the first read: the next pass comes with no wait.
            (60, False, 0),   # After that pass: the first read is made, no request is begun, and the loop waits.
            (60, False, 1),   # After the pass that followed the wait: the request is begun.
        ], waits
        assert rig.records['request']['status'] == 'open' and rig.records['request']['id'] != 'c' * 32
        assert manager._update_health_initial is False and health.cached_evidence(manager).get('setupRecord')
        assert c.state['currentStatus']['title'] != 'Component verification is incomplete'
