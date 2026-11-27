"""Explicit installed CLI/native status and wheel ownership acceptance owner.

Run only in the named owned DTU. Existing source/CLI interpreters are read-only;
consumer/package environments and synthetic state are created beneath NEW work.
Missing prerequisites fail, never skip. This is not an ordinary pytest lane.
"""
from __future__ import annotations

import argparse
import email
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import socket
import subprocess
import sys
import tomllib
import urllib.request
import zipfile

from integration_checks import ROOT, source_identity


def wheel_identity(path, expected):
    path = Path(path).resolve(strict=True)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        metadata = [name for name in names if name.endswith('.dist-info/METADATA')]
        if len(metadata) != 1:
            raise ValueError('Expected one wheel METADATA: ' + str(path))
        fields = email.message_from_bytes(archive.read(metadata[0]))
        if fields['Name'].lower().replace('_', '-') != expected:
            raise ValueError('Wrong wheel distribution: ' + str(path))
        return {'path': str(path), 'name': expected, 'version': fields['Version'],
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def published_core(identity):
    url = 'https://pypi.org/pypi/amplifier-core/' + identity['version'] + '/json'
    with urllib.request.urlopen(url, timeout=30) as response:
        metadata = json.load(response)
    if not any(row['filename'] == Path(identity['path']).name
               and row['digests']['sha256'] == identity['sha256']
               and row['packagetype'] == 'bdist_wheel' for row in metadata['urls']):
        raise ValueError('Core wheel does not match a published registry wheel')
    return {'url': url, 'wheel': identity}


def wheel_package_prefixes(project):
    """Map the supported literal Hatch package roots to installed prefixes."""
    build = project['tool']['hatch']['build']
    wheel = build['targets']['wheel']
    # As in qualify_default_installed.verify_candidate_wheel, do not silently
    # approximate Hatch's configurable source rewriting or selection language.
    if any({'sources', 'only-include', 'include'} & config.keys() for config in (build, wheel)):
        raise ValueError('Review unsupported wheel source selection')
    packages = wheel.get('packages', build.get('packages'))
    if not isinstance(packages, list) or not packages:
        raise ValueError('Expected explicit wheel packages')
    paths = []
    for package in packages:
        if (not isinstance(package, str) or not package
                or any(char in package for char in '*?[]!\\')
                or PurePosixPath(package).is_absolute()
                or '..' in PurePosixPath(package).parts
                or PurePosixPath(package).as_posix() != package or package == '.'):
            raise ValueError('Review unsupported wheel package path: ' + repr(package))
        paths.append(PurePosixPath(package))
    # Hatch generates a source rewrite for each package's parent. Overlapping
    # parents or a rewrite inside another package need more than basename mapping.
    parents = {path.parent for path in paths if path.parent != PurePosixPath('.')}
    if (any(a != b and a.is_relative_to(b) for a in parents for b in parents)
            or any(parent.is_relative_to(path) for parent in parents for path in paths)):
        raise ValueError('Review unsupported overlapping wheel package roots')
    if len({path.name for path in paths}) != len(paths):
        raise ValueError('Ambiguous wheel package destination')
    return {package: path.name for package, path in zip(packages, paths)}


def matching_source(wheel, prefix, source, force_include=None):
    count = 0
    packaged = set()
    # The same author directory now carries installed-only standalone tests.
    # These exact test roots belong in source archives, never either wheel.
    tests = source/'tests' if prefix == 'amplifier_status' else None
    with zipfile.ZipFile(wheel) as archive:
        for item in archive.infolist():
            if item.is_dir() or not item.filename.startswith(prefix+'/'):
                continue
            path = source / item.filename[len(prefix)+1:]
            for target, original in sorted((force_include or {}).items(), key=lambda row: -len(row[0])):
                if item.filename == target or item.filename.startswith(target+'/'):
                    path = original / item.filename[len(target)+1:] if item.filename != target else original
                    break
            if not path.is_file() or path.read_bytes() != archive.read(item):
                raise ValueError('Candidate wheel differs from reviewed source: '+item.filename)
            if tests is not None and path.is_relative_to(tests):
                raise ValueError('Candidate wheel includes standalone tests: '+item.filename)
            if path.is_relative_to(source):
                packaged.add(path.relative_to(source).as_posix())
            count += 1
    if not count:
        raise ValueError('Candidate wheel has no expected package files')
    missing = {p.relative_to(source).as_posix() for p in source.rglob('*.py')
               if '__pycache__' not in p.parts and (tests is None or not p.is_relative_to(tests))} - packaged
    if missing:
        raise ValueError('Candidate wheel omits reviewed Python files: '+', '.join(sorted(missing)))
    return count


PREFLIGHT = r'''
import hashlib, importlib, importlib.metadata as m, importlib.util, json, pathlib, sys, zipfile
kind, core_path, status_path = sys.argv[1:]
def compare(name, wheel, prefix):
    dist=m.distribution(name)
    direct=json.loads(dist.read_text('direct_url.json') or 'null')
    if direct and direct.get('dir_info',{}).get('editable'):
        raise RuntimeError(name+' must not be editable')
    count=0; native=[]
    with zipfile.ZipFile(wheel) as archive:
        for item in archive.infolist():
            if item.is_dir() or not item.filename.startswith(prefix+'/'): continue
            path=pathlib.Path(dist.locate_file(item.filename))
            if not path.is_file() or path.read_bytes()!=archive.read(item):
                raise RuntimeError('Installed wheel bytes differ: '+str(path))
            count+=1
            if path.suffix in {'.so','.pyd'}: native.append(str(path))
    if not count: raise RuntimeError('Wheel has no package files: '+name)
    return {'version':dist.version,'direct':direct,'filesMatched':count,'native':native,
            'package':str(pathlib.Path(dist.locate_file(prefix)).resolve())}
core=compare('amplifier-core',core_path,'amplifier_core')
if not core['native']: raise RuntimeError('Published native Core extension is missing')
modules=['amplifier_core','amplifier_foundation','amplifier_module_loop_live','amplifier_module_context_simple']
if kind=='cli':
    if importlib.util.find_spec('amplifier_web') is not None:
        raise RuntimeError('CLI environment must be independent of Unified')
    modules += ['amplifier_app_cli.main','amplifier_status']
    status=compare('amplifier-status-emitter',status_path,'amplifier_status')
    cli=m.distribution('amplifier-app-cli')
    if json.loads(cli.read_text('direct_url.json') or '{}').get('dir_info',{}).get('editable'):
        raise RuntimeError('CLI must be installed noneditably')
else:
    if importlib.util.find_spec('amplifier_app_cli') is not None:
        raise RuntimeError('Unified environment must not include CLI')
    status=None
imports={name:importlib.import_module(name).__file__ for name in modules}
if pathlib.Path(imports['amplifier_core']).resolve().parent != pathlib.Path(core['package']):
    raise RuntimeError('Imported Core is shadowed by a different source')
if kind=='cli':
    if pathlib.Path(imports['amplifier_status']).resolve().parent != pathlib.Path(status['package']):
        raise RuntimeError('Imported status hook is shadowed by a different source')
    if pathlib.Path(imports['amplifier_app_cli.main']).resolve().parent != pathlib.Path(cli.locate_file('amplifier_app_cli')).resolve():
        raise RuntimeError('Imported CLI is shadowed by a different source')
packages=[{'name':dist.metadata['Name'],'version':dist.version,
           'direct':json.loads(dist.read_text('direct_url.json') or 'null')} for dist in m.distributions()]
print(json.dumps({'python':sys.version,'prefix':sys.prefix,'imports':imports,
                  'core':core,'status':status,'packages':packages}))
'''


def run_owned(command, *, cwd, env, log, timeout):
    """Own one process group through success, failure, timeout or cancellation."""
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    try:
        return process.wait(timeout=timeout)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owned-dtu', required=True)
    parser.add_argument('--python', required=True, help='Prepared Unified native integration interpreter')
    parser.add_argument('--cli-python', required=True, help='Independent installed CLI + candidate status wheel')
    parser.add_argument('--unified-wheel', type=Path, required=True)
    parser.add_argument('--status-wheel', type=Path, required=True)
    parser.add_argument('--core-wheel', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True, help='New disposable directory outside source')
    parser.add_argument('--uv', default='uv')
    parser.add_argument('--source-revision')
    parser.add_argument('--cli-sources', type=Path, required=True, help='Reviewed exact public CLI source-store inputs')
    parser.add_argument('--cli-sources-sha256', required=True)
    parser.add_argument('--host-sources', type=Path, required=True, help='Reviewed exact Unified capture source-store input')
    parser.add_argument('--host-sources-sha256', required=True)
    args = parser.parse_args(argv)
    if socket.gethostname() != args.owned_dtu:
        raise ValueError('Run inside the explicitly named owned DTU')
    if not sys.platform.startswith('linux'):
        raise ValueError('This owner requires the Linux DTU; native desktop testing is separate')
    work = args.work.resolve()
    if work == ROOT or ROOT in work.parents or work in ROOT.parents:
        raise ValueError('Work must be separate from source')
    # The fixture's input validator imports only the standard library. Reject
    # missing/mismatched inputs before publication lookup or environment effects.
    spec = importlib.util.spec_from_file_location(
        'status_emitter_probe', Path(__file__).resolve().parents[1]/'tests/fixtures/status_emitter_probe.py')
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    inputs = probe.read_cli_sources(args.cli_sources, args.cli_sources_sha256)
    host_inputs = probe.read_host_sources(args.host_sources, args.host_sources_sha256)
    probe.separate_source_work(work, inputs, host_inputs)
    work.mkdir(parents=True, exist_ok=False)
    env = {key: value for key, value in os.environ.items()
           if key in {'PATH', 'LANG', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}}
    for name in ('home', 'tmp', 'cache'):
        (work / name).mkdir()
    env.update(HOME=str(work/'home'), TMPDIR=str(work/'tmp'), UV_CACHE_DIR=str(work/'cache'),
               UV_NO_BUILD_PACKAGE='amplifier-core', PYTHONDONTWRITEBYTECODE='1')
    receipt = {'passed': False, 'lane': 'status-installed', 'host': socket.gethostname(),
               'work': str(work), 'commands': []}

    def run(name, command, timeout=180):
        entry = {'name': name, 'argv': list(map(str, command)), 'exitCode': None}
        receipt['commands'].append(entry)
        with (work/(name+'.log')).open('w') as log:
            entry['exitCode'] = run_owned(entry['argv'], cwd=work, env=env, log=log, timeout=timeout)
        if entry['exitCode']:
            raise ValueError(name+' failed; inspect '+str(work/(name+'.log')))

    try:
        receipt['source'] = source_identity(args.source_revision)
        wheels = {name: wheel_identity(path, name) for name, path in (
            ('amplifier-unified', args.unified_wheel),
            ('amplifier-status-emitter', args.status_wheel), ('amplifier-core', args.core_wheel))}
        receipt['wheels'] = wheels
        project = tomllib.loads((ROOT/'pyproject.toml').read_text())
        packages = wheel_package_prefixes(project)
        receipt['wheelPackagePrefixes'] = packages
        force_include = {target: ROOT/original for original, target in
                         project['tool']['hatch']['build']['targets']['wheel']['force-include'].items()}
        receipt['wheelSourceFiles'] = {
            'unified': {package: matching_source(wheels['amplifier-unified']['path'], prefix, ROOT/package, force_include)
                        for package, prefix in packages.items()},
            'status': matching_source(wheels['amplifier-status-emitter']['path'], 'amplifier_status', ROOT/'amplifier_web/status_protocol')}
        minimum = next(item.removeprefix('amplifier-core>=') for item in
                       project['project']['dependencies']
                       if item.startswith('amplifier-core>='))
        if tuple(map(int, wheels['amplifier-core']['version'].split('.'))) < tuple(map(int, minimum.split('.'))):
            raise ValueError('Core is older than the candidate dependency minimum')
        receipt['corePublication'] = published_core(wheels['amplifier-core'])
        for kind, python in (('host', args.python), ('cli', args.cli_python)):
            run(kind+'-preflight', [python, '-I', '-c', PREFLIGHT, kind,
                wheels['amplifier-core']['path'], wheels['amplifier-status-emitter']['path']])
            receipt[kind] = json.loads((work/(kind+'-preflight.log')).read_text())
        for kind in ('consumer', 'package'):
            directory = work/(kind+'-env')
            run(kind+'-create', [args.uv, 'venv', '--python', args.python, str(directory)])
            python = directory/'bin/python'
            selected = [wheels['amplifier-status-emitter']['path']]
            if kind == 'package': selected.append(wheels['amplifier-unified']['path'])
            run(kind+'-install', [args.uv, 'pip', 'install', '--python', str(python), '--no-deps', *selected])
        env.update(CLI_PYTHON=args.cli_python, CONSUMER_PYTHON=str(work/'consumer-env/bin/python'))
        native = [args.python, '-I', str(ROOT/'tests/fixtures/status_emitter_probe.py'), str(work/'native')]
        native += ['--cli-sources', str(args.cli_sources.resolve()),
                   '--cli-sources-sha256', args.cli_sources_sha256,
                   '--host-sources', str(args.host_sources.resolve()),
                   '--host-sources-sha256', args.host_sources_sha256]
        run('native', native, timeout=240)
        receipt['native'] = json.loads((work/'native/result.json').read_text())
        if receipt['native'].get('passed') is not True:
            raise ValueError('Native probe did not establish success')
        copied = work/'status_package_probe.py'
        shutil.copyfile(ROOT/'tests/fixtures/status_package_probe.py', copied)
        run('package', [str(work/'package-env/bin/python'), '-I', str(copied)])
        receipt['package'] = json.loads((work/'package.log').read_text().splitlines()[-1])
        if receipt['package'].get('passed') is not True:
            raise ValueError('Installed package probe did not establish success')
        receipt['sourceAfter'] = source_identity(args.source_revision)
        if receipt['source'] != receipt['sourceAfter']:
            raise ValueError('Qualification source changed during execution')
        receipt['passed'] = True
    except Exception as error:
        receipt['error'] = str(error)
    finally:
        (work/'result.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps({'passed': receipt['passed'], 'receipt': str(work/'result.json')}))
    return 0 if receipt['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
