"""Explicit installed CLI/native status and wheel ownership acceptance owner.

Run only in the named owned DTU. Existing source/CLI interpreters are read-only;
consumer/package environments and synthetic state are created beneath NEW work.
Missing prerequisites fail, never skip. This is not an ordinary pytest lane.
"""
from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
from pathlib import Path
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


def matching_source(wheel, prefix, source, force_include=None):
    count = 0
    packaged = set()
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
            if path.is_relative_to(source):
                packaged.add(path.relative_to(source).as_posix())
            count += 1
    if not count:
        raise ValueError('Candidate wheel has no expected package files')
    missing = {p.relative_to(source).as_posix() for p in source.rglob('*.py')
               if '__pycache__' not in p.parts} - packaged
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
    args = parser.parse_args(argv)
    if socket.gethostname() != args.owned_dtu:
        raise ValueError('Run inside the explicitly named owned DTU')
    if not sys.platform.startswith('linux'):
        raise ValueError('This owner requires the Linux DTU; native desktop testing is separate')
    work = args.work.resolve()
    if work == ROOT or ROOT in work.parents or work in ROOT.parents:
        raise ValueError('Work must be separate from source')
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
            process = subprocess.Popen(entry['argv'], cwd=work, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                entry['exitCode'] = process.wait(timeout=timeout)
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
        if entry['exitCode']:
            raise ValueError(name+' failed; inspect '+str(work/(name+'.log')))

    try:
        receipt['source'] = source_identity(args.source_revision)
        wheels = {name: wheel_identity(path, name) for name, path in (
            ('amplifier-unified', args.unified_wheel),
            ('amplifier-status-emitter', args.status_wheel), ('amplifier-core', args.core_wheel))}
        receipt['wheels'] = wheels
        project = tomllib.loads((ROOT/'pyproject.toml').read_text())
        force_include = {target: ROOT/original for original, target in
                         project['tool']['hatch']['build']['targets']['wheel']['force-include'].items()}
        receipt['wheelSourceFiles'] = {
            'unified': {package: matching_source(wheels['amplifier-unified']['path'], package, ROOT/package, force_include)
                        for package in project['tool']['hatch']['build']['targets']['wheel']['packages']},
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
        run('native', [args.python, '-I', str(ROOT/'tests/fixtures/status_emitter_probe.py'), str(work/'native')], timeout=240)
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
