"""Trusted H controller/external verifier; stdlib/API only, never imports C.

H must first be reviewed, merged and explicitly nominated by a maintainer. CLI
arguments are that operator's selection, not authority supplied by an artifact.
No phase-one PR check calls issue/verify or starts native work. Three FUTURE jobs:
credential-bearing control, credential-free candidate Docker execution, external
verification. Only the middle job owns one daemon/two sequential resources.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

from candidate_native_protocol import (REPOSITORY, WORKFLOW, KIND, require, sha, select, save,
                                       verify_cases, digest, PLAN_FIELDS, PROFILES, dependencies_absent,
                                       validate_request)

QUALIFIERS = (WORKFLOW, 'scripts/candidate_native_protocol.py', 'scripts/candidate_native_control.py',
              'scripts/candidate_native_execution.py', 'tests/fixtures/candidate_native_adapter.py',
              'tests/test_candidate_native_runtime.py')
SOURCES = tuple('amplifier_web/' + n for n in (
    'environment_cleanup_launcher.py', 'test_environment_worker.py', 'environment_retention.py',
    'environment_cleanup_execution.py', 'task_environments.py', 'test_environment_profiles.py',
    'test_environment_preparation.py', 'test_environment_initialize_guest.py', 'test_environment_prepare_guest.py',
    'test_job_guest.py', 'test_job_transport.py', 'test_source_transfer.py'))
STEPS = ('Validate fixed selection before candidate code', 'Build and install exact candidate',
         'Run two sequential prepared profiles', 'Upload native evidence')


class APIReadFailure(ValueError):
    """Finite diagnostics only; never expose gh argv, stderr, body or credentials."""
    def __init__(self, category, endpoint, *, returncode=None):
        self.diagnostic = {'category': category, 'endpoint': endpoint}
        if type(returncode) is int:
            self.diagnostic['returncode'] = returncode
        super().__init__('Candidate controller API read failed: ' + category)


class GitHub:
    def __init__(self):
        self.deadline = time.monotonic() + 120

    def get(self, path, *, binary=False, limit=8 * 1024 ** 2):
        # All paths constructed here from fixed literals or validated integers/SHA.
        endpoint = {'': 'repository', 'actions': 'actions', 'git': 'git',
                    'compare': 'ancestry', 'rules': 'rules', 'pulls': 'pull_request',
                    'contents': 'qualifier'}.get(path.split('/', 1)[0], 'other')
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise APIReadFailure('api_deadline', endpoint)
        target = 'repos/' + REPOSITORY + ('/' + path if path else '')
        try:
            process = subprocess.run(['gh', 'api', target], capture_output=True, timeout=left, check=True)
        except subprocess.CalledProcessError as exc:
            raise APIReadFailure('api_nonzero', endpoint, returncode=exc.returncode) from None
        except subprocess.TimeoutExpired:
            raise APIReadFailure('api_timeout', endpoint) from None
        if len(process.stdout) > limit:
            raise APIReadFailure('api_oversize', endpoint)
        if binary:
            return process.stdout
        try:
            return json.loads(process.stdout)
        except (ValueError, UnicodeError):
            raise APIReadFailure('api_json', endpoint) from None


def facts(api, nomination, run_id, attempt):
    require(type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0, 'Invalid run/attempt')
    n = nomination
    require(all(sha(n[k], 40) for k in ('harness', 'harnessTree', 'candidate', 'candidateTree'))
            and type(n['pr']) is int and n['pr'] > 0, 'Invalid immutable selection')
    f = {'repository': api.get(''), 'run': api.get(f'actions/runs/{run_id}/attempts/{attempt}'),
         'harness': api.get('git/commits/' + n['harness']), 'candidate': api.get('git/commits/' + n['candidate']),
         'ancestry': api.get('compare/main...' + n['harness']), 'rules': api.get('rules/branches/main'),
         'pr': api.get('pulls/' + str(n['pr']))}
    # Also compare latest attempt. Re-running cannot validate a stale grant.
    latest = api.get(f'actions/runs/{run_id}')
    require(latest['run_attempt'] == attempt and latest['head_sha'] == n['harness'], 'Run attempt changed')
    return f


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True, timeout=30).strip()


def issue(api, nomination, run_id, attempt, root, output):
    root = Path(root).resolve()
    f = facts(api, nomination, run_id, attempt)
    selection = select(nomination, f)
    require(git(root, 'rev-parse', 'HEAD') == nomination['harness']
            and git(root, 'rev-parse', 'HEAD^{tree}') == nomination['harnessTree']
            and not git(root, 'status', '--porcelain', '--untracked-files=no'), 'Controller checkout differs from nominated H')
    # Check trusted qualifier blobs without loading/importing any candidate file.
    htree = api.get('git/trees/' + nomination['harnessTree'] + '?recursive=1')
    ctree = api.get('git/trees/' + nomination['candidateTree'] + '?recursive=1')
    require(not htree.get('truncated') and not ctree.get('truncated'), 'Truncated source tree')
    h = {r['path']: r for r in htree['tree']}; c = {r['path']: r for r in ctree['tree']}
    hashes = {}
    for name in QUALIFIERS:
        raw = (root / name).read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        require(h[name]['type'] == 'blob' and h[name]['mode'] == '100644' and h[name]['sha'] == blob,
                'Nominated H qualifier bytes differ')
        hashes[name] = hashlib.sha256(raw).hexdigest()
    require(all(c[k]['type'] == 'blob' and c[k]['mode'] == '100644' for k in SOURCES), 'Invalid candidate source entry')
    record = {'kind': KIND, 'selection': selection, 'facts': f, 'qualifiers': hashes,
              'sourceBlobs': {k: c[k]['sha'] for k in SOURCES},
              'jobPlan': {'total': 3, 'control': 1, 'dockerExecution': 1, 'verification': 1,
                          'maxLivePreparedResources': 1, 'profiles': ['python-project', 'browser-test']}}
    save(output, record)
    return record


def verify_job(proof, api_facts, jobs, artifact, raw_zip, *, nomination, run_id, attempt, external=True):
    """Pure provenance checks fed independent API facts, NOT candidate claims."""
    # select intentionally requires an active run. For completed external review,
    # validate terminal success separately and reuse the same identity checks.
    f = json.loads(json.dumps(api_facts))
    original = f['run']
    if external:
        require(original['status'] == 'completed' and original['conclusion'] == 'success', 'Qualification run not successful')
    else:
        require(original['status'] == 'in_progress' and original['conclusion'] is None, 'Qualification run no longer active')
    original['status'], original['conclusion'] = 'in_progress', None
    selection = select(nomination, f, now=proof['selection']['issued'])
    require(proof['selection'] == selection and selection['run'] == run_id and selection['attempt'] == attempt,
            'Proof selection differs from independent nomination/run')
    require(artifact.get('name') == f'candidate-native-{run_id}-{attempt}' and not artifact.get('expired')
            and artifact.get('workflow_run', {}).get('id') == run_id
            and artifact['workflow_run'].get('head_sha') == nomination['harness']
            and artifact.get('digest') == 'sha256:' + hashlib.sha256(raw_zip).hexdigest(), 'Artifact origin/digest changed')
    matches = [j for j in jobs if j.get('name') == 'Candidate Docker execution']
    require(len(matches) == 1 and matches[0].get('run_id') == run_id and matches[0].get('run_attempt') == attempt
            and matches[0].get('conclusion') == 'success', 'Missing exact execution job attempt')
    steps = {s['name'] for s in matches[0].get('steps', []) if s.get('status') == 'completed' and s.get('conclusion') == 'success'}
    require(set(STEPS) <= steps, 'Missing successful fixed execution steps')
    controls = [j for j in jobs if j.get('name') == 'Trusted candidate selection']
    require(len(controls) == 1 and controls[0].get('conclusion') == 'success'
            and controls[0].get('run_id') == run_id and controls[0].get('run_attempt') == attempt,
            'Missing independent control job attempt')
    if external:
        verifiers = [j for j in jobs if j.get('name') == 'External candidate evidence verification']
        require(len(verifiers) == 1 and verifiers[0].get('conclusion') == 'success'
                and verifiers[0].get('run_id') == run_id and verifiers[0].get('run_attempt') == attempt,
                'Missing separate verification job')
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        require(len(archive.namelist()) == len(set(archive.namelist())), 'Duplicate evidence member')
        for name, maximum in [('report.json', 4 * 1024 ** 2), ('junit.xml', 4 * 1024 ** 2), ('admission.json', 1024 ** 2)]:
            require(archive.getinfo(name).file_size <= maximum, 'Evidence member exceeds bound')
        admission = json.loads(archive.read('admission.json'))
        require(admission == proof, 'Harness admission differs')
        report = json.loads(archive.read('report.json'))
        verify_cases(report, archive.read('junit.xml'), selection)
        wheel_name = report['wheelFile']
        require(Path(wheel_name).name == wheel_name and wheel_name.endswith('.whl')
                and archive.getinfo(wheel_name).file_size <= 128 * 1024 ** 2
                and hashlib.sha256(archive.read(wheel_name)).hexdigest() == report['wheelSha256'], 'Original candidate wheel missing/changed')
        verify_payload(archive, report, proof)
    return {'kind': KIND, 'passed': True, 'stage': 'external-final' if external else 'workflow-check-only',
            'run': run_id, 'attempt': attempt,
            'candidate': nomination['candidate'], 'candidateTree': nomination['candidateTree'],
            'harness': nomination['harness'], 'artifactId': artifact['id'], 'artifactDigest': artifact['digest'],
            'trust': 'reviewed first-party code; origin authenticated, not hostile-runner attestation'}

def verify_payload(archive, report, proof):
    """Join original wheel/source members and raw lifecycle artifacts, not booleans alone."""
    installed = json.loads(archive.read('installed.json'))
    origins = json.loads(archive.read('module-origins.json'))
    registry = json.loads(archive.read('registry.json'))
    selection = proof['selection']
    require(installed['kind'] == origins['kind'] == registry['kind'] == KIND
            and installed['wheelSha256'] == report['wheelSha256'] and installed['wheelFile'] == report['wheelFile']
            and installed['sourceBlobs'] == proof['sourceBlobs']
            and registry['selectionDigest'] == digest(selection) and registry['cancelled'] is False
            and len(registry['entries']) == 2, 'Installed or registry evidence changed')
    prefix, site = Path(installed['prefix']), None
    require(prefix.is_absolute() and prefix.name == 'installed' and installed['python'] == str(prefix / 'bin/python')
            and origins['installation']['prefix'] == str(prefix), 'Installed prefix evidence changed')
    with zipfile.ZipFile(io.BytesIO(archive.read(report['wheelFile']))) as wheel:
        require(len(wheel.namelist()) == len(set(wheel.namelist())), 'Duplicate original wheel members')
        record, = [n for n in wheel.namelist() if n.endswith('.dist-info/RECORD')]
        rows = list(csv.reader(io.StringIO(wheel.read(record).decode())))
        records = {r[0]: r[1:] for r in rows}
        require(len(records) == len(rows), 'Duplicate original RECORD')
        for member in SOURCES:
            require(wheel.getinfo(member).file_size <= 262144, 'Oversized selected member')
            body = wheel.read(member)
            blob = hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest()
            actual = hashlib.sha256(body).hexdigest()
            require(blob == proof['sourceBlobs'][member]
                    and records[member] == ['sha256=' + base64.urlsafe_b64encode(bytes.fromhex(actual)).decode().rstrip('='), str(len(body))],
                    'Original wheel/source/RECORD join failed')
            origin = origins['members'][member]
            path = Path(origin['path'])
            require(path.is_relative_to(prefix) and path.as_posix().endswith('/site-packages/' + member)
                    and origin['sha256'] == actual, 'Installed origin/member join failed')
            this_site = path.parents[1]
            require(site is None or site == this_site, 'Mixed installed module roots')
            site = this_site
    nonces, resources = set(), set()
    for index, profile in enumerate(PROFILES):
        case, entry = report['cases'][index], registry['entries'][index]
        plan, ack = case['registration']['plan'], case['registration']['ack']
        require(set(plan) == PLAN_FIELDS and plan['kind'] == KIND and plan['profile'] == profile
                and plan['selectionDigest'] == digest(selection) and plan['run'] == selection['run']
                and plan['attempt'] == selection['attempt'] and plan['expires'] == selection['expires']
                and entry['plan'] == plan and entry['ack'] == ack and entry['state'] == 'absent'
                and sha(plan['nonce']) and plan['nonce'] not in nonces and plan['resource'] not in resources
                and ack == {'kind': KIND, 'planDigest': digest(plan), 'selectionDigest': digest(selection),
                            'run': plan['run'], 'attempt': plan['attempt'], 'nonce': plan['nonce']},
                'Cross-owner/run/replayed registration')
        nonces.add(plan['nonce']); resources.add(plan['resource'])
        calls = case['calls']
        require([c['action'] for c in calls] == ['start', 'execute', 'stop', 'remove'], 'Missing/duplicate lifecycle effects')
        for call in calls:
            result = call['result']
            request = result['request']
            require(request['state'] == 'completed' and request['resourceId'] == plan['resource']
                    and request['sessionId'] == plan['owner'], 'Unsettled or foreign original request')
        removed = calls[-1]['result']['request']
        loaded = removed['cleanupExecution']
        require(loaded['kind'] == KIND and loaded['status'] == 'permit-recorded'
                and loaded['selectionDigest'] == digest(selection) and loaded['resource'] == plan['resource']
                and loaded['wheelSha256'] == report['wheelSha256']
                and loaded['readySha256'] == digest(loaded['ready'])
                and loaded['ready']['phase'] == 'loaded' and sha(loaded['ready']['nonce']),
                'Missing exact candidate loaded-input permit')
        ready_members = loaded['ready']['members']
        require([m['name'] for m in ready_members] == ['amplifier_web/environment_retention.py', 'amplifier_web/test_environment_worker.py']
                and all(m['capture'] == 'executed-source-buffer'
                    and m['sha256'] == origins['members'][m['name']]['sha256'] for m in ready_members), 'Loaded member mismatch')
        absence = json.loads(archive.read(profile + '-absence.json'))
        observation = absence['observation']
        require(absence['ok'] is True and observation['absenceConfirmed'] is True
                and observation['rootOwner'] == plan['owner'] and observation['universeId'] == plan['universe']
                and observation['operationId'] == plan['launch'] and observation['backendIdentity'] == plan['backend'],
                'Independent absence owner changed')
        dependencies_absent(case['dependencyAfterRetirement'])
        artifact_name = profile + '-artifacts.tar'
        require(hashlib.sha256(archive.read(artifact_name)).hexdigest() == case['artifactSha256'], 'Retained native artifact changed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('issue', 'verify'))
    for key in ('harness', 'harness-tree', 'candidate', 'candidate-tree', 'pr', 'run', 'attempt', 'output'):
        parser.add_argument('--' + key, required=True, type=int if key in ('pr', 'run', 'attempt') else str)
    parser.add_argument('--root', default='.')
    parser.add_argument('--internal', action='store_true', help='Workflow verifier before run terminal; external acceptance must omit')
    for key in ('requested-candidate', 'requested-candidate-tree', 'requested-pr'):
        parser.add_argument('--' + key)
    args = parser.parse_args(argv)
    diagnostic = {
        'kind': 'nonproduction-candidate-diagnostic', 'phase': 'started', 'operation': args.operation,
        'stage': 'request', 'identity': {
            **{key: getattr(args, key) if sha(getattr(args, key), 40) else None
               for key in ('harness', 'harness_tree', 'candidate', 'candidate_tree')},
            **{key: getattr(args, key) if getattr(args, key) > 0 else None for key in ('pr', 'run', 'attempt')}},
    }
    failure_path = Path(args.output).with_name('failure.json')
    # Persist a safe prerequisite record BEFORE request validation/API/issuance.
    save(failure_path, diagnostic)
    try:
        requested = (args.requested_candidate, args.requested_candidate_tree, args.requested_pr)
        if any(value is not None for value in requested):
            validate_request(
                {'CANDIDATE': args.candidate, 'CANDIDATE_TREE': args.candidate_tree, 'CANDIDATE_PR': str(args.pr)},
                dict(zip(('REQUESTED_C', 'REQUESTED_C_TREE', 'REQUESTED_PR'), requested)))
        diagnostic['stage'] = args.operation
        execute(args, GitHub())
    except Exception as exc:
        diagnostic.update(phase='failed', **(
            exc.diagnostic if isinstance(exc, APIReadFailure) else
            {'category': 'command_nonzero', 'returncode': exc.returncode} if isinstance(exc, subprocess.CalledProcessError) else
            {'category': 'command_timeout'} if isinstance(exc, subprocess.TimeoutExpired) else
            {'category': 'filesystem'} if isinstance(exc, OSError) else
            {'category': 'validation'} if isinstance(exc, (ValueError, KeyError, TypeError)) else
            {'category': 'controller'}))
        save(failure_path, diagnostic)
        # Exception strings, captured streams and environment are intentionally absent.
        print(json.dumps(diagnostic, sort_keys=True), file=sys.stderr)
        return 1
    diagnostic['phase'] = 'completed'
    save(failure_path, diagnostic)
    return 0


def execute(args, api):
    n = {'repository': REPOSITORY, 'harness': args.harness, 'harnessTree': args.harness_tree,
         'candidate': args.candidate, 'candidateTree': args.candidate_tree, 'pr': args.pr}
    if args.operation == 'issue':
        require(not args.internal, 'Internal flag does not issue authority')
        issue(api, n, args.run, args.attempt, args.root, args.output)
    else:
        f = facts(api, n, args.run, args.attempt)
        artifacts = api.get(f'actions/runs/{args.run}/artifacts?per_page=100')
        require(artifacts['total_count'] <= 100, 'Artifact list incomplete')
        artifact, = [a for a in artifacts['artifacts'] if a['name'] == f'candidate-native-{args.run}-{args.attempt}']
        require(0 < artifact['size_in_bytes'] <= 256 * 1024 ** 2, 'Oversized candidate evidence')
        raw = api.get(f"actions/artifacts/{artifact['id']}/zip", binary=True, limit=256 * 1024 ** 2)
        jobs = api.get(f'actions/runs/{args.run}/attempts/{args.attempt}/jobs?per_page=100')
        require(jobs['total_count'] <= 100, 'Incomplete jobs')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            proof = json.loads(archive.read('admission.json'))
        # Independently compare qualifier blobs to exact nominated H via GitHub.
        for name in QUALIFIERS:
            blob = api.get('contents/' + name + '?ref=' + n['harness'])
            body = base64.b64decode(blob['content'], validate=False)
            require(hashlib.sha256(body).hexdigest() == proof['qualifiers'][name], 'Uploaded qualifier differs from nominated H')
        ctree = api.get('git/trees/' + n['candidateTree'] + '?recursive=1')
        require(not ctree.get('truncated'), 'Incomplete candidate tree')
        blobs = {r['path']: r['sha'] for r in ctree['tree'] if r['type'] == 'blob' and r['mode'] == '100644'}
        require(proof['sourceBlobs'] == {k: blobs[k] for k in SOURCES}, 'Uploaded candidate source bindings changed')
        save(args.output, verify_job(proof, f, jobs['jobs'], artifact, raw,
             nomination=n, run_id=args.run, attempt=args.attempt, external=not args.internal))


if __name__ == '__main__':
    raise SystemExit(main())
