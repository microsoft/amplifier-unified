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
    ('fork', False), ('conditional', False), ('local', False), ('subdirectory', False),
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
    raw = ('[project]\nname="fixture-skills"\nversion="1"\ndependencies=[]\n'
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
async def test_runtime_git_sibling_keeps_its_dependency_but_not_redundant_local_uv_mapping(selected, alias_fork):
    s = selected
    child = s.root / 'modules/tool'; child.mkdir(parents=True)
    (child / 'pyproject.toml').write_text(
        '[project]\nname="fixture-tool"\nversion="1"\ndependencies=["' + NAME + ' @ git+' + URL + '@main"]\n'
        '[tool.uv.sources]\n' + NAME + '={path="../..",editable=true}\n' +
        (NAME.replace('-', '_') + '={git="' + URL + '-fork",branch="main"}\n' if alias_fork else ''))
    git(s.root, 'add', 'modules'); git(s.root, 'commit', '-m', 'runtime sibling and dev mapping')
    s.marker.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': git(s.root, 'rev-parse', 'HEAD')}))
    s.batch.names['fixture-tool'] = child
    report = await s.collector.report(s.batch, s.config)
    assert report['packages'][NAME]['status'] == ('authored-constraint' if alias_fork else 'selected')


@pytest.mark.parametrize('fragment', ['unknown=value', 'subdirectory=&subdirectory=modules/x'])
def test_optional_direct_git_unknown_fragment_remains_protective(fragment):
    raw = ('[project]\nname="fixture"\nversion="1"\n'
           '[project.optional-dependencies]\nremote=["' + NAME + ' @ git+' + URL + '@main#' + fragment + '"]\n').encode()
    assert policy.declarations(raw) == {}
    assert None in policy.constraint_declarations(raw)[NAME]
