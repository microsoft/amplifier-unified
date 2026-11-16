"""Nonproduction candidate-test wire checks. Not an official-delivery authority.

Only the separately nominated accepted H controller may call select with fresh
independent API facts. Python objects/JSON are not unforgeable against reviewed
candidate code in its Docker job. External verification authenticates origin,
not the truth of statements by a compromised runner.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

REPOSITORY = 'microsoft/amplifier-unified'
WORKFLOW = '.github/workflows/prepared-runtime-native.yml'
KIND = 'nonproduction-candidate-qualification'
PROFILES = ['python-project', 'browser-test']
ACTIONS = ['start', 'execute', 'stop', 'remove', 'reconcile']
PLAN_FIELDS = {'kind', 'selectionDigest', 'run', 'attempt', 'profile', 'owner', 'root', 'source',
               'backend', 'resource', 'launch', 'universe', 'sourceDigest', 'action', 'nonce', 'expires'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(value, length=64):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{' + str(length) + '}', value) is not None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_request(nominated, requested):
    require(isinstance(nominated, dict) and set(nominated) == {'CANDIDATE', 'CANDIDATE_TREE', 'CANDIDATE_PR'}
            and isinstance(requested, dict) and set(requested) == {'REQUESTED_C', 'REQUESTED_C_TREE', 'REQUESTED_PR'},
            'Missing maintainer candidate nomination')
    require(sha(nominated['CANDIDATE'], 40) and sha(nominated['CANDIDATE_TREE'], 40)
            and isinstance(nominated['CANDIDATE_PR'], str)
            and re.fullmatch('[1-9][0-9]*', nominated['CANDIDATE_PR']) is not None
            and [nominated[k] for k in ('CANDIDATE', 'CANDIDATE_TREE', 'CANDIDATE_PR')] ==
                [requested['REQUESTED_C'], requested['REQUESTED_C_TREE'], requested['REQUESTED_PR']],
            'Dispatch request is not the separately nominated candidate')


def save(path, value):
    """Run-local journal, not tamper-proof custody against candidate Docker."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def select(nomination, facts, *, now=None):
    """Nomination comes from maintainer control, never candidate/job assertions."""
    n, f = nomination, facts
    require(isinstance(n, dict) and set(n) == {'repository', 'harness', 'harnessTree', 'candidate', 'candidateTree', 'pr'}, 'Missing explicit harness nomination')
    require(n['repository'] == REPOSITORY and all(sha(n[k], 40) for k in ('harness', 'harnessTree', 'candidate', 'candidateTree'))
            and type(n['pr']) is int and n['pr'] > 0, 'Invalid immutable nomination')
    try:
        run, pr = f['run'], f['pr']
        require(f['repository']['full_name'] == REPOSITORY and f['repository']['default_branch'] == 'main', 'Wrong repository')
        require(run['event'] == 'workflow_dispatch' and run['path'] == WORKFLOW
                and run['head_repository']['full_name'] == REPOSITORY and run['head_sha'] == n['harness']
                and run['status'] == 'in_progress' and run['conclusion'] is None
                and type(run['id']) is int and run['id'] > 0
                and type(run['run_attempt']) is int and run['run_attempt'] > 0, 'Wrong or cancelled harness run')
        require(f['harness']['sha'] == n['harness'] and f['harness']['tree']['sha'] == n['harnessTree']
                and f['ancestry']['status'] in ('behind', 'identical'), 'Harness is not nominated accepted main source')
        rules = f['rules']
        require(any(x['type'] == 'pull_request' and x['parameters']['required_approving_review_count'] >= 1 for x in rules)
                and any(x['type'] == 'required_status_checks' and any(c['context'] == 'Fast release checks'
                    for c in x['parameters']['required_status_checks']) for x in rules), 'Main review/check protection missing')
        require(pr['number'] == n['pr'] and pr['state'] == 'open' and pr['head']['sha'] == n['candidate']
                and pr['head']['repo']['full_name'] == pr['base']['repo']['full_name'] == REPOSITORY
                and pr['base']['ref'] == 'main' and f['candidate']['sha'] == n['candidate']
                and f['candidate']['tree']['sha'] == n['candidateTree'], 'Moved candidate, wrong repository or tree')
    except (KeyError, TypeError) as exc:
        raise ValueError('Incomplete independent API facts') from exc
    now = int(time.time() if now is None else now)
    return {'kind': KIND, **n, 'run': run['id'], 'attempt': run['run_attempt'],
            'profiles': list(PROFILES), 'actions': list(ACTIONS), 'issued': now, 'expires': now + 3600}


def validate_selection(value, *, now=None):
    require(isinstance(value, dict) and set(value) == {'kind', 'repository', 'harness', 'harnessTree', 'candidate', 'candidateTree',
            'pr', 'run', 'attempt', 'profiles', 'actions', 'issued', 'expires'}, 'Invalid TEST selection schema')
    require(value['kind'] == KIND and value['repository'] == REPOSITORY
            and all(sha(value[k], 40) for k in ('harness', 'harnessTree', 'candidate', 'candidateTree'))
            and value['profiles'] == PROFILES and value['actions'] == ACTIONS
            and all(type(value[k]) is int and value[k] > 0 for k in ('pr', 'run', 'attempt', 'issued', 'expires'))
            and value['expires'] - value['issued'] == 3600, 'Invalid TEST selection binding')
    require(value['issued'] <= (time.time() if now is None else now) <= value['expires'], 'Expired TEST selection')


def registration_plan(selection, *, profile, owner, root, source, backend, resource, launch, universe, source_digest, nonce):
    return {'kind': KIND, 'selectionDigest': digest(selection), 'run': selection['run'], 'attempt': selection['attempt'],
            'profile': profile, 'owner': owner, 'root': str(root), 'source': str(source), 'backend': backend,
            'resource': resource, 'launch': launch, 'universe': universe, 'sourceDigest': source_digest,
            'action': 'start', 'nonce': nonce, 'expires': selection['expires']}


class Registry:
    """One live sequential resource. Consumed/unknown plans never replay."""
    def __init__(self, selection, path, *, clock=time.time, current=lambda: None):
        self.selection, self.path, self.clock, self.current = selection, Path(path), clock, current
        self.value = json.loads(self.path.read_text()) if self.path.exists() else {
            'kind': KIND, 'selectionDigest': digest(selection), 'cancelled': False, 'entries': []}
        require(self.value['selectionDigest'] == digest(selection), 'Registry belongs to another selection')

    def check(self):
        validate_selection(self.selection, now=self.clock())
        self.current()  # Fresh independent run/attempt/cancel observation before each effect.
        validate_selection(self.selection, now=self.clock())  # Observation can block past expiry.
        require(not self.value['cancelled'], 'Cancelled TEST authorization')

    def validate_plan(self, plan):
        self.check()
        require(isinstance(plan, dict) and set(plan) == PLAN_FIELDS, 'Invalid registration plan schema')
        require(plan['kind'] == KIND and plan['selectionDigest'] == digest(self.selection)
                and (plan['run'], plan['attempt'], plan['expires']) ==
                    (self.selection['run'], self.selection['attempt'], self.selection['expires'])
                and plan['action'] == 'start' and plan['profile'] in PROFILES
                and sha(plan['sourceDigest']) and sha(plan['nonce'])
                and all(isinstance(plan[k], str) and 0 < len(plan[k]) <= 4096 for k in ('owner', 'resource', 'launch', 'universe')),
                'Registration scope changed')
        for key in ('root', 'source'):
            require(isinstance(plan[key], str) and Path(plan[key]).is_absolute() and '..' not in Path(plan[key]).parts,
                    'Invalid registered path')
        require(isinstance(plan['backend'], dict) and set(plan['backend']) == {'backend', 'daemon'}
                and plan['backend']['backend'] == 'docker' and isinstance(plan['backend']['daemon'], str)
                and bool(plan['backend']['daemon']), 'Invalid registered backend')

    def register(self, plan):
        self.validate_plan(plan)
        entries = self.value['entries']
        require(all(e['state'] == 'absent' for e in entries), 'Prior resource is live, unknown or registration unconsumed')
        require(len(entries) < 2 and plan['profile'] == PROFILES[len(entries)], 'Wrong sequential profile')
        require(all(e['plan']['nonce'] != plan['nonce'] and e['plan']['resource'] != plan['resource'] for e in entries), 'Replayed registration')
        ack = {'kind': KIND, 'planDigest': digest(plan), 'selectionDigest': digest(self.selection),
               'run': plan['run'], 'attempt': plan['attempt'], 'nonce': plan['nonce']}
        entries.append({'plan': plan, 'ack': ack, 'state': 'registered'})
        save(self.path, self.value)  # fsync registration precedes acknowledgement.
        return dict(ack)

    def consume(self, plan, ack, *, action):
        self.validate_plan(plan)
        require(action == 'start' and bool(self.value['entries']), 'No exact registered action')
        entry = self.value['entries'][-1]
        require(entry['state'] == 'registered', 'Registration already consumed or uncertain')
        require(entry['plan'] == plan and entry['ack'] == ack, 'Registration ACK changed')
        entry['state'] = 'consumed'
        save(self.path, self.value)  # Save before prepare/launch; lost result remains held.

    def recheck(self, resource, *, action):
        self.check()
        require(action in ACTIONS and self.value['entries'] and self.value['entries'][-1]['state'] == 'consumed'
                and self.value['entries'][-1]['plan']['resource'] == resource, 'Wrong active resource/action')

    def bind_snapshot(self, resource, snapshot):
        self.recheck(resource, action='execute')
        require(isinstance(snapshot, dict) and sha(snapshot.get('archive_sha256')), 'Invalid admitted snapshot')
        entry = self.value['entries'][-1]
        require('snapshot' not in entry, 'Snapshot already bound')
        entry['snapshot'] = snapshot
        save(self.path, self.value)

    def settle(self, resource, *, cleanup, absent):
        entry = self.value['entries'][-1]
        require(entry['plan']['resource'] == resource and entry['state'] == 'consumed', 'Wrong settlement owner')
        entry['state'] = 'absent' if cleanup == 'completed' and absent is True else 'unknown'
        save(self.path, self.value)

    def cancel(self):
        self.value['cancelled'] = True
        save(self.path, self.value)


def empty_report(selection, *, wheel):
    return {'kind': KIND, 'selection': selection, 'wheelSha256': wheel, 'cases': []}


def dependencies_absent(inspection):
    require(isinstance(inspection, dict) and inspection.get('complete') is True
            and inspection.get('candidates') == [] and inspection.get('protectedIds') == [],
            'Dependency custody is unresolved or inventory is incomplete')


def production_preserved(root, base):
    """Source-only PR check; caller supplies fetched actual PR base, not moving main."""
    require(sha(base, 40), 'Invalid reviewed PR base')
    result = subprocess.run(['git', 'cat-file', '-e', base + '^{commit}'], cwd=root, capture_output=True)
    require(result.returncode == 0, 'Reviewed PR base was not fetched')
    result = subprocess.run(['git', 'diff', '--name-only', base, '--', 'amplifier_web', 'pyproject.toml', 'uv.lock', 'components'],
                            cwd=root, capture_output=True, check=True)
    require(result.stdout == b'', 'CI/test-only PR changed production inputs')


def verify_cases(report, xml, selection):
    require(isinstance(report, dict) and report.get('kind') == KIND and report.get('selection') == selection
            and sha(report.get('wheelSha256')), 'Wrong TEST report identity/wheel')
    rows = report.get('cases')
    require(isinstance(rows, list) and len(rows) == 2 and [r.get('profile') for r in rows] == PROFILES, 'Wrong case selection')
    for row in rows:
        require(all(row.get(k) is True for k in ('passed', 'nativeMap', 'tmpNoexec', 'absent', 'sourceUnchanged'))
                and row.get('cleanup') == 'completed' and sha(row.get('artifactSha256')), 'Incomplete native/cleanup evidence')
    require(len(xml) <= 4 * 1024 ** 2 and b'<!' not in xml, 'Invalid/oversized JUnit')
    root = ET.fromstring(xml)
    require(root.tag == 'testsuites' and len(root) == 1 and root[0].tag == 'testsuite'
            and {k: root[0].get(k) for k in ('tests', 'failures', 'errors', 'skipped')} ==
                {'tests': '2', 'failures': '0', 'errors': '0', 'skipped': '0'}, 'Wrong JUnit suite counts')
    cases = root.findall('.//testcase')
    expected = [('test_candidate_native_runtime', 'test_prepared_runtime[' + profile + ']') for profile in PROFILES]
    require([(c.get('classname'), c.get('name')) for c in cases] == expected
            and all(not any(c.find(tag) is not None for tag in ('failure', 'error', 'skipped')) for c in cases),
            'Missing, duplicate, failed or skipped native cases')
    require(all(s.get('failures', '0') == s.get('errors', '0') == s.get('skipped', '0') == '0'
                for s in root.iter('testsuite')), 'Failed JUnit suite')
