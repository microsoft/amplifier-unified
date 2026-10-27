"""Restriction compatibility must not invent package-selection authority."""
import json
from itertools import permutations

import pytest

from amplifier_web import runtime_source_policy as policy
from amplifier_web import runtime_source_alignment as alignment
from amplifier_web import runtime_policy_reconciliation as migration
from test_runtime_policy_reconciliation import selected, URL, NAME, git


@pytest.mark.parametrize('kind,accepted', [
    ('branch', True), ('rev', True), ('tag', False), ('pin', False),
    ('fork', False), ('conditional', False), ('local', False), ('subdirectory', False), ('dynamic', False),
])
async def test_optional_source_restricts_independently_selected_package(selected, kind, accepted):
    s = selected
    source = {'git': URL, 'branch': 'main'}
    if kind in {'rev', 'tag'}:
        source = {'git': URL, kind: 'main'}
    elif kind == 'pin':
        source = {'git': URL, 'rev': s.old}
    elif kind == 'fork':
        source['git'] += '-fork'
    elif kind == 'conditional':
        source['marker'] = "sys_platform == 'linux'"
    elif kind == 'local':
        source = {'path': '../..'}
    elif kind == 'subdirectory':
        source['subdirectory'] = 'different'
    from amplifier_web.runtime_environment import toml_value
    sibling = s.root / 'modules/skills'; sibling.mkdir(parents=True)
    raw = ('[project]\nname="fixture-skills"\nversion="1"\n' +
           ('dynamic=["dependencies"]\n' if kind == 'dynamic' else 'dependencies=[]\n') +
           '[project.optional-dependencies]\nremote=["' + NAME + '"]\n'
           '[tool.uv.sources]\n' + NAME + '=' + toml_value(source) + '\n').encode()
    (sibling / 'pyproject.toml').write_bytes(raw)
    git(s.root, 'add', 'modules'); git(s.root, 'commit', '-m', 'optional source')
    head = git(s.root, 'rev-parse', 'HEAD')
    s.marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': head}))
    s.batch.names['fixture-skills'] = sibling
    assert policy.declarations(raw) == {}  # Not authority to select this optional package.
    report = await s.collector.report(s.batch, s.config)
    assert report['packages'][NAME]['status'] == ('selected' if accepted else 'authored-constraint')
    checked = {NAME: {'url': URL, 'ref': 'main', 'subdirectory': '', 'revision': head}}
    if accepted:
        alignment.verify_checked_selection(checked, report, exact=True)
    else:
        with pytest.raises(ValueError) as error:
            alignment.verify_checked_selection(checked, report, exact=True)
        assert error.value.diagnostic_facts['reason'] == 'runtime-source-constraint'


@pytest.mark.parametrize('order', list(permutations(('rev', 'branch', 'tag'))))
async def test_conflicting_tag_cannot_be_erased_by_later_compatible_edges(selected, order):
    s = selected
    for index, kind in enumerate(order):
        path = s.root / ('modules/edge-' + str(index)); path.mkdir(parents=True)
        name = 'fixture-edge-' + str(index)
        value = ('[project]\nname="' + name + '"\nversion="1"\ndependencies=["' + NAME + '"]\n'
                 '[tool.uv.sources]\n' + NAME + '={git="' + URL + '",' + kind + '="main"}\n')
        (path / 'pyproject.toml').write_text(value)
        s.batch.names[name] = path
    git(s.root, 'add', 'modules'); git(s.root, 'commit', '-m', 'constraint alternatives')
    s.marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': git(s.root, 'rev-parse', 'HEAD')}))
    report = await s.collector.report(s.batch, s.config)
    assert report['packages'][NAME]['status'] == 'authored-constraint'


def test_same_manifest_constraint_alternatives_keep_unknown_and_do_not_select():
    raw = ('[project]\nname="fixture"\nversion="1"\n'
           '[project.optional-dependencies]\nremote=["' + NAME + '"]\n'
           '[tool.uv.sources]\n' + NAME + '=[{path="../local"},{git="' + URL + '",branch="main"}]\n').encode()
    assert policy.declarations(raw) == {}
    assert None in policy.constraint_declarations(raw)[NAME]


@pytest.mark.parametrize('alias_fork', [False, True])
@pytest.mark.parametrize('optional', [False, True])
async def test_runtime_git_sibling_keeps_its_dependency_but_not_redundant_local_uv_mapping(selected, alias_fork, optional):
    s = selected
    child = s.root / 'modules/tool'; child.mkdir(parents=True)
    (child / 'pyproject.toml').write_text(
        '[project]\nname="fixture-tool"\nversion="1"\n' +
        ('[project.optional-dependencies]\nrunner=' if optional else 'dependencies=') +
        '["' + NAME + ' @ git+' + URL + '@main"]\n'
        '[tool.uv.sources]\n' + NAME + '={path="../..",editable=true}\n' +
        (NAME.replace('-', '_') + '={git="' + URL + '-fork",branch="main"}\n' if alias_fork else ''))
    git(s.root, 'add', 'modules'); git(s.root, 'commit', '-m', 'runtime sibling and dev mapping')
    s.marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': git(s.root, 'rev-parse', 'HEAD')}))
    s.batch.names['fixture-tool'] = child
    report = await s.collector.report(s.batch, s.config)
    assert report['packages'][NAME]['status'] == ('authored-constraint' if alias_fork else 'selected')


@pytest.mark.parametrize('change', [
    'none', 'unlisted', 'glob', 'excluded', 'alias', 'unknown-extra', 'conditional',
    'runtime', 'specifier', 'override', 'local', 'nested',
])
async def test_workspace_development_sources_require_exact_selected_members(selected, change):
    s = selected
    child = s.root / 'modules/tool'; child.mkdir(parents=True)
    child_name = 'fixture-tool'
    (child / 'pyproject.toml').write_text('[project]\nname="fixture-tool"\nversion="1"\n')
    dev = [NAME + '[mcp,convert]', child_name]
    if change == 'unknown-extra': dev[1] += '[missing]'
    if change == 'conditional': dev[1] += "; python_version >= '3.13'"
    if change == 'specifier': dev[1] += '==1'
    members = ['.', 'modules/tool']
    if change == 'unlisted': members = ['.']
    if change == 'glob': members = ['.', 'modules/*']
    raw = ('[project]\nname="' + NAME + '"\nversion="1"\n' +
           ('dependencies=["fixture-tool"]\n' if change == 'runtime' else '') +
           '[project.optional-dependencies]\nmcp=[]\nconvert=[]\n'
           '[dependency-groups]\ndev=' + json.dumps(dev) + '\n' +
           ('[tool.uv]\nconstraint-dependencies=["fixture-tool==1"]\n' if change == 'override' else '') +
           '[tool.uv.sources]\n' + NAME + '={workspace=true}\nfixture-tool={workspace=true}\n' +
           ('fixture_tool={git="' + URL + '-fork",branch="main"}\n' if change == 'alias' else '') +
           '[tool.uv.workspace]\nmembers=' + json.dumps(members) + '\n' +
           ('exclude=["modules/tool"]\n' if change == 'excluded' else '')).encode()
    (s.root / 'pyproject.toml').write_bytes(raw)
    git(s.root, 'add', 'pyproject.toml', 'modules')
    git(s.root, 'commit', '-m', 'workspace source proof')
    head = git(s.root, 'rev-parse', 'HEAD')
    s.marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': head}))
    s.batch.names[child_name] = child
    if change == 'local':
        s.collector.local_sources.add(child)
    if change == 'nested':
        git(child, 'init', '-b', 'main')
        git(child, 'config', 'user.email', 'fixture@example.invalid')
        git(child, 'config', 'user.name', 'Fixture')
        git(child, 'remote', 'add', 'origin', URL + '-other')
        git(child, 'add', 'pyproject.toml')
        git(child, 'commit', '-m', 'independent checkout')
        (child / '.amplifier_cache_meta.json').write_text(json.dumps({
            'git_url': URL + '-other', 'ref': 'main', 'commit': git(child, 'rev-parse', 'HEAD')}))
        s.collector.references.add((URL + '-other', 'main'))
    report = await s.collector.report(s.batch, s.config)
    assert policy.declarations(raw) == {}  # Workspace/dev entries select nothing.
    if change == 'none':
        assert report['packages'][NAME]['status'] == 'selected'
        assert report['packages'][child_name]['status'] == 'selected'
        alignment.verify_checked_selection({
            NAME: {'url': URL, 'ref': 'main', 'subdirectory': '', 'revision': head},
            child_name: {'url': URL, 'ref': 'main', 'subdirectory': 'modules/tool', 'revision': head},
        }, report, exact=True)
    else:
        assert report['packages'][child_name]['status'] != 'selected'


@pytest.mark.parametrize('fragment', ['unknown=value', 'subdirectory=&subdirectory=modules/x'])
def test_optional_direct_git_unknown_fragment_remains_protective(fragment):
    raw = ('[project]\nname="fixture"\nversion="1"\n'
           '[project.optional-dependencies]\nremote=["' + NAME + ' @ git+' + URL + '@main#' + fragment + '"]\n').encode()
    assert policy.declarations(raw) == {}
    assert None in policy.constraint_declarations(raw)[NAME]
