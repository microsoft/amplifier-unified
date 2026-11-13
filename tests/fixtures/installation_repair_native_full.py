"""CI-only FULL REALAPP repair acceptance; no local native execution.

CLI (setup-python 3.13, uv on PATH, GitHub-hosted Ubuntu / macOS 26):
  python -I -B tests/fixtures/installation_repair_native_full.py prepare|run|cleanup
Parent workflow must set INSTALLATION_REPAIR_FULL_ACCEPTED_REVISION to its exact
reviewed production freeze, UV_PYTHON_DOWNLOADS=never, serialize the native jobs,
allow public Git/PyPI dependency acquisition, and run cleanup + evidence upload
with always(). Upload ONLY RUNNER_TEMP/installation-repair-full-evidence, never
the private fixture, logs, plans, token, environment or uv cache.

One real happy path plus settled exact API retry/status, not the tiny harness's
five failure mechanics. No private dev-loop, provider, fake runtime, .pth sharing,
fake package, doctor, health or probe. The ordinary full local candidate is built
with release.stage_source / uv build / release.verify_dist at the existing version.
The disclosed acquisition-only sitecustomize asserts LOCAL candidate delivery,
not published trust; -I probes/doctor and the -I -S worker cannot load it.
Its passive audit hook records only repair manager-dispatch labels/PIDs, not
arguments/environments; exact retry must leave the observed attempt count at one.
Requires bearer health.hostInstanceId as the canonical service UUID, separate
from updater health.instanceId (32 hex), and atomic admissions.json (schema 1).
Unknown/missing API or legacy admission shapes FAIL; no compatibility fallback.
Failure diagnostics are passive provenance-verified metadata observations only:
raw source URLs/refs and private plans never ship, equality never waives readiness,
and a diagnostic error never replaces the original native failure.
No sudo, OS trust install, bus provisioning, publication, or live adoption.
Private storage is retained for the disposable runner owner to reclaim.
"""
from __future__ import annotations

import argparse
import ast
import base64
import configparser
from contextlib import contextmanager
import csv
from email.parser import Parser
import fnmatch
import hashlib
import hmac
import http.client
import importlib.util
from importlib import metadata
from itertools import islice
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import ssl
import sys
import time
import tomllib
import uuid
import zipfile

SOURCE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('_native_repair_helpers',
                                            Path(__file__).with_name('installation_repair_native.py'))
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
check, load, save, digest, execute = native.check, native.load, native.save, native.digest, native.execute
OWN_PATHS = ('tests/fixtures/installation_repair_native_full.py',
             'tests/fixtures/installation_repair_acquisition.py',
             'tests/test_installation_repair_native_full.py')
LIMITS = {'officialPublishedTrust': False, 'localFullCandidate': True,
          'realDefaultDependencies': True, 'realHealthDoctorProbes': True,
          'nativeWindows': False, 'liveAdoption': False,
          'substitutions': ['disclosed acquisition-only PYTHONPATH adapter / local candidate delivery']}

# Executed ONLY in CI by the real installed Python, without the adapter.
# Test data is seeded through the ordinary AppService persistence owner. No
# server/background flags, runtime doubles, provider selection or message send.
PRECONFIGURE = r'''
import asyncio,json,sys
from pathlib import Path
from amplifier_web.service import AppService
from amplifier_web import deployment,tls
async def main():
 home,workspace=map(Path,sys.argv[1:3]); port=int(sys.argv[3])
 service=AppService(home,workspace=workspace)
 try:
  await service.dispatch('settings.update',{'patch':{'updates':{'autoCheck':False,'autoInstall':False}}},origin='ui')
  service.clients.attach('full-repair-sentinel',kind='api')
  with service.clients.bind('full-repair-sentinel'):
   result=await service.dispatch('session.create',{'workspace':str(workspace),'title':'Retained repair sentinel','select':False},origin='ui')
   identity=result['sessionId']; session=service._session(identity)
   service._message(session,'user','Retained historical message; never submitted to a provider.')
   service.clients.draft(identity,'Retained unsent draft; never an instruction.')
   service._save_changes(sessions=(identity,))
 finally:
  await service.close()
 config=deployment.load_server_config(home,overrides={'port':port,'bind':['127.0.0.1']})
 tls.setup_local_ca(home,config)
 print(json.dumps({'sessionId':identity}))
asyncio.run(main())
'''

# Read-only joins through maintained persisted-view decoding, not another host.
SENTINEL = r'''
import hashlib,json,sqlite3,sys
from pathlib import Path
from amplifier_web.state_records import load_owner
from amplifier_web.session_projection import hydrate
home=Path(sys.argv[1]); identity=sys.argv[2]
db=sqlite3.connect((home/'app.sqlite3').as_uri()+'?mode=ro',uri=True)
row,_=load_owner(db,identity)
assert row is not None
state={'sessions':[row]}
hydrate(home,state,db)
messages=[{key:m[key] for key in ('id','role','text')} for m in state['sessions'][0]['messages']]
assert messages and messages[0]['text']=='Retained historical message; never submitted to a provider.'
client=json.loads(db.execute('SELECT value FROM client_views WHERE id=?',('full-repair-sentinel',)).fetchone()[0])
draft=client['drafts'][identity]
assert draft=='Retained unsent draft; never an instruction.'
def sha(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
print(json.dumps({'sessionId':identity,'messagesSHA256':sha(messages),'draftSHA256':sha(draft)}))
db.close()
'''


def gate(action):
    tiny_root, _ = native.gate()
    if sys.platform == 'darwin':
        check(platform.mac_ver()[0].split('.')[0] == '26', 'full-app qualification requires macOS 26')
    else:
        check(platform.freedesktop_os_release().get('ID') == 'ubuntu', 'full-app qualification requires Ubuntu')
    if action != 'cleanup':
        revision = execute('git', '-C', SOURCE, 'rev-parse', 'HEAD').stdout.strip()
        check(os.environ.get('INSTALLATION_REPAIR_FULL_ACCEPTED_REVISION') == revision,
              'parent must accept the exact production freeze before native execution')
    root = tiny_root.with_name('installation-repair-native-full')
    evidence = tiny_root.with_name('installation-repair-full-evidence')
    check(not root.is_symlink() and not evidence.is_symlink(), 'redirected full-app fixture')
    return root, evidence


def source_index():
    selected = [*OWN_PATHS, *native.TEST_PATHS, 'scripts/release.py', 'pyproject.toml',
                *('amplifier_web/' + name for name in native.PRODUCTION)]
    if (SOURCE / native.WORKFLOW).exists():
        selected.append(native.WORKFLOW)
    check(not execute('git', '-C', SOURCE, 'status', '--porcelain').stdout.strip(),
          'full candidate requires a clean committed source tree')
    return {'revision': execute('git', '-C', SOURCE, 'rev-parse', 'HEAD').stdout.strip(),
            'tree': execute('git', '-C', SOURCE, 'rev-parse', 'HEAD^{tree}').stdout.strip(),
            'sha256': {name: digest(SOURCE / name) for name in selected},
            'workflowPresent': (SOURCE / native.WORKFLOW).exists()}


def payload_sources(project, tracked):
    """Map every tracked package/force-include input, not only repair siblings."""
    build = project['tool']['hatch']['build']['targets']['wheel']
    result = {}
    for name in tracked:
        if any(fnmatch.fnmatch('/' + name, pattern) for pattern in build.get('exclude', [])):
            continue
        for package in build['packages']:
            if name.startswith(package + '/'):
                result[Path(package).name + name[len(package):]] = name
        for source, target in build.get('force-include', {}).items():
            if name == source or name.startswith(source + '/'):
                result[target + name[len(source):]] = name
    return result


def requirement_key(value):
    """Canonicalize the project's simple default requirements without installs.

    Hatch normalizes comma-separated specifier order. Markers/unknown grammar
    deliberately fail rather than being compared with a lossy heuristic.
    """
    check(';' not in value, 'extend the metadata join before adding marked defaults')
    name, separator, url = value.partition(' @ ')
    if separator:
        return (re.sub(r'[-_.]+', '-', name.lower()), '@' + url)
    match = re.fullmatch(r'([A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?)\s*(.*)', value)
    check(match is not None, 'invalid default dependency metadata')
    name, constraints = match.groups()
    if constraints.lstrip().startswith('@'):
        return (re.sub(r'[-_.]+', '-', name.lower()), re.sub(r'\s+', '', constraints))
    return (re.sub(r'[-_.]+', '-', name.lower()), tuple(sorted(
        re.sub(r'\s+', '', part) for part in constraints.split(',') if part.strip())))


def wheel_join(path, project, revision):
    """Verify full source payload, default dependency metadata, entrypoints/RECORD."""
    release = native.module(SOURCE / 'scripts/release.py')
    tracked = execute('git', '-C', SOURCE, 'ls-files', '-z').stdout.split('\0')
    inputs = payload_sources(project, filter(None, tracked))
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        check(len(set(names)) == len(names), 'duplicate wheel member')
        release.reject_ci_checkouts(names)
        check(not any(name.endswith('.pth') or '_native_fixture' in name for name in names),
              'wheel contains a test substitute or dependency-sharing hook')
        for target, source in inputs.items():
            check(target in names and archive.read(target) == (SOURCE / source).read_bytes(),
                  'full wheel source/payload mismatch')
        release.verify_frontend(SOURCE, names, archive.read, 'Full-app candidate')
        expected = release.source_record_bytes(SOURCE, 'microsoft/amplifier-unified',
                                               revision, project['project']['version'])
        release.verify_source_record(names, archive.read, expected, 'Full-app candidate')
        check(expected is not None, 'full-app candidate predates source-record packaging')
        info = 'amplifier_unified-' + project['project']['version'] + '.dist-info/'
        metadata = Parser().parsestr(archive.read(info + 'METADATA').decode())
        check(metadata['Name'] == 'amplifier-unified'
              and metadata['Version'] == project['project']['version'], 'wrong wheel metadata')
        # Optional feature markers are not default requirements.
        actual = {requirement_key(value) for value in metadata.get_all('Requires-Dist', []) if ';' not in value}
        check(all(requirement_key(value) in actual for value in project['project']['dependencies']),
              'full default host dependencies were not packaged')
        entries = configparser.ConfigParser()
        entries.read_string(archive.read(info + 'entry_points.txt').decode())
        check(dict(entries['console_scripts']) == project['project']['scripts'],
              'public launcher entrypoints changed')
        rows = list(csv.reader(io.StringIO(archive.read(info + 'RECORD').decode())))
        check({row[0] for row in rows} == set(names), 'wheel RECORD coverage mismatch')
        for name, checksum, size in rows:
            if name == info + 'RECORD':
                continue
            raw = archive.read(name)
            encoded = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
            check(checksum == 'sha256=' + encoded and int(size) == len(raw), 'wheel RECORD mismatch')
        payload = {name: hashlib.sha256(archive.read(name)).hexdigest()
                   for name in names if name != info + 'RECORD'}
    return {'sha256': payload, 'sourceFiles': len(inputs), 'wheelFiles': len(payload),
            'defaultDependencies': len(project['project']['dependencies']),
            'entrypoints': dict(entries['console_scripts']), 'sourceRecord': json.loads(expected)}


def installed_join(prefix, payload):
    sites = list(Path(prefix).glob('lib/python*/site-packages'))
    check(len(sites) == 1, 'installed package has no unique site directory')
    for name, expected in payload['sha256'].items():
        check(digest(sites[0] / name) == expected, 'installed payload differs from candidate wheel')
    return {'wheelFilesMatched': len(payload['sha256']), 'payloadSHA256': hashlib.sha256(
        json.dumps(payload['sha256'], sort_keys=True).encode()).hexdigest()}


def request(config, path, body=None):
    """Real CA/SAN/expiry validation AND exact connected leaf pin, before token."""
    pin = config['tls']['pin']
    check(isinstance(pin, str) and re.fullmatch('[a-f0-9]{64}', pin), 'invalid leaf pin')
    context = ssl.create_default_context(cafile=config['tls']['ca'])
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    connection = http.client.HTTPSConnection('127.0.0.1', config['port'], timeout=60, context=context)
    try:
        connection.connect()
        observed = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        check(hmac.compare_digest(observed, pin), 'full-app listener TLS pin mismatch')
        token = (Path(config['home']) / 'config/auth/control-token').read_text().strip()
        connection.request('GET' if body is None else 'POST', path,
                           body=None if body is None else json.dumps(body),
                           headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                                    'X-Amplifier-State-Transport': 'delta-v1'})
        response = connection.getresponse()
        check(response.status == 200, 'full-app authenticated API request failed (HTTP ' + str(response.status) + ')')
        raw = response.read(4_000_001)
        check(len(raw) <= 4_000_000, 'full-app API response exceeds bound')
        return json.loads(raw)
    finally:
        connection.close()


def action(config, name, args):
    # No automatic retry and no generic command-id dedup to hide repair retries.
    value = request(config, '/api/actions', {'action': name, 'args': args})
    check(isinstance(value, dict) and value.get('accepted') is True
          and isinstance(value.get('result'), dict),
          'full-app API returned an unrecognized accepted/result envelope')
    return value['result']


def health_identities(health, config):
    """Service UUID binds actions; updater hex identity binds running generation.

    UUID() accepts 32-hex input too, so require the canonical hyphenated service
    spelling. Never substitute/normalize updater instanceId into hostInstanceId.
    """
    check(isinstance(health, dict) and health.get('ok') is True
          and health.get('app') == 'amplifier-unified'
          and health.get('revision') == config['revision']
          and health.get('version') == config['version'], 'authenticated health source/app mismatch')
    host = health.get('hostInstanceId')
    try:
        parsed = uuid.UUID(host) if isinstance(host, str) else None
    except ValueError:
        parsed = None
    check(parsed is not None and str(parsed) == host, 'authenticated health requires a service hostInstanceId UUID')
    instance = health.get('instanceId')
    check(isinstance(instance, str) and re.fullmatch('[a-f0-9]{32}', instance)
          and instance != parsed.hex, 'authenticated health requires a distinct updater instanceId (32 hex)')
    return host, instance


def preview_confirmation(config, evidence, health):
    host, _ = health_identities(health, config)
    try:
        preview = action(config, 'maintenance.installationRepairPreview', {})
    except (RuntimeError, OSError, http.client.HTTPException, json.JSONDecodeError) as error:
        save(evidence / 'preview.json', {'passed': False, 'failure': 'preview-api-input-or-response',
             'errorType': type(error).__name__,
             'detail': str(error) if isinstance(error, RuntimeError) else 'Preview response unavailable; no confirmation sent.'})
        raise
    save(evidence / 'preview.json', {key: preview.get(key) for key in
         ('supported', 'previewId', 'hostInstanceId', 'scope', 'dataDir', 'target', 'reason')})
    check(preview.get('supported') is True,
          'real full-app preview refused; inspect preview evidence and production binding/admission, do not bypass')
    check(preview.get('hostInstanceId') == host
          and preview.get('scope') == 'application-installation'
          and preview.get('dataDir') == config['home']
          and preview.get('installedExtras') == []
          and preview.get('restart') == 'managed-service',
          'preview changed service UUID, installation scope, data root or default features')
    check(preview.get('target') == {'version': config['version'], 'revision': config['revision'],
                                    'source': config['artifact']['url']},
          'preview did not select exact full candidate')
    check(isinstance(preview.get('previewId'), str) and re.fullmatch('[a-f0-9]{32}', preview['previewId']),
          'preview has no exact preview ID')
    return preview, {'previewId': preview['previewId'], 'hostInstanceId': host, 'confirm': True}


def admitted_operation(repair_root, preview_id):
    """Read the sole atomic admission, never a legacy/per-preview fallback."""
    admissions = load(repair_root / 'admissions.json')
    exact_id = lambda value: isinstance(value, str) and re.fullmatch('[a-f0-9]{32}', value)
    check(isinstance(admissions, dict) and set(admissions) == {'schema', 'current', 'previews'}
          and type(admissions['schema']) is int and admissions['schema'] == 1
          and isinstance(admissions['previews'], dict)
          and all(exact_id(key) and exact_id(value) for key, value in admissions['previews'].items())
          and (admissions['current'] is None or admissions['current'] in admissions['previews'].values()),
          'unrecognized atomic installation repair admission')
    identity = admissions['previews'].get(preview_id)
    if identity is None:
        return None
    check(identity == admissions['current'], 'confirmed preview is not the current atomic repair admission')
    return identity


def confirm_once(root, config, confirmation):
    save(root / 'confirmation.json', confirmation)  # exact intent BEFORE send
    try:
        action(config, 'maintenance.installationRepairConfirm', confirmation)
    except (OSError, http.client.HTTPException, RuntimeError, json.JSONDecodeError):
        # Even a failed HTTP/JSON response can follow durable admission.
        # Inspect disk, never turn uncertainty into another confirm request.
        pass
    repair_root = Path(config['home']) / 'installation-repair'
    identity = native.wait_for(lambda: admitted_operation(repair_root, confirmation['previewId']),
                               seconds=120, description='durable atomic one-shot admission after confirm')
    return repair_root / identity


def require_plan_scope(plan, config, health, confirmation, operation_id):
    host, _ = health_identities(health, config)
    check(plan.get('hostInstanceId') == host == confirmation['hostInstanceId']
          and plan.get('home') == config['home'] and plan.get('extras') == []
          and plan.get('operationId') == operation_id and plan.get('previewId') == confirmation['previewId']
          and plan.get('target') == {'version': config['version'], 'revision': config['revision'],
                                    'source': config['artifact']['url']},
          'plan changed service UUID, installation scope, current operation or candidate')


def require_ready(receipt):
    check(receipt['status'] == 'ready' and receipt.get('doctorVerified') is True
          and receipt.get('healthVerified') is True and receipt.get('reinstalled') is True,
          'full-app repair did not verify ready; retain failed evidence, never relabel')


def value_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def diagnostic_error_type(name):
    safe = {'ValueError', 'TypeError', 'KeyError', 'FileNotFoundError', 'PermissionError', 'OSError',
            'RuntimeError', 'TimeoutError', 'TimeoutExpired', 'CommandFailed', 'BlockingIOError',
            'InterruptedError', 'Exception'}
    return name if isinstance(name, str) and name in safe else 'Exception'


def diagnostic_graph(rows):
    """The worker's graph shape, with stricter bounded printable identifiers."""
    check(isinstance(rows, list) and 0 < len(rows) <= 512, 'diagnostic graph size/shape refused')
    for row in rows:
        check(isinstance(row, dict) and isinstance(row.get('name'), str)
              and re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', row['name']) and len(row['name']) <= 200
              and isinstance(row.get('version'), str) and re.fullmatch('[A-Za-z0-9.!+_-]{1,200}', row['version'])
              and isinstance(row.get('direct'), (dict, type(None))), 'diagnostic graph row refused')
    names = [row['name'] for row in rows]
    check(len(names) == len(set(names)) and names.count('amplifier-unified') == 1,
          'diagnostic graph duplicate/missing app')
    return sorted(rows, key=lambda row: row['name'])


def compare_diagnostic_graphs(candidate, failed):
    """Hash all source values. Never publish raw URLs, refs, metadata or keys."""
    candidate, failed = diagnostic_graph(candidate), diagnostic_graph(failed)
    fields = ('name', 'version', 'stamp', 'direct', 'direct.url', 'direct.vcs_info',
              'direct.vcs_info.vcs', 'direct.vcs_info.commit_id', 'direct.vcs_info.requested_revision',
              'direct.archive_info', 'direct.dir_info')

    def field(row, path):
        value = row
        for key in path.split('.'):
            if not isinstance(value, dict) or key not in value:
                return {'present': False}
            value = value[key]
        return {'present': True, 'sha256': value_hash(value)}

    def summary(row):
        result = {'name': row['name'], 'version': row['version'], 'rowSHA256': value_hash(row)}
        direct = row.get('direct')
        vcs = direct.get('vcs_info') if isinstance(direct, dict) else None
        commit = vcs.get('commit_id') if isinstance(vcs, dict) else None
        if isinstance(commit, str) and re.fullmatch('[a-f0-9]{40}', commit):
            result['gitCommit'] = commit
        return result

    left, right = ({row['name']: row for row in rows} for rows in (candidate, failed))
    differences = []
    for name in sorted(left.keys() | right.keys()):
        a, b = left.get(name), right.get(name)
        if a != b:
            differences.append({'name': name, 'candidatePresent': a is not None, 'failedPresent': b is not None,
                'fields': {path: {'equal': field(a, path) == field(b, path),
                                  'candidate': field(a, path), 'failed': field(b, path)} for path in fields}})
    return {'equal': candidate == failed, 'candidateSHA256': value_hash(candidate), 'failedSHA256': value_hash(failed),
            'candidatePackages': [summary(row) for row in candidate], 'failedPackages': [summary(row) for row in failed],
            'differenceCount': len(differences), 'differences': differences[:64],
            'differencesTruncated': len(differences) > 64}


def passive_prefix_graph(worker, plan, prefix):
    """GRAPH_PROBE-equivalent metadata only; never run/import retained code.

    The UNMODIFIED worker proves app provenance before any discovery or further
    prefix inspection. .dist-info reads use its non-symlink/ownership checks;
    Distribution.discover is scoped to the one proven site, never sys.path.
    """
    before = worker._installed_provenance(plan, prefix)
    sites = list(prefix.glob('lib/python*/site-packages'))
    check(len(sites) == 1, 'diagnostic prefix has no unique site')
    site = worker.directory(sites[0])
    distributions = list(islice(metadata.Distribution.discover(path=[str(site)]), 513))
    check(0 < len(distributions) <= 512, 'diagnostic distribution count refused')
    remaining = 16_000_000

    def read(path, limit=1_000_000):
        nonlocal remaining
        raw = worker._bytes(path, limit=min(limit, remaining), single=False)
        remaining -= len(raw)
        return raw

    rows, distribution_hashes = [], []
    for distribution in distributions:
        # CPython's stdlib PathDistribution is only a directory locator here.
        # Do not call metadata properties that could read linked/unbounded files.
        check(isinstance(distribution, metadata.PathDistribution), 'nonphysical diagnostic distribution')
        info = worker.directory(Path(distribution._path))
        check(info.parent == site and info.name.endswith('.dist-info'), 'foreign diagnostic distribution path')
        raw_metadata = read(info / 'METADATA')
        parsed = Parser().parsestr(raw_metadata.decode())
        name = re.sub('[-_.]+', '-', parsed.get('Name', '').lower())
        direct_path = info / 'direct_url.json'
        raw_direct = read(direct_path, 65_536) if worker._exists(direct_path) else None
        row = {'name': name, 'version': parsed.get('Version'),
               'direct': json.loads(raw_direct) if raw_direct is not None else None}
        if name == 'amplifier-unified':
            row['stamp'] = json.loads(read(site / 'amplifier_web/release-source.json', 16_384))
        rows.append(row)
        distribution_hashes.append({'name': name, 'metadataSHA256': hashlib.sha256(raw_metadata).hexdigest(),
             'directSHA256': hashlib.sha256(raw_direct).hexdigest() if raw_direct is not None else None})
    # Exactly the production normalization for the app; dependencies UNCHANGED.
    rows = diagnostic_graph(rows)
    normalized = diagnostic_graph([worker._app_source(plan, row) if row['name'] == 'amplifier-unified' else row
                                   for row in rows])
    sources = {}
    protocol = None
    for name in (*native.PRODUCTION, 'release-source.json'):
        raw = read(site / 'amplifier_web' / name)
        sources[name] = {'sha256': hashlib.sha256(raw).hexdigest(),
                         'matchesCandidateSource': raw == (SOURCE / 'amplifier_web' / name).read_bytes()
                         if name != 'release-source.json' else None}
        if name == 'installation_repair.py':
            # Static literal only, not an import/protocol execution.
            values = [node.value.value for node in ast.parse(raw).body
                      if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                      and any(isinstance(target, ast.Name) and target.id == 'PROTOCOL' for target in node.targets)]
            protocol = values[0] if len(values) == 1 and type(values[0]) is int else None
    check(worker._installed_provenance(plan, prefix) == before, 'diagnostic prefix changed while reading')
    return normalized, {'rawGraphSHA256': value_hash(rows), 'normalizedGraphSHA256': value_hash(normalized),
        'distributionHashes': sorted(distribution_hashes, key=lambda row: row['name']),
        'sourceHashes': sources, 'staticProtocol': protocol}


def failure_diagnostics(folder, plan, receipt):
    """Failure-only observation, not causal qualification or a recovery action."""
    errors = []
    result = {'diagnosticOnly': True, 'executesRetainedCode': False, 'causeQualified': False,
              'originalErrorType': diagnostic_error_type(receipt['errorType']) if receipt.get('errorType') is not None else None,
              'diagnosticErrors': errors}

    def observe(section, read):
        try:
            return read()
        except Exception as error:
            errors.append({'section': section, 'errorType': diagnostic_error_type(type(error).__name__)})
            return None

    def known_worker():
        check(re.fullmatch('[a-f0-9]{32}', folder.name)
              and folder == Path(plan['home']) / 'installation-repair' / plan['operationId']
              and folder.resolve() == folder, 'diagnostic operation path refused')
        for name in native.SIBLINGS:
            path = folder / name
            check(not path.is_symlink() and digest(path) == digest(SOURCE / 'amplifier_web' / name),
                  'diagnostic worker sibling differs from exact source')
        return native.module(folder / 'installation_repair_worker.py')

    worker = observe('worker-source', known_worker)
    if worker is None:
        return result
    result['workerArtifacts'] = {name: digest(folder / name) for name in native.SIBLINGS}
    result['workerArtifacts'].update(graphProbeSHA256=hashlib.sha256(worker.GRAPH_PROBE.encode()).hexdigest(),
                                     protocolProbeSHA256=hashlib.sha256(worker.PROTOCOL_PROBE.encode()).hexdigest())

    def artifact_hash():
        path = Path(plan['artifact']['path'])
        check(path.parent == folder and path.name == 'amplifier_unified-' + plan['target']['version'] + '-py3-none-any.whl'
              and re.fullmatch('[a-f0-9]{64}', plan['artifact']['sha256']), 'diagnostic artifact path refused')
        actual = hashlib.sha256(worker._bytes(path, private=True, limit=64_000_000)).hexdigest()
        return {'sha256': actual, 'matchesExpected': actual == plan['artifact']['sha256']}

    result['operationArtifact'] = observe('operation-artifact', artifact_hash)
    # Only booleans/known schema leave the private receipt/child ledger.
    boundary = receipt.get('effectBoundary')
    result['effectBoundary'] = ({key: boundary[key] for key in ('schema', 'stopIntent', 'stopSettled', 'mutationIntent')}
        if isinstance(boundary, dict) and type(boundary.get('schema')) is int and boundary['schema'] == 1
        and all(type(boundary.get(key)) is bool for key in ('stopIntent', 'stopSettled', 'mutationIntent')) else None)

    def child_proof():
        raw = worker._bytes(folder / 'child-boundary.json', private=True, limit=16_384)
        value = json.loads(raw)
        check(isinstance(value, dict) and set(value) == {'schema', 'settled'}
              and type(value['schema']) is int and value['schema'] == 1 and type(value['settled']) is bool,
              'diagnostic child ledger shape refused')
        return {'schema': 1, 'settled': value['settled'], 'sha256': hashlib.sha256(raw).hexdigest()}

    result['childSettlement'] = observe('child-settlement', child_proof)

    def candidate_graph():
        raw = worker._bytes(folder / 'candidate-graph.json', private=True)
        rows = diagnostic_graph(json.loads(raw))
        result['candidateGraphSHA256'] = hashlib.sha256(raw).hexdigest()
        return rows

    candidate = observe('candidate-graph', candidate_graph)
    # Uncertain rollback/children: no prefix inspection, and never execution.
    if (receipt.get('status') != 'rolled-back' or receipt.get('stage') != 'rolled-back'
            or not result['childSettlement'] or result['childSettlement']['settled'] is not True):
        result['prefixInspection'] = 'refused-unproven-rollback-or-child-settlement'
        return result
    reset = native.module(folder / 'reset.py')  # exact source checked above
    failed = reset.tool_recovery_root(Path(plan['toolRoot'])) / plan['operationId'] / 'failed'
    observed = observe('failed-prefix', lambda: passive_prefix_graph(worker, plan, failed))
    if observed is not None:
        failed_rows, result['failedPrefix'] = observed
        if candidate is not None:
            result['graphComparison'] = observe('graph-comparison', lambda: compare_diagnostic_graphs(candidate, failed_rows))
    observed = observe('candidate-prefix', lambda: passive_prefix_graph(worker, plan, folder / 'candidate-tools/amplifier-unified'))
    if observed is not None:
        candidate_rows, result['candidatePrefix'] = observed
        result['candidateDiskMatchesRecordedGraph'] = candidate_rows == candidate if candidate is not None else None
    return result


def accept_ready(folder, plan, receipt, evidence):
    if not (receipt.get('status') == 'ready' and receipt.get('doctorVerified') is True
            and receipt.get('healthVerified') is True and receipt.get('reinstalled') is True):
        try:
            report = failure_diagnostics(folder, plan, receipt)
        except Exception as error:
            # Never replace native failure with a diagnostic failure/traceback.
            report = {'diagnosticOnly': True, 'causeQualified': False,
                      'originalErrorType': diagnostic_error_type(receipt['errorType']) if receipt.get('errorType') is not None else None,
                      'diagnosticErrors': [{'section': 'diagnostic-collection',
                                            'errorType': diagnostic_error_type(type(error).__name__)}]}
        try:
            save(evidence / 'failure-diagnostics.json', report)
        except OSError:
            pass  # Original operation.json and readiness failure remain authoritative.
    require_ready(receipt)


def dispatches(root, label, pid):
    path = root / 'dispatch-audit.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    check(rows == [{'label': label, 'servicePID': pid}], 'expected exactly one real manager dispatch attempt')
    return rows


@contextmanager
def controller_lease(root):
    import fcntl
    with (root / 'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def prepare(root, evidence):
    check(not root.exists() and not evidence.exists(), 'fixture already exists; inspect, never replay prepare')
    index = source_index()
    root.mkdir(mode=0o700)
    evidence.mkdir(mode=0o700)
    save(root / 'owner.json', {'definition': None, 'helpers': [], 'servicePIDs': [], 'helperPIDs': [],
                              'runStarted': False, 'cleanupStarted': False})
    with controller_lease(root):
        prepare_fixture(root, evidence, index)


def prepare_fixture(root, evidence, index):
    env = native.manager_environment()
    env.update(UV_PYTHON_DOWNLOADS='never', UV_LINK_MODE='copy')
    native.manager_capability(env)
    check(not native.paths().exists() and not native.paths().is_symlink(), 'preexisting definition')
    if sys.platform == 'linux':
        check(native.properties(native.UNIT, env).get('LoadState') == 'not-found', 'preexisting unit')
    else:
        check(not native.launch_job(native.LABEL, env), 'preexisting launchd job')
    uv = shutil.which('uv')
    check(uv is not None, 'workflow must provide uv')
    base = str(Path(sys.executable).resolve())
    check(not Path(base).is_relative_to(root), 'base Python must outlive the fixture/tool environment')
    project = tomllib.loads((SOURCE / 'pyproject.toml').read_text())
    version = project['project']['version']
    release = native.module(SOURCE / 'scripts/release.py')
    stage = release.stage_source(SOURCE, 'microsoft/amplifier-unified', index['revision'], version)
    check(stage['staged'], 'candidate must support maintained source staging')
    dist = root / 'dist'
    execute(uv, 'build', '--python', base, '--out-dir', dist, SOURCE, env=env, timeout=1800)
    release.verify_dist(SOURCE, dist, version, 'microsoft/amplifier-unified', index['revision'])
    artifacts = native.module(SOURCE / 'amplifier_web/app_release_artifacts.py')
    wheel = dist / artifacts.filename(version)
    descriptor = {'path': str(wheel), 'sha256': digest(wheel),
                  'url': artifacts.SOURCE + '/releases/download/v' + version + '/' + wheel.name}
    artifacts.verify(descriptor, version, index['revision'])
    payload = wheel_join(wheel, project, index['revision'])
    home, tools, bins, workspace = (root / name for name in ('data', 'tools', 'bin', 'workspace'))
    for folder in (home, tools, bins, workspace):
        folder.mkdir(mode=0o700)
    env.update(UV_PYTHON_DOWNLOADS='never', UV_TOOL_DIR=str(tools), UV_TOOL_BIN_DIR=str(bins),
               UV_LINK_MODE='copy', AMPLIFIER_SESSION_STATE_HOME=str(root / 'shared-state'),
               AMPLIFIER_HOME=str(root / 'amplifier-home'), AMPLIFIER_WEB_HOME=str(home),
               AMPLIFIER_GITHUB_ACCESS='anonymous')
    env['PATH'] = str(bins) + os.pathsep + env['PATH']
    # No --no-deps, dev sync, private key, offline constraint or .pth dependency sharing.
    execute(uv, 'tool', 'install', '--python', base, '--from', wheel, 'amplifier-unified', env=env, timeout=1800)
    prefix = tools / 'amplifier-unified'
    initial_join = installed_join(prefix, payload)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    python = prefix / 'bin/python'
    seed = json.loads(execute(python, '-I', '-c', PRECONFIGURE, home, workspace, port, env=env).stdout)
    sentinel = json.loads(execute(python, '-I', '-c', SENTINEL, home, seed['sessionId'], env=env).stdout)
    leaf = home / 'config/tls/leaf.crt'
    pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(leaf.read_text())).hexdigest()
    adapter = root / 'acquisition'
    adapter.mkdir(mode=0o700)
    original = Path(__file__).with_name('installation_repair_acquisition.py')
    shutil.copyfile(original, adapter / original.name)
    # sitecustomize is outside the wheel/prefix and contains no production override.
    site = ('import os\nfrom installation_repair_acquisition import install\n'
            "install(os.environ['INSTALLATION_REPAIR_FULL_MANIFEST'])\n")
    (adapter / 'sitecustomize.py').write_text(site)
    config = {'root': str(root), 'home': str(home), 'toolRoot': str(tools), 'binRoot': str(bins),
              'workspace': str(workspace), 'port': port, 'basePython': base, 'uv': uv,
              'version': version, 'revision': index['revision'], 'artifact': descriptor,
              'sourceIndex': index, 'payload': payload, 'sentinel': sentinel,
              'tls': {'pin': pin, 'ca': str(home / 'config/tls/ca.crt')},
              'adapterSHA256': digest(original), 'sitecustomizeSHA256': digest(adapter / 'sitecustomize.py'),
              'nativeHarness': str(Path(native.__file__).resolve()),
              'nativeHarnessSHA256': digest(native.__file__)}
    save(root / 'fixture.json', config)
    # Python ignores these in -I / -I -S. Only the normal public service launcher
    # (initial and successor) can load the disclosed acquisition adapter.
    env.update(PYTHONPATH=str(adapter), INSTALLATION_REPAIR_FULL_MANIFEST=str(root / 'fixture.json'),
               GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted')
    if sys.platform == 'darwin':
        env['AMPLIFIER_UNIFIED_LAUNCHD_LABEL'] = native.LABEL
    save(root / 'service-env.json', env)  # private, never uploaded
    save(evidence / 'inputs.json', {'sourceIndex': index, 'version': version,
         'artifactSHA256': descriptor['sha256'], 'payload': payload, 'initialInstalledJoin': initial_join,
         'adapterSHA256': config['adapterSHA256'], 'sitecustomizeSHA256': config['sitecustomizeSHA256'],
         'tlsLeafSHA256': pin, 'platform': sys.platform, 'os': platform.platform(),
         'uvVersion': execute(uv, '--version').stdout.strip(), 'limits': LIMITS})


def capture_service(root, env):
    owner = load(root / 'owner.json')
    if sys.platform == 'darwin' and not native.launch_job(native.LABEL, env):
        return 0
    observation = native.service_observation(env)
    pids = ([int(observation.get('MainPID') or '0')] if sys.platform == 'linux'
            else list(map(int, observation['pids'])))
    for pid in pids:
        if pid:
            # Exact owned bytes/inode and loaded definition, never an arbitrary PID.
            native.owned_definition(owner['definition'], env, [pid])
            row = {'pid': pid, 'start': native.pid_start(pid, env)}
            check(row['start'], 'managed PID has no observed start identity')
            if row not in owner['servicePIDs']:
                owner['servicePIDs'].append(row)
    save(root / 'owner.json', owner)
    return pids[0] if len(pids) == 1 else 0


def observe_helper(root, helper, env):
    observed = native.owned_helper_observation(helper, env)
    pids = ([int(observed['MainPID'])] if sys.platform == 'linux' and observed.get('MainPID') not in {None, '', '0'}
            else [int(pid) for row in observed.get('domains', []) for pid in row['pids']])
    owner = load(root / 'owner.json')
    for pid in pids:
        start = native.pid_start(pid, env)
        if start and {'pid': pid, 'start': start} not in owner['helperPIDs']:
            owner['helperPIDs'].append({'pid': pid, 'start': start})
    save(root / 'owner.json', owner)
    return observed


def register_service(root, config, env):
    path = native.paths()
    check(not path.exists() and not path.is_symlink(), 'preexisting service definition')
    raw = native.definition(Path(config['binRoot']) / 'amplifier-unified', Path(config['home']),
                            Path(config['workspace']), env)
    owner = load(root / 'owner.json')
    owner['definition'] = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'home': config['home']}
    save(root / 'owner.json', owner)  # intent BEFORE creating any definition
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    owner['definition']['stat'] = [path.stat().st_dev, path.stat().st_ino]
    save(root / 'owner.json', owner)
    reset = native.module(SOURCE / 'amplifier_web/reset.py')
    if sys.platform == 'linux':
        execute('systemctl', '--user', 'daemon-reload', env=env)
    execute(*reset.service_commands(Path(config['home']))[1], env=env)


def run(root, evidence):
    config = load(root / 'fixture.json')
    check(source_index() == config['sourceIndex'], 'prepared/live source inputs changed')
    owner = load(root / 'owner.json')
    check(not owner['runStarted'] and not owner['cleanupStarted'], 'native run already attempted; do not replay')
    owner['runStarted'] = True
    save(root / 'owner.json', owner)
    adapter = root / 'acquisition'
    check(digest(adapter / 'installation_repair_acquisition.py') == config['adapterSHA256']
          and digest(adapter / 'sitecustomize.py') == config['sitecustomizeSHA256'],
          'prepared acquisition adapter changed')
    env = load(root / 'service-env.json')
    prefix = Path(config['toolRoot']) / 'amplifier-unified'
    sentinel_path = prefix / 'full-repair-old-prefix'
    old_sentinel = b'Original installation retained, not disposable repair debris.\n'
    sentinel_path.write_bytes(old_sentinel)
    old_stat = [prefix.stat().st_dev, prefix.stat().st_ino]
    register_service(root, config, env)
    old_pid = native.wait_for(lambda: capture_service(root, env), description='real managed initial PID')
    health = native.wait_for(lambda: request(config, '/api/health'), seconds=180, description='real initial HTTPS health')
    old_host, old_instance = health_identities(health, config)
    preview, confirmation = preview_confirmation(config, evidence, health)
    folder = confirm_once(root, config, confirmation)
    repair_root = Path(config['home']) / 'installation-repair'
    identity = folder.name
    plan = load(folder / 'plan.json')
    helper = load(root / 'owner.json')['helpers']
    check(len(helper) == 1 and helper[0]['folder'] == str(folder), 'helper lacks prior acquisition custody')
    for name in native.SIBLINGS:
        check(digest(folder / name) == digest(SOURCE / 'amplifier_web' / name),
              'prepared helper differs from exact live source')
    check(digest(plan['artifact']['path']) == config['artifact']['sha256'], 'operation artifact mismatch')
    require_plan_scope(plan, config, health, confirmation, identity)
    # Copy equality is necessary but never enough: the genuine worker performs
    # all qualification/admission/stop/swap/start/checks, without a test barrier.
    def progress():
        observe_helper(root, helper[0], env)
        return native.terminal(folder)
    # Candidate + fresh dependency acquisition each have their production 1800s
    # bound. Do not cut the second installer short or label its outcome failed.
    receipt = native.wait_for(progress, seconds=4200, description='full-app repair terminal receipt')
    settled = native.wait_for(lambda: observe_helper(root, helper[0], env),
                              lambda row: row['settled'], seconds=120, description='full-app helper settled')
    # The still-owned helper can supersede an uncertain launch observation.
    # Inspect its canonical receipt after settlement, without another dispatch.
    receipt = load(folder / 'operation.json')
    safe_receipt = {key: receipt.get(key) for key in
                    ('operationId', 'previewId', 'hostInstanceId', 'status', 'stage', 'reinstalled',
                     'doctorVerified', 'healthVerified', 'serviceState', 'errorType')}
    save(evidence / 'operation.json', {'receipt': safe_receipt, 'helper': settled, 'planSHA256': digest(folder / 'plan.json')})
    accept_ready(folder, plan, receipt, evidence)
    audit = dispatches(root, helper[0]['label'], old_pid)
    new_pid = native.wait_for(lambda: capture_service(root, env),
                              lambda pid: pid > 0 and pid != old_pid, description='real successor manager PID')
    successor = request(config, '/api/health')
    new_host, new_instance = health_identities(successor, config)
    check(new_host != old_host and new_instance != old_instance
          and successor['installationRepair'] == {'protocol': 1, 'operationId': identity},
          'authenticated successor health/current operation/protocol mismatch')
    check(not native.pid_start(old_pid, env), 'original service PID survived repair')
    worker = native.module(folder / 'installation_repair_worker.py')
    check(worker._service_pid(plan) == new_pid, 'successor is not exact native manager process')
    recovery = native.module(SOURCE / 'amplifier_web/reset.py').tool_recovery_root(Path(config['toolRoot'])) / identity / 'previous'
    check([recovery.stat().st_dev, recovery.stat().st_ino] == old_stat
          and (recovery / sentinel_path.name).read_bytes() == old_sentinel,
          'old installation prefix was not retained')
    check([prefix.stat().st_dev, prefix.stat().st_ino] != old_stat, 'prefix was not replaced')
    graph = worker.installed_graph(plan, prefix / 'bin/python', plan['env'])
    check(graph == load(folder / 'candidate-graph.json') and len(graph) > 1, 'candidate/fresh full graph mismatch')
    check((folder / 'constraints.txt').stat().st_size > 0, 'full candidate constraints missing')
    candidate_join = installed_join(folder / 'candidate-tools/amplifier-unified', config['payload'])
    fresh_join = installed_join(prefix, config['payload'])
    retained = json.loads(execute(prefix / 'bin/python', '-I', '-c', SENTINEL, config['home'],
                                  config['sentinel']['sessionId'], env=plan['env']).stdout)
    check(retained == config['sentinel'], 'actual saved messages/draft changed')
    # Deliberate SETTLED exact retry, not an automatic retry of lost acknowledgement.
    # Different API invocation/no generic command id exercises the repair mapping.
    stable = {'prefix': worker._path_snapshot(prefix),
              'launcher': worker.file_identity(Path(config['binRoot']) / 'amplifier-unified'),
              'operation': digest(folder / 'operation.json'), 'helper': settled,
              'folders': sorted(path.name for path in repair_root.iterdir() if path.is_dir())}
    repeated = action(config, 'maintenance.installationRepairConfirm', confirmation)
    by_preview = action(config, 'maintenance.installationRepairStatus', {'previewId': preview['previewId']})
    by_operation = action(config, 'maintenance.installationRepairStatus', {'operationId': identity})
    check(repeated == receipt and by_preview['operation'] == receipt and by_operation['operation'] == receipt,
          'settled exact retry/status did not return canonical operation')
    time.sleep(.6)
    after = {'prefix': worker._path_snapshot(prefix),
             'launcher': worker.file_identity(Path(config['binRoot']) / 'amplifier-unified'),
             'operation': digest(folder / 'operation.json'),
             'helper': native.owned_helper_observation(helper[0], env),
             'folders': sorted(path.name for path in repair_root.iterdir() if path.is_dir())}
    check(stable == after and capture_service(root, env) == new_pid
          and health_identities(request(config, '/api/health'), config) == (new_host, new_instance)
          and dispatches(root, helper[0]['label'], old_pid) == audit,
          'exact retry/status changed installation/helper/service identity')
    save(evidence / 'result.json', {'passed': True, 'operationId': identity, 'oldPID': old_pid, 'newPID': new_pid,
         'oldInstanceId': old_instance, 'newInstanceId': new_instance,
         'oldHostInstanceId': old_host, 'newHostInstanceId': new_host,
         'doctorVerified': True, 'healthVerified': True, 'protocol': 1, 'oldPrefixRetained': True,
         'sentinel': retained, 'candidateInstalledJoin': candidate_join, 'freshInstalledJoin': fresh_join,
         'candidateFreshGraphEqual': True, 'graphPackages': len(graph),
         'resolvedGraph': [{'name': row['name'], 'version': row['version'],
                            'gitRevision': (row.get('direct') or {}).get('vcs_info', {}).get('commit_id')}
                           for row in graph],
         'graphSHA256': digest(folder / 'candidate-graph.json'), 'exactRetryStatusUnchanged': True,
         'observedManagerDispatchAttempts': len(audit), 'limits': LIMITS})


def cleanup(root, evidence):
    if not (root / 'owner.json').exists():
        return
    owner = load(root / 'owner.json')
    if not owner['definition'] and not owner['helpers']:
        return  # Never inspect/remove a preexisting definition refused by prepare.
    check(not owner['cleanupStarted'], 'cleanup already attempted; inspect saved result, do not replay unknown input')
    owner['cleanupStarted'] = True
    save(root / 'owner.json', owner)
    results = {'passed': False, 'helpers': [], 'definitionAbsent': False, 'processesExited': False}
    save(evidence / 'cleanup.json', results)
    env = native.manager_environment()
    native.manager_capability(env)
    for helper in owner['helpers']:
        observed = native.wait_for(lambda: observe_helper(root, helper, env),
                                   lambda row: row['settled'], seconds=1800, description='owned full helper settlement')
        if sys.platform == 'darwin':
            for row in observed['domains']:
                check(native.owned_helper_observation(helper, env)['settled'], 'helper became active')
                execute('launchctl', 'bootout', row['domain'] + '/' + helper['label'], env=env, check_result=False)
            native.wait_for(lambda: not native.launch_job(helper['label'], env), description='full helper absence')
        else:
            if observed['LoadState'] != 'not-found':
                execute('systemctl', '--user', 'reset-failed', helper['label'] + '.service', env=env, check_result=False)
            native.wait_for(lambda: native.properties(helper['label'] + '.service', env).get('LoadState') == 'not-found',
                            description='full helper collected')
        results['helpers'].append({'label': helper['label'], 'settled': observed, 'absent': True})
        save(evidence / 'cleanup.json', results)
    if owner['definition']:
        # Also discover a genuine successor when the run failed before its readback.
        capture_service(root, env)
        owner = load(root / 'owner.json')
        pids = [row['pid'] for row in owner['servicePIDs']]
        path = native.owned_definition(owner['definition'], env, pids)
        reset = native.module(SOURCE / 'amplifier_web/reset.py')
        execute(*reset.service_commands(Path(owner['definition']['home']))[0], env=env, check_result=False)
        if sys.platform == 'linux':
            native.wait_for(lambda: native.properties(native.UNIT, env).get('MainPID') == '0', description='full host stopped')
        else:
            native.wait_for(lambda: not native.launch_job(native.LABEL, env), description='full host bootout')
        native.owned_definition(owner['definition'], env, pids)
        path.unlink()
        if sys.platform == 'linux':
            execute('systemctl', '--user', 'daemon-reload', env=env)
            execute('systemctl', '--user', 'reset-failed', native.UNIT, env=env, check_result=False)
            native.wait_for(lambda: native.properties(native.UNIT, env).get('LoadState') == 'not-found', description='full unit absence')
        else:
            check(not native.launch_job(native.LABEL, env), 'full label remains')
    owner = load(root / 'owner.json')
    native.wait_for(lambda: all(not native.pid_start(row['pid'], env)
                               for row in owner['servicePIDs'] + owner['helperPIDs']),
                    description='full host/helper PIDs exited')
    results.update(passed=True, definitionAbsent=not native.paths().exists(), processesExited=True,
                   servicePIDs=owner['servicePIDs'], helperPIDs=owner['helperPIDs'])
    check(results['definitionAbsent'], 'full definition remains')
    save(evidence / 'cleanup.json', results)
    # No broad deletion of prefixes, private plans, keys, logs or source.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run', 'cleanup'))
    args = parser.parse_args()
    os.umask(0o077)
    root, evidence = gate(args.action)
    try:
        # The adapter's sole custody write occurs during synchronous confirm
        # acquisition, while this process does not write owner.json.
        if root.exists():
            with controller_lease(root):
                {'run': run, 'cleanup': cleanup, 'prepare': prepare}[args.action](root, evidence)
        else:
            {'prepare': prepare, 'cleanup': cleanup, 'run': run}[args.action](root, evidence)
    except Exception as error:
        if evidence.is_dir():
            # Exception text/tracebacks can contain tokens, argv or private plans.
            save(evidence / (args.action + '-failure.json'), {'passed': False, 'errorType': type(error).__name__,
                 'detail': str(error) if isinstance(error, RuntimeError) else 'private fixture retained for inspection'})
        raise SystemExit('Full-app native fixture failed; inspect sanitized evidence and retained private state.') from None


if __name__ == '__main__':
    main()