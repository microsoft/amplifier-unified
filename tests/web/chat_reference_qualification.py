"""Fixed chat-reference qualification commands, expected cases and evidence checks."""
import hashlib
import json
import math
import re


UNIT_NAMES = (
    'chat reference metadata is a fixed bounded data shape',
    *(f'chat reference rejects {name}' for name in (
        'action', 'URL', 'foreign source', 'foreign message', 'missing target',
        'wrong version', 'invalid revision', 'unbounded label', 'control character',
        'missing availability')),
    'chat reference rejects duplicate, oversized and expanded envelopes',
    'copy uses an available message without another request',
    'copy reads an explicitly addressed off-page chat without changing selection',
    'missing scoped message reports failure instead of copying unrelated content',
    'copy retains prose and appends only the host-supplied inert reference fallback',
    'held route stays blocked through exercise and drains once before unregistration',
    'drain includes concurrent callbacks and waiting unroute joins a final protocol arrival',
    'callback errors become an awaited failure, not ignored or retried',
    'primary assertion survives successful release and cleanup',
    'primary error is retained with callback and teardown errors as secondary',
    'early-started action rejection stays observable until it is awaited',
    'cleanup failure alone fails and cannot replace a prior primary failure',
)
BOUNDARY = 'app004-same-document'


def initial_report(provenance):
    # Fixed commands, not a second user-programmable plan or scenario selector.
    report = {'schemaVersion': 1, 'source': provenance or {}, 'complete': False, 'passed': False,
            'checks': [
                {'id': 'units', 'status': 'unrun', 'reason': 'not_started', 'timeoutSeconds': 60,
                 'command': ['node', '--test', '--test-reporter=tap',
                             'tests/chat-reference.test.mjs', 'tests/message-copy.test.mjs',
                             '../../tests/web/chat-reference-harness.test.mjs'], 'cwd': '.build/web-client'},
                {'id': 'reload-negative', 'status': 'unrun', 'reason': 'not_started',
                 'timeoutSeconds': 300, 'command': ['node', 'tests/web/chat-reference-browser.mjs']},
            ]}
    if (provenance or {}).get('browserLayout') == 'legacy-frontend':
        report['checks'][0].update(cwd='frontend', command=[
            'node', '--test', '--test-reporter=tap', 'tests/chat-reference.test.mjs',
            'tests/message-copy.test.mjs', 'tests/chat-reference-harness.test.mjs'])
        report['checks'][1].update(cwd='frontend', command=['node', 'tests/chat-reference-browser.mjs'])
    return report


def environment(row, folder, token):
    if row['id'] == 'reload-negative':
        return {'CHAT_REFERENCE_NEGATIVE_RELOAD': '1', 'APP004_QUALIFICATION_TOKEN': token,
                'CHAT_REFERENCE_EVIDENCE_DIR': str(folder / 'negative')}
    return {}


def check_units(log):
    """Require the exact 23 flat Node test identities, not just process exit zero."""
    text = log.read_text()
    names = re.findall(r'^ok \d+ - (.+)$', text, re.M)
    counts = {key: re.findall(rf'^# {key} (\d+)$', text, re.M)
              for key in ('tests', 'pass', 'fail', 'cancelled', 'skipped', 'todo')}
    expected = {key: [str(23 if key in ('tests', 'pass') else 0)] for key in counts}
    if (counts != expected or len(names) != 23 or set(names) != set(UNIT_NAMES)
            or re.search(r'^not ok\b|^Bail out!', text, re.M)):
        raise ValueError('unit_identity_or_count_mismatch')


def check_reload(folder, token):
    """Only the deliberately broken genuine click at the exact assertion qualifies."""
    def read(name):
        path = folder / name
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('oversized_negative_evidence')
        return json.loads(path.read_text())
    outcome, routes = read('outcome.json'), read('route-lifecycle.json')
    proof, primary = outcome.get('identityBoundary', {}), outcome.get('primary', {})
    exit_state = outcome.get('fixtureExit', {})
    if (not token or outcome.get('qualificationToken') != token
            or outcome.get('negativeReload') is not True
            or proof.get('id') != BOUNDARY
            or not all(proof.get(key) is True for key in
                       ('attachmentAccepted', 'cardVisible', 'draftHeld', 'clickCompleted', 'attempted'))
            or proof.get('verdict') != 'failed'
            or not isinstance(proof.get('targetSessionId'), str) or not proof['targetSessionId']
            or proof.get('selectedSessionId') != proof['targetSessionId']
            or primary.get('name') != 'AssertionError' or primary.get('code') != 'ERR_ASSERTION'
            or primary.get('boundary') != BOUNDARY
            or outcome.get('browserErrors') != []
            or outcome.get('secondary') != [] or exit_state != {'code': 0, 'signal': None}
            or type(exit_state.get('code')) is not int):
        raise ValueError('wrong_negative_boundary_or_cleanup')
    before, after = proof.get('before', {}), proof.get('after', {})
    for marker in (before, after):
        if (set(marker) != {'document', 'timeOrigin', 'client'}
                or any(not isinstance(marker.get(key), str) or not marker[key]
                       for key in ('document', 'client'))
                or type(marker.get('timeOrigin')) not in (int, float)
                or not math.isfinite(marker['timeOrigin']) or marker['timeOrigin'] <= 0):
            raise ValueError('missing_identity_markers')
    if before['document'] == after['document'] or before['timeOrigin'] == after['timeOrigin']:
        raise ValueError('reload_not_observed')
    # The negative stops at the FIRST boundary, so only held-draft routing exists.
    if not isinstance(routes, list) or not routes or len(routes) > 10000:
        raise ValueError('missing_route_lifecycle')
    phases, started, completed = [], [], []
    for row in routes:
        if (not isinstance(row, dict) or row.get('route') != 'held-draft' or 'error' in row
                or row.get('phase') not in
                ('registered', 'started', 'completed', 'gate-released', 'drained', 'unregistered')):
            raise ValueError('failed_or_unexpected_route')
        phase = row['phase']
        if phase in ('started', 'completed'):
            if type(row.get('id')) is not int or row['id'] < 1:
                raise ValueError('invalid_route_identity')
            if phase == 'completed' and row['id'] not in started:
                raise ValueError('route_completed_without_start')
            (started if phase == 'started' else completed).append(row['id'])
        else:
            if row.get('id') is not None:
                raise ValueError('invalid_route_phase')
            phases.append(phase)
    if (phases != ['registered', 'gate-released', 'drained', 'unregistered']
            or routes[0]['phase'] != 'registered' or routes[-1]['phase'] != 'unregistered'
            or not started or len(set(started)) != len(started)
            or sorted(started) != sorted(completed)):
        raise ValueError('unfinished_route_lifecycle')


def apply_verdict(row, folder):
    """Preserve raw exits and failure diagnostics alongside the separate verdict."""
    try:
        if (row['reason'] or row['cleanup'] != {'remaining': [], 'terminated': 0}
                or type(row.get('exitCode')) is not int):
            raise ValueError('process_or_cleanup_failure')
        if row['id'] == 'units':
            if row.get('exitCode') != 0:
                raise ValueError('unit_process_failed')
            check_units(folder / row['log'])
            files = [row['log']]
        else:
            if row.get('exitCode') != 1:
                raise ValueError('negative_exit_not_one')
            check_reload(folder / 'negative', row['executionToken'])
            files = [row['log'], 'negative/outcome.json', 'negative/route-lifecycle.json']
        row['evidenceSha256'] = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in files}
        row['status'], row['verdict'] = 'passed', 'exact_units' if row['id'] == 'units' else 'expected_identity_failure'
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        row['status'], row['verdict'] = 'failed', 'rejected'
        row['verdictReason'] = str(exc)
