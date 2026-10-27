"""Incremental ROOT + SIBLING qualification with an optional source constraint.

Run only in the managed Linux/Python 3.13 guest. Real Foundation prepares and
installs seven synthetic distributions into a disposable target interpreter;
fixture-foundation never replaces amplifier-foundation in the test interpreter.
The profile-probe adapter does not mount a conversation or call a provider.
"""
import importlib.metadata
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_environment as environments
from amplifier_web import runtime_policy_reconciliation as reconciliation
from amplifier_web import runtime_qualification as qualification
from amplifier_web import runtime_source_alignment as alignment
from amplifier_web import update_checks, updates
from amplifier_web.host.config import HostConfig
from test_runtime_checked_cache import BACKEND, Diagnostics, git


URLS = {name: 'https://example.invalid/incremental-' + name
        for name in ('foundation', 'recipes', 'memory', 'skills', 'provider')}
FOUNDATION = 'fixture-foundation'
PROVIDER = 'fixture-provider'
REQUIREMENT = FOUNDATION + ' @ git+' + URLS['foundation'] + '@main'
PACKAGES = {
    FOUNDATION: ('foundation', ''),
    'fixture-recipes': ('recipes', ''),
    'fixture-recipes-tool': ('recipes', 'modules/recipes-tool'),
    'fixture-memory': ('memory', ''),
    'fixture-memory-hook': ('memory', 'modules/memory-hook'),
    'fixture-skills': ('skills', ''),
    PROVIDER: ('provider', ''),
}
BASELINE = 'a' * 32
# The minimal backend must preserve real dependency metadata as well as RECORD.
# Same-version revisions differ in an installed payload, not merely cache JSON.
METADATA_EXPRESSION = (
    "'Metadata-Version: 2.3\\nName: ' + info['name'] + '\\nVersion: ' + version + '\\n'"
)
FIXTURE_BACKEND = BACKEND.replace(
    METADATA_EXPRESSION,
    METADATA_EXPRESSION +
    " + ''.join('Requires-Dist: ' + value + '\\n' for value in info.get('dependencies', []))" +
    " + ''.join('Provides-Extra: ' + extra + '\\n' + "
    "''.join('Requires-Dist: ' + value + '; extra == ' + repr(extra) + '\\n' "
    "for value in values) for extra, values in info.get('optional-dependencies', {}).items())",
).replace(
    "        # Installers need every payload entry to remove old-version metadata.",
    "        wheel.writestr(name + '.revision.txt', Path('revision.txt').read_bytes())\n"
    "        # Installers need every payload entry to remove old-version metadata.",
)


def source_files(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file() and '.git' not in path.parts}


def markers(project):
    sites = list((project / '.venv/lib').glob('python*/site-packages'))
    assert len(sites) == 1, 'This fixture requires the managed Linux guest'
    return {name: (sites[0] / (name.replace('-', '_') + '.revision.txt')).read_text()
            for name in PACKAGES}


def assert_installed(project, commits):
    graph = {row['name']: row for row in qualification.installed_graph(project)}
    assert set(graph) == set(PACKAGES)
    installed = environments.installed_sources(project)
    for name, (repository, subdirectory) in PACKAGES.items():
        assert graph[name]['version'] == '0.1.0'
        assert isinstance(graph[name]['directUrl']['dir_info'], dict)
        assert graph[name]['cacheSource']['revision'] == commits[repository]
        assert not graph[name]['cacheSource']['dirty']
        assert {key: installed[name][key] for key in ('url', 'current', 'subdirectory')} == {
            'url': URLS[repository], 'current': commits[repository], 'subdirectory': subdirectory}
        assert installed[name]['cacheManaged'] and not installed[name].get('override')
    # Verify the fixture backend did not silently erase the edges under test.
    sites = list((project / '.venv/lib').glob('python*/site-packages'))
    distributions = {dist.metadata['Name']: dist for dist in
                     importlib.metadata.distributions(path=[str(path) for path in sites])}
    for name, (repository, _) in PACKAGES.items():
        if repository in {'recipes', 'memory'}:
            assert REQUIREMENT in distributions[name].requires
            if PACKAGES[name][1]:
                assert 'fixture-' + repository + ' @ git+' + URLS[repository] + '@main' in distributions[name].requires
    assert distributions['fixture-skills'].metadata.get_all('Provides-Extra') == ['remote-sources']
    assert distributions['fixture-skills'].requires == [
        "fixture-foundation; extra == 'remote-sources'"]
    return graph


@pytest.fixture
async def incremental_sources(tmp_path, monkeypatch, request):
    from amplifier_foundation import Bundle
    from amplifier_foundation.modules import activator
    from amplifier_foundation.modules.batch import DependencyBatch
    from amplifier_foundation.sources.resolver import SimpleSourceResolver

    # Real Bundle.prepare adds resolved module roots to sys.path. Restore them
    # after this test; the target wheel installation remains a different venv.
    monkeypatch.setattr(sys, 'path', list(sys.path))
    assert sys.platform == 'linux' and sys.version_info[:2] == (3, 13), 'Run in the owned Linux/Python 3.13 guest'
    uv = shutil.which('uv')
    assert uv, 'The managed guest must supply uv'
    assert FIXTURE_BACKEND.count('Requires-Dist:') == 2
    assert "name + '.revision.txt'" in FIXTURE_BACKEND
    with pytest.raises(importlib.metadata.PackageNotFoundError):
        importlib.metadata.distribution(FOUNDATION)
    kind = getattr(request, 'param', 'main')
    home = tmp_path / 'app'
    home.mkdir()
    shared = tmp_path / 'shared-settings'
    shared.mkdir()
    (shared / 'settings.yaml').write_text('{}')
    for key, value in {
        'AMPLIFIER_WEB_HOME': str(home), 'AMPLIFIER_HOME': str(shared),
        'AMPLIFIER_UNIFIED_RELEASE': '', 'XDG_CONFIG_HOME': str(tmp_path / 'config'),
        'UV_CACHE_DIR': str(tmp_path / 'uv-cache'), 'UV_NO_INDEX': '1',
        'UV_PYTHON_DOWNLOADS': 'never', 'PYTHONDONTWRITEBYTECODE': '1',
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_COUNT': '0',
        'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': 'file',
    }.items():
        monkeypatch.setenv(key, value)
    for key in ('AMPLIFIER_SOURCE_STORE', 'UV_OFFLINE', 'UV_NO_CONFIG', 'UV_CONFIG_FILE',
                'UV_OVERRIDE', 'UV_CONSTRAINT', 'UV_BUILD_CONSTRAINT', 'UV_PROJECT_ENVIRONMENT',
                'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_DIR', 'GIT_WORK_TREE'):
        monkeypatch.delenv(key, raising=False)
    repos = {name: tmp_path / ('origin-' + name) for name in URLS}
    transport = tmp_path / 'gitconfig'
    transport.write_text(''.join('[url "' + root.as_uri() + '"]\n insteadOf = ' + URLS[name] + '\n'
                                 for name, root in repos.items()))
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(transport))
    commits = {}
    for repository, root in repos.items():
        root.mkdir()
        git(root, 'init', '-b', 'main')
        git(root, 'config', 'user.name', 'Fixture')
        git(root, 'config', 'user.email', 'fixture@example.invalid')
        (root / 'bundle.md').write_text('---\nbundle:\n  name: fixture-' + repository + '\n---\n')
        for name, (owner, subdirectory) in PACKAGES.items():
            if owner != repository:
                continue
            path = root / subdirectory
            path.mkdir(parents=True, exist_ok=True)
            dependencies = [REQUIREMENT] if owner in {'recipes', 'memory'} else []
            if subdirectory and owner in {'recipes', 'memory'}:
                dependencies.append('fixture-' + owner + ' @ git+' + URLS[owner] + '@main')
            content = ('[project]\nname=' + json.dumps(name) + '\nversion="0.1.0"\n'
                       'dependencies=' + json.dumps(dependencies) + '\n')
            if subdirectory and owner in {'recipes', 'memory'}:
                content += '[tool.uv.sources]\nfixture-' + owner + '={path="../..",editable=true}\n'
            if repository == 'skills':
                optional = {'git': URLS['foundation'], 'branch': 'main'}
                if kind == 'pin':
                    optional = {'git': URLS['foundation'], 'rev': commits['foundation']}
                elif kind == 'fork':
                    optional['git'] += '-fork'
                elif kind == 'conditional':
                    optional['marker'] = "sys_platform == 'never-fixture'"
                elif kind == 'local':
                    optional = {'path': '../local-foundation', 'editable': True}
                else:
                    assert kind == 'main'
                content += ('[project.optional-dependencies]\nremote-sources=["fixture-foundation"]\n'
                            '[tool.uv.sources]\nfixture-foundation=' + environments.toml_value(optional) + '\n')
            content += '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n'
            (path / 'pyproject.toml').write_text(content)
            (path / 'backend.py').write_text(FIXTURE_BACKEND)
            (path / 'revision.txt').write_text(repository + ':0\n')
        git(root, 'add', '.')
        git(root, 'commit', '-m', 'installable fixture ' + repository)
        commits[repository] = git(root, 'rev-parse', 'HEAD')
    original_process = updates.process
    commands = []
    async def process(*args, **kwargs):
        commands.append(args)
        if args[:4] == ('git', 'remote', 'get-url', 'origin'):
            # insteadOf is transport, not authority to relabel a Git source file://.
            kwargs['env'] = {**os.environ, **(kwargs.get('env') or {}), 'GIT_CONFIG_GLOBAL': os.devnull}
        return await original_process(*args, **kwargs)
    monkeypatch.setattr(updates, 'process', process)
    manifest = tmp_path / 'runtime-base.toml'
    manifest.write_text('[project]\nname="fixture-runtime"\nversion="1"\nrequires-python=">=3.13"\ndependencies=[]\n')
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    manager = SimpleNamespace(home=home, diagnostics=Diagnostics())
    async def publish(**kwargs):
        pass
    manager.publish = publish
    manager.stage_cache_sources = lambda stage, rows: updates.UpdateManager.stage_cache_sources(manager, stage, rows)
    target = SimpleNamespace(project=None)
    original_activator = activator.ModuleActivator
    class OwnedActivator(original_activator):
        def __init__(self, *args, **kwargs):
            assert target.project is not None and target.project.is_relative_to(home / 'runtime')
            kwargs['install_python'] = str(target.project / '.venv/bin/python')
            super().__init__(*args, **kwargs)
    monkeypatch.setattr(activator, 'ModuleActivator', OwnedActivator)

    def make_stage(generation, previous=None):
        stage = environments.receipt_directory(home, generation)
        stage.mkdir(parents=True)
        (stage / 'shared-config').mkdir()
        (stage / 'shared-config/settings.yaml').write_text('{}')
        if previous is not None:
            shutil.copytree(previous / 'foundation', stage / 'foundation')
        return stage

    async def collect(stage, project, overrides, *, optional_only=False, reverse=False):
        target.project = project
        config = HostConfig(stage, stage, {}, stage / 'foundation', shared, global_only=True)
        cache = config.registry_home / 'cache'
        collector = reconciliation.Collector()
        roots = {}
        for repository in (('skills',) if optional_only else URLS):
            reference = 'git+' + URLS[repository] + '@main'
            roots[repository] = (await SimpleSourceResolver(cache_dir=cache).resolve(reference)).active_path
            collector.observe(reference, config)
        if optional_only:
            bundle = Bundle(name='fixture', base_path=roots['skills'],
                            tools=[{'module': 'skills-fixture', 'source': str(roots['skills'])}])
            expected = {'fixture-skills': roots['skills']}
        else:
            tools = [
                {'module': 'foundation-fixture', 'source': str(roots['foundation'])},
                {'module': 'skills-fixture', 'source': str(roots['skills'])},
                {'module': 'recipes-fixture', 'source': str(roots['recipes'] / 'modules/recipes-tool')},
            ]
            bundle = Bundle(name='fixture', base_path=roots['foundation'],
                source_base_paths=dict(reversed(list(roots.items()))) if reverse else roots,
                tools=list(reversed(tools)) if reverse else tools,
                hooks=[{'module': 'memory-fixture', 'source': str(roots['memory'] / 'modules/memory-hook')}],
                providers=[{'module': 'provider-fixture', 'source': str(roots['provider'])}])
            expected = {name: roots[repository] / subdirectory
                        for name, (repository, subdirectory) in PACKAGES.items()}
        batch = DependencyBatch()
        await bundle.prepare(strict=True, refresh_dependencies=True, dependency_batch=batch,
                             cache_dir=cache, install_overrides=overrides)
        assert batch.names == expected  # Includes both real root and sibling entries.
        assert not batch.installed and batch.report()['resolverTransactions'] == 0
        report = await collector.report(batch, config)
        return SimpleNamespace(project=project, batch=batch, collector=collector, config=config, roots=roots, report=report,
                               graph=qualification.installed_graph(project))

    async def empty_project(stage):
        project = environments.prepare_project(home, stage.name)
        await updates.process(uv, 'sync', '--project', str(project), '--python', sys.executable, timeout=90)
        overrides = await qualification.prepare_overrides(project, stage / 'runtime-install-overrides.txt')
        return project, overrides

    return SimpleNamespace(home=home, repos=repos, commits=commits, manager=manager, collect=collect,
                           make_stage=make_stage, empty_project=empty_project, commands=commands)


async def test_optional_main_constraint_survives_two_provider_only_updates(incremental_sources, monkeypatch):
    """Old code must reach authored-constraint/ProtectedRuntimeSource, not setup failure."""
    from amplifier_web import bundles, update_sources
    c = incremental_sources
    # Establish an already qualified installation, independently of the guard
    # under regression. No collector result or installed graph is fabricated.
    baseline = c.make_stage(BASELINE)
    project, overrides = await c.empty_project(baseline)
    initial = await c.collect(baseline, project, overrides)
    assert await initial.batch.install() == {'sources': 7, 'packages': 7, 'resolverTransactions': 1}
    assert_installed(project, c.commits)
    final = await qualification.freeze(c.manager, BASELINE, project)
    qualification.verify_recorded(final, baseline)
    assert_installed(final, c.commits)
    policies = json.loads((baseline / 'runtime-sources.json').read_text())
    assert set(policies) == set(PACKAGES)
    assert all(row['ref'] == 'main' and row['policyOrigin']['kind'] == 'verified-cache-branch'
               for row in policies.values())
    foundation_policy = policies[FOUNDATION]
    pointer = c.home / 'updates/active.json'
    pointer.write_text(json.dumps({'current': BASELINE, 'previous': None}))
    failure = c.home / 'updates/prior-failure.json'
    failure.write_text('{"status":"failed","attemptId":"retained-fixture-failure"}')
    failure_bytes = failure.read_bytes()
    retained = []
    def retain(stage, project):
        retained.append((stage, source_files(stage), project,
                         qualification.installed_graph(project), qualification.package_content(project), markers(project)))
    def assert_retained(pointer_bytes):
        assert pointer.read_bytes() == pointer_bytes and failure.read_bytes() == failure_bytes
        for stage, files, project, graph, payload, content in retained:
            assert source_files(stage) == files
            qualification.verify_recorded(project, stage)
            assert qualification.installed_graph(project) == graph
            assert qualification.package_content(project) == payload and markers(project) == content
    retain(baseline, final)
    monkeypatch.setattr(bundles, 'profile_candidates', lambda config: ['fixture'])
    monkeypatch.setattr(bundles, 'offered_profiles', lambda config: ['fixture'])
    # The fixture target intentionally contains no real Foundation installation.
    monkeypatch.setattr(update_sources, 'worker_supports_shared', lambda project: False)
    process = updates.process
    state = SimpleNamespace(stage=None, collections=[], events=[], cycle=0)
    async def adapter(*args, **kwargs):
        if not any(str(arg).endswith('update_probe.py') for arg in args):
            return await process(*args, **kwargs)
        stage = state.stage
        project = Path(args[args.index('--project') + 1])
        overrides = Path(args[args.index('--install-overrides') + 1])
        if '--read-only' in args:
            # Count the post-freeze boundary; no synthetic conversation mount.
            qualification.verify_recorded(project, stage)
            assert (stage / 'runtime-installed.json').is_file()
            state.events.append('compatibility-boundary')
            return ''
        entry = await c.collect(stage, project, overrides, reverse=state.cycle == 2)
        state.collections.append(entry)
        if '--source-policy-only' in args:
            state.events.append('collect')
            Path(args[args.index('--source-policy-only') + 1]).write_text(json.dumps(entry.report))
        else:
            assert '--profiles' in args and '--checked-sources' in args
            checked = json.loads(Path(args[args.index('--checked-sources') + 1]).read_text())
            selected = await alignment.checked_batch_sources(entry.batch, entry.collector, entry.config, checked)
            alignment.verify_checked_selection(checked, selected, exact=True)
            state.events.append('install')
            assert await entry.batch.install() == {'sources': 7, 'packages': 7, 'resolverTransactions': 1}
            assert_installed(project, c.commits)
        return ''
    monkeypatch.setattr(updates, 'process', adapter)
    previous = baseline
    provider_revisions = [c.commits['provider']]
    for cycle, generation in enumerate(('b' * 32, 'c' * 32), 1):
        # Only the unrelated provider changes, without a distribution version bump.
        (c.repos['provider'] / 'revision.txt').write_text('provider:' + str(cycle) + '\n')
        git(c.repos['provider'], 'commit', '-am', 'provider payload ' + str(cycle))
        c.commits['provider'] = git(c.repos['provider'], 'rev-parse', 'HEAD')
        provider_revisions.append(c.commits['provider'])
        assert len(set(provider_revisions)) == cycle + 1
        assert git(c.repos['foundation'], 'rev-parse', 'HEAD') == foundation_policy['revision']
        rows = await environments.update_inventory(c.home)
        assert {row.get('package') for row in rows} == set(PACKAGES)
        assert reconciliation.pending(c.home, previous.name, rows) is None
        # Availability comes from real local Git transport, not prefilled latest
        # values or a patched inventory. Only provider has new source bytes.
        for row in rows:
            assert row['eligible'] and row['ref'] == 'main' and not row.get('override')
            proof = await update_checks.ref_policy(row['url'], row['ref'], updates.process)
            assert proof['kind'] == 'branch'
            row['latest'] = proof['revision']
            row['status'] = 'update' if row['latest'] != row['current'] else 'current'
        assert [row['package'] for row in rows if row['status'] == 'update'] == [PROVIDER]
        c.manager.inventory = rows
        stage = c.make_stage(generation, previous)
        state.stage, state.cycle, state.collections, state.events = stage, cycle, [], []
        pointer_bytes = pointer.read_bytes()
        try:
            await updates.UpdateManager.validate(c.manager, stage, generation)
        except environments.ProtectedRuntimeSource as error:
            # Preserve the precise old-code discriminator before re-raising it.
            # Runtime sync has occurred; the Foundation batch has NOT installed.
            facts = error.diagnostic_facts
            assert facts['errorType'] == 'ValueError' and facts['package'] == FOUNDATION
            assert facts['reason'] in {'protected-runtime-source', 'runtime-source-constraint'}
            assert state.events == ['collect'] and len(state.collections) == 1
            entry = state.collections[0]
            assert entry.report['packages'][FOUNDATION] == {'status': 'authored-constraint'}
            assert all(entry.report['packages'][name]['status'] in {'selected', 'authored-constraint'}
                       for name in PACKAGES if name != FOUNDATION)
            assert not entry.batch.installed and entry.batch.report()['resolverTransactions'] == 0
            assert qualification.installed_graph(entry.project) == entry.graph
            assert not (stage / 'runtime-installed.json').exists()
            assert not (stage / 'runtime.lock').exists()
            assert_retained(pointer_bytes)
            raise
        assert state.events == ['collect', 'install', 'compatibility-boundary']
        assert len(state.collections) == 2
        first, fresh = state.collections
        assert first.batch is not fresh.batch and not first.batch.installed
        assert first.batch.report()['resolverTransactions'] == 0
        assert fresh.batch.report()['resolverTransactions'] == 1
        for entry in state.collections:
            assert entry.report['packages'][FOUNDATION]['status'] == 'selected'
            assert entry.report['packages'][FOUNDATION]['revision'] == foundation_policy['revision']
            assert {name: row['status'] for name, row in entry.report['packages'].items()} == dict.fromkeys(PACKAGES, 'selected')
        assert first.report['packages'][PROVIDER]['revision'] == provider_revisions[-2]
        assert fresh.report['packages'][PROVIDER]['revision'] == provider_revisions[-1]
        final = environments.project_path(c.home, generation)
        qualification.verify_recorded(final, stage)
        assert_installed(final, c.commits)
        assert json.loads((stage / 'runtime-sources.json').read_text())[FOUNDATION] == foundation_policy
        expected_markers = {name: repository + ':' + str(cycle if repository == 'provider' else 0) + '\n'
                            for name, (repository, _) in PACKAGES.items()}
        assert markers(final) == expected_markers
        assert_retained(pointer_bytes)
        retain(stage, final)
        # Fixture-only promotion after verified qualification enables the next
        # incremental cycle; validate() itself must never move the pointer.
        pointer.write_text(json.dumps({'current': generation, 'previous': previous.name}))
        previous = stage
    with pytest.raises(importlib.metadata.PackageNotFoundError):
        importlib.metadata.distribution(FOUNDATION)


@pytest.mark.parametrize('incremental_sources', ['pin', 'fork', 'conditional', 'local'], indirect=True)
async def test_optional_incompatible_source_refuses_before_batch_install(incremental_sources):
    c = incremental_sources
    stage = c.make_stage('d' * 32)
    project, overrides = await c.empty_project(stage)
    entry = await c.collect(stage, project, overrides)
    checked = {}
    for name, (repository, subdirectory) in PACKAGES.items():
        proof = await update_checks.ref_policy(URLS[repository], 'main', updates.process)
        assert proof['kind'] == 'branch' and proof['revision'] == c.commits[repository]
        checked[name] = {'url': URLS[repository], 'ref': 'main',
                         'revision': proof['revision'], 'subdirectory': subdirectory}
    (stage / 'runtime-sources.json').write_text(json.dumps(checked))
    before = source_files(stage)
    graph = qualification.installed_graph(project)
    start = len(c.commands)
    with pytest.raises(environments.ProtectedRuntimeSource) as caught:
        await alignment.align_checked_sources(c.manager, stage, selection=entry.report)
    # Keep these behavioral controls usable on old source too. Exact wording of
    # the newly distinguished constraint diagnostic has its own focused tests.
    facts = caught.value.diagnostic_facts
    assert facts['errorType'] == 'ValueError' and facts['package'] == FOUNDATION
    assert facts['reason'] in {'protected-runtime-source', 'runtime-source-constraint'}
    assert entry.report['packages'][FOUNDATION]['status'] == 'authored-constraint'
    assert not entry.batch.installed and entry.batch.report()['resolverTransactions'] == 0
    assert source_files(stage) == before and qualification.installed_graph(project) == graph == []
    assert not any({'fetch', 'checkout', 'restore'}.intersection(command)
                   for command in c.commands[start:] if command[0] == 'git')
    assert not (stage / 'runtime.lock').exists() and not (stage / 'runtime-installed.json').exists()


async def test_compatible_optional_source_does_not_select_foundation(incremental_sources):
    c = incremental_sources
    stage = c.make_stage('e' * 32)
    project, overrides = await c.empty_project(stage)
    entry = await c.collect(stage, project, overrides, optional_only=True)
    assert set(entry.batch.names) == {'fixture-skills'}
    assert set(entry.report['packages']) == {'fixture-skills'}
    assert entry.report['packages']['fixture-skills']['status'] == 'selected'
    assert FOUNDATION not in entry.report['packages']
    assert not entry.batch.installed and qualification.installed_graph(project) == []
