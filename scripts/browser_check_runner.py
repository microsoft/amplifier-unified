"""Serial background browser checks, with durable coverage and owned-process cleanup.

No shell, retry, parallel execution, or success-on-error. Each scenario owns a
Linux process group and inherited environment tag (Chromium can detach). Leaked
processes fail that scenario; uncertain cleanup leaves later scenarios unrun.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import uuid


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(output, report):
    temporary = output / 'results.tmp'
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(output / 'results.json')


OWNER_ENV = 'AMPLIFIER_BROWSER_CHECK_OWNER'


def process_identities():
    """Snapshot only scalar process identities before a scenario can inherit its tag."""
    result = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] != 'Z':
                result[int(entry.name)] = fields[19]
        except (FileNotFoundError, ProcessLookupError):
            pass
    return result


def owned_members(group, token, known, preexisting=None):
    """Return PID/start-time identities, including tagged detached descendants."""
    members = {}
    marker = (OWNER_ENV + '=' + token).encode()
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] == 'Z':
                continue
            in_group = int(fields[2]) == group
            if in_group or known.get(int(entry.name)) == fields[19]:
                members[int(entry.name)] = fields[19]
                continue
            # A process that predates this scenario cannot have inherited its
            # fresh token. Hosted runners have unrelated same-user processes
            # whose environ is deliberately unreadable. PID alone is unsafe:
            # a reused PID must still go through ownership discovery.
            if (preexisting or {}).get(int(entry.name)) == fields[19]:
                continue
            if not in_group and entry.stat().st_uid != os.geteuid():
                continue
            try:
                with (entry / 'environ').open('rb') as stream:
                    environment = stream.read(1024 * 1024 + 1)
            except PermissionError:
                # Linux may revoke environ access during exit before /proc is
                # reaped. Only a proven zombie/disappearance is safe to ignore.
                if (entry / 'stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                    continue
                raise
            if len(environment) > 1024 * 1024:
                raise RuntimeError('Process ownership environment exceeds inspection bound')
            if in_group or marker in environment.split(b'\0'):
                members[int(entry.name)] = fields[19]
        except (FileNotFoundError, ProcessLookupError):
            pass
    return members


def retire_group(group, token, preexisting=None):
    known = {}
    def inspect():
        current = owned_members(group, token, known, preexisting)
        # Once ownership is proven, retain PID/start-time identity while the
        # process exits; Linux can revoke its environ before it becomes a zombie.
        known.update(current)
        return current
    # Fixtures commonly send termination in finally before Node itself exits.
    # Give that normal shutdown time to finish before declaring an orphan.
    grace = time.monotonic() + .5
    while inspect() and time.monotonic() < grace:
        time.sleep(.05)
    initial = inspect()
    for sig in (signal.SIGTERM, signal.SIGKILL):
        current = inspect()
        if not current:
            return {'remaining': [], 'terminated': len(initial)}
        for pid, birth in current.items():
            try:
                handle = os.pidfd_open(pid)
                try:
                    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                    if fields[19] == birth:
                        signal.pidfd_send_signal(handle, sig)
                finally:
                    os.close(handle)
            except (FileNotFoundError, ProcessLookupError):
                pass
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and inspect():
            time.sleep(.05)
    return {'remaining': list(inspect()), 'terminated': len(initial)}


def validate_plan(plan):
    rows = plan['scenarios']
    if plan.get('schemaVersion') != 1 or not 1 <= len(rows) <= 200:
        raise ValueError('Invalid browser plan')
    seen = set()
    for row in rows:
        name, command = row['id'], row['command']
        if (not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name)
                or name in seen or not command or not all(isinstance(x, str) for x in command)):
            raise ValueError('Invalid or duplicate scenario')
        seen.add(name)
    return rows


def initial_report(plan, provenance):
    return {'schemaVersion': 1, 'source': provenance or {}, 'complete': False, 'passed': False,
            'scenarios': [{**row, 'status': 'unrun', 'reason': 'not_started'}
                          for row in validate_plan(plan)]}


APP004_UNIT_NAMES = (
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
APP004_BOUNDARY = 'app004-same-document'


def initial_app004(provenance):
    # Fixed commands, not a second user-programmable plan or scenario selector.
    return {'schemaVersion': 1, 'source': provenance or {}, 'complete': False, 'passed': False,
            'checks': [
                {'id': 'units', 'status': 'unrun', 'reason': 'not_started', 'timeoutSeconds': 60,
                 'command': ['node', '--test', '--test-reporter=tap',
                             'tests/chat-reference.test.mjs', 'tests/message-copy.test.mjs',
                             'tests/chat-reference-harness.test.mjs']},
                {'id': 'reload-negative', 'status': 'unrun', 'reason': 'not_started',
                 'timeoutSeconds': 300, 'command': ['node', 'tests/chat-reference-browser.mjs']},
            ]}


def save_app004(output, report):
    folder = output / 'app004-qualification'
    folder.mkdir(parents=True, exist_ok=True)
    save(folder, report)


def app004_units(log):
    """Require the exact 23 flat Node test identities, not just process exit zero."""
    text = log.read_text()
    names = re.findall(r'^ok \d+ - (.+)$', text, re.M)
    counts = {key: re.findall(rf'^# {key} (\d+)$', text, re.M)
              for key in ('tests', 'pass', 'fail', 'cancelled', 'skipped', 'todo')}
    expected = {key: [str(23 if key in ('tests', 'pass') else 0)] for key in counts}
    if (counts != expected or len(names) != 23 or set(names) != set(APP004_UNIT_NAMES)
            or re.search(r'^not ok\b|^Bail out!', text, re.M)):
        raise ValueError('unit_identity_or_count_mismatch')


def app004_negative(folder, token):
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
            or proof.get('id') != APP004_BOUNDARY
            or not all(proof.get(key) is True for key in
                       ('attachmentAccepted', 'cardVisible', 'draftHeld', 'clickCompleted', 'attempted'))
            or proof.get('verdict') != 'failed'
            or not isinstance(proof.get('targetSessionId'), str) or not proof['targetSessionId']
            or proof.get('selectedSessionId') != proof['targetSessionId']
            or primary.get('name') != 'AssertionError' or primary.get('code') != 'ERR_ASSERTION'
            or primary.get('boundary') != APP004_BOUNDARY
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


def app004_verdict(row, folder):
    """Preserve raw exits and failure diagnostics alongside the separate verdict."""
    try:
        if (row['reason'] or row['cleanup'] != {'remaining': [], 'terminated': 0}
                or type(row.get('exitCode')) is not int):
            raise ValueError('process_or_cleanup_failure')
        if row['id'] == 'units':
            if row.get('exitCode') != 0:
                raise ValueError('unit_process_failed')
            app004_units(folder / row['log'])
            files = [row['log']]
        else:
            if row.get('exitCode') != 1:
                raise ValueError('negative_exit_not_one')
            app004_negative(folder / 'negative', row['executionToken'])
            files = [row['log'], 'negative/outcome.json', 'negative/route-lifecycle.json']
        row['evidenceSha256'] = {name: digest(folder / name) for name in files}
        row['status'], row['verdict'] = 'passed', 'exact_units' if row['id'] == 'units' else 'expected_identity_failure'
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        row['status'], row['verdict'] = 'failed', 'rejected'
        row['verdictReason'] = str(exc)


def run_scenarios(root, plan, output, *, budget=1500, case_timeout=300, provenance=None,
                  app004_qualification=False):
    output.mkdir(parents=True, exist_ok=True)
    report = initial_report(plan, provenance)
    save(output, report)  # Even a killed job leaves its complete intended coverage.
    qualification = initial_app004(provenance) if app004_qualification else None
    if qualification:
        save_app004(output, qualification)
    deadline = time.monotonic() + budget
    stopped = None
    old_handlers = {}

    def interrupted(signum, _frame):
        nonlocal stopped
        stopped = 'cancelled'

    for sig in (signal.SIGTERM, signal.SIGINT):
        old_handlers[sig] = signal.signal(sig, interrupted)
    try:
        entries = [(row, False) for row in report['scenarios']]
        if qualification:
            entries += [(row, True) for row in qualification['checks']]
        for row, extra in entries:
            folder = output / 'app004-qualification' if extra else output
            if extra:
                # Finish the normal receipt BEFORE extras, even if an extra is killed.
                report['complete'] = all(r['status'] in ('passed', 'failed') for r in report['scenarios'])
                report['passed'] = all(r['status'] == 'passed' for r in report['scenarios'])
                save(output, report)
                if any(r.get('cleanup') != {'remaining': [], 'terminated': 0} for r in report['scenarios']):
                    stopped = stopped or 'unsafe_normal_cleanup'
                if deadline - time.monotonic() < row['timeoutSeconds']:
                    stopped = stopped or 'budget_exhausted'
            if stopped or time.monotonic() >= deadline:
                stopped = stopped or 'budget_exhausted'
                break
            row.update(status='running', reason=None, log=row['id'] + '.log')
            (save_app004(output, qualification) if extra else save(output, report))
            print('START ' + row['id'], flush=True)
            started, process, token = time.monotonic(), None, uuid.uuid4().hex
            if extra:
                row['executionToken'] = token
                save_app004(output, qualification)
            preexisting = None
            try:
                env = {**os.environ, **{k: v.replace('{root}', str(root))
                                       for k, v in row.get('env', {}).items()}}
                env[OWNER_ENV] = token
                if extra and row['id'] == 'reload-negative':
                    env.update(CHAT_REFERENCE_NEGATIVE_RELOAD='1', APP004_QUALIFICATION_TOKEN=token,
                               CHAT_REFERENCE_EVIDENCE_DIR=str(folder / 'negative'))
                preexisting = process_identities()
                with (folder / row['log']).open('wb') as log:
                    process = subprocess.Popen(row['command'], cwd=root / 'frontend', env=env,
                                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    while process.poll() is None and not stopped:
                        limit = row['timeoutSeconds'] if extra else case_timeout
                        if time.monotonic() >= min(deadline, started + limit):
                            row['reason'] = 'timeout'
                            break
                        time.sleep(.05)
                    row['exitCode'] = process.poll()
            except OSError as exc:
                row['reason'] = type(exc).__name__
            finally:
                try:
                    cleanup = retire_group(process.pid, token, preexisting) if process else {'remaining': [], 'terminated': 0}
                except (OSError, RuntimeError) as exc:
                    cleanup = {'remaining': ['unknown'], 'terminated': 0, 'error': type(exc).__name__}
                row['cleanup'] = cleanup
                if process and not cleanup['remaining']:
                    process.wait(timeout=2)
                row['durationSeconds'] = round(time.monotonic() - started, 3)
                row['reason'] = row['reason'] or stopped
                row['status'] = ('passed' if row.get('exitCode') == 0 and not row['reason']
                                 and cleanup['terminated'] == 0 and not cleanup['remaining'] else 'failed')
                if extra:
                    app004_verdict(row, folder)
                if cleanup['remaining']:
                    stopped = 'unsafe_cleanup'
                (save_app004(output, qualification) if extra else save(output, report))
            print(row['status'].upper() + ' ' + row['id'], flush=True)
        for row, extra in entries:
            if row['status'] == 'unrun':
                row['reason'] = stopped or 'interrupted'
        report['complete'] = all(row['status'] in ('passed', 'failed') for row in report['scenarios'])
        report['passed'] = all(row['status'] == 'passed' for row in report['scenarios'])
        save(output, report)
        if qualification:
            qualification['complete'] = all(r['status'] in ('passed', 'failed') for r in qualification['checks'])
            qualification['passed'] = all(r['status'] == 'passed' for r in qualification['checks'])
            qualification['normalPassed'] = report['passed']
            qualification['combinedPassed'] = report['passed'] and qualification['passed']
            save_app004(output, qualification)
        return 0 if report['passed'] and (not qualification or qualification['passed']) else 1
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def attachment_consumption_plan(plan):
    """One fixed diagnostic; retain the ordinary plan and process owner."""
    expected = {'id': 'attachment-consumption',
                'command': ['node', 'tests/attachment-consumption-browser.mjs'],
                'env': {'AMPLIFIER_TEST_ARTIFACTS': '{root}/output/browser-checks/attachment-consumption'}}
    rows = [row for row in validate_plan(plan) if row['id'] == expected['id']]
    if rows != [expected]:
        raise ValueError('Attachment consumption requires its exact registered owner')
    return {'schemaVersion': 1, 'scenarios': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--budget-seconds', type=int, default=1500)
    parser.add_argument('--initialize', action='store_true', help='Record unrun coverage before dependency setup')
    parser.add_argument('--app004-qualification', action='store_true',
                        help='Append fixed APP004 units and reload control, without reducing normal coverage')
    parser.add_argument('--attachment-consumption', action='store_true',
                        help='Run only the fixed registered attachment consumption diagnostic')
    args = parser.parse_args()
    if args.attachment_consumption and args.app004_qualification:
        parser.error('Attachment consumption and APP004 are separate selections')
    root = Path(__file__).resolve().parents[1]
    git = lambda *parts: subprocess.check_output(['git', *parts], cwd=root).strip()
    provenance = {'revision': git('rev-parse', 'HEAD').decode(),
                  'tree': git('rev-parse', 'HEAD^{tree}').decode(),
                  'trackedDiffSha256': hashlib.sha256(git('diff', '--binary', 'HEAD')).hexdigest(),
                  'planSha256': digest(args.plan), 'runnerSha256': digest(Path(__file__)),
                  'runId': os.environ.get('GITHUB_RUN_ID'), 'runAttempt': os.environ.get('GITHUB_RUN_ATTEMPT')}
    build = root / 'amplifier_web/static/build.json'
    provenance['buildMetadataSha256'] = digest(build) if build.is_file() else None
    plan = json.loads(args.plan.read_text())
    if args.attachment_consumption:
        plan = attachment_consumption_plan(plan)
        provenance['selection'] = 'attachment-consumption-only'
    if args.initialize:
        args.output.mkdir(parents=True, exist_ok=True)
        save(args.output, initial_report(plan, provenance))
        if args.app004_qualification:
            save_app004(args.output, initial_app004(provenance))
        return 0
    return run_scenarios(root, plan, args.output.resolve(),
                         budget=args.budget_seconds, provenance=provenance,
                         app004_qualification=args.app004_qualification)


if __name__ == '__main__':
    raise SystemExit(main())
