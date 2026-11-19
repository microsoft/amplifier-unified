"""Authored branch policy survives immutable installs without unpinning overrides."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import pytest

from amplifier_web import runtime_environment as environments
from amplifier_web import runtime_source_policy as policy
from test_runtime_ref_identity import normalized_runtime, ROW, REVISION


@pytest.fixture
def prepared(tmp_path):
    home = tmp_path / 'app'
    generation = 'a' * 32
    project = environments.prepare_project(home, generation)
    content = ('[project]\nname="worker"\nversion="1"\ndependencies=["fixture-component"]\n'
               '[tool.uv.sources]\nfixture-component={git="' + ROW['url'] + '",branch="main",subdirectory="module"}\n')
    (project / 'pyproject.toml').write_text(content)
    source = ROW['url'] + '?' + urlencode({'rev': REVISION, 'subdirectory': 'module'}) + '#' + REVISION
    (project / 'uv.lock').write_text('version=1\n[[package]]\nname="fixture-component"\nversion="1"\nsource={git="' + source + '"}\n')
    graph = [{'name': 'fixture-component', 'version': '1', 'directUrl': {
        'url': ROW['url'], 'subdirectory': 'module',
        'vcs_info': {'vcs': 'git', 'requested_revision': REVISION, 'commit_id': REVISION}}}]
    return home, generation, project, graph


def chosen(prepared):
    return policy.freeze_policies(*prepared)['fixture-component']


def test_new_unchecked_dependency_recovers_prepared_branch_before_freeze(prepared):
    result = chosen(prepared)
    assert result['ref'] == 'main'
    assert result['revision'] == REVISION
    assert result['policyOrigin'] == policy.origin(result, 'prepared-manifest',
        manifestSha256=hashlib.sha256((prepared[2] / 'pyproject.toml').read_bytes()).hexdigest())


@pytest.mark.parametrize('kind', ['dependency', 'group', 'override'])
def test_untyped_requirement_does_not_unpin_normalized_revision(prepared, kind):
    project = prepared[2]
    requirement = 'fixture-component @ git+' + ROW['url'] + '@release/next#subdirectory=module'
    prefix = '[project]\nname="worker"\nversion="1"\n'
    suffix = {'dependency': 'dependencies=[' + json.dumps(requirement) + ']\n',
              'group': '[dependency-groups]\nmanaged=[' + json.dumps(requirement) + ']\n',
              'override': '[tool.uv]\noverride-dependencies=[' + json.dumps(requirement) + ']\n'}[kind]
    content = (prefix + suffix).encode()
    if kind == 'group':
        content = environments.augmented_manifest(prefix.encode(), [{**ROW, 'ref': 'release/next'}])
    (project / 'pyproject.toml').write_bytes(content)
    assert chosen(prepared)['ref'] == REVISION


@pytest.mark.parametrize('case', ['authored-pin', 'installed-tag', 'installed-branch', 'fork', 'subdirectory',
                                 'different-commit', 'different-lock-branch', 'ambiguous-declaration', 'missing-declaration'])
def test_explicit_or_uncertain_identity_is_not_relabelled(prepared, case):
    _, _, project, graph = prepared
    manifest = project / 'pyproject.toml'
    lock = project / 'uv.lock'
    direct = graph[0]['directUrl']
    if case == 'authored-pin':
        manifest.write_text(manifest.read_text().replace('branch="main"', 'rev="' + REVISION + '"'))
    elif case in {'installed-tag', 'installed-branch'}:
        direct['vcs_info']['requested_revision'] = 'v1.2.3' if case == 'installed-tag' else 'feature'
    elif case == 'fork':
        direct['url'] += '-fork'
    elif case == 'subdirectory':
        direct['subdirectory'] = 'other'
    elif case == 'different-commit':
        direct['vcs_info']['commit_id'] = 'b' * 40
    elif case == 'different-lock-branch':
        lock.write_text(lock.read_text().replace('rev=' + REVISION, 'branch=other'))
    elif case == 'ambiguous-declaration':
        manifest.write_text(manifest.read_text() + '[dependency-groups]\nother=["fixture-component @ git+' + ROW['url'] + '@stable#subdirectory=module"]\n')
    elif case == 'missing-declaration':
        manifest.write_text('[project]\nname="worker"\nversion="1"\n')
    assert chosen(prepared)['ref'] != 'main'


def test_adjacent_newer_cache_never_reinterprets_git_distribution(prepared):
    home, generation, project, graph = prepared
    (project / 'pyproject.toml').write_text('[project]\nname="worker"\nversion="1"\n')
    cache = environments.receipt_directory(home, generation) / 'foundation/cache/fixture'
    cache.mkdir(parents=True)
    (cache / '.amplifier_cache_meta.json').write_text(json.dumps({
        'git_url': ROW['url'], 'ref': 'main', 'commit': 'b' * 40}))
    assert chosen(prepared)['ref'] == REVISION


def test_legacy_pin_is_unchanged_and_missing_origin_is_visible(prepared):
    home, generation, project, graph = prepared
    receipt = environments.receipt_directory(home, generation)
    receipt.mkdir(parents=True, exist_ok=True)
    (home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    (receipt / 'runtime-sources.json').write_text(json.dumps({'fixture-component': {
        'url': ROW['url'], 'ref': REVISION, 'revision': REVISION, 'subdirectory': 'module'}}))
    row = next(row for row in environments.inventory(home, graph=graph) if row.get('package') == 'fixture-component')
    assert row['ref'] == REVISION and not row['eligible']
    assert row['policyOrigin']['kind'] == 'unrecorded'
    assert any('origin is not recorded' in text for text in row['usageEvidence'])
    selected = environments.verify_resolution(environments.locked_sources(project)['fixture-component'], {**row, 'latest': REVISION})
    assert selected['policyOrigin']['kind'] == 'unrecorded'


async def test_stage_freeze_carries_origin_and_next_update_rebinds_exact_revision(normalized_runtime):
    from amplifier_web.runtime_qualification import freeze
    manager, generation, receipt, graph = normalized_runtime
    prepared = await environments.stage(manager, generation, [ROW], finalize=False)
    await freeze(manager, generation, prepared)
    recorded = json.loads((receipt / 'runtime-sources.json').read_text())[ROW['package']]
    assert recorded['policyOrigin'] == policy.origin(recorded, 'checked-source')
    (manager.home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    observed = next(row for row in environments.inventory(manager.home, graph=graph) if row.get('package') == ROW['package'])
    assert observed['policyOrigin'] == recorded['policyOrigin']
    newer = 'd' * 40
    source = environments.urlsplit(ROW['url'] + '?rev=' + newer + '&subdirectory=module#' + newer)
    result = environments.verify_resolution(source, {**observed, 'latest': newer})
    assert result['ref'] == 'main' and result['revision'] == newer
    assert result['policyOrigin'] == policy.origin(result, 'checked-source')


async def test_freeze_recovers_new_authored_dependency_with_no_checked_row(normalized_runtime):
    from amplifier_web.runtime_qualification import freeze
    manager, generation, receipt, graph = normalized_runtime
    project = await environments.stage(manager, generation, [ROW], finalize=False)
    (receipt / 'runtime-sources.json').write_text('{}')
    manifest = project / 'pyproject.toml'
    manifest.write_text(manifest.read_text().replace('rev="main"', 'branch="main"'))
    await freeze(manager, generation, project)
    saved = json.loads((receipt / 'runtime-sources.json').read_text())[ROW['package']]
    assert saved['ref'] == 'main'
    assert saved['policyOrigin']['kind'] == 'prepared-manifest'


@pytest.fixture
def app_generated(prepared, monkeypatch, request):
    """The old app-direct @main -> unresolved/fullSHA frozen receipt shape."""
    home, generation, project, graph = prepared
    requirement = 'fixture-component @ git+' + ROW['url'] + '@main#subdirectory=module'
    content = ('[project]\nname="worker"\nversion="1"\ndependencies=[' + json.dumps(requirement) +
               ']\n[tool.uv]\noverride-dependencies=[' + json.dumps(requirement) + ']\n').encode()
    if getattr(request, 'param', None) == 'source-table':
        content = ('[project]\nname="worker"\nversion="1"\ndependencies=["fixture-component"]\n'
                   '[tool.uv.sources]\nfixture-component={git="' + ROW['url'] +
                   '",rev="main",subdirectory="module"}\n').encode()
    manifest = home.parent / 'app.toml'
    manifest.write_bytes(content)
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    monkeypatch.setattr('amplifier_web.app_updates.installed_extras', lambda: [])
    (project / 'pyproject.toml').write_bytes(content)
    saved = policy.freeze_policies(*prepared)
    assert saved['fixture-component']['policyOrigin']['kind'] == 'unresolved-reference'
    receipt = environments.receipt_directory(home, generation)
    receipt.mkdir(parents=True, exist_ok=True)
    # Faithful old writer inputs; prospective recovery may not edit any of them.
    (receipt / 'runtime-base.toml').write_bytes(content)
    (receipt / 'runtime.toml').write_bytes(content)
    (receipt / 'runtime.lock').write_bytes((project / 'uv.lock').read_bytes())
    (receipt / 'runtime-installed.json').write_text(json.dumps(graph))
    (receipt / 'runtime-sources.json').write_text(json.dumps(saved))
    monkeypatch.setattr(environments, 'project_path', lambda *args, **kwargs: project)
    rows = [{'kind': 'runtime dependency', 'package': 'fixture-component',
             'current': REVISION, 'eligible': False, 'status': 'pinned', **saved['fixture-component']}]
    monkeypatch.setattr('amplifier_web.runtime_qualification.installed_graph', lambda _: graph)
    return home, generation, project, graph, receipt, manifest, rows


@pytest.mark.parametrize('case', ['branch', 'tag', 'same-name-tag', 'unknown', 'moved',
                                 'separate-config', 'external-override', 'authored-pin', 'local-source',
                                 'during-proof'])
async def test_new_app_direct_policy_requires_exact_branch_kind(app_generated, monkeypatch, case):
    home, generation, project, graph, receipt, manifest, _ = app_generated
    baseline = manifest.read_bytes()
    if case == 'authored-pin':
        baseline = baseline.replace(b'@main', ('@' + REVISION).encode())
        (project / 'pyproject.toml').write_bytes(baseline)
    if case == 'local-source':
        baseline += b'\n[tool.uv.sources]\nfixture-component={path="../custom"}\n'
        (project / 'pyproject.toml').write_bytes(baseline)
    if case == 'separate-config': (project / 'uv.toml').write_text('')
    if case == 'external-override': monkeypatch.setenv('UV_OVERRIDE', '/not-read/custom.txt')
    calls = []
    async def runner(*args, **kwargs):
        calls.append(args)
        if case == 'during-proof':
            (project / 'pyproject.toml').write_bytes(baseline + b'\n# concurrent source edit\n')
        return (('b' * 40 if case == 'moved' else REVISION) + '\trefs/heads/main\n'
                if case in {'branch', 'same-name-tag', 'moved', 'during-proof'} else '') + (
                REVISION + '\trefs/tags/main\n' if case in {'tag', 'same-name-tag'} else '')
    policies = policy.freeze_policies(home, generation, project, graph)
    if case == 'during-proof':
        with pytest.raises(environments.ProtectedRuntimeSource):
            await policy.qualify_app_policies(policies, {}, project, baseline, runner)
        assert policies['fixture-component']['ref'] == REVISION
        return
    result = await policy.qualify_app_policies(policies, {}, project, baseline, runner)
    saved = result['fixture-component']
    assert (saved['ref'] == 'main') is (case == 'branch')
    assert len(calls) <= 1
    if case == 'branch':
        assert saved['policyOrigin']['kind'] == 'verified-app-declaration'


@pytest.mark.parametrize('case', [
    'branch', 'tag', 'same-name-tag', 'unknown', 'missing-proof', 'changed-graph',
    'changed-lock', 'changed-manifest', 'changed-declaration', 'authored-pin',
    'unknown-origin', 'wrong-original-ref', 'wrong-policy', 'fork', 'local', 'dirty',
    'separate-config', 'external-override', 'during-proof',
])
@pytest.mark.parametrize('app_generated', ['requirement-url', 'source-table'], indirect=True)
async def test_old_app_direct_recovery_preserves_unexplained_pins_and_receipts(app_generated, monkeypatch, case):
    home, generation, project, graph, receipt, manifest, rows = app_generated
    if case == 'missing-proof': (receipt / 'runtime-installed.json').unlink()
    if case == 'changed-graph': graph[0]['version'] = 'changed'
    if case == 'changed-lock': (project / 'uv.lock').write_bytes(b'version=1\n')
    if case == 'changed-manifest': (project / 'pyproject.toml').write_bytes(b'[project]\n')
    if case in {'changed-declaration', 'authored-pin'}:
        ref = ('other' if case == 'changed-declaration' else REVISION).encode()
        manifest.write_bytes(manifest.read_bytes().replace(b'@main', b'@' + ref)
                             .replace(b'rev="main"', b'rev="' + ref + b'"'))
    if case == 'unknown-origin': rows[0]['policyOrigin']['kind'] = 'unrecorded'
    if case == 'wrong-original-ref': rows[0]['policyOrigin']['originalRef'] = 'other'
    if case == 'wrong-policy': (receipt / 'runtime-sources.json').write_text('{}')
    if case == 'fork': rows[0]['url'] += '-fork'
    if case == 'local': rows[0]['override'] = True
    if case == 'dirty': rows[0]['cacheManaged'] = True
    if case == 'separate-config': (project / 'uv.toml').write_text('')
    if case == 'external-override': monkeypatch.setenv('UV_OVERRIDE', '/not-read/custom.txt')
    before = {path: path.read_bytes() for path in receipt.iterdir()}
    calls = []
    async def runner(*args, **kwargs):
        calls.append(args)
        if case == 'during-proof':
            manifest.write_bytes(manifest.read_bytes() + b'\n# concurrent policy edit\n')
        # A branch can have moved since installation: old bytes are independently
        # verified; ordinary Check must still bind the next candidate's commit.
        return (('b' * 40) + '\trefs/heads/main\n' if case in {'branch', 'same-name-tag', 'during-proof'} else '') + (
                REVISION + '\trefs/tags/main\n' if case in {'tag', 'same-name-tag'} else '')
    if case == 'during-proof':
        with pytest.raises(environments.ProtectedRuntimeSource):
            await policy.recover_app_policies(home, generation, rows, graph, runner)
    else:
        result = await policy.recover_app_policies(home, generation, rows, graph, runner)
        assert (result[0]['ref'] == 'main') is (case == 'branch')
        assert result[0]['current'] == REVISION
        if case == 'branch':
            assert result[0]['eligible']
            assert result[0]['policyOrigin']['kind'] == 'verified-app-declaration'
        else:
            assert not result[0]['eligible']
    assert len(calls) <= 1
    assert {path: path.read_bytes() for path in receipt.iterdir()} == before


@pytest.mark.parametrize('case', ['owned', 'dirty', 'outside', 'changed-subdirectory'])
def test_only_installed_owned_clean_cache_can_record_a_declaration(prepared, case):
    home, generation, project, graph = prepared
    root = environments.receipt_directory(home, generation) / 'foundation/cache/fixture'
    root.mkdir(parents=True)
    installed = root / 'module'
    if case == 'outside':
        installed = home.parent / 'other-cache/module'
    graph[:] = [{'name': 'fixture-component', 'version': '1', 'path': str(installed),
                 'directUrl': {'url': installed.as_uri(), 'dir_info': {}},
                 'cacheSource': {'url': ROW['url'], 'ref': 'main', 'revision': REVISION,
                                 'subdirectory': 'other/nested' if case == 'changed-subdirectory' else 'module',
                                 'dirty': case == 'dirty'}}]
    result = policy.freeze_policies(*prepared)
    if case == 'owned':
        assert result['fixture-component']['ref'] == 'main'
        assert result['fixture-component']['policyOrigin']['kind'] == 'owned-cache-declaration'
    else:
        assert 'fixture-component' not in result


@pytest.mark.parametrize('ancestor', ['cache', 'foundation', 'generation', 'home', 'module'])
def test_linked_cache_or_owner_ancestor_cannot_establish_policy(prepared, ancestor):
    home, generation, project, graph = prepared
    receipt = environments.receipt_directory(home, generation)
    root = receipt / 'foundation/cache/fixture'
    installed = root / 'module'
    installed.mkdir(parents=True)
    target = {'cache': root.parent, 'foundation': root.parent.parent,
              'generation': receipt, 'home': home, 'module': installed}[ancestor]
    external = home.parent / ('external-' + ancestor)
    target.rename(external)
    target.symlink_to(external, target_is_directory=True)
    graph[:] = [{'name': 'fixture-component', 'version': '1', 'path': str(installed),
                 'directUrl': {'url': installed.as_uri(), 'dir_info': {}},
                 'cacheSource': {'url': ROW['url'], 'ref': 'main', 'revision': REVISION,
                                 'subdirectory': 'module', 'dirty': False}}]
    assert 'fixture-component' not in policy.freeze_policies(*prepared)
    # installed_graph resolves directory links before producing path evidence.
    graph[0]['path'] = str(installed.resolve())
    assert 'fixture-component' not in policy.freeze_policies(*prepared)


@pytest.mark.parametrize('section', ['[project.optional-dependencies]\ncustom=',
                                    '[tool.uv]\ndev-dependencies=',
                                    '[tool.uv]\nconstraint-dependencies=',
                                    '[tool.uv]\nbuild-constraint-dependencies='])
def test_pin_in_other_authored_requirement_collections_is_not_converted(section):
    branch = 'fixture @ git+https://example.invalid/fixture@main'
    pin = 'fixture @ git+https://example.invalid/fixture@' + REVISION
    content = ('[project]\nname="worker"\nversion="1"\ndependencies=[' + json.dumps(branch) + ']\n'
               + section + '[' + json.dumps(pin) + ']\n').encode()
    assert 'fixture' not in policy.declarations(content)


def test_marked_requirement_participates_in_conflict_detection():
    branch = 'fixture @ git+https://example.invalid/fixture@main'
    pin = 'fixture @ git+https://example.invalid/fixture@' + REVISION + ' ; python_version >= "3.13"'
    content = ('[project]\nname="worker"\nversion="1"\ndependencies=' + json.dumps([branch, pin])).encode()
    assert 'fixture' not in policy.declarations(content)
    content = ('[project]\nname="worker"\nversion="1"\ndependencies=' + json.dumps([branch + ' ; python_version >= "3.13"'])).encode()
    assert 'fixture' not in policy.declarations(content)


@pytest.mark.parametrize('alternate', [
    '[{git="https://example.invalid/fixture",rev="' + REVISION + '",marker="python_version >= \'3.13\'"}]',
    '[{git="https://example.invalid/fixture",rev="main"},{git="https://example.invalid/fixture",tag="v1.0"}]',
    '[{path="../local"}]', '[]', '["unsupported"]', '{path="../local"}',
])
def test_marker_source_arrays_and_unknown_overrides_do_not_hide_a_pin(alternate):
    content = ('[project]\nname="worker"\nversion="1"\ndependencies=["fixture @ git+https://example.invalid/fixture@main"]\n'
               '[tool.uv.sources]\nfixture=' + alternate + '\n').encode()
    assert 'fixture' not in policy.declarations(content)


def test_marker_source_array_without_selection_does_not_authorize_recovery():
    content = b'''[project]
name="worker"
version="1"
[tool.uv.sources]
fixture=[{git="https://example.invalid/fixture",rev="main",marker="python_version >= '3.13'"}]
'''
    assert 'fixture' not in policy.declarations(content)

@pytest.mark.parametrize('kind', ['explicit-pin', 'local-source', 'symlink', 'empty'])
def test_separate_uv_configuration_withholds_manifest_branch_recovery(prepared, kind):
    home, generation, project, graph = prepared
    config = project / 'uv.toml'
    if kind == 'symlink':
        config.symlink_to(project / 'missing-config')
    else:
        config.write_text({'explicit-pin': '[sources]\nfixture-component={git="' + ROW['url'] + '",rev="' + REVISION + '"}\n',
                           'local-source': '[sources]\nfixture-component={path="../local"}\n',
                           'empty': ''}[kind])
    saved = policy.freeze_policies(*prepared)['fixture-component']
    assert saved['ref'] == REVISION
    assert saved['policyOrigin']['kind'] == 'resolution-reference'


@pytest.mark.parametrize('case', ['tag', 'branch-tag-conflict', 'inactive-requirement',
                                 'inactive-source', 'optional', 'unused-group', 'optional-source', 'ambiguous-rev', 'ambiguous-uri'])
def test_normalized_ref_needs_active_unambiguous_non_tag_authority(prepared, case):
    _, _, project, _ = prepared
    requirement = 'fixture-component @ git+' + ROW['url'] + '@stable#subdirectory=module'
    data = {'project': {'name': 'worker', 'version': '1'}}
    source = {'git': ROW['url'], 'subdirectory': 'module'}
    if case in {'tag', 'ambiguous-rev'}:
        data['project']['dependencies'] = ['fixture-component']
        data['tool'] = {'uv': {'sources': {'fixture-component': {**source, ('tag' if case == 'tag' else 'rev'): 'stable'}}}}
    elif case == 'branch-tag-conflict':
        data['project']['dependencies'] = ['fixture-component']
        data['tool'] = {'uv': {'sources': {'fixture-component': [
            {**source, 'branch': 'stable', 'marker': "sys_platform == 'linux'"},
            {**source, 'tag': 'stable', 'marker': "sys_platform != 'linux'"}]}}}
    elif case == 'ambiguous-uri':
        data['project']['dependencies'] = [requirement]
    elif case == 'inactive-requirement':
        data['project']['dependencies'] = [requirement + ' ; sys_platform == "unavailable-platform"']
    elif case == 'inactive-source':
        data['project']['dependencies'] = ['fixture-component']
        data['tool'] = {'uv': {'sources': {'fixture-component': {
            **source, 'branch': 'stable', 'marker': "sys_platform == 'unavailable-platform'"}}}}
    elif case == 'optional':
        data['project']['optional-dependencies'] = {'unused': [requirement]}
    elif case == 'unused-group':
        data['dependency-groups'] = {'unused': [requirement]}
    else:
        data['project']['optional-dependencies'] = {'unused': ['fixture-component']}
        data['tool'] = {'uv': {'sources': {'fixture-component': {**source, 'branch': 'stable'}}}}
    content = '\n'.join(json.dumps(key) + ' = ' + environments.toml_value(value) for key, value in data.items())
    (project / 'pyproject.toml').write_text(content)
    saved = chosen(prepared)
    assert saved['ref'] == REVISION
    assert saved['policyOrigin']['kind'] == ('tag-reference' if case == 'tag' else
        'unresolved-reference' if case in {'ambiguous-rev', 'ambiguous-uri'} else 'resolution-reference')


@pytest.mark.parametrize('kind', ['unresolved-reference', 'tag-reference'])
async def test_unrelated_stage_freeze_preserves_protected_origin(normalized_runtime, monkeypatch, kind):
    from amplifier_web.runtime_qualification import freeze
    manager, generation, receipt, graph = normalized_runtime
    held = {**ROW, 'ref': REVISION, 'current': REVISION, 'latest': REVISION, 'eligible': False}
    held['policyOrigin'] = policy.origin({**held, 'revision': REVISION}, kind, originalRef='stable')
    manifest = environments.manifest_path()
    manifest.write_text(manifest.read_text().replace('rev="main"', 'rev="' + REVISION + '"'))
    async def current_manifest(_):
        return [held], manifest.read_bytes()
    monkeypatch.setattr(environments, 'update_manifest', current_manifest)
    project = await environments.stage(manager, generation, [], finalize=False)
    await freeze(manager, generation, project)
    saved = json.loads((receipt / 'runtime-sources.json').read_text())[ROW['package']]
    assert saved['ref'] == REVISION
    assert saved['policyOrigin'] == held['policyOrigin']
    (manager.home / 'updates/active.json').write_text(json.dumps({'current': generation}))
    row = next(row for row in environments.inventory(manager.home, graph=graph) if row.get('package') == ROW['package'])
    assert not row['eligible']
    assert row['policyOrigin'] == held['policyOrigin']


@pytest.mark.parametrize('lock', ['absent', 'mismatched'])
def test_untyped_installed_name_alone_is_never_moving_policy(prepared, lock):
    _, _, project, graph = prepared
    graph[0]['directUrl']['vcs_info']['requested_revision'] = 'stable'
    (project / 'pyproject.toml').write_text('[project]\nname="worker"\nversion="1"\n')
    if lock == 'absent':
        (project / 'uv.lock').unlink()
    saved = chosen(prepared)
    assert saved['ref'] == REVISION
    assert saved['policyOrigin']['kind'] == 'unresolved-reference'
    assert saved['policyOrigin']['originalRef'] == 'stable'


@pytest.mark.parametrize('collection', ['override-dependencies', 'constraint-dependencies'])
def test_constraint_without_selected_dependency_does_not_authorize_branch(prepared, collection):
    _, _, project, _ = prepared
    (project / 'pyproject.toml').write_text('[project]\nname="worker"\nversion="1"\n'
        '[tool.uv]\n' + collection + '=["fixture-component"]\n'
        '[tool.uv.sources]\nfixture-component={git="' + ROW['url'] + '",branch="stable",subdirectory="module"}\n')
    assert chosen(prepared)['ref'] == REVISION
