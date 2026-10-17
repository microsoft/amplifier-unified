"""Select owned integration checks and require execution, including collection.

The ordinary host suite may lack optional dependencies. Its designated integration
owner may not silently skip them. This runner never installs dependencies or retries
work; lane preparation is separate and its receipts are retained alongside JUnit.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'scripts/integration-coverage.json'


def manifest():
    return json.loads(MANIFEST.read_text())


def select_lanes(changed=None, coverage=None, *, include_manual=False):
    coverage = coverage or manifest()
    return [{'lane': name, 'os': lane['runner']} for name, lane in coverage['lanes'].items()
            if lane['mode'] in ({'automatic', 'manual'} if include_manual else {'automatic'}) and (changed is None or any(
                fnmatch.fnmatchcase(path, pattern)
                for path in changed for pattern in coverage['commonPaths'] + lane['paths']))]


def verify_junit(path, selectors):
    root = ET.parse(path).getroot()
    if any(n.tag in {'failure', 'error', 'skipped'} for n in root.iter()):
        raise ValueError('Integration results contain a failure, error, or unexpected skip')
    for suite in root.iter('testsuite'):
        if any(int(suite.get(key, '0')) for key in ('failures', 'errors', 'skipped')):
            raise ValueError('Integration suite counters report a failure, error, or skip')
    cases = list(root.iter('testcase'))
    if not cases:
        raise ValueError('Integration report contains no executed cases')
    identities = [(c.get('classname', ''), c.get('name', '')) for c in cases]
    for selector in selectors:
        file, _, function = selector.partition('::')
        module = file.removesuffix('.py').replace('/', '.')
        if not any((cls == module or cls.startswith(module + '.')) and
                   (not function or name == function or name.startswith(function + '['))
                   for cls, name in identities):
            raise ValueError('Required integration selector did not execute: ' + selector)
    return len(cases)


def source_identity(revision=None):
    # Include served assets and browser fixtures as well as Python. Hash all
    # file types in source directories so an image/WASM/HTML dependency cannot
    # silently escape provenance merely because it is not executable code.
    excluded = {'__pycache__', 'node_modules', '.pytest_cache', '.git', 'output'}
    directories = [*ROOT.glob('amplifier_*'), ROOT / 'scripts', ROOT / 'tests',
                   ROOT / '.github/workflows', ROOT / 'packages',
                   *(ROOT / 'frontend' / name for name in ('src', 'tests', 'scripts', 'public'))]
    files = sorted({p for parent in directories if parent.is_dir() for p in parent.rglob('*')
                    if p.is_file() and not excluded.intersection(p.relative_to(ROOT).parts)} |
                   {p for p in (ROOT / 'frontend').glob('*') if p.is_file()} |
                   {ROOT / 'pyproject.toml', ROOT / 'uv.lock'} |
                   {p for p in (ROOT / 'bundle.yaml',) if p.is_file()})
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode() + b'\0' + path.read_bytes() + b'\0')
    if revision is None:
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, capture_output=True)
        revision = result.stdout.strip() if result.returncode == 0 else 'unversioned-archive'
    return {'baseRevision': revision, 'testedSourceDigest': digest.hexdigest()}


def run_lane(lane, python, output, environment=None, revision=None):
    spec = manifest()['lanes'][lane]
    if spec.get('command'):
        raise ValueError('This lane has an explicit qualification runner: ' + ' '.join(spec['command']))
    output.mkdir(parents=True, exist_ok=True)
    junit = output / 'junit.xml'
    # Never accept a stale successful report after an interrupted or empty run.
    junit.unlink(missing_ok=True)
    receipt = output / 'result.json'
    receipt.unlink(missing_ok=True)
    env = os.environ.copy()
    env.update(environment or {})
    command = [python, '-m', 'pytest', *spec['selectors'], '-q', '--tb=short',
               '--junitxml=' + str(junit)]
    result = {'lane': lane, 'selectors': spec['selectors'], 'exitCode': None,
              **source_identity(revision),
              'passed': False}
    try:
        completed = subprocess.run(command, cwd=ROOT, env=env)
        result['exitCode'] = completed.returncode
        after = source_identity(revision)
        result['sourceUnchanged'] = all(result[key] == value for key, value in after.items())
        if not result['sourceUnchanged']:
            result['sourceAfter'] = after
            raise ValueError('Integration source changed during test execution')
        result['tests'] = verify_junit(junit, spec['selectors'])
        if completed.returncode:
            raise ValueError('pytest exited unsuccessfully')
        result['passed'] = True
    except (OSError, ValueError, ET.ParseError) as error:
        result['error'] = str(error)
    receipt.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan')
    plan.add_argument('--changed', type=Path, help='newline-separated changed paths; absent means all automated lanes')
    plan.add_argument('--include-manual', action='store_true', help='Also report affected manual owners; does not execute them')
    run = commands.add_parser('run')
    run.add_argument('--lane', choices=manifest()['lanes'], required=True)
    run.add_argument('--python', default=sys.executable)
    run.add_argument('--output', type=Path, required=True)
    run.add_argument('--env-json', type=Path)
    run.add_argument('--source-revision', help='Base revision for an exported source tree; exact tested bytes are also hashed')
    check = commands.add_parser('check')
    check.add_argument('--lane', choices=manifest()['lanes'], required=True)
    check.add_argument('--junit', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'plan':
        print(json.dumps({'include': select_lanes(args.changed.read_text().splitlines() if args.changed else None,
                                                 include_manual=args.include_manual)}))
        return 0
    if args.command == 'check':
        print(f"{verify_junit(args.junit, manifest()['lanes'][args.lane]['selectors'])} passed; no skips")
        return 0
    return run_lane(args.lane, args.python, args.output.resolve(),
                    json.loads(args.env_json.read_text()) if args.env_json else None, args.source_revision)


if __name__ == '__main__':
    raise SystemExit(main())
