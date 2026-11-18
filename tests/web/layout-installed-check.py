"""Qualify selected layout journeys against one already-built wheel in the DTU.

Reuse the locked prepared dependencies; never import the app from source. Keep the
original wheel, exact installed-file identity and bounded browser results together.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from web_test_harness import stage_web_test_harness
from browser_check_runner import run_scenarios


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--scenario', action='append', required=True)
    parser.add_argument('--budget', type=int, default=600)
    parser.add_argument('--case-timeout', type=int, default=180)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    contract = json.loads(Path(os.environ['AMPLIFIER_TEST_CONTRACT']).read_text())
    assert contract['profileId'] == 'browser-test'
    wheel = args.wheel.resolve()
    digest = lambda data: hashlib.sha256(data).hexdigest()
    identity = {'wheelSha256': digest(wheel.read_bytes()), 'files': {}}
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.startswith('amplifier_web/') and not name.endswith('/'):
                identity['files'][name] = digest(archive.read(name))
    identity_path = output / 'wheel-identity.json'
    identity_path.write_text(json.dumps(identity, indent=2) + '\n')
    python = output / 'venv/bin/python'
    subprocess.run(['uv', 'venv', '--python', sys.executable, str(python.parent.parent)], check=True)
    site = python.parent.parent / 'lib' / ('python' + '.'.join(map(str, sys.version_info[:2]))) / 'site-packages'
    (site / 'prepared-dependencies.pth').write_text(sysconfig.get_path('purelib') + '\n')
    subprocess.run(['uv', 'pip', 'install', '--python', str(python), '--no-deps', str(wheel)], check=True)
    env = {key: value for key, value in os.environ.items() if key not in ('PYTHONPATH', 'PYTHONHOME')}
    probe = '''import json,hashlib,sys
from pathlib import Path
import amplifier_web
root=Path(amplifier_web.__file__).resolve().parent
assert root.is_relative_to(Path(sys.argv[2]))
assert not any(Path(p or '.').resolve().is_relative_to(Path('/workspace')) for p in sys.path)
identity=json.loads(Path(sys.argv[1]).read_text())
assert all(hashlib.sha256((root.parent/name).read_bytes()).hexdigest()==value for name,value in identity['files'].items())
print(root)
'''
    package = subprocess.check_output([str(python), '-I', '-c', probe, str(identity_path), str(python.parent.parent)], cwd=output, env=env, text=True).strip()
    harness = output / 'harness'
    support = stage_web_test_harness(ROOT, harness, source_helpers=('settings-navigation.js',))
    (harness / '.venv').symlink_to(python.parent.parent, target_is_directory=True)
    scenarios = []
    for name in args.scenario:
        assert name in {'canvas-chrome', 'canvas-dirty', 'canvas-close', 'canvas-refresh-catchup',
                        'compact-composer', 'markdown-composer', 'composer-return-host', 'layout-spacing-host', 'conversation-error-host', 'settings-mobile',
                        'layout-pass2-settings', 'layout-pass2-work', 'layout-pass2-priority'}
        target = output / name
        scenarios.append({'id': name, 'command': ['node', f'tests/web/{name}-browser.mjs'], 'env': {
            'AMPLIFIER_TEST_PYTHON': str(python), 'AMPLIFIER_TEST_PACKAGE_ROOT': package,
            'AMPLIFIER_SHELL_NODE_MODULES': str(ROOT / '.build/web-client/node_modules'),
            'AMPLIFIER_TEST_INSTALLED_PACKAGE': package, 'AMPLIFIER_TEST_WHEEL_IDENTITY': str(identity_path),
            'AMPLIFIER_TEST_ARTIFACTS': str(target), 'AMPLIFIER_TEST_OUTPUT': str(target), 'PYTHONPATH': ''}})
    result = run_scenarios(harness, {'schemaVersion': 1, 'scenarios': scenarios}, output / 'browser',
                           budget=args.budget, case_timeout=args.case_timeout,
                           provenance={'wheel': identity['wheelSha256'], 'sourceHelpers': support, 'contract': contract})
    (output / 'result.json').write_text(json.dumps({'exit': result, 'packageRoot': package,
        'wheelSha256': identity['wheelSha256'], 'sourceHelpers': support,
        'limits': ['Locked development dependencies reused, not a minimal-dependency install',
                   'Synthetic runtime; no paid provider, physical mobile keyboard or native platform acceptance']}, indent=2) + '\n')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
