"""Prepare one disposable CI integration lane, then use the common result guard."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from integration_checks import ROOT, run_lane
import integration_clients
import integration_external
import integration_native


def execute(lane, work, output):
    work = work.resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sources = ROOT / '.ci'
    if lane == 'native':
        prepared = integration_native.prepare(ROOT, work, None, 'uv', sys.executable,
                                              loop_source=sources / 'loop-live')
        env = json.loads(Path(prepared['environment']).read_text())
        (output / 'preparation.json').write_text(Path(prepared['preparation']).read_text())
        for name in ('pyproject.toml', 'uv.lock'):
            (output / name).write_bytes((work / name).read_bytes())
        return run_lane(lane, prepared['python'], output, env)
    if lane in integration_clients.LANES:
        prepared = integration_clients.prepare(
            lane, work, ROOT, python=sys.executable,
            cli_source=sources / 'cli' if lane == 'cli-interop' else None,
            loop_source=sources / 'loop-live' if lane == 'cli-interop' else None,
            filesystem_source=sources / 'tool-filesystem' if lane == 'filesystem-aliases' else None,
            apply_patch_source=sources / 'filesystem-bundle/modules/tool-apply-patch' if lane == 'filesystem-aliases' else None,
            install_browser_dependencies=sys.platform.startswith('linux'))
        env = prepared['environment']
        python = prepared['interpreter']
        (output / 'preparation.json').write_text(json.dumps(prepared, indent=2) + '\n')
        (output / 'packages.txt').write_bytes((work / 'evidence/packages.txt').read_bytes())
        subprocess.run([python, str(ROOT / 'scripts/integration_clients.py'), 'preflight',
                        '--lane', lane, '--state-dir', str(work), '--output', str(output / 'preflight.json')],
                       env={**os.environ, **env}, check=True)
        return run_lane(lane, python, output, env)
    if lane not in {'context-intelligence', 'connected-documents'}:
        raise ValueError('This lane requires its explicit manual or publication owner')
    work.mkdir(parents=True)
    for name in ('home', 'tmp', 'cache'):
        (work / name).mkdir()
    env = {**os.environ, 'HOME': str(work / 'home'), 'TMPDIR': str(work / 'tmp'),
           'UV_CACHE_DIR': str(work / 'cache'), 'UV_PROJECT_ENVIRONMENT': str(work / 'env'),
           'UV_NO_BUILD_PACKAGE': 'amplifier-core'}
    env.pop('VIRTUAL_ENV', None)
    subprocess.run(['uv', 'sync', '--locked', '--group', 'dev', '--python', sys.executable],
                   cwd=ROOT, env=env, check=True)
    python = str(work / 'env/bin/python')
    dependencies = [str(sources / 'ci-server')] if lane == 'context-intelligence' else [
        str(sources / 'loop-live'), str(sources / 'm365/modules/tool-m365-auth'),
        str(sources / 'm365/modules/tool-m365-documents'),
        'amplifier-module-context-simple @ git+https://github.com/microsoft/amplifier-module-context-simple@main']
    subprocess.run(['uv', 'pip', 'install', '--python', python, *dependencies], env=env, check=True)
    selected_sources = ['ci-server'] if lane == 'context-intelligence' else ['m365', 'loop-live']
    receipt = {name: integration_clients.checked_source(sources / name) for name in selected_sources}
    (output / 'sources.json').write_text(json.dumps(receipt, indent=2) + '\n')
    (output / 'packages.txt').write_text(subprocess.check_output(
        ['uv', 'pip', 'freeze', '--python', python], env=env, text=True))
    if lane == 'context-intelligence':
        return integration_external.run(python, work / 'server', output, 'bolt://127.0.0.1:17687')
    env['AMPLIFIER_M365_TEST_SOURCE'] = str(sources / 'm365')
    return run_lane(lane, python, output, env)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lane', required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(execute(args.lane, args.work, args.output))
