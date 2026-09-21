"""Refresh an isolated release test environment and retain its resolved graph.

This qualifies the installed default host dependencies. Optional extras are
qualified by their own installation paths; no private repository access is
inferred from an unused optional entry in the development lock.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--verify', action='store_true', help='Verify the recorded graph without resolving or installing')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if Path(sys.prefix).resolve() != (root / '.venv').resolve():
        raise ValueError('Run this with the isolated checkout .venv interpreter.')
    sys.path.insert(0, str(root))
    from amplifier_web.app_component_graph import digest, installed_graph, requirements

    evidence = args.evidence.resolve()
    if args.verify:
        report = json.loads((evidence / 'resolution.json').read_text())
        graph = installed_graph()
        if (report.get('ok') is not True or report.get('resolved') != graph
                or report.get('graphSha256') != digest(graph)
                or (evidence / 'qualified-overrides.txt').read_text() != requirements(graph)):
            raise ValueError('The host dependency graph changed after qualification.')
        return
    evidence.mkdir(parents=True, mode=0o700, exist_ok=False)
    shutil.copy2(root / 'uv.lock', evidence / 'development.lock')
    before = installed_graph()
    report = {'startedAt': datetime.now(timezone.utc).isoformat(),
              'scope': 'installed host dependencies; optional extras require separate qualification',
              'before': before, 'passes': [], 'ok': False}
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('uv is required for release dependency qualification.')
    try:
        refreshed = set()
        for _ in range(8):
            graph = installed_graph()
            components = [item for item in graph if item['name'].startswith('amplifier-')]
            names = {item['name'] for item in components}
            if not names - refreshed:
                break
            current = [{**item, 'revision': 'main'} if item.get('url') else item for item in components]
            # Registry components follow their published channel; Git components
            # follow canonical main. Exact constraints are written only afterward.
            requested = [line.replace('==', '>=', 1) for line in requirements(current).splitlines()]
            flags = [value for name in sorted(names) for value in ('--upgrade-package', name, '--refresh-package', name)]
            subprocess.run([uv, 'pip', 'install', '--python', sys.executable, *flags, *requested], check=True)
            report['passes'].append(sorted(names))
            refreshed |= names
        else:
            raise RuntimeError('Installed Amplifier dependencies did not stabilize.')
        graph = installed_graph()
        report.update(ok=True, resolved=graph, graphSha256=digest(graph))
        (evidence / 'qualified-overrides.txt').write_text(requirements(graph))
    finally:
        (evidence / 'resolution.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
