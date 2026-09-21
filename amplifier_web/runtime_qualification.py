"""Freeze a freshly prepared worker graph; never refresh a recorded generation."""
from __future__ import annotations

import importlib.metadata
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tomllib
from urllib.parse import unquote, urlsplit, urlunsplit

from . import runtime_environment as environments


def installed_graph(project):
    """Read complete PEP 610 provenance, including Foundation's editable caches."""
    paths = list((Path(project) / '.venv').glob('lib/python*/site-packages'))
    paths += [Path(project) / '.venv/Lib/site-packages']
    rows = []
    for dist in importlib.metadata.distributions(path=[str(path) for path in paths if path.is_dir()]):
        row = {'name': environments.package_name(dist.metadata['Name']), 'version': dist.version}
        direct = json.loads(dist.read_text('direct_url.json') or '{}')
        if direct:
            row['directUrl'] = direct
            parsed = urlsplit(direct.get('url', ''))
            if parsed.scheme == 'file' and direct.get('dir_info') is not None:
                path = Path(unquote(parsed.path)).resolve()
                row['path'] = str(path)
                for parent in (path, *path.parents):
                    meta = parent / '.amplifier_cache_meta.json'
                    if meta.is_file() and (parent / '.git').exists():
                        data = json.loads(meta.read_text())
                        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=parent, text=True).strip()
                        dirty = subprocess.check_output(['git', '--no-optional-locks', 'status', '--porcelain', '--untracked-files=no'], cwd=parent, text=True).strip()
                        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=parent).split(b'\0')
                        tree = hashlib.sha256()
                        for raw in sorted(item for item in tracked if item):
                            file = parent / raw.decode()
                            tree.update(raw + b'\0')
                            tree.update(file.read_bytes() if file.is_file() and not file.is_symlink() else b'<non-regular>')
                        row['cacheSource'] = {'url': data['git_url'], 'ref': data.get('ref') or 'HEAD',
                                              'revision': revision, 'subdirectory': str(path.relative_to(parent)),
                                              'dirty': bool(dirty), 'trackedContentSha256': tree.hexdigest()}
                        break
        rows.append(row)
    names = [row['name'] for row in rows]
    if len(names) != len(set(names)):
        raise ValueError('The worker contains ambiguous duplicate installed distributions.')
    return sorted(rows, key=lambda row: row['name'])


def requirement(row):
    direct = row.get('directUrl') or {}
    vcs = direct.get('vcs_info') or {}
    if vcs.get('vcs') == 'git':
        value = row['name'] + ' @ git+' + direct['url'] + '@' + vcs['commit_id']
        if direct.get('subdirectory'):
            value += '#subdirectory=' + direct['subdirectory']
        return value
    if row.get('path'):
        return row['name'] + ' @ ' + Path(row['path']).as_uri()
    if direct:
        return row['name'] + ' @ ' + direct['url']
    return row['name'] + '==' + row['version']


def override_content(project):
    """Hold the already checked resolver graph while new module deps refresh."""
    data = tomllib.loads((Path(project) / 'uv.lock').read_text())
    lines = []
    for row in data.get('package', []):
        source = row.get('source') or {}
        if source.get('git'):
            parsed = urlsplit(source['git'])
            from urllib.parse import parse_qs
            query = parse_qs(parsed.query)
            value = row['name'] + ' @ git+' + urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', '')) + '@' + parsed.fragment
            if query.get('subdirectory'):
                value += '#subdirectory=' + query['subdirectory'][0]
            lines.append(value)
        elif source.get('registry'):
            lines.append(row['name'] + '==' + row['version'])
        elif source.get('editable') or source.get('directory'):
            path = (Path(project) / (source.get('editable') or source['directory'])).resolve()
            # A direct file requirement loses editable mode during a later
            # uv pip install. Preserve both the path and PEP 610 editability.
            lines.append(('-e ' if source.get('editable') else row['name'] + ' @ ') + path.as_uri())
    return '\n'.join(sorted(lines)) + '\n'


def lock_overrides(project, target):
    Path(target).write_text(override_content(project))
    return Path(target)


def verify_install_overrides(project, target):
    """Verify a recorded policy without re-exporting or rewriting its receipt."""
    target = Path(target)
    if not target.is_file():
        raise ValueError('The recorded worker installation policy is missing; its generation was preserved.')
    policy_file = target.with_name('runtime-install-policy.json')
    if policy_file.exists():
        policy = json.loads(policy_file.read_text())
        valid = (policy.get('version') == 2
                 and policy.get('lockSha256') == hashlib.sha256((Path(project) / 'uv.lock').read_bytes()).hexdigest()
                 and policy.get('overridesSha256') == hashlib.sha256(target.read_bytes()).hexdigest())
    else:
        # 0.20 receipts used the original universal-lock formatter. Do not
        # silently replace those exact policies with a new interpretation.
        valid = target.read_text() == override_content(project)
    if not valid:
        raise ValueError('The recorded worker installation policy changed; its generation was preserved.')
    return target


async def prepare_overrides(project, target):
    """Export resolver-selected markers instead of flattening a universal lock.

    uv owns dependency-edge marker propagation, extras, groups and version
    forks. A frozen/offline export never resolves newer sources. Store its exact
    policy once, so ordinary recorded workers do not depend on exporter changes.
    """
    from .updates import process
    from .deployment import write_private
    project, target = Path(project).resolve(), Path(target)
    if (target.parent / 'runtime-installed.json').exists():
        return verify_install_overrides(project, target)
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('Install uv to prepare the Amplifier runtime.')
    output = await process(uv, 'export', '--frozen', '--offline', '--no-emit-project',
                           '--no-header', '--no-hashes', '--no-annotate', '--project', str(project),
                           cwd=project, timeout=90)
    # Exported local requirements are relative to the resolver project, while
    # the module installer can run anywhere. Keep their exact editable paths.
    local_names = {}
    for row in tomllib.loads((project / 'uv.lock').read_text()).get('package', []):
        source = row.get('source') or {}
        path = source.get('editable') or source.get('directory')
        if path:
            local_names[(project / path).resolve()] = row['name']
    lines = []
    for line in output.splitlines():
        value, separator, marker = line.partition(' ; ')
        editable = value.startswith('-e ')
        reference = value[3:] if editable else value
        if editable or reference.startswith(('.', '/')):
            parsed = urlsplit(reference)
            path = Path(unquote(parsed.path)) if parsed.scheme == 'file' else project / reference
            path = path.resolve()
            if path not in local_names:
                raise ValueError('An exported local source did not match its worker lock.')
            value = ('-e ' if editable else local_names[path] + ' @ ') + path.as_uri()
        lines.append(value + separator + marker)
    content = '\n'.join(sorted(lines)) + '\n'
    write_private(target, content)
    write_private(target.with_name('runtime-install-policy.json'), json.dumps({
        'version': 2, 'lockSha256': hashlib.sha256((project / 'uv.lock').read_bytes()).hexdigest(),
        'overridesSha256': hashlib.sha256(content.encode()).hexdigest(),
    }) + '\n')
    return target


def frozen_manifest(content, graph):
    """A generation receipt reproduces all installed packages, including locals.

    Future updates start from the packaged branch manifest, never these exact
    generation constraints. Editable paths and their source evidence stay local.
    """
    data = tomllib.loads(content.decode())
    project_name = environments.package_name(data['project']['name'])
    uv = data.setdefault('tool', {}).setdefault('uv', {})
    sources = uv.setdefault('sources', {})
    overrides = list(uv.get('override-dependencies', []))
    qualified = []
    for row in graph:
        if row['name'] == project_name:
            continue
        name = row['name']
        if row.get('path'):
            # Preserve the actual editable module and its checked cache path.
            sources[name] = {'path': row['path'], 'editable': bool(row['directUrl'].get('dir_info', {}).get('editable'))}
            qualified.append(name)
            overrides = [value for value in overrides if environments.package_name(value.split(' ', 1)[0]) != name]
            overrides.append(requirement(row))
        else:
            value = requirement(row)
            qualified.append(value)
            overrides = [value for value in overrides if environments.package_name(value.split(' ', 1)[0]) != name]
            overrides.append(value)
            # A frozen direct reference replaces this receipt's branch source.
            sources.pop(name, None)
    data.setdefault('dependency-groups', {})['qualified-installed'] = qualified
    uv['default-groups'] = sorted(set(uv.get('default-groups', []) + ['qualified-installed']))
    uv['override-dependencies'] = overrides
    return ('# Immutable worker qualification receipt; future updates use the packaged manifest.\n' +
            '\n'.join(json.dumps(key) + ' = ' + environments.toml_value(value) for key, value in data.items()) + '\n').encode()


async def freeze(manager, generation, project):
    """Freeze the post-probe graph into a distinct project and verify its install."""
    from .updates import process
    receipt = environments.receipt_directory(manager.home, generation)
    if (receipt / 'runtime.lock').exists():
        raise ValueError('A recorded worker generation cannot be refreshed.')
    graph = installed_graph(project)
    policies = {}
    for row in graph:
        vcs = row.get('directUrl', {}).get('vcs_info', {})
        if vcs.get('vcs') == 'git':
            policies[row['name']] = {'url': row['directUrl']['url'], 'ref': vcs.get('requested_revision') or vcs['commit_id'],
                                     'subdirectory': row['directUrl'].get('subdirectory', '')}
    # Already checked manifest branches remain policy when the refresh install
    # encoded their qualified override commit as its requested revision.
    from urllib.parse import parse_qs
    for name, source in environments.locked_sources(project).items():
        query = parse_qs(source.query)
        ref = next(iter(query.get('branch') or query.get('rev') or query.get('tag') or ['']), '')
        record = next((row for row in graph if row['name'] == name), None)
        vcs = (record or {}).get('directUrl', {}).get('vcs_info', {})
        if name in policies and vcs.get('commit_id') == source.fragment and ref:
            policies[name]['ref'] = ref
    if not graph:
        raise ValueError('The prepared worker has no installed distribution evidence.')
    content = frozen_manifest(environments.manifest_path().read_bytes(), graph)
    final = environments.prepare_project(manager.home, generation, content=content)
    uv = shutil.which('uv')
    # Retain already qualified registry URLs and artifact hashes as resolver
    # input; add only the post-preparation packages missing from that lock.
    shutil.copy2(Path(project) / 'uv.lock', final / 'uv.lock')
    await manager.diagnostics.run('ecosystem-runtime-freeze', process, uv, 'lock', '--project', str(final), '--python', '3.13', timeout=900)
    await manager.diagnostics.run('ecosystem-runtime-freeze-install', process, uv, 'sync', '--locked', '--project', str(final), '--python', '3.13', timeout=900)
    actual = installed_graph(final)
    # Reinstallation may encode a branch request as the frozen commit. Source
    # identity, subdirectory, resolved commit, version and editable cache still
    # must match exactly.
    def identity(rows):
        result = []
        for row in rows:
            direct = json.loads(json.dumps(row.get('directUrl') or {}))
            if direct.get('vcs_info'):
                direct['vcs_info'].pop('requested_revision', None)
            result.append({**row, 'directUrl': direct})
        return result
    if identity(actual) != identity(graph):
        raise ValueError('The frozen worker installation differs from its prepared source graph.')
    receipt.joinpath('runtime-base.toml').write_bytes(environments.manifest_path().read_bytes())
    shutil.copy2(final / 'pyproject.toml', receipt / 'runtime.toml')
    shutil.copy2(final / 'uv.lock', receipt / 'runtime.lock')
    receipt.joinpath('runtime-sources.json').write_text(json.dumps(policies, indent=2) + '\n')
    await manager.diagnostics.run('ecosystem-runtime-policy', prepare_overrides, final, receipt / 'runtime-install-overrides.txt')
    receipt.joinpath('runtime-installed.json').write_text(json.dumps(actual, indent=2) + '\n')
    return final


def verify_recorded(project, receipt, *, allow_additions=False):
    evidence = Path(receipt) / 'runtime-installed.json'
    if not evidence.exists():
        return
    expected = json.loads(evidence.read_text())
    actual = installed_graph(project)
    if allow_additions:
        names = {row['name'] for row in expected}
        actual = [row for row in actual if row['name'] in names]
    if actual != expected:
        raise ValueError('The worker graph changed after qualification; its recorded generation was preserved.')


def active_install_overrides(home, current_override=None):
    """Use a recorded generation's ordinary installer policy, never refresh.

    An explicit user override remains authoritative. Legacy generations have no
    new policy. Validate an existing graph before mounting another conversation.
    """
    from .updates import active_release
    generation = active_release(home).get('current')
    receipt = environments.receipt_directory(home, generation)
    if not (receipt / 'runtime-installed.json').exists():
        return None
    target = receipt / 'runtime-install-overrides.txt'
    compatibility = Path(__file__).parent / 'runtime_deps/compatibility.txt'
    if current_override and current_override not in {str(target), str(compatibility)}:
        return None
    project = environments.project_path(home, generation)
    if (project / 'uv.lock').read_bytes() != (receipt / 'runtime.lock').read_bytes():
        raise ValueError('The recorded worker installation policy changed; its generation was preserved.')
    verify_install_overrides(project, target)
    verify_recorded(project, receipt, allow_additions=True)
    return target
