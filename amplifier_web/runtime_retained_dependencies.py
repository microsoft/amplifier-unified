"""Check version constraints hidden by generated retained-source overrides."""
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import tomllib

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def verify(project):
    data = tomllib.loads((Path(project) / 'pyproject.toml').read_text())
    targets = data.get('tool', {}).get('amplifier-unified', {}).get('generated-source-overrides', {})
    if not targets:
        return
    roots = list((Path(project) / '.venv').glob('lib/python*/site-packages'))
    roots += [Path(project) / '.venv/Lib/site-packages']
    rows = list(importlib.metadata.distributions(path=[str(root) for root in roots if root.is_dir()]))
    python = Path(project) / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    # The worker is on this host, but can use a different Python version from
    # the serving app. Evaluate markers against that actual interpreter.
    version = json.loads(subprocess.check_output([str(python), '-c',
        'import json,platform,sys; print(json.dumps([platform.python_version(),'
        'platform.python_implementation(),sys.implementation.name,list(sys.implementation.version)]))'], text=True, timeout=30))
    implementation = '.'.join(str(part) for part in version[3][:3])
    if version[3][3] != 'final':
        implementation += version[3][3][0] + str(version[3][4])
    environment = {**default_environment(), 'python_full_version': version[0],
        'python_version': '.'.join(version[0].split('.')[:2]), 'platform_python_implementation': version[1],
        'implementation_name': version[2], 'implementation_version': implementation}
    requirements = data.get('project', {}).get('dependencies', []) + data.get(
        'dependency-groups', {}).get('unified-managed-runtime', [])
    verify_metadata(rows, targets, environment, requirements)


def verify_metadata(distributions, targets, environment, requirements=()):
    rows = {canonicalize_name(dist.metadata['Name']): dist for dist in distributions}
    if not set(targets).issubset(rows):
        raise ValueError('A retained runtime dependency is missing from the prepared environment.')
    active = {name: {''} for name in rows}
    def consider(value, extra):
        requirement = Requirement(value)
        if requirement.marker and not requirement.marker.evaluate({**environment, 'extra': extra}):
            return
        target = canonicalize_name(requirement.name)
        if target in targets and not requirement.specifier.contains(rows[target].version, prereleases=True):
            raise ValueError('A retained runtime source conflicts with an installed dependency version requirement.')
        if target in rows:
            active[target].update(requirement.extras)
    for value in requirements:
        consider(value, '')
    checked = set()
    while pending := [(name, extra) for name, extras in active.items() for extra in extras
                      if (name, extra) not in checked]:
        for name, extra in pending:
            checked.add((name, extra))
            for value in rows[name].requires or ():
                consider(value, extra)
