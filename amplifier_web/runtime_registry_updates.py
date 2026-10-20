"""Narrow registry policy for app-owned Core; frozen versions are evidence only."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement, InvalidRequirement

CORE = 'amplifier-core'
INDEX = 'https://pypi.org/simple'
RECEIPT = 'runtime-registry-sources.json'


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('The registry component version could not be verified.')
    return tuple(map(int, value.split('.')))


def core_requirements(values):
    result = []
    for value in values:
        requirement = Requirement(value)
        if re.sub(r'[-_.]+', '-', requirement.name).lower() == CORE:
            result.append(requirement)
    return result


def open_policy(content):
    """Only the shipped wheel-only PyPI policy, never a user pin or source."""
    try:
        data = tomllib.loads(content.decode())
        uv = data.get('tool', {}).get('uv', {})
        requirements = core_requirements(data.get('project', {}).get('dependencies', []))
        if len(requirements) != 1:
            return None
        requirement = requirements[0]
        bounds = list(requirement.specifier)
        extra_groups = [*data.get('dependency-groups', {}).values(),
                        *data.get('project', {}).get('optional-dependencies', {}).values(),
                        uv.get('dev-dependencies', [])]
        if any(core_requirements([value for value in group if isinstance(value, str)]) for group in extra_groups):
            return None
        if (requirement.url or requirement.marker or requirement.extras or len(bounds) != 1
                or bounds[0].operator != '>=' or CORE not in uv.get('no-build-package', [])
                or any(re.sub(r'[-_.]+', '-', name).lower() == CORE for name in uv.get('sources', {}))
                or core_requirements(uv.get('override-dependencies', []))
                or core_requirements(uv.get('constraint-dependencies', []))
                or uv.get('index') != [{'url': INDEX, 'default': True}]
                or any(key in uv for key in ('index-url', 'extra-index-url', 'find-links',
                                             'no-index', 'index-strategy'))):
            return None
        version(bounds[0].version)
        return bounds[0].version
    except (ValueError, KeyError, TypeError, InvalidRequirement):
        return None


def registry_lock(project):
    path = Path(project) / 'uv.lock'
    if not path.is_file():
        return None
    rows = [row for row in tomllib.loads(path.read_text()).get('package', []) if row.get('name') == CORE]
    if len(rows) != 1 or rows[0].get('source') != {'registry': INDEX}:
        return None
    row = rows[0]
    version(row.get('version'))
    return {'version': row['version'], 'registry': INDEX}


def managed_source(project, receipt, baseline, graph, current_manifest):
    """Require base policy, resolver identity, and actual installed agreement."""
    minimum = open_policy(baseline)
    if minimum is None or open_policy(current_manifest) is None:
        return None
    rows = [row for row in graph if row.get('name') == CORE]
    if len(rows) != 1:
        return None
    record = rows[0]
    if record.get('directUrl') or record.get('path') or record.get('cacheSource'):
        return None
    locked = registry_lock(project)
    if not locked or locked['version'] != record.get('version') or version(record['version']) < version(minimum):
        return None
    receipt = Path(receipt)
    expected_manifest = receipt / 'runtime.toml'
    expected_manifest = expected_manifest.read_bytes() if expected_manifest.exists() else baseline
    actual_manifest = Path(project) / 'pyproject.toml'
    if not actual_manifest.is_file() or actual_manifest.read_bytes() != expected_manifest:
        return None
    installed = receipt / 'runtime-installed.json'
    if installed.exists():
        saved = [row for row in json.loads(installed.read_text()) if row.get('name') == CORE]
        if saved != rows:
            return None
    return {'current': record['version'], 'registry': INDEX, 'registryManaged': True,
            'provenance': 'app-managed PyPI wheel', 'minimumVersion': open_policy(current_manifest)}


async def check(row):
    from .app_component_graph import index_latest
    from .update_checks import index_latest as cached_index
    latest = await cached_index(CORE, lambda: index_latest(CORE))
    newest, current = version(latest), version(row.get('current'))
    if newest < version(row['minimumVersion']):
        raise ValueError('No published Core release satisfies the app requirement.')
    row.update(latest=latest, status='update' if newest > current else 'current')


def prepare_manifest(content, observed, selected):
    """Pin one candidate's checked Core version; preserve unrelated-tier Core."""
    managed = next((row for row in observed if row.get('package') == CORE and row.get('registryManaged')), None)
    chosen = next((row for row in selected if row.get('package') == CORE and row.get('registryManaged')), None)
    if chosen and (not managed or any(chosen.get(key) != managed.get(key)
                      for key in ('current', 'registry', 'minimumVersion'))):
        raise ValueError('Core changed since checking; check for updates again.')
    if not managed:
        return content, {}
    minimum = open_policy(content)
    if minimum is None:
        raise ValueError('The app-managed Core policy changed before preparation.')
    current = version(managed['current'])
    target = chosen.get('latest') if chosen and chosen.get('status') == 'update' else managed['current']
    if version(target) < current or version(target) < version(minimum):
        raise ValueError('The checked Core release is older than the current policy.')
    data = tomllib.loads(content.decode())
    uv = data['tool']['uv']
    uv.setdefault('override-dependencies', []).append(CORE + '==' + target)
    from .runtime_environment import toml_value
    generated = ('# Isolated candidate; its registry versions were checked before preparation.\n' +
                 '\n'.join(json.dumps(key) + ' = ' + toml_value(value) for key, value in data.items()) + '\n').encode()
    return generated, {CORE: {'version': target, 'registry': INDEX}}


def verify_candidate(project, expected, *, graph=None):
    """No direct/local substitution or version movement can cross qualification."""
    if not expected:
        return
    if set(expected) != {CORE} or expected[CORE].get('registry') != INDEX:
        raise ValueError('Unknown registry update policy.')
    version(expected[CORE].get('version'))
    if registry_lock(project) != expected[CORE]:
        raise ValueError('The resolved Core wheel differs from its checked registry version.')
    if graph is not None:
        records = [row for row in graph if row.get('name') == CORE]
        if (len(records) != 1 or records[0].get('version') != expected[CORE]['version']
                or any(records[0].get(key) for key in ('directUrl', 'path', 'cacheSource'))):
            raise ValueError('The installed Core wheel differs from its checked registry version.')
