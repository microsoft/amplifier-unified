"""Focused managed-runner package proof; no network, Git metadata or host install.

Uses the existing browser runner and the managed multi-quote fresh-venv pattern.
Run after the bundled frontend build. Prepared dependencies remain read-only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from installed_candidate_wheels import load_candidates, regular, require, sha

ROOT = Path(__file__).resolve().parents[2]
OUT = Path('/artifacts/composer-installed')
sys.path.insert(0, str(ROOT / 'scripts'))
from browser_check_runner import run_scenarios


def digest(data):
    return hashlib.sha256(data).hexdigest()


def manifest(root):
    result = {}
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(name for name in folders if name not in {'node_modules', '__pycache__'})
        for name in sorted(files):
            path = Path(directory) / name
            result[str(path.relative_to(root))] = digest(path.read_bytes())
    return result


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + '\n')


def command(name, args, *, cwd=ROOT, env=None):
    with (OUT / (name + '.log')).open('w') as stream:
        result = subprocess.run(args, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=180)
    if result.returncode:
        raise RuntimeError(f'{name}: exit {result.returncode}')


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--header-only', action='store_true',
                        help='Fixed header-occlusion regression plus served-asset parity.')
    modes.add_argument('--markdown-only', action='store_true',
                        help='Markdown/GFM input and quote journeys plus served-asset parity.')
    modes.add_argument('--optimize-only', action='store_true',
                       help='Only real-host Optimize; no provider/CLI/campaign acceptance.')
    parser.add_argument('--canonical-format', choices=('legacy', 'committed'), default='legacy',
                        help='Optimize fixture source format; committed requires actual T2 inventory evidence.')
    parser.add_argument('--candidate-wheel-manifest', type=Path,
                        help='Optimize only: bounded offline Foundation+CI wheel selections.')
    parser.add_argument('--installed-runtime-profile', type=Path,
                        help='Optimize only: reuse an independently artifact-derived installed runtime; no build/install.')
    parser.add_argument('--installed-runtime-profile-sha256')
    parser.add_argument('--server-provenance', type=Path,
                        help='Optimize only: gitless manager packet (or existing environment ABI).')
    parser.add_argument('--server-provenance-sha256')
    parser.add_argument('--expected-client-commit',
                        help='Optimize only: independently selected accepted client revision (or existing environment ABI).')
    args = parser.parse_args(argv)
    if not args.optimize_only and any((args.candidate_wheel_manifest, args.server_provenance,
                                       args.server_provenance_sha256, args.expected_client_commit,
                                       args.canonical_format != 'legacy', args.installed_runtime_profile,
                                       args.installed_runtime_profile_sha256)):
        parser.error('Candidate wheels and server provenance are only for --optimize-only')
    if bool(args.installed_runtime_profile) != bool(args.installed_runtime_profile_sha256):
        parser.error('Installed runtime profile and SHA-256 must be supplied together')
    if args.installed_runtime_profile and args.candidate_wheel_manifest:
        parser.error('Reuse an installed runtime or install candidate wheels, not both')
    return args


def selected_scenarios(args):
    return (['optimize-storage'] if args.optimize_only else
            ['markdown-composer', 'multi-quote', 'compact-composer'] if args.markdown_only else
            ['composer-header', 'compact-composer'] if args.header_only else
            ['composer-fidelity', 'composer-options', 'compact-composer', 'composer-primary-action', 'message-delivery'])


def provenance_packet(args):
    path = args.server_provenance or os.environ.get('AMPLIFIER_TEST_SERVER_PROVENANCE')
    expected = args.server_provenance_sha256 or os.environ.get('AMPLIFIER_TEST_SERVER_PROVENANCE_SHA256')
    require(path and expected, 'Optimize requires a gitless manager provenance packet and its SHA-256')
    path = regular(path)
    require(path.stat().st_size <= 32768, 'Server provenance packet exceeds bound')
    data = path.read_bytes()
    require(sha(data) == expected, 'Server provenance SHA-256 mismatch')
    packet = json.loads(data)
    import re
    require(re.fullmatch('[0-9a-f]{40}', packet.get('serverCommit', '')) is not None,
            'Server provenance must pin an immutable commit')
    for key, relative in [('fixtureSha256', 'tests/fixtures/optimize_storage_ui_server.py'),
                          ('journeySha256', 'tests/web/optimize-storage-browser.mjs')]:
        require(packet.get(key) == sha(regular(ROOT / relative).read_bytes()),
                'Server provenance does not bind the reviewed ' + key)
    return data, expected


def accepted_client(args):
    import re
    expected = args.expected_client_commit or os.environ.get('AMPLIFIER_OPTIMIZE_EXPECTED_CLIENT_COMMIT', '')
    require(re.fullmatch('[0-9a-f]{40}', expected) is not None, 'Optimize requires an explicit accepted client commit')
    selected = json.loads(regular(ROOT / 'components/web-client.source.json').read_text())
    require(selected['source']['revision'] == expected, 'Selected client differs from accepted client commit')
    return expected


def candidate_install_args(python, candidates):
    return ['uv', 'pip', 'install', '--python', str(python), '--no-index', '--no-deps', '--reinstall',
            *[row['wheelPath'] for row in candidates['candidates']]]


def installed_runtime_selection(path, expected):
    """Validate manager selection without importing or executing candidate code."""
    import re
    require(re.fullmatch('[a-f0-9]{64}', expected or '') is not None, 'Installed profile needs SHA-256')
    path = regular(path)
    require(path.stat().st_size <= 8 * 1024 * 1024, 'Installed profile exceeds bound')
    raw = path.read_bytes()
    require(sha(raw) == expected, 'Installed profile SHA-256 mismatch')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'Duplicate installed profile member')
            value[key] = item
        return value
    data = json.loads(raw, object_pairs_hook=unique)
    require(type(data) is dict, 'Invalid installed runtime profile')
    values = [data.get(key) for key in ('environment_root', 'python', 'app_root')]
    require(all(type(value) is str and Path(value).is_absolute() and '..' not in Path(value).parts
                for value in values), 'Installed runtime paths must be explicit')
    environment, python, package = map(Path, values)
    require(environment.resolve(strict=True) == environment
            and not environment.is_relative_to(ROOT.resolve())
            and not ROOT.resolve().is_relative_to(environment)
            and (environment / 'pyvenv.cfg').is_file(), 'Installed runtime must be outside checkout')
    require(python == environment / 'bin/python' and python.is_file() and os.access(python, os.X_OK),
            'Select the installed qualification interpreter')
    require(package.resolve(strict=True) == package and package.name == 'amplifier_web'
            and package.is_relative_to(environment), 'Installed application escaped runtime')
    return path, raw, environment, python, package


def existing_runtime_identity(args):
    """Reuse the actual artifact profile and maintained installed-origin guard."""
    path, raw, venv, python, package = installed_runtime_selection(
        args.installed_runtime_profile, args.installed_runtime_profile_sha256)
    require(not OUT.resolve().is_relative_to(venv), 'Evidence output cannot modify installed runtime')
    import tomllib
    profile_files = json.loads(raw).get('files')
    require(type(profile_files) is dict, 'Installed runtime file manifest is missing')
    prefix = str(package.relative_to(venv)) + '/'
    files = {'amplifier_web/' + relative[len(prefix):]: expected
             for relative, expected in profile_files.items()
             if type(relative) is str and relative.startswith(prefix)}
    require(files and all(name in files for name in (
        'amplifier_web/runtime_worker.py', 'amplifier_web/runtime_bootstrap.py',
        'amplifier_web/host/media_sources.json', 'amplifier_web/host/children.py',
        'amplifier_web/storage_optimize_canonical.py')), 'Installed application manifest is incomplete')
    force = tomllib.loads((ROOT / 'pyproject.toml').read_text())['tool']['hatch']['build']['targets']['wheel']['force-include']
    for name, expected in files.items():
        source = ROOT / name
        for source_name, target in sorted(force.items(), key=lambda item: len(item[1]), reverse=True):
            if name == target or name.startswith(target + '/'):
                source = ROOT / source_name / name[len(target):].lstrip('/') if name != target else ROOT / source_name
                break
        require(source.resolve().is_relative_to(ROOT.resolve()) and source.is_file()
                and sha(source.read_bytes()) == expected,
                'Installed application differs from selected source: ' + name)
    saved = OUT / 'installed-runtime-profile.json'
    saved.write_bytes(raw)
    probe = '''import sys,json,hashlib
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import normal_media_worker_probe as helper
target=helper.InstalledTarget(sys.argv[2])
assert hashlib.sha256(target.raw).hexdigest()==sys.argv[3]
target.check_runtime()
# Nonimporting validation of ALL profile files precedes app bootstrap/imports.
for relative in target.files:target.verify_file(target.environment/relative)
target.bootstrap_app()
helper.INSTALLED_TARGET=target
origins=target.check_origins()
catalog=helper.catalog_status()
assert not catalog['mismatches']
print(json.dumps(dict(verified=True,origins=origins,catalog=catalog,
                     files=target.files,environment=str(target.environment),
                     packageRoot=str(target.app))))
'''
    env = {key: value for key, value in os.environ.items()
           if key not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE', 'WARM_LOOP_SOURCE')}
    command('existing-runtime-identity', [str(python), '-I', '-B', '-c', probe,
            str(ROOT / 'tests/fixtures'), str(saved), args.installed_runtime_profile_sha256],
            cwd=OUT, env=env)
    verified = json.loads((OUT / 'existing-runtime-identity.log').read_text())
    require(verified.get('verified') is True and verified['packageRoot'] == str(package)
            and verified['environment'] == str(venv), 'Installed runtime identity differs')
    require(verified['files'] == profile_files, 'Installed file manifest changed during verification')
    static = {p.removeprefix('amplifier_web/static/'): value for p, value in files.items()
              if p.startswith('amplifier_web/static/')}
    require(static == manifest(ROOT / 'amplifier_web/static'), 'Installed static differs from selected build')
    identity = {'installedProfileSha256': sha(raw), 'files': files, 'static': static}
    write('wheel-identity.json', identity)  # Existing consumer filename; no invented wheel hash.
    return venv, python, str(package), static, identity, verified


def optimize_proof(output, static, package, candidates, packet_sha, *, canonical_format='legacy'):
    paths = list(output.glob('optimize-storage-browser-*/result.json'))
    require(len(paths) == 1, 'Require exactly one actual Optimize browser receipt')
    proof = json.loads(paths[0].read_text())
    require(proof.get('passed') is True and proof.get('browserExecuted') is True
            and proof.get('acceptanceComplete') is True and proof.get('notCovered') == []
            and not proof.get('cleanupFailure') and not proof.get('failure'),
            'Optimize browser acceptance is incomplete')
    provenance = proof['provenance']
    require(provenance.get('provenancePacketSha256') == packet_sha, 'Browser used different provenance')
    require(provenance.get('componentSha256') == static.get('client-component.json'),
            'Browser component manifest differs from installed wheel')
    served = proof.get('servedScripts')
    require(served and all(row['status'] == 200 and static.get(row['path'].lstrip('/')) == row['sha256']
                           for row in served), 'Positive installed served-byte join missing')
    require(candidates.get('verified') is True, 'Installed component identities are missing')
    if canonical_format == 'committed':
        row = proof.get('committedCanonicalHold', {})
        require(provenance.get('canonicalFormat') == 'committed'
                and row.get('owner') == 'foundation' and row.get('status') == 'held'
                and type(row.get('canonicalRecords')) is int and row['canonicalRecords'] == 1
                and type(row.get('canonicalInlineImages')) is int and row['canonicalInlineImages'] == 1
                and type(row.get('bytes')) is int and row['bytes'] > 0
                and type(row.get('sessionId')) is str and bool(row['sessionId'])
                and type(row.get('reason')) is str
                and 'Original image provenance and retention admission are unavailable' in row['reason'],
                'Committed Optimize requires an actual checked canonical held outcome')
    return {'receipt': str(paths[0]), 'packageRoot': package, 'servedScripts': served,
            'installedComponents': candidates, 'browserReceiptSha256': sha(paths[0].read_bytes())}


def main(argv=None):
    args = parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=False)
    result = {'status': 'running', 'limits': ['Read-only locked development dependencies, not minimal production dependency install', 'Synthetic runtime, no real provider/physical IME/audio']}
    if args.optimize_only:
        result['scope'] = 'Optimize-only neutral component/browser qualification; not campaign/provider/CLI acceptance'
    write('result.json', result)
    try:
        contract = json.loads(Path(os.environ['AMPLIFIER_TEST_CONTRACT']).read_text())
        assert contract['profileId'] == 'browser-test'
        write('execution-contract.json', contract)
        candidates = {'manifestSha256': None, 'candidates': []}
        if args.optimize_only:
            require(not OUT.resolve().is_relative_to('/workspace'), 'Optimize qualification must run outside /workspace')
            client_commit = accepted_client(args)
            packet, packet_sha = provenance_packet(args)
            result['acceptedClientCommit'] = client_commit
            (OUT / 'server-provenance.json').write_bytes(packet)
            if args.candidate_wheel_manifest:
                candidates = load_candidates(args.candidate_wheel_manifest)
                manifest_bytes = regular(args.candidate_wheel_manifest).read_bytes()
                require(sha(manifest_bytes) == candidates['manifestSha256'], 'Candidate manifest changed during staging')
                (OUT / 'candidate-wheel-manifest.json').write_bytes(manifest_bytes)
                # Freeze exact reviewed inputs in owned output before installation.
                (OUT / 'candidate-wheels').mkdir()
                for row in candidates['candidates']:
                    destination = OUT / 'candidate-wheels' / Path(row['wheelPath']).name
                    shutil.copyfile(regular(row['wheelPath']), destination)
                    require(sha(destination.read_bytes()) == row['sha256'], 'Candidate changed during staging')
                    row['wheelPath'] = str(destination)
            write('candidate-wheel-identity.json', candidates)
        # Hash actual copied inputs. Managed source has no Git metadata.
        inputs = {}
        for folder in ['.build/web-client/src', '.build/web-client/tests', 'tests/web', 'tests/fixtures']:
            inputs.update({folder + '/' + p: value for p, value in manifest(ROOT / folder).items()})
        for file in ['pyproject.toml', 'uv.lock', 'tests/web/package.json', 'tests/web/package-lock.json', '.build/web-client/package.json', '.build/web-client/package-lock.json', '.build/web-client/vite.config.js']:
            inputs[file] = digest((ROOT / file).read_bytes())
        if args.optimize_only:
            inputs['components/web-client.source.json'] = sha(regular(ROOT / 'components/web-client.source.json').read_bytes())
        source = {'inputs': inputs, 'static': manifest(ROOT / 'amplifier_web/static')}
        write('source.json', source)
        env = {key: value for key, value in os.environ.items() if key not in ('PYTHONPATH', 'PYTHONHOME')}
        if args.installed_runtime_profile:
            venv, python, package, static, identity, component_identity = existing_runtime_identity(args)
            result['limits'][0] = 'Explicit artifact-profile installed runtime reused without build/install or prepared-dependency fallback'
            result['installedRuntimeProfileSha256'] = identity['installedProfileSha256']
        else:
            command('wheel-build', ['/opt/unified-prepared/build-venv/bin/python', '-m', 'build', '--wheel', '--no-isolation', '--outdir', str(OUT / 'wheel'), str(ROOT)])
            wheel, = (OUT / 'wheel').glob('*.whl')
            venv = OUT / 'venv'
            command('venv', ['uv', 'venv', '--python', sys.executable, str(venv)])
            site = venv / 'lib' / ('python' + '.'.join(map(str, sys.version_info[:2]))) / 'site-packages'
            (site / 'prepared-dependencies.pth').write_text(sysconfig.get_path('purelib') + '\n')
            python = venv / 'bin/python'
            install_env = ({**os.environ, 'UV_COMPILE_BYTECODE': '0', 'UV_LINK_MODE': 'copy'}
                           if args.optimize_only else None)
            if args.optimize_only and candidates['candidates']:
                # No resolver, moving dependency, network or shared-layer mutation.
                command('install-components', candidate_install_args(python, candidates), env=install_env)
            command('install', ['uv', 'pip', 'install', '--python', str(python), '--no-deps',
                    *(['--no-index'] if args.optimize_only else []),
                    '--reinstall-package', 'amplifier-unified', str(wheel)], env=install_env)
            expected = {}
            with zipfile.ZipFile(wheel) as archive:
                for name in archive.namelist():
                    if name.startswith('amplifier_web/') and not name.endswith('/'):
                        expected[name] = digest(archive.read(name))
            static = {p.removeprefix('amplifier_web/static/'): value for p, value in expected.items() if p.startswith('amplifier_web/static/')}
            assert static == source['static'], 'Wheel differs from built static'
            identity = {'wheelSha256': digest(wheel.read_bytes()), 'files': expected, 'static': static}
            write('wheel-identity.json', identity)
            if args.optimize_only:
                # Validate ALL selected distributions before any candidate imports.
                shutil.copy2(Path(__file__).with_name('installed_candidate_wheels.py'), OUT / 'installed_candidate_wheels.py')
                command('component-identity', [str(python), '-I', '-B', str(OUT / 'installed_candidate_wheels.py'),
                        '--installed-probe', str(OUT / 'candidate-wheel-identity.json'), str(venv)], cwd=OUT, env=env)
                component_identity = json.loads((OUT / 'component-identity.log').read_text())
            probe = '''import sys,json,hashlib
from pathlib import Path
import amplifier_core._engine,amplifier_foundation,aiohttp,amplifier_web.server
root=Path(amplifier_web.__file__).resolve().parent
expected=json.loads(Path(sys.argv[1]).read_text())
assert root.is_relative_to(Path(sys.argv[2])),str(root)
assert not any(Path(p or '.').resolve().is_relative_to(Path('/workspace')) for p in sys.path)
assert all(hashlib.sha256((root.parent/p).read_bytes()).hexdigest()==h for p,h in expected['files'].items())
print(json.dumps({'packageRoot':str(root),'verified':True,'fileCount':len(expected['files']),'wheelSha256':expected['wheelSha256']}))
'''
            command('identity', [str(python), '-I', *(['-B'] if args.optimize_only else []),
                    '-c', probe, str(OUT / 'wheel-identity.json'), str(venv)], cwd=OUT, env=env)
            package = json.loads((OUT / 'identity.log').read_text())['packageRoot']
        harness = OUT / 'harness'
        shutil.copytree(ROOT / 'tests/fixtures', harness / 'tests/fixtures')
        shutil.copytree(ROOT / 'tests/web', harness / 'tests/web', ignore=shutil.ignore_patterns('node_modules', '__pycache__'))
        for folder in ['src', 'tests']:
            shutil.copytree(ROOT / '.build/web-client' / folder, harness / '.build/web-client' / folder,
                            ignore=shutil.ignore_patterns('node_modules', '__pycache__'))
        for name in ['package.json', 'package-lock.json']:
            shutil.copy2(ROOT / '.build/web-client' / name, harness / '.build/web-client' / name)
        (harness / '.build/web-client/node_modules').symlink_to(ROOT / '.build/web-client/node_modules', target_is_directory=True)
        (harness / 'tests/web/node_modules').symlink_to(ROOT / 'tests/web/node_modules', target_is_directory=True)
        (harness / '.venv').symlink_to(venv, target_is_directory=True)
        if args.optimize_only:
            # The journey reads committed selection and the actually installed
            # static component manifest, never .git or checkout authoring bytes.
            (harness / 'components').mkdir()
            shutil.copy2(ROOT / 'components/web-client.source.json', harness / 'components/web-client.source.json')
            (harness / 'amplifier_web').mkdir()
            (harness / 'amplifier_web/static').symlink_to(Path(package) / 'static', target_is_directory=True)
        scenarios = []
        selected = selected_scenarios(args)
        result['selection'] = selected
        for name in selected:
            output = OUT / name
            scenario_env = {'AMPLIFIER_TEST_PYTHON': str(python), 'AMPLIFIER_TEST_PACKAGE_ROOT': package,
                            'PYTHONPATH': '', 'AMPLIFIER_TEST_ARTIFACTS': str(output), 'AMPLIFIER_TEST_OUTPUT': str(output)}
            if args.optimize_only:
                scenario_env.update(AMPLIFIER_TEST_INSTALLED_PACKAGE=package,
                    PYTHONHOME='',
                    AMPLIFIER_OPTIMIZE_FIXTURE_CANONICAL_FORMAT=args.canonical_format,
                    AMPLIFIER_TEST_SERVER_PROVENANCE=str(OUT / 'server-provenance.json'),
                    AMPLIFIER_TEST_SERVER_PROVENANCE_SHA256=packet_sha,
                    AMPLIFIER_OPTIMIZE_EXPECTED_CLIENT_COMMIT=client_commit)
            scenarios.append({'id': name, 'command': ['node', f'tests/web/{name}-browser.mjs'], 'env': scenario_env})
        plan = {'schemaVersion': 1, 'scenarios': scenarios}
        code = run_scenarios(harness, plan, OUT / 'browser', budget=300,
                             case_timeout=240 if args.optimize_only else 90,
                             provenance={'inputs': inputs, 'wheel': identity.get('wheelSha256'),
                                         'installedProfile': identity.get('installedProfileSha256')})
        result.update(status='passed' if code == 0 else 'failed', browserExit=code, packageRoot=package, wheelSha256=identity.get('wheelSha256'), staticCount=len(static))
        if code == 0 and args.optimize_only:
            result['optimize'] = optimize_proof(OUT / 'optimize-storage', static, package,
                                               component_identity, packet_sha,
                                               canonical_format=args.canonical_format)
        elif code == 0:
            proof = json.loads((OUT / 'compact-composer/compact-composer.json').read_text())
            served = proof.get('installedServedAssets')
            assert served and all(static.get(name) == value for name, value in served.items()), 'Positive served-byte proof missing'
            result['servedAssets'] = served
        write('result.json', result)
        return code
    except Exception as error:
        result.update(status='error', error=str(error)); write('result.json', result); raise


if __name__ == '__main__':
    raise SystemExit(main())
