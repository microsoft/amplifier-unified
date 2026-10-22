"""One diagnostic run of the unchanged published 0.27.3 tool-details owner.

This branch-only helper records provenance and observes the original boundary.
It does not build the client, retry the scenario, or change its assertions.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'output/tool-window-diagnostic'
BASE = '94681e70089e92dce7f73b5c8f7a070e7efd6bd9'
ALLOWED = {
    '.github/workflows/browser-checks.yml',
    'tests/test_release_qualification.py',
    'scripts/diagnostics/tool_window/run.py',
    'scripts/diagnostics/tool_window/sitecustomize.py',
}


def command(*args, cwd=ROOT):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def write(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, indent=2) + '\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes():
    return {name: digest(ROOT / name) for name in
            command('git', 'ls-files', '-z').split('\0') if name}


def prepare():
    if OUT.exists():
        raise RuntimeError('Diagnostic output already exists; no replay')
    inputs = json.loads(os.environ['DIAGNOSTIC_INPUTS'])
    if inputs.get('tool_details_diagnostic') is not True or any(
            bool(value) for name, value in inputs.items()
            if name != 'tool_details_diagnostic'):
        raise ValueError('Select only tool_details_diagnostic')
    subprocess.run(['git', 'merge-base', '--is-ancestor', BASE, 'HEAD'], cwd=ROOT, check=True)
    changed = command('git', 'diff', '--name-only', BASE, 'HEAD').splitlines()
    if not set(changed) <= ALLOWED or command('git', 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError('Only the declared diagnostic delta may differ from published source')
    source = hashes()
    statics = {name: value for name, value in source.items() if name.startswith('amplifier_web/static/')}
    build = json.loads((ROOT / 'amplifier_web/static/build.json').read_text())
    if len(statics) != 311 or build != {'version': '0.27.3', 'id': '3ec80f60b4a0c696'}:
        raise ValueError('Unexpected published client identity')
    plan = json.loads((ROOT / 'frontend/tests/browser-checks.json').read_text())
    rows = [row for row in plan['scenarios'] if row['id'] == 'tool-details']
    if len(rows) != 1 or rows[0]['command'] != ['node', 'tests/tool-details-browser.mjs']:
        raise ValueError('The fixed maintained scenario is unavailable')
    write('source-before.json', {
        'publishedSource': BASE, 'diagnosticHead': command('git', 'rev-parse', 'HEAD'),
        'tree': command('git', 'rev-parse', 'HEAD^{tree}'), 'diagnosticOnlyDelta': changed,
        'runId': os.environ.get('GITHUB_RUN_ID'), 'attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
        'sourceSha256': source, 'staticSha256': statics, 'build': build,
        'limits': 'Source-mode synthetic fixture; passive tracing can change timing. A pass is non-reproduction.',
    })
    write('plan.json', {'schemaVersion': 1, 'scenarios': rows})
    subprocess.run([sys.executable, 'scripts/browser_check_runner.py', '--plan',
                    str(OUT / 'plan.json'), '--output', str(OUT / 'browser'), '--initialize'],
                   cwd=ROOT, check=True)


def identity():
    import amplifier_core
    import amplifier_web
    distributions = []
    for dist in importlib.metadata.distributions():
        row = {'name': dist.metadata['Name'], 'version': dist.version}
        direct = dist.read_text('direct_url.json')
        if direct:
            data = json.loads(direct)
            row['directOriginSha256'] = hashlib.sha256(direct.encode()).hexdigest()
            row['vcsInfo'] = data.get('vcs_info')
            row['editable'] = data.get('dir_info', {}).get('editable', False)
        distributions.append(row)
    browser = json.loads(command('node', '--input-type=module', '-e',
        "import {chromium} from '@playwright/test'; import {createRequire} from 'node:module'; "
        "const require=createRequire(import.meta.url); "
        "console.log(JSON.stringify({path:chromium.executablePath(),playwright:require('@playwright/test/package.json').version}));",
        cwd=ROOT / 'frontend'))
    executable = Path(browser['path'])
    browser.update(sha256=digest(executable), version=command(str(executable), '--version'))
    write('runtime.json', {
        'python': sys.version, 'platform': platform.platform(), 'machine': platform.machine(),
        'appOrigin': str(Path(amplifier_web.__file__).resolve()),
        'coreOrigin': str(Path(amplifier_core.__file__).resolve()),
        'distributions': sorted(distributions, key=lambda row: row['name'].lower()),
        'node': command('node', '--version'), 'uv': command('uv', '--version'), 'browser': browser,
        'dependencyLocks': {name: digest(ROOT / name) for name in ('uv.lock', 'frontend/package-lock.json')},
    })


def verify():
    before_path = OUT / 'source-before.json'
    if not before_path.exists():
        write('verification.json', {'qualified': False, 'reason': 'Preparation failed; no scenario proof'})
        return 1
    before = json.loads(before_path.read_text())
    after = hashes()
    changed = sorted(name for name in before['sourceSha256'].keys() | after.keys()
                     if before['sourceSha256'].get(name) != after.get(name))
    owner_output = ROOT / 'output/browser-checks/tool-details'
    if owner_output.exists():
        shutil.copytree(owner_output, OUT / 'scenario-evidence')
    trace = []
    for path in sorted((OUT / 'worker-trace').glob('window-*.jsonl')):
        trace.extend(json.loads(line) for line in path.read_text().splitlines())
    cursors = [row for row in trace if row.get('phase') == 'cursor-check']
    results = OUT / 'browser/results.json'
    result = json.loads(results.read_text()) if results.exists() else None
    write('verification.json', {
        'sourceUnchanged': not changed, 'changedInputs': changed,
        'runtimeRecorded': (OUT / 'runtime.json').exists(), 'traceRecords': len(trace),
        'cursorChecks': len(cursors),
        'cursorRejections': [row for row in cursors if row.get('status') == 'raised'],
        'windowFailures': [row for row in trace if row.get('phase') == 'window-read' and row.get('status') == 'raised'],
        'scenario': result,
        'interpretation': 'Original scenario outcome retained. Passing with tracing is non-reproduction, not a repair.',
    })
    return int(bool(changed) or not cursors or not (OUT / 'runtime.json').exists())


if __name__ == '__main__':
    if sys.argv[1:] == ['prepare']:
        prepare()
    elif sys.argv[1:] == ['identity']:
        identity()
    elif sys.argv[1:] == ['verify']:
        raise SystemExit(verify())
    else:
        raise SystemExit('Expected prepare, identity, or verify')
