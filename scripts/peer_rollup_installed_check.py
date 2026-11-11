"""Fixed isolated wheel check for the bounded peer-rollup correction.

Uses the managed profile's declared build interpreter and locked dependency
installation; never resolves packages or installs into the host/prepared venv.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import tomllib
import zipfile

from web_test_harness import stage_web_test_harness

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_member(root, name, force_include):
    """Use the packaging owner's declared relocation, never skip missing bytes."""
    member = PurePosixPath(name)
    if member.is_absolute() or '..' in member.parts:
        raise ValueError('Unsafe wheel member')
    for source, target in sorted(force_include.items(), key=lambda item: len(item[1]), reverse=True):
        destination = PurePosixPath(target)
        if member == destination or destination in member.parents:
            return root / source / member.relative_to(destination) if member != destination else root / source
    return root / member


def main():
    contract_path = Path(os.environ['AMPLIFIER_TEST_CONTRACT'])
    contract = json.loads(contract_path.read_text())
    if contract['workspace'] != str(ROOT) or contract['profileId'] != 'browser-test':
        raise ValueError('Use the owned managed browser-test environment.')
    output = Path(contract['artifacts']) / 'peer-rollup-wheel'
    output.mkdir(exist_ok=True)
    build_python = contract['pythonBuild']['python']
    report = {'passed': False, 'source': contract['source'], 'steps': []}

    def run(name, argv, *, cwd=ROOT, env=None, expected=0):
        with (output / (name + '.log')).open('w') as stream:
            result = subprocess.run(argv, cwd=cwd, env=env, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=300)
        report['steps'].append({'name': name, 'exitCode': result.returncode})
        if result.returncode != expected:
            raise ValueError(f'{name} exited {result.returncode}, expected {expected}')
        return (output / (name + '.log')).read_text()

    try:
        run('build', [build_python, '-m', 'build', '--wheel', '--no-isolation',
                      '--outdir', str(output / 'dist'), str(ROOT)])
        wheel, = (output / 'dist').glob('*.whl')
        force_include = tomllib.loads((ROOT / 'pyproject.toml').read_text())['tool']['hatch']['build']['targets']['wheel']['force-include']
        with zipfile.ZipFile(wheel) as archive:
            assert archive.testzip() is None
            files = {name: sha(archive.read(name)) for name in archive.namelist()
                     if name.startswith(('amplifier_web/', 'amplifier_operations/')) and not name.endswith('/')}
            for name, digest in files.items():
                assert sha(source_member(ROOT, name, force_include).read_bytes()) == digest, name
        report.update(wheelSha256=sha(wheel.read_bytes()), packageFiles=len(files),
                      staticFiles=sum(name.startswith('amplifier_web/static/') for name in files))
        with tempfile.TemporaryDirectory(prefix='peer-rollup-installed-') as temporary:
            temp = Path(temporary)
            venv, harness = temp / 'venv', temp / 'harness'
            harness.mkdir()
            clean = dict(os.environ)
            clean.pop('PYTHONPATH', None)
            run('venv', [sys.executable, '-m', 'venv', str(venv)], env=clean)
            python = venv / 'bin/python'
            paths_script = 'import json,sysconfig; print(json.dumps([sysconfig.get_path("purelib"),sysconfig.get_path("platlib")]))'
            dependencies = json.loads(run('dependency-paths', [contract['python'], '-I', '-c', paths_script],
                                          cwd=temp, env=clean))
            local_paths = json.loads(run('venv-paths', [str(python), '-I', '-c', paths_script], cwd=temp, env=clean))
            local_site = Path(local_paths[0])
            assert local_site.is_relative_to(venv)
            dependencies = sorted(set(dependencies))
            assert all(Path(path).is_absolute() and Path(path).is_relative_to(Path(contract['python']).parent.parent)
                       and Path(path).is_dir() and not Path(path).is_relative_to(ROOT) for path in dependencies)
            # Add only declared locked libraries, after this venv's wheel. A
            # parent venv's --system-site-packages would expose the base Python,
            # not the prepared dependency installation. No parent .pth executes.
            (local_site / 'locked-prepared-dependencies.pth').write_text('\n'.join(dependencies) + '\n')
            report['lockedDependencyPaths'] = dependencies
            run('install', [str(python), '-I', '-m', 'pip', 'install', '--no-index',
                            '--no-deps', '--ignore-installed', '--disable-pip-version-check', str(wheel)], cwd=temp, env=clean)
            support = stage_web_test_harness(ROOT, harness, python_tests=True)
            report['harnessSupport'] = support
            origins_file = output / 'origins.json'
            run('origins', [str(python), '-I', '-c',
                'import amplifier_web,amplifier_operations,aiohttp,pytest,json,pathlib; '
                f'pathlib.Path({str(origins_file)!r}).write_text(json.dumps({{"web":amplifier_web.__file__,"operations":amplifier_operations.__file__,"aiohttp":aiohttp.__file__,"pytest":pytest.__file__}}))'],
                cwd=temp, env=clean)
            origins = json.loads(origins_file.read_text())
            package = Path(origins['web']).parent.parent
            assert package.is_relative_to(venv) and all(Path(origins[name]).is_relative_to(package) for name in ('web', 'operations'))
            assert all(any(Path(origins[name]).is_relative_to(path) for path in dependencies)
                       for name in ('aiohttp', 'pytest'))
            def verify():
                for name, digest in files.items():
                    assert sha((package / name).read_bytes()) == digest, name
            verify()
            env = {**clean, 'AMPLIFIER_TEST_PYTHON': str(python),
                   'AMPLIFIER_TEST_INSTALLED_STATIC': '1', 'PEER_PREVIEW_PARTIAL_RECEIVER': '1',
                   'AMPLIFIER_PROOF_DIR': str(output / 'browser'),
                   'AMPLIFIER_SHELL_NODE_MODULES': str(ROOT / '.build/web-client/node_modules')}
            run('installed-browser', ['node', 'tests/web/peer-rollup-browser.mjs'], cwd=harness, env=env)
            browser = json.loads((output / 'browser/peer-rollup-result.json').read_text())
            assert [row['width'] for row in browser['observations']] == [1280, 390]
            assert all(row['runCount'] == 1 and row['sourceButtons'] == 3 and row['previewOpened']
                       and row['exactCopy'] and row['draftUnchanged'] and row['noRuntime']
                       for row in browser['observations'])
            verify()
            report.update(origins=origins, payloadsVerifiedBeforeAfter=True, browser=browser)
        report['passed'] = True
    finally:
        (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({key: value for key, value in report.items() if key != 'browser'}))


if __name__ == '__main__':
    main()
