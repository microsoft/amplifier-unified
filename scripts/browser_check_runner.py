"""Serial background browser checks, with durable coverage and owned-process cleanup.

No shell, retry, parallel execution, or success-on-error. Each scenario owns a
Linux process group and inherited environment tag (Chromium can detach). Leaked
processes fail that scenario; uncertain cleanup leaves later scenarios unrun.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
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


def owned_members(group, token, known):
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


def retire_group(group, token):
    known = {}
    def inspect():
        current = owned_members(group, token, known)
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


def run_scenarios(root, plan, output, *, budget=1500, case_timeout=300, provenance=None):
    output.mkdir(parents=True, exist_ok=True)
    report = initial_report(plan, provenance)
    save(output, report)  # Even a killed job leaves its complete intended coverage.
    deadline = time.monotonic() + budget
    stopped = None
    old_handlers = {}

    def interrupted(signum, _frame):
        nonlocal stopped
        stopped = 'cancelled'

    for sig in (signal.SIGTERM, signal.SIGINT):
        old_handlers[sig] = signal.signal(sig, interrupted)
    try:
        for row in report['scenarios']:
            if stopped or time.monotonic() >= deadline:
                stopped = stopped or 'budget_exhausted'
                break
            row.update(status='running', reason=None, log=row['id'] + '.log')
            save(output, report)
            print('START ' + row['id'], flush=True)
            started, process, token = time.monotonic(), None, uuid.uuid4().hex
            try:
                env = {**os.environ, **{k: v.replace('{root}', str(root))
                                       for k, v in row.get('env', {}).items()}}
                env[OWNER_ENV] = token
                with (output / row['log']).open('wb') as log:
                    process = subprocess.Popen(row['command'], cwd=root / 'frontend', env=env,
                                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    while process.poll() is None and not stopped:
                        if time.monotonic() >= min(deadline, started + case_timeout):
                            row['reason'] = 'timeout'
                            break
                        time.sleep(.05)
                    row['exitCode'] = process.poll()
            except OSError as exc:
                row['reason'] = type(exc).__name__
            finally:
                try:
                    cleanup = retire_group(process.pid, token) if process else {'remaining': [], 'terminated': 0}
                except (OSError, RuntimeError) as exc:
                    cleanup = {'remaining': ['unknown'], 'terminated': 0, 'error': type(exc).__name__}
                row['cleanup'] = cleanup
                if process and not cleanup['remaining']:
                    process.wait(timeout=2)
                row['durationSeconds'] = round(time.monotonic() - started, 3)
                row['reason'] = row['reason'] or stopped
                row['status'] = ('passed' if row.get('exitCode') == 0 and not row['reason']
                                 and cleanup['terminated'] == 0 and not cleanup['remaining'] else 'failed')
                if cleanup['remaining']:
                    stopped = 'unsafe_cleanup'
                save(output, report)
            print(row['status'].upper() + ' ' + row['id'], flush=True)
        for row in report['scenarios']:
            if row['status'] == 'unrun':
                row['reason'] = stopped or 'interrupted'
        report['complete'] = all(row['status'] in ('passed', 'failed') for row in report['scenarios'])
        report['passed'] = all(row['status'] == 'passed' for row in report['scenarios'])
        save(output, report)
        return 0 if report['passed'] else 1
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--budget-seconds', type=int, default=1500)
    parser.add_argument('--initialize', action='store_true', help='Record unrun coverage before dependency setup')
    args = parser.parse_args()
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
    if args.initialize:
        args.output.mkdir(parents=True, exist_ok=True)
        save(args.output, initial_report(plan, provenance))
        return 0
    return run_scenarios(root, plan, args.output.resolve(),
                         budget=args.budget_seconds, provenance=provenance)


if __name__ == '__main__':
    raise SystemExit(main())
