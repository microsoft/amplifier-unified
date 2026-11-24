"""Chat-reference browser proof and its opt-in supervisor integration."""
import hashlib
import json
import shutil
import subprocess
import sys

import pytest

from test_browser_check_runner import ROOT, execute, runner, scenario

checks = runner.chat_reference_checks()


# Independent literal identities: do not construct the positive TAP fixture from
# the validator's allow-list, or a changed/missing identity could validate itself.
APP004_EXPECTED_GROUPS = (
    (
        'chat reference metadata is a fixed bounded data shape',
        'chat reference rejects action',
        'chat reference rejects URL',
        'chat reference rejects foreign source',
        'chat reference rejects foreign message',
        'chat reference rejects missing target',
        'chat reference rejects wrong version',
        'chat reference rejects invalid revision',
        'chat reference rejects unbounded label',
        'chat reference rejects control character',
        'chat reference rejects missing availability',
        'chat reference rejects duplicate, oversized and expanded envelopes',
    ),
    (
        'copy uses an available message without another request',
        'copy reads an explicitly addressed off-page chat without changing selection',
        'missing scoped message reports failure instead of copying unrelated content',
        'copy retains prose and appends only the host-supplied inert reference fallback',
    ),
    (
        'held route stays blocked through exercise and drains once before unregistration',
        'drain includes concurrent callbacks and waiting unroute joins a final protocol arrival',
        'callback errors become an awaited failure, not ignored or retried',
        'primary assertion survives successful release and cleanup',
        'primary error is retained with callback and teardown errors as secondary',
        'early-started action rejection stays observable until it is awaited',
        'cleanup failure alone fails and cannot replace a prior primary failure',
    ),
)
APP004_EXPECTED_NAMES = tuple(name for group in APP004_EXPECTED_GROUPS for name in group)
ABSENT = object()


def app004_tap():
    return ('TAP version 13\n'
            + ''.join(f'ok {index} - {name}\n' for index, name in enumerate(APP004_EXPECTED_NAMES, 1))
            + '1..23\n# tests 23\n# suites 0\n# pass 23\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n')


def app004_witness(token='fresh-fixture-token'):
    # Synthetic validator input, NOT evidence of a browser/card/Node execution.
    outcome = {
        'qualificationToken': token, 'negativeReload': True,
        'identityBoundary': {
            'id': 'app004-same-document', 'attachmentAccepted': True, 'cardVisible': True,
            'draftHeld': True, 'clickCompleted': True, 'attempted': True, 'verdict': 'failed',
            'targetSessionId': 'fixture-target', 'selectedSessionId': 'fixture-target',
            'before': {'document': 'before-document', 'timeOrigin': 1000, 'client': 'fixture-client'},
            'after': {'document': 'after-document', 'timeOrigin': 2000, 'client': 'fixture-client'},
        },
        'primary': {'name': 'AssertionError', 'code': 'ERR_ASSERTION', 'boundary': 'app004-same-document'},
        'browserErrors': [], 'secondary': [], 'fixtureExit': {'code': 0, 'signal': None},
    }
    routes = [
        {'route': 'held-draft', 'phase': 'registered'},
        {'route': 'held-draft', 'phase': 'started', 'id': 1},
        {'route': 'held-draft', 'phase': 'gate-released'},
        {'route': 'held-draft', 'phase': 'completed', 'id': 1},
        {'route': 'held-draft', 'phase': 'drained'},
        {'route': 'held-draft', 'phase': 'unregistered'},
    ]
    return outcome, routes


def write_app004_witness(folder, token='fresh-fixture-token'):
    folder.mkdir(parents=True, exist_ok=True)
    outcome, routes = app004_witness(token)
    (folder / 'outcome.json').write_text(json.dumps(outcome))
    (folder / 'route-lifecycle.json').write_text(json.dumps(routes))
    return outcome, routes


def change_app004_field(value, path, replacement):
    keys = path.split('.')
    for key in keys[:-1]:
        value = value[key]
    if replacement is ABSENT:
        value.pop(keys[-1])
    else:
        value[keys[-1]] = replacement


def prepare_app004_verdict(tmp_path, identity='reload-negative'):
    folder = tmp_path / 'qualification'
    write_app004_witness(folder / 'negative')
    row = {'id': identity, 'status': 'failed' if identity == 'reload-negative' else 'passed',
           'reason': None, 'exitCode': 1 if identity == 'reload-negative' else 0,
           'cleanup': {'remaining': [], 'terminated': 0}, 'executionToken': 'fresh-fixture-token',
           'log': identity + '.log'}
    (folder / row['log']).write_text(app004_tap() if identity == 'units' else 'synthetic assertion failure\n')
    return row, folder


def mock_app004_commands(monkeypatch, *, units_source=None, negative_source=None, timeout=2):
    # Only descriptors are substituted. Real scheduler, ownership, deadlines and
    # verdicts run against harmless Python children; this is not Node/Chromium QA.
    initial = checks.initial_report
    outcome, routes = app004_witness()
    if units_source is None:
        units_source = (f'print({app004_tap()!r},end="");import os;'
                        'print("# fixture-owner "+os.environ["AMPLIFIER_BROWSER_CHECK_OWNER"])')
    if negative_source is None:
        negative_source = (
            'import json,os;from pathlib import Path;'
            'token=os.environ["APP004_QUALIFICATION_TOKEN"];'
            'assert token and token!="inherited-stale-token";'
            'assert token==os.environ["AMPLIFIER_BROWSER_CHECK_OWNER"];'
            'assert os.environ["CHAT_REFERENCE_NEGATIVE_RELOAD"]=="1";'
            f'outcome=json.loads({json.dumps(outcome)!r});outcome["qualificationToken"]=token;'
            'folder=Path(os.environ["CHAT_REFERENCE_EVIDENCE_DIR"]);folder.mkdir(parents=True,exist_ok=True);'
            '(folder/"outcome.json").write_text(json.dumps(outcome));'
            f'(folder/"route-lifecycle.json").write_text({json.dumps(routes)!r});'
            'print("synthetic negative witness");raise SystemExit(1)'
        )
    def fixed(provenance):
        report = initial(provenance)
        for row, source in zip(report['checks'], (units_source, negative_source), strict=True):
            row['command'] = [sys.executable, '-c', source]
            row['timeoutSeconds'] = timeout
        return report
    monkeypatch.setattr(checks, 'initial_report', fixed)
    monkeypatch.setattr(runner, 'chat_reference_checks', lambda: checks)


def test_app004_fixed_commands_and_literal_twenty_three_identities():
    provenance = {'revision': 'b' * 40}
    report = checks.initial_report(provenance)
    assert report == {
        'schemaVersion': 1, 'source': provenance, 'complete': False, 'passed': False,
        'checks': [
            {'id': 'units', 'status': 'unrun', 'reason': 'not_started', 'timeoutSeconds': 60,
             'command': ['node', '--test', '--test-reporter=tap', 'tests/chat-reference.test.mjs',
                         'tests/message-copy.test.mjs', '../../tests/web/chat-reference-harness.test.mjs'],
             'cwd': '.build/web-client'},
            {'id': 'reload-negative', 'status': 'unrun', 'reason': 'not_started', 'timeoutSeconds': 300,
             'command': ['node', 'tests/web/chat-reference-browser.mjs']},
        ],
    }
    assert tuple(map(len, APP004_EXPECTED_GROUPS)) == (12, 4, 7)
    assert len(APP004_EXPECTED_NAMES) == len(set(APP004_EXPECTED_NAMES)) == 23
    assert tuple(checks.UNIT_NAMES) == APP004_EXPECTED_NAMES
    assert checks.BOUNDARY == 'app004-same-document'


@pytest.mark.parametrize('corruption', [
    'missing-case', 'duplicate-name', 'foreign-name', 'extra-case', 'wrong-tests', 'wrong-pass',
    'failure', 'cancelled', 'skipped', 'todo', 'skip-directive', 'todo-directive',
    'not-ok', 'bail-out', 'duplicate-summary', 'missing-summary',
])
def test_app004_units_requires_exact_unique_unskipped_tap(tmp_path, corruption):
    log = tmp_path / 'units.log'
    valid = app004_tap()
    log.write_text(valid)
    assert checks.check_units(log) is None
    first = f'ok 1 - {APP004_EXPECTED_NAMES[0]}'
    mutations = {
        'missing-case': valid.replace(first + '\n', ''),
        'duplicate-name': valid.replace(APP004_EXPECTED_NAMES[-1], APP004_EXPECTED_NAMES[0]),
        'foreign-name': valid.replace(APP004_EXPECTED_NAMES[-1], 'unrelated unit'),
        'extra-case': valid + 'ok 24 - unrelated unit\n',
        'wrong-tests': valid.replace('# tests 23', '# tests 22'),
        'wrong-pass': valid.replace('# pass 23', '# pass 22'),
        'failure': valid.replace('# fail 0', '# fail 1'),
        'cancelled': valid.replace('# cancelled 0', '# cancelled 1'),
        'skipped': valid.replace('# skipped 0', '# skipped 1'),
        'todo': valid.replace('# todo 0', '# todo 1'),
        'skip-directive': valid.replace(first, first + ' # SKIP unavailable'),
        'todo-directive': valid.replace(first, first + ' # TODO later'),
        'not-ok': valid + 'not ok 24 - unexpected failure\n',
        'bail-out': valid + 'Bail out! stopped\n',
        'duplicate-summary': valid + '# pass 23\n',
        'missing-summary': valid.replace('# skipped 0\n', ''),
    }
    log.write_text(mutations[corruption])
    with pytest.raises(ValueError):
        checks.check_units(log)


@pytest.mark.parametrize('path,replacement', [
    ('qualificationToken', 'stale-fixture-token'), ('qualificationToken', ABSENT),
    ('negativeReload', False), ('negativeReload', 'true'),
    ('identityBoundary', {}), ('identityBoundary.id', 'some-other-assertion'),
    ('identityBoundary.verdict', 'passed'),
    *((f'identityBoundary.{flag}', False) for flag in
      ('attachmentAccepted', 'cardVisible', 'draftHeld', 'clickCompleted', 'attempted')),
    ('identityBoundary.clickCompleted', 1),
    ('identityBoundary.targetSessionId', ''), ('identityBoundary.targetSessionId', ABSENT),
    ('identityBoundary.selectedSessionId', 'wrong-target'),
    ('primary', {}), ('primary.name', 'Error'), ('primary.name', 'TimeoutError'),
    ('primary.code', 'OTHER_ERROR'), ('primary.boundary', 'later-focus-assertion'),
    ('primary.boundary', ABSENT), ('secondary', [{'name': 'Error', 'message': 'cleanup failed'}]),
    ('browserErrors', ABSENT), ('browserErrors', ['uncaught browser error']),
    ('fixtureExit', None), ('fixtureExit.code', 1), ('fixtureExit.code', False),
    ('fixtureExit.signal', 'SIGTERM'),
])
def test_app004_negative_requires_fresh_exact_genuine_click_boundary(tmp_path, path, replacement):
    outcome, _ = write_app004_witness(tmp_path)
    assert checks.check_reload(tmp_path, 'fresh-fixture-token') is None
    change_app004_field(outcome, path, replacement)
    (tmp_path / 'outcome.json').write_text(json.dumps(outcome))
    with pytest.raises(ValueError):
        checks.check_reload(tmp_path, 'fresh-fixture-token')


@pytest.mark.parametrize('path,replacement', [
    *((f'identityBoundary.{side}.{key}', ABSENT)
      for side in ('before', 'after') for key in ('document', 'timeOrigin', 'client')),
    *((f'identityBoundary.{side}.timeOrigin', value)
      for side in ('before', 'after') for value in (False, 0, -1, '2000')),
    *((f'identityBoundary.{side}.{key}', '')
      for side in ('before', 'after') for key in ('document', 'client')),
    ('identityBoundary.after.document', 'before-document'),
    ('identityBoundary.after.timeOrigin', 1000),
    ('identityBoundary.after', {'document': 'before-document', 'timeOrigin': 1000, 'client': 'other-client'}),
    ('identityBoundary.after.extra', 'not-a-marker'),
    ('identityBoundary.before.timeOrigin', float('nan')),
    ('identityBoundary.after.timeOrigin', float('inf')),
])
def test_app004_negative_requires_full_markers_and_both_reload_changes(tmp_path, path, replacement):
    outcome, _ = write_app004_witness(tmp_path)
    # A retained client alone does not invalidate a proven document reload.
    assert outcome['identityBoundary']['before']['client'] == outcome['identityBoundary']['after']['client']
    assert checks.check_reload(tmp_path, 'fresh-fixture-token') is None
    change_app004_field(outcome, path, replacement)
    (tmp_path / 'outcome.json').write_text(json.dumps(outcome))
    with pytest.raises(ValueError):
        checks.check_reload(tmp_path, 'fresh-fixture-token')


@pytest.mark.parametrize('corruption', [
    'empty', 'foreign-route', 'error', 'null-error', 'missing-completion', 'missing-drain',
    'missing-unregister', 'duplicate-start', 'duplicate-completion', 'completion-before-start',
    'wrong-id', 'boolean-id', 'missing-id', 'unknown-phase', 'reordered-phases',
    'missing-register', 'missing-gate', 'oversized',
])
def test_app004_negative_rejects_failed_or_unfinished_routes(tmp_path, corruption):
    _, routes = write_app004_witness(tmp_path)
    assert checks.check_reload(tmp_path, 'fresh-fixture-token') is None
    if corruption == 'empty':
        routes = []
    elif corruption == 'foreign-route':
        routes[1]['route'] = 'later-navigation'
    elif corruption in ('error', 'null-error'):
        routes[3]['error'] = 'Route is already handled!' if corruption == 'error' else None
    elif corruption.startswith('missing-') and corruption != 'missing-id':
        phase = {'missing-completion': 'completed', 'missing-drain': 'drained',
                 'missing-unregister': 'unregistered', 'missing-register': 'registered',
                 'missing-gate': 'gate-released'}[corruption]
        routes = [row for row in routes if row['phase'] != phase]
    elif corruption == 'duplicate-start':
        routes.insert(2, dict(routes[1]))
    elif corruption == 'duplicate-completion':
        routes.insert(4, dict(routes[3]))
    elif corruption == 'completion-before-start':
        routes[1], routes[3] = routes[3], routes[1]
    elif corruption == 'wrong-id':
        routes[3]['id'] = 2
    elif corruption == 'boolean-id':
        routes[1]['id'] = True
    elif corruption == 'missing-id':
        del routes[1]['id']
    elif corruption == 'unknown-phase':
        routes[3]['phase'] = 'failed'
    elif corruption == 'reordered-phases':
        routes[2], routes[4] = routes[4], routes[2]
    elif corruption == 'oversized':
        routes *= 1667
    (tmp_path / 'route-lifecycle.json').write_text(json.dumps(routes))
    with pytest.raises(ValueError):
        checks.check_reload(tmp_path, 'fresh-fixture-token')


@pytest.mark.parametrize('corruption', [
    'missing-outcome', 'missing-routes', 'invalid-json', 'outcome-list', 'proof-list', 'primary-list',
    'marker-list', 'routes-object', 'null-route', 'oversized-outcome', 'oversized-routes',
    'missing-log', 'stale-token',
])
def test_app004_verdict_rejects_missing_or_malformed_evidence(tmp_path, corruption):
    row, folder = prepare_app004_verdict(tmp_path)
    control = dict(row)
    checks.apply_verdict(control, folder)
    assert control['status'] == 'passed' and control['verdict'] == 'expected_identity_failure'
    negative = folder / 'negative'
    outcome_path, routes_path = negative / 'outcome.json', negative / 'route-lifecycle.json'
    outcome, routes = app004_witness()
    if corruption == 'missing-outcome':
        outcome_path.unlink()
    elif corruption == 'missing-routes':
        routes_path.unlink()
    elif corruption == 'invalid-json':
        outcome_path.write_text('{')
    elif corruption in ('outcome-list', 'proof-list', 'primary-list', 'marker-list'):
        if corruption == 'outcome-list':
            outcome = []
        else:
            change_app004_field(outcome, {'proof-list': 'identityBoundary', 'primary-list': 'primary',
                                         'marker-list': 'identityBoundary.before'}[corruption], [])
        outcome_path.write_text(json.dumps(outcome))
    elif corruption == 'routes-object':
        routes_path.write_text('{}')
    elif corruption == 'null-route':
        routes[1] = None
        routes_path.write_text(json.dumps(routes))
    elif corruption in ('oversized-outcome', 'oversized-routes'):
        path = outcome_path if corruption == 'oversized-outcome' else routes_path
        # Whitespace keeps the JSON valid: rejection must be the size limit.
        path.write_text(path.read_text() + ' ' * (1024 * 1024))
    elif corruption == 'missing-log':
        (folder / row['log']).unlink()
    elif corruption == 'stale-token':
        row['executionToken'] = 'different-run-token'
    checks.apply_verdict(row, folder)
    assert row['status'] == 'failed' and row['verdict'] == 'rejected'
    assert row['verdictReason']
    assert row['exitCode'] == 1 and row['reason'] is None
    assert 'evidenceSha256' not in row


@pytest.mark.parametrize('identity', ['units', 'reload-negative'])
def test_app004_verdict_retains_raw_exit_and_hashes_exact_inputs(tmp_path, identity):
    row, folder = prepare_app004_verdict(tmp_path, identity)
    raw = {key: row[key] for key in ('exitCode', 'reason', 'cleanup', 'executionToken', 'log')}
    checks.apply_verdict(row, folder)
    assert row['status'] == 'passed'
    assert row['verdict'] == ('exact_units' if identity == 'units' else 'expected_identity_failure')
    assert {key: row[key] for key in raw} == raw
    files = ([row['log']] if identity == 'units'
             else [row['log'], 'negative/outcome.json', 'negative/route-lifecycle.json'])
    assert row['evidenceSha256'] == {
        name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in files}


@pytest.mark.parametrize('identity,field,value', [
    *(('reload-negative', 'exitCode', code) for code in (0, 2, -15, None, True)),
    *(('units', 'exitCode', code) for code in (1, 7, -15, None, False)),
    *((identity, 'reason', reason) for identity in ('units', 'reload-negative')
      for reason in ('timeout', 'cancelled', 'FileNotFoundError')),
    *((identity, 'cleanup', cleanup) for identity in ('units', 'reload-negative')
      for cleanup in ({'remaining': [], 'terminated': 1},
                      {'remaining': [123], 'terminated': 0},
                      {'remaining': ['unknown'], 'terminated': 0, 'error': 'PermissionError'})),
])
def test_app004_verdict_never_waives_raw_failure_or_unexpected_green(tmp_path, identity, field, value):
    row, folder = prepare_app004_verdict(tmp_path, identity)
    control = dict(row)
    checks.apply_verdict(control, folder)
    assert control['status'] == 'passed'
    row[field] = value
    raw = json.dumps({key: row[key] for key in ('exitCode', 'reason', 'cleanup')}, sort_keys=True)
    checks.apply_verdict(row, folder)
    assert row['status'] == 'failed' and row['verdict'] == 'rejected'
    assert row['verdictReason']
    assert json.dumps({key: row[key] for key in ('exitCode', 'reason', 'cleanup')}, sort_keys=True) == raw
    assert 'evidenceSha256' not in row


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('normal_exit', [0, 7])
def test_app004_scheduler_is_opt_in_and_never_waives_normal_failure(tmp_path, monkeypatch, enabled, normal_exit):
    mock_app004_commands(monkeypatch)
    monkeypatch.setenv('APP004_QUALIFICATION_TOKEN', 'inherited-stale-token')
    rows = [scenario('normal', f'print("normal result");raise SystemExit({normal_exit})'),
            scenario('independent', 'print("independent result")')]
    code, report, output = execute(
        tmp_path, rows, budget=30, **({'app004_qualification': True} if enabled else {}))
    assert code == (1 if normal_exit else 0)
    assert report['complete'] and report['passed'] is (normal_exit == 0)
    assert [row['id'] for row in report['scenarios']] == ['normal', 'independent']
    assert [row['command'] for row in report['scenarios']] == [row['command'] for row in rows]
    assert [row['exitCode'] for row in report['scenarios']] == [normal_exit, 0]
    assert [row['status'] for row in report['scenarios']] == ['failed' if normal_exit else 'passed', 'passed']
    assert all(row['reason'] is None for row in report['scenarios'])
    assert 'normal result' in (output / 'normal.log').read_text()
    folder = output / 'app004-qualification'
    if not enabled:
        assert not folder.exists()
        return
    qualification = json.loads((folder / 'results.json').read_text())
    assert qualification['source'] == report['source']
    assert qualification['complete'] and qualification['passed']
    assert qualification['normalPassed'] is (normal_exit == 0)
    assert qualification['combinedPassed'] is (normal_exit == 0)
    units, negative = qualification['checks']
    assert [row['id'] for row in qualification['checks']] == ['units', 'reload-negative']
    assert [row['exitCode'] for row in qualification['checks']] == [0, 1]
    assert [row['verdict'] for row in qualification['checks']] == ['exact_units', 'expected_identity_failure']
    assert units['executionToken'] != negative['executionToken']
    assert all(row['executionToken'] != 'inherited-stale-token' for row in qualification['checks'])
    assert '# fixture-owner ' + units['executionToken'] in (folder / units['log']).read_text()
    assert json.loads((folder / 'negative/outcome.json').read_text())['qualificationToken'] == negative['executionToken']


def test_app004_initial_and_running_receipts_are_durable_before_each_launch(tmp_path, monkeypatch):
    mock_app004_commands(monkeypatch)
    original = subprocess.Popen
    snapshots = []
    def launch(*args, **kwargs):
        output = tmp_path / 'results'
        snapshots.append((
            json.loads((output / 'results.json').read_text()),
            json.loads((output / 'app004-qualification/results.json').read_text()),
            kwargs['env'][runner.OWNER_ENV],
        ))
        return original(*args, **kwargs)
    monkeypatch.setattr(runner.subprocess, 'Popen', launch)
    code, _, _ = execute(tmp_path, [scenario('normal', 'pass')], app004_qualification=True, budget=30)
    assert code == 0 and len(snapshots) == 3
    normal, initial, _ = snapshots[0]
    assert normal['scenarios'][0]['status'] == 'running' and not normal['complete']
    assert initial['source'] == normal['source']
    assert not initial['complete'] and not initial['passed']
    assert [(row['status'], row['reason']) for row in initial['checks']] == [('unrun', 'not_started')] * 2
    assert [row['id'] for row in initial['checks']] == ['units', 'reload-negative']
    for index, (_, qualification, token) in enumerate(snapshots[1:]):
        completed_normal = snapshots[index + 1][0]
        assert completed_normal['complete'] and completed_normal['passed']
        assert qualification['checks'][index]['status'] == 'running'
        assert qualification['checks'][index]['executionToken'] == token
    assert snapshots[1][1]['checks'][1]['status'] == 'unrun'
    assert snapshots[2][1]['checks'][0]['status'] == 'passed'


@pytest.mark.parametrize('enabled', [False, True])
def test_app004_cli_initialize_saves_unrun_without_launching_commands(tmp_path, monkeypatch, enabled):
    (tmp_path / 'scripts').mkdir()
    script = tmp_path / 'scripts/browser_check_runner.py'
    shutil.copy(ROOT / 'scripts/browser_check_runner.py', script)
    if enabled:
        owner = tmp_path / 'tests/web/chat_reference_qualification.py'
        owner.parent.mkdir(parents=True)
        shutil.copy(ROOT / 'tests/web/chat_reference_qualification.py', owner)
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'schemaVersion': 1, 'scenarios': [scenario('normal', 'pass')]}))
    monkeypatch.setattr(runner, '__file__', str(script))
    def source(command, **kwargs):
        assert kwargs['cwd'] == tmp_path
        return {('git', 'rev-parse', 'HEAD'): b'a' * 40,
                ('git', 'rev-parse', 'HEAD^{tree}'): b'b' * 40,
                ('git', 'diff', '--binary', 'HEAD'): b''}[tuple(command)]
    def forbidden(*args, **kwargs):
        pytest.fail('Initialization must not launch any normal or qualification command')
    monkeypatch.setattr(runner.subprocess, 'check_output', source)
    monkeypatch.setattr(runner.subprocess, 'Popen', forbidden)
    output = tmp_path / 'evidence'
    monkeypatch.setattr(sys, 'argv', [str(script), '--plan', str(plan), '--output', str(output),
                                    '--initialize', *(['--app004-qualification'] if enabled else [])])
    assert runner.main() == 0
    report = json.loads((output / 'results.json').read_text())
    assert not report['complete'] and not report['passed']
    assert report['scenarios'][0]['status'] == 'unrun'
    assert report['scenarios'][0]['reason'] == 'not_started'
    assert report['source']['planSha256'] == hashlib.sha256(plan.read_bytes()).hexdigest()
    qualification_path = output / 'app004-qualification/results.json'
    if enabled:
        assert json.loads(qualification_path.read_text()) == checks.initial_report(report['source'])
    else:
        assert not qualification_path.exists()


@pytest.mark.parametrize('exhaust_after', ['normal', 'units'])
def test_app004_extras_share_original_deadline_and_require_remaining_budget(tmp_path, monkeypatch, exhaust_after):
    mock_app004_commands(monkeypatch)
    original_clock, retire = runner.time.monotonic, runner.retire_group
    offset, retired = [0], []
    monkeypatch.setattr(runner.time, 'monotonic', lambda: original_clock() + offset[0])
    def cleanup(*args):
        result = retire(*args)
        retired.append(args[1])
        if len(retired) == (1 if exhaust_after == 'normal' else 2):
            offset[0] += 9  # Less than either extra's two-second budget remains.
        return result
    monkeypatch.setattr(runner, 'retire_group', cleanup)
    code, report, output = execute(tmp_path, [scenario('normal', 'pass')], app004_qualification=True, budget=10)
    assert code == 1 and report['complete'] and report['passed']
    qualification = json.loads((output / 'app004-qualification/results.json').read_text())
    assert not qualification['complete'] and not qualification['passed']
    assert [row['status'] for row in qualification['checks']] == (
        ['unrun', 'unrun'] if exhaust_after == 'normal' else ['passed', 'unrun'])
    for row in qualification['checks']:
        if row['status'] == 'unrun':
            assert row['reason'] == 'budget_exhausted'
            assert 'executionToken' not in row and 'exitCode' not in row
            assert not (output / 'app004-qualification' / (row['id'] + '.log')).exists()
    assert len(retired) == (1 if exhaust_after == 'normal' else 2)


@pytest.mark.parametrize('cancel_at', ['normal', 'units'])
def test_app004_cancellation_reaps_current_and_keeps_extras_unrun(tmp_path, monkeypatch, cancel_at):
    cancel = 'import os,signal,time;os.kill(os.getppid(),signal.SIGTERM);time.sleep(60)'
    mock_app004_commands(monkeypatch, units_source=cancel if cancel_at == 'units' else None)
    code, report, output = execute(
        tmp_path, [scenario('normal', cancel if cancel_at == 'normal' else 'pass'), scenario('later', 'pass')],
        app004_qualification=True, budget=30)
    assert code == 1
    qualification = json.loads((output / 'app004-qualification/results.json').read_text())
    active = report['scenarios'][0] if cancel_at == 'normal' else qualification['checks'][0]
    assert active['status'] == 'failed' and active['reason'] == 'cancelled'
    assert active['cleanup']['remaining'] == []
    assert not qualification['complete'] and not qualification['passed']
    assert [row['status'] for row in qualification['checks']] == (
        ['unrun', 'unrun'] if cancel_at == 'normal' else ['failed', 'unrun'])
    if cancel_at == 'normal':
        assert report['scenarios'][1]['status'] == 'unrun' and not report['complete']
    else:
        assert report['complete'] and report['passed']
    assert qualification['checks'][1]['reason'] == 'cancelled'
    assert not (output / 'app004-qualification/reload-negative.log').exists()


@pytest.mark.parametrize('stage,remaining,terminated,reason', [
    ('normal', [], 1, 'unsafe_normal_cleanup'),
    ('normal', ['unknown'], 0, 'unsafe_cleanup'),
    ('units', ['unknown'], 0, 'unsafe_cleanup'),
])
def test_app004_unsafe_cleanup_cannot_launch_remaining_extras(tmp_path, monkeypatch, stage, remaining, terminated, reason):
    mock_app004_commands(monkeypatch)
    retire, retired = runner.retire_group, []
    def cleanup(*args):
        actual = retire(*args)
        assert actual == {'remaining': [], 'terminated': 0}
        retired.append(args[1])
        if len(retired) == (1 if stage == 'normal' else 2):
            # Inject an uncertain cleanup receipt only after real child cleanup.
            return {'remaining': remaining, 'terminated': terminated}
        return actual
    monkeypatch.setattr(runner, 'retire_group', cleanup)
    code, report, output = execute(tmp_path, [scenario('normal', 'pass')], app004_qualification=True, budget=30)
    assert code == 1
    assert report['complete'] and report['passed'] is (stage == 'units')
    qualification = json.loads((output / 'app004-qualification/results.json').read_text())
    assert not qualification['complete'] and not qualification['passed']
    assert [row['status'] for row in qualification['checks']] == (
        ['unrun', 'unrun'] if stage == 'normal' else ['failed', 'unrun'])
    for row in qualification['checks']:
        if row['status'] == 'unrun':
            assert row['reason'] == reason
            assert not (output / 'app004-qualification' / (row['id'] + '.log')).exists()
    assert len(retired) == (1 if stage == 'normal' else 2)


@pytest.mark.parametrize('timeout_at', ['units', 'reload-negative'])
def test_app004_extra_timeout_is_failure_not_expected_negative(tmp_path, monkeypatch, timeout_at):
    slow = 'import time;time.sleep(60)'
    mock_app004_commands(monkeypatch, units_source=slow if timeout_at == 'units' else None,
                         negative_source=slow if timeout_at == 'reload-negative' else None)
    initial = checks.initial_report
    def short(provenance):
        report = initial(provenance)
        next(row for row in report['checks'] if row['id'] == timeout_at)['timeoutSeconds'] = .15
        return report
    monkeypatch.setattr(checks, 'initial_report', short)
    code, report, output = execute(tmp_path, [scenario('normal', 'pass')], app004_qualification=True, budget=30)
    assert code == 1 and report['complete'] and report['passed']
    qualification = json.loads((output / 'app004-qualification/results.json').read_text())
    assert qualification['complete'] and not qualification['passed']
    row = next(row for row in qualification['checks'] if row['id'] == timeout_at)
    assert row['status'] == 'failed' and row['reason'] == 'timeout'
    assert row['verdict'] == 'rejected' and row['exitCode'] is None
    assert row['cleanup']['remaining'] == []
    other = next(row for row in qualification['checks'] if row['id'] != timeout_at)
    assert other['status'] == 'passed'
