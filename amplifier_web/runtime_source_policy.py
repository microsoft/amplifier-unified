"""Keep authored update policy distinct from a qualified install's exact commit.

Only the prepared manifest or an exact owned cache declaration can establish a
new policy. A nearby checkout, current upstream HEAD, or a package name cannot
explain a legacy pin. Such pins remain intact with their missing origin visible.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import tomllib
from urllib.parse import parse_qs, urlsplit, urlunsplit


def origin(policy, kind, **evidence):
    return {'kind': kind, **{key: policy.get(key, '') for key in
                            ('url', 'ref', 'subdirectory', 'revision')}, **evidence}


def with_origin(policy, kind, **evidence):
    return {**policy, 'policyOrigin': origin(policy, kind, **evidence)}


def carried_origin(policy, previous=None):
    """Rebind evidence only while the chosen repository/ref/subdirectory agree."""
    old = previous or {}
    valid = (isinstance(old, dict) and old.get('kind') and
             all(old.get(key, '') == policy.get(key, '') for key in ('url', 'ref', 'subdirectory')))
    return origin(policy, old['kind'] if valid else 'checked-source',
                  **({key: old[key] for key in ('manifestSha256',) if key in old} if valid else {}))


def declarations(content):
    """Read explicit prepared requirements, including dynamically added groups."""
    from .runtime_environment import package_name
    data = tomllib.loads(content.decode())
    found = {}

    def add(name, source):
        name = package_name(name)
        if name in found and found[name] != source:
            found[name] = None  # Conflicting declarations are not policy evidence.
        else:
            found[name] = source

    def requirement(value):
        if not isinstance(value, str):
            return
        match = re.fullmatch(r'([\w.-]+)(?:\[[^]]+\])?\s*@\s*(git\+[^\s;]+)\s*', value)
        if not match:
            return
        parsed = urlsplit(match[2][4:])
        path, separator, ref = parsed.path.rpartition('@')
        fragments = parse_qs(parsed.fragment)
        if not separator or not ref or len(fragments.get('subdirectory', [''])) != 1:
            return
        add(match[1], {'url': urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, '')),
                       'ref': ref, 'subdirectory': fragments.get('subdirectory', [''])[0]})

    for value in data.get('project', {}).get('dependencies', []):
        requirement(value)
    for group in data.get('dependency-groups', {}).values():
        for value in group:
            requirement(value)
    uv = data.get('tool', {}).get('uv', {})
    for value in uv.get('override-dependencies', []):
        requirement(value)
    for name, source in uv.get('sources', {}).items():
        if not isinstance(source, dict) or not source.get('git'):
            continue
        refs = [source[key] for key in ('branch', 'rev', 'tag') if source.get(key)]
        if len(refs) == 1:
            add(name, {'url': source['git'], 'ref': refs[0],
                       'subdirectory': source.get('subdirectory', '')})
    return {name: value for name, value in found.items() if value is not None}


def freeze_policies(home, generation, project, graph):
    """Capture policy before an immutable manifest pins every installed byte.

    The caller still overlays checked policies with its strict installed-identity
    comparison. This helper cannot excuse a changed revision during qualification.
    """
    from . import runtime_environment as environments
    manifest = Path(project) / 'pyproject.toml'
    content = manifest.read_bytes() if manifest.is_file() else b''
    declared = declarations(content)
    digest = hashlib.sha256(content).hexdigest()
    locked = environments.locked_sources(project)
    policies = {name: with_origin(policy, 'owned-shared-binding') for name, policy in
                environments.shared_build_policies(home, generation, graph).items()}
    cache_root = (environments.receipt_directory(home, generation) / 'foundation/cache').resolve()
    for row in graph:
        name = row['name']
        cached = row.get('cacheSource') or {}
        if cached and not cached.get('dirty') and row.get('path') and name not in policies:
            directory = Path(row['path'])
            for _ in Path(cached['subdirectory']).parts:
                directory = directory.parent
            if not directory.is_symlink() and directory.resolve().parent == cache_root:
                policy = {'url': cached['url'], 'ref': cached['ref'], 'revision': cached['revision'],
                          'subdirectory': '' if cached['subdirectory'] == '.' else cached['subdirectory']}
                policies[name] = with_origin(policy, 'owned-cache-declaration')
        direct = row.get('directUrl') or {}
        vcs = direct.get('vcs_info') or {}
        if vcs.get('vcs') != 'git':
            continue
        policy = {'url': direct['url'], 'ref': vcs.get('requested_revision') or vcs['commit_id'],
                  'revision': vcs['commit_id'], 'subdirectory': direct.get('subdirectory', '')}
        kind = 'installed-reference'
        resolved = locked.get(name)
        declaration = declared.get(name)
        if resolved is not None:
            query = parse_qs(resolved.query, keep_blank_values=True)
            refs = [value for key in ('branch', 'rev', 'tag') for value in query.get(key, [])]
            exact = (urlunsplit((resolved.scheme, resolved.netloc, resolved.path, '', '')) == policy['url']
                     and resolved.fragment == policy['revision']
                     and query.get('subdirectory', ['']) == [policy['subdirectory']] and len(refs) == 1
                     and (policy['ref'] == refs[0] or
                          (policy['ref'] == policy['revision'] and re.fullmatch('[a-f0-9]{40}', policy['revision']))))
            if exact:
                policy['ref'] = refs[0]
                kind = 'resolution-reference'
                # Only a full normalized commit at this exact installed identity
                # is interchangeable with the prepared declaration's original ref.
                normalized = (query.get('rev') == [policy['revision']]
                              and re.fullmatch('[a-f0-9]{40}', policy['revision']))
                if (declaration and declaration['url'] == policy['url']
                        and declaration['subdirectory'] == policy['subdirectory']
                        and (refs[0] == declaration['ref'] or normalized)):
                    policy['ref'] = declaration['ref']
                    kind = 'prepared-manifest'
        policies[name] = with_origin(policy, kind,
                                    **({'manifestSha256': digest} if kind == 'prepared-manifest' else {}))
    return policies
