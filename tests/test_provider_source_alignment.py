"""One provider module has one reference in the app's own two source files.

A profile composes a provider that has no source of its own from
`amplifier_web/provider_sources.json`. The worker environment gets the same
repository from `amplifier_web/runtime_deps/pyproject.toml` (a decision support
pack adds rows from `runtime_deps/features.toml`). A component update that
prepares a worker environment compares the two references in
`runtime_source_alignment.verify_checked_selection` and refuses the update when
they differ, also when both name the same commit.

Releases 0.108.0 and 0.109.0 named a commit in the first file and the branch
`main` in the second for the OpenAI module. On a PC whose "OpenAI API"
connection takes its source from the list, no component update that prepares a
worker environment could complete. (A PC whose settings name the module's
branch themselves was not affected.) No test read both files, so no check
failed. These cases read both.

What the cases compare: a module of the list with a worker package, when both
name the same repository and subdirectory as a Git source. For a module that
the worker manifest does not declare they compare nothing. One more case reads
every entry of the list: it fails when the entry's reference is one that the
update reads as fixed (`updates.pinned`: a commit, `refs/tags/...`, or a
version number with a dot). It does not know another tag name.

Changing only the worker manifest to the same commit does not repair an
installed PC: `runtime_environment.augmented_manifest` keeps the reference that
the installation already has. `tests/test_update_with_pinned_provider.py` runs
the real update for five installation states.
"""
import json
from pathlib import Path
import tomllib

import pytest

from amplifier_web import runtime_environment
from amplifier_web.runtime_environment import ProtectedRuntimeSource
from amplifier_web.runtime_source_alignment import verify_checked_selection
from amplifier_web.selected_runtime_dependencies import git_source
from amplifier_web.updates import pinned, source_key

PRODUCT = Path(runtime_environment.__file__).parent
LIST = 'amplifier_web/provider_sources.json'
MANIFEST = 'amplifier_web/runtime_deps/pyproject.toml'
FEATURES = 'amplifier_web/runtime_deps/features.toml'
OPENAI = 'provider-openai'
COMMIT = '0640ac5f42bd6aa437f94cc0256a1c5090cb0a34'


def composed():
    """What a profile composes for a provider that has no source of its own."""
    return json.loads((PRODUCT / 'provider_sources.json').read_text())


def declared():
    """What a new worker environment declares, with every support pack installed."""
    features = tomllib.loads((PRODUCT / 'runtime_deps/features.toml').read_text())['features']
    content = runtime_environment.declared_manifest(extras=sorted(features))
    return tomllib.loads(content.decode())['tool']['uv']['sources']


def pairs(composed, declared):
    """Each module of the list with each worker package of the same repository and subdirectory."""
    packages = {}
    for package, row in declared.items():
        if isinstance(row, dict) and row.get('git'):
            place = source_key(row['git'], 'HEAD')[0], row.get('subdirectory', '')
            packages.setdefault(place, []).append((package, row))
    found = []
    for module, reference in sorted(composed.items()):
        source = git_source(reference)
        assert source is not None, f'{LIST}: {module} is not a Git reference with a ref: {reference}'
        for package, row in packages.get((source_key(source['url'], 'HEAD')[0], source['subdirectory']), []):
            found.append((module, source['ref'], package, row.get('branch') or row.get('rev') or row.get('tag')))
    return found


def differences(composed, declared):
    return [
        f'{module}: {LIST} names "{ref}"; {MANIFEST} (or {FEATURES}) names "{other}" for {package}.'
        for module, ref, package, other in pairs(composed, declared) if ref != other
    ]


def test_each_provider_has_one_reference_in_the_list_and_in_the_worker_manifest():
    found = differences(composed(), declared())
    assert not found, (
        'A component update is refused for a module that has two references '
        '(runtime_source_alignment.verify_checked_selection). Change both files together. '
        'A change of the worker manifest from a branch to a commit does not reach an installed PC '
        '(runtime_environment.augmented_manifest). ' + ' '.join(found))


def fixed(composed):
    return {module: reference for module, reference in composed.items()
            if pinned(git_source(reference)['ref'])}


def test_the_list_names_no_reference_that_the_update_reads_as_fixed():
    # tests/test_standalone.py holds this rule for the shipped .py, .toml, .yaml and .txt
    # files. It does not read .json files, and the pin of pull request 2294 was in this one.
    found = fixed(composed())
    assert not found, (
        f'{LIST} names a commit, a tag or a version. The app\'s own sources follow a branch '
        '(tests/test_standalone.py); a fixed reference also stops component updates when the '
        f'worker manifest names a branch for the same module: {found}')


def test_a_commit_and_a_version_number_in_the_list_are_found():
    address = 'git+https://github.com/microsoft/amplifier-module-provider-openai@'
    assert fixed({'a': address + COMMIT, 'b': address + 'v1.2.0', 'c': address + 'main'}) == {
        'a': address + COMMIT, 'b': address + 'v1.2.0'}


def test_a_package_in_another_subdirectory_of_the_repository_is_another_source():
    # The update guard compares the subdirectory too. Two packages of one repository
    # are two sources; only the package in the module's own subdirectory is compared.
    url = 'https://example.invalid/modules'
    listed = {'provider-x': f'git+{url}@main#subdirectory=modules/provider-x'}
    declared = {'amplifier-module-provider-x': {'git': url, 'rev': COMMIT, 'subdirectory': 'modules/provider-x'},
                'amplifier-module-other': {'git': url, 'rev': 'v2', 'subdirectory': 'modules/other'}}
    assert [package for _, _, package, _ in pairs(listed, declared)] == ['amplifier-module-provider-x']
    assert len(differences(listed, declared)) == 1


def test_the_comparison_reads_the_openai_module_and_the_support_packs():
    # A renamed key or a moved file must not leave nothing to compare.
    modules = {module for module, _, _, _ in pairs(composed(), declared())}
    assert {OPENAI, 'provider-anthropic', 'provider-azure-openai', 'provider-jev'} <= modules


@pytest.mark.parametrize('listed,manifest', [(COMMIT, 'main'), ('main', COMMIT)],
                         ids=['commit-in-the-list', 'commit-in-the-manifest'])
def test_a_commit_in_one_file_and_a_branch_in_the_other_is_reported_with_both_places(listed, manifest):
    url = 'https://github.com/microsoft/amplifier-module-provider-openai'
    found = differences({OPENAI: f'git+{url}@{listed}'},
                        {'amplifier-module-provider-openai': {'git': url, 'rev': manifest}})
    assert found == [
        f'{OPENAI}: {LIST} names "{listed}"; {MANIFEST} (or {FEATURES}) names "{manifest}" '
        'for amplifier-module-provider-openai.']


def guard(listed, manifest, revision=COMMIT):
    """The update guard's own function with the two references of one module.

    The rows are written here in the form that the update's collector and its
    checked record have. The real collector runs in
    tests/test_update_with_pinned_provider.py.
    """
    name = 'amplifier-module-provider-openai'
    url = 'https://github.com/microsoft/amplifier-module-provider-openai'
    checked = {name: {'url': url, 'ref': manifest, 'subdirectory': '', 'revision': revision}}
    package = {'status': 'explicit-pin' if pinned(listed) else 'selected', 'url': url, 'ref': listed,
               'subdirectory': '', 'revision': revision, 'manifestSha256': 'not-read'}
    verify_checked_selection(checked, {'packages': {name: package}, 'batchPaths': {name: '/not-read'}})


def test_the_update_guard_accepts_the_two_references_of_the_openai_module():
    listed = git_source(composed()[OPENAI])['ref']
    row = declared()['amplifier-module-provider-openai']
    guard(listed, row.get('branch') or row.get('rev') or row.get('tag'))


def test_the_update_guard_refuses_a_commit_in_the_list_and_a_branch_in_the_manifest():
    # The state of releases 0.108.0 and 0.109.0. The branch is at the same commit.
    with pytest.raises(ProtectedRuntimeSource) as refused:
        guard(COMMIT, 'main')
    assert refused.value.diagnostic_facts == {
        'errorType': 'ValueError', 'reason': 'protected-runtime-source',
        'package': 'amplifier-module-provider-openai'}
