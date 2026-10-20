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
    content = ('[project]\nname="worker"\nversion="1"\n'
               '[tool.uv.sources]\nfixture-component={git="' + ROW['url'] + '",rev="main",subdirectory="module"}\n')
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
def test_transitive_authored_requirement_is_not_a_standing_pin(prepared, kind):
    project = prepared[2]
    requirement = 'fixture-component @ git+' + ROW['url'] + '@release/next#subdirectory=module'
    prefix = '[project]\nname="worker"\nversion="1"\n'
    suffix = {'dependency': 'dependencies=[' + json.dumps(requirement) + ']\n',
              'group': '[dependency-groups]\nmanaged=[' + json.dumps(requirement) + ']\n',
              'override': '[tool.uv]\noverride-dependencies=[' + json.dumps(requirement) + ']\n'}[kind]
    (project / 'pyproject.toml').write_text(prefix + suffix)
    assert chosen(prepared)['ref'] == 'release/next'


@pytest.mark.parametrize('case', ['authored-pin', 'installed-tag', 'installed-branch', 'fork', 'subdirectory',
                                 'different-commit', 'different-lock-branch', 'ambiguous-declaration', 'missing-declaration'])
def test_explicit_or_uncertain_identity_is_not_relabelled(prepared, case):
    _, _, project, graph = prepared
    manifest = project / 'pyproject.toml'
    lock = project / 'uv.lock'
    direct = graph[0]['directUrl']
    if case == 'authored-pin':
        manifest.write_text(manifest.read_text().replace('rev="main"', 'rev="' + REVISION + '"'))
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
    await freeze(manager, generation, project)
    saved = json.loads((receipt / 'runtime-sources.json').read_text())[ROW['package']]
    assert saved['ref'] == 'main'
    assert saved['policyOrigin']['kind'] == 'prepared-manifest'


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
