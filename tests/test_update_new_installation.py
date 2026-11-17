"""What an update check lists on a new installation, before its first component update.

Setup prepared the worker environment and recorded it. No component generation
is selected, so a conversation fetches a declared source and installs its
modules at its first use: the source's binding goes below `<data>/foundation`,
and a module is installed from a build view of the shared source store.

The check must offer neither as a component update. An absent declared source
was listed "to install", and the next check withdrew it. A module of the first
chat was read as a fixed commit, which started a component update that changed
no component.

The real inventory, policy and classification code runs here on the files that
setup and a first chat leave, with this machine's Git. Only the location of the
shared source store is supplied. Synchronous tests with light imports on
purpose: the focused native Windows lane has no async pytest plugin and no
Amplifier Foundation.
"""
import json
import subprocess
from types import SimpleNamespace

import pytest

from amplifier_web import runtime_environment as environments
from amplifier_web import runtime_policy_reconciliation as policy
from amplifier_web import runtime_qualification as qualification
from amplifier_web import update_sequence, update_sources
from amplifier_web.deployment import write_private
from amplifier_web.updates import active_release
from test_update_health_setup_record import distribution, setup_record  # noqa: F401  (fixture)

NAME = 'amplifier-module-fixture'
URL = 'https://example.invalid/amplifier-bundle-fixture'
SOURCE = {'url': 'https://example.invalid/amplifier-bundle-declared', 'ref': 'main'}
GENERATION = 'a' * 32


def git(folder, *command):
    return subprocess.run(['git', *command], cwd=folder, check=True, capture_output=True, text=True,
                          timeout=60).stdout.strip()


@pytest.fixture
def first_chat(setup_record, monkeypatch):
    """One module as the first chat's worker installs it, and the binding that the chat writes."""
    install = setup_record
    for name in ('UV_CONFIG_FILE', 'UV_OVERRIDE', 'UV_CONSTRAINT', 'UV_BUILD_CONSTRAINT'):
        monkeypatch.delenv(name, raising=False)  # A caller's own resolver policy is not this installation's.
    store = install.home / 'source-store'
    monkeypatch.setattr(update_sources, 'store_environment', lambda home: {'AMPLIFIER_SOURCE_STORE': str(store)})
    build = store / 'builds' / ('b' * 64)
    (build / 'module').mkdir(parents=True)
    (build / 'module' / 'module.py').write_bytes(b'value = 1\n')
    git(build, 'init', '-q', '-b', 'main')
    git(build, 'add', 'module')
    git(build, '-c', 'user.email=test@example.invalid', '-c', 'user.name=Fixture', 'commit', '-q', '-m', 'one')
    revision = git(build, 'rev-parse', 'HEAD')
    # Foundation marks a build view with its commit. That is its identity, not the branch that it follows.
    (build / '.amplifier_cache_meta.json').write_text(json.dumps(
        {'git_url': URL, 'ref': revision, 'commit': revision, 'buildInput': True, 'immutable': False}))
    folder = distribution(install.project, NAME, '0.1.0')
    (folder / 'direct_url.json').write_text(json.dumps({'url': (build / 'module').as_uri(), 'dir_info': {}}))
    # The branch is in the binding. The conversation writes it below its Foundation home.
    binding = install.home / 'foundation' / 'cache' / '.source-bindings' / ('c' * 64 + '.json')
    binding.parent.mkdir(parents=True)
    binding.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': revision}))
    assert not active_release(install.home)
    return SimpleNamespace(**vars(install), build=build, revision=revision, binding=binding)


def policies(install, generation=None):
    return environments.shared_build_policies(install.home, generation, qualification.installed_graph(install.project))


def test_a_module_of_the_first_chat_follows_the_branch_of_its_binding(first_chat):
    rows = environments.inventory(first_chat.home)
    module = next(row for row in rows if row.get('package') == NAME)
    assert module['provenance'] == 'installed Foundation cache module' and module['cacheManaged'] is True
    assert (module['ref'], module['current']) == ('main', first_chat.revision)
    assert (module['eligible'], module['status']) == (True, 'not_checked')
    assert 'policyOrigin' not in module  # Nothing about it is unexplained.


def test_no_component_update_is_offered_after_the_first_chat(first_chat):
    rows = environments.inventory(first_chat.home)
    assert policy.legacy_candidates(rows) == {}
    assert policy.pending(first_chat.home, None, rows) is None
    # "Refresh included components" was the one row that started the component update.
    assert [row['id'] for row in rows if row['id'] == 'runtime:environment' or row['status'] == 'update'] == []
    assert sorted(row['package'] for row in rows) == ['amplifier-core', NAME]


def test_the_binding_names_the_branch_only_for_this_build_at_this_commit(first_chat):
    assert policies(first_chat) == {
        NAME: {'url': URL, 'ref': 'main', 'revision': first_chat.revision, 'subdirectory': 'module'}}


def moved_to_the_receipt_folder(install):
    target = install.receipt / 'foundation' / 'cache' / '.source-bindings' / install.binding.name
    target.parent.mkdir(parents=True)
    install.binding.replace(target)


def marked_as_a_plain_checkout(install):
    meta = install.build / '.amplifier_cache_meta.json'
    meta.write_text(json.dumps({**json.loads(meta.read_text()), 'buildInput': False}))


UNPROVEN = {
    'no-binding': lambda i: i.binding.unlink(),
    'another-commit': lambda i: i.binding.write_text(json.dumps({'git_url': URL, 'ref': 'main', 'commit': 'd' * 40})),
    'another-source': lambda i: i.binding.write_text(json.dumps(
        {'git_url': URL + '-other', 'ref': 'main', 'commit': i.revision})),
    # Two refs name this commit: which one the module follows is not known.
    'two-refs': lambda i: i.binding.with_name('e' * 64 + '.json').write_text(json.dumps(
        {'git_url': URL, 'ref': 'release', 'commit': i.revision})),
    # Setup's receipt folder is not the Foundation home of an installation without a generation.
    'binding-in-setups-receipt-folder': moved_to_the_receipt_folder,
    'not-a-build-view': marked_as_a_plain_checkout,
}


@pytest.mark.parametrize('change', sorted(UNPROVEN))
def test_without_one_exact_binding_the_commit_stays_a_fixed_version(first_chat, change):
    UNPROVEN[change](first_chat)
    assert policies(first_chat) == {}


def test_a_selected_generation_reads_only_its_own_bindings(first_chat):
    # The data folder's bindings belong to the time before the generation.
    assert policies(first_chat, GENERATION) == {}
    own = (environments.receipt_directory(first_chat.home, GENERATION)
           / 'foundation' / 'cache' / '.source-bindings' / first_chat.binding.name)
    own.parent.mkdir(parents=True)
    own.write_bytes(first_chat.binding.read_bytes())
    first_chat.binding.unlink()
    assert policies(first_chat, GENERATION)[NAME]['ref'] == 'main'
    assert policies(first_chat) == {}


# ---- A declared source that no conversation has fetched yet ----

def declared(monkeypatch, packages=('amplifier-core',)):
    monkeypatch.setattr(update_sequence, 'included_sources', lambda: (set(packages), [dict(SOURCE)]))


def test_a_declared_source_that_no_conversation_has_fetched_is_not_listed(setup_record, monkeypatch):
    declared(monkeypatch)
    assert update_sequence.classify(setup_record.home, []) == []
    # The same with the enabled app behaviors' own selection, as a check passes it.
    selection = {'sources': [dict(SOURCE)], 'witness': 'f' * 64}
    assert update_sequence.classify(setup_record.home, [], selection=selection) == []


def test_no_source_of_the_shipped_behaviors_is_listed_before_its_first_use(setup_record):
    _, sources = update_sequence.included_sources()
    assert sources  # The shipped app behaviors declare Git sources.
    assert update_sequence.classify(setup_record.home, []) == []


def test_a_fetched_source_is_an_ordinary_included_row(setup_record, monkeypatch):
    declared(monkeypatch)
    fetched = {'id': 'binding:' + 'c' * 64, 'kind': 'bundle / module', **SOURCE, 'current': 'a' * 40,
               'status': 'not_checked', 'eligible': True}
    rows = update_sequence.classify(setup_record.home, [fetched])
    assert rows == [fetched] and fetched['updateTier'] == 'included' and 'missing' not in fetched


def test_a_selected_generation_still_lists_an_absent_declared_source(setup_record, monkeypatch):
    """A generation is immutable: only a component update adds a source to it."""
    declared(monkeypatch)
    home = setup_record.home
    write_private(home / 'updates' / 'active.json', json.dumps({'current': GENERATION}), owned_root=home)
    write_private(environments.receipt_directory(home, GENERATION) / 'runtime.lock', 'version=1\n', owned_root=home)
    row, = update_sequence.classify(home, [])
    assert (row['kind'], row['missing'], row['eligible'], row['updateTier']) == ('included source', True, True, 'included')
    assert (row['url'], row['ref'], row['current']) == (SOURCE['url'], 'main', None)


def test_an_installation_with_no_worker_environment_still_lists_everything_for_its_first_update(tmp_path, monkeypatch):
    declared(monkeypatch)
    rows = update_sequence.classify(tmp_path / 'app', [])
    assert [(row['kind'], row['missing'], row['status']) for row in rows] == [
        ('runtime environment', True, 'update'), ('included source', True, 'not_checked')]
