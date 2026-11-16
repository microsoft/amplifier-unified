"""Setup's record as the installed proof of a new installation.

Setup (`amplifier-unified runtime prepare`, with no AI account) installs the
declared worker dependencies and records them. No component update has run, so
no generation is selected. Update health then verifies that record with the
checks of a conversation-worker start.

The real status reader and the real installed-package reader run here. The
worker environment is a directory of package metadata files. Setup's record is
a weaker proof than a qualified generation; these tests also hold its limits.

Synchronous tests with light imports on purpose: the focused native Windows
lane has no async pytest plugin and no application dependencies.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from amplifier_web import host_dependencies
from amplifier_web import installation_runtime
from amplifier_web import runtime_environment as environments
from amplifier_web import runtime_qualification as qualification
from amplifier_web import update_health as health
from amplifier_web.deployment import write_private
from amplifier_web.updates import active_release

MANIFEST = b'''[project]
name="amplifier-web-runtime"
version="1"
dependencies=["amplifier-core>=2.0.2"]
[tool.uv]
no-build-package=["amplifier-core"]
[[tool.uv.index]]
url="https://pypi.org/simple"
default=true
'''
LOCK = ('version=1\n[[package]]\nname="amplifier-core"\nversion="2.0.2"\n'
        'source={ registry = "https://pypi.org/simple" }\n')
PACKAGES = (('amplifier-core', '2.0.2'), ('PyYAML', '6.0.3'))
UP_TO_DATE = ('No newer version of the app or its components was found. '
              'The components that setup recorded are installed as recorded.')
QUALIFIED = ('The current application and qualified components are ready. Earlier attempts remain in history; '
             'configured fixed versions are kept.')
INCOMPLETE = ('The active component graph or configuration could not be verified. Current components are kept. '
              'Check for updates and inspect diagnostics if this remains.')
# A complete check that found nothing newer, as the update manager publishes it.
CHECKED = {'phase': 'checked', 'sequence': {'stage': 'complete'}, 'application': {'status': 'current'}, 'items': []}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def distribution(project, name, version):
    """One installed package, as the worker environment's metadata records it."""
    site = project / '.venv' / ('Lib/site-packages' if os.name == 'nt' else 'lib/python3.13/site-packages')
    folder = site / f"{name.replace('-', '_')}-{version}.dist-info"
    folder.mkdir(parents=True)
    (folder / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n')
    return folder


@pytest.fixture
def setup_record(tmp_path, monkeypatch):
    """The files that setup leaves: the worker project, its packages and the baseline receipts."""
    manifest = tmp_path / 'manifest.toml'
    manifest.write_bytes(MANIFEST)
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    home = tmp_path / 'app'  # The data folder of a consumer installation: <root>/app.
    receipt = environments.receipt_directory(home)
    # Setup writes its receipts with the private writer; so does this fixture.
    write_private(receipt / 'runtime.toml', MANIFEST, owned_root=home)
    write_private(receipt / 'runtime.lock', LOCK, owned_root=home)
    project = environments.prepare_project(home)
    for name, version in PACKAGES:
        distribution(project, name, version)
    write_private(receipt / 'runtime-installed.json', json.dumps(qualification.installed_graph(project)),
                  owned_root=home)
    overrides = receipt / 'runtime-install-overrides.txt'
    write_private(overrides, 'amplifier-core==2.0.2\n', owned_root=home)
    write_private(receipt / 'runtime-install-policy.json', json.dumps(
        {'version': 2, 'lockSha256': digest(project / 'uv.lock'), 'overridesSha256': digest(overrides)}),
        owned_root=home)
    write_private(receipt / 'installation-prepared.json', json.dumps(
        {'schema': 1, 'lockSha256': digest(receipt / 'runtime.lock'), 'connectionRequired': True,
         'workerReady': False}), owned_root=home)
    assert not active_release(home)  # No component generation is selected.
    return SimpleNamespace(home=home, receipt=receipt, project=project)


def evidence(install):
    return health.setup_evidence(SimpleNamespace(home=install.home))


def reported(install, state=CHECKED):
    """The page status for `state`, with the proof that this installation can give."""
    return health.current(state, evidence(install))


def replace_package(install, name, version, new_version=None):
    shutil.rmtree(next((install.project / '.venv').rglob(f"{name.replace('-', '_')}-{version}.dist-info")))
    if new_version:
        distribution(install.project, name, new_version)


def test_the_fixture_is_a_verified_setup_record(setup_record):
    status = installation_runtime.status(setup_record.home)
    assert status['ok'] and status['prepared'] and status['generation'] is None
    assert status['packageCount'] == len(PACKAGES) and status['workerReady'] is False


def test_a_verified_setup_record_reports_up_to_date_and_does_not_say_qualified(setup_record):
    assert evidence(setup_record) == {'setupRecord': True, 'packageCount': len(PACKAGES)}
    status = reported(setup_record)
    assert (status['status'], status['title'], status['detail']) == ('current', 'You\u2019re up to date', UP_TO_DATE)
    assert status['actionRequired'] is False and status['action'] is None and status['component'] is None
    assert 'qualified' not in status['detail']


def test_a_package_added_after_setup_keeps_the_report(setup_record):
    # A conversation installs further modules; a worker start accepts that too.
    distribution(setup_record.project, 'amplifier-module-example', '0.1.0')
    assert reported(setup_record)['detail'] == UP_TO_DATE


CHANGES = {
    'changed-package': lambda i: replace_package(i, 'PyYAML', '6.0.3', '6.0.4'),
    'removed-package': lambda i: replace_package(i, 'PyYAML', '6.0.3'),
    'changed-lock': lambda i: (i.project / 'uv.lock').write_text('changed'),
    'changed-manifest': lambda i: (i.project / 'pyproject.toml').write_text('changed'),
    'changed-policy': lambda i: (i.receipt / 'runtime-install-overrides.txt').write_text('amplifier-core==9.9.9\n'),
    'missing-marker': lambda i: (i.receipt / 'installation-prepared.json').unlink(),
    'foreign-marker': lambda i: (i.receipt / 'installation-prepared.json').write_text(
        json.dumps({'schema': 1, 'lockSha256': 'f' * 64})),
    'missing-record': lambda i: (i.receipt / 'runtime-installed.json').unlink(),
    'empty-record': lambda i: (i.receipt / 'runtime-installed.json').write_text('[]'),
    'no-setup-record': lambda i: shutil.rmtree(i.receipt),  # An installation that setup did not record.
    # Same name and version, another source: the package now comes from a Git commit.
    'changed-source': lambda i: (next((i.project / '.venv').rglob('PyYAML-6.0.3.dist-info')) / 'direct_url.json'
                                 ).write_text(json.dumps({'url': 'https://example.invalid/pyyaml',
                                                          'vcs_info': {'vcs': 'git', 'commit_id': 'a' * 40}})),
}


@pytest.mark.parametrize('change', sorted(CHANGES))
def test_a_missing_or_changed_setup_record_keeps_the_incomplete_report(setup_record, change):
    CHANGES[change](setup_record)
    assert evidence(setup_record) == {}
    status = reported(setup_record)
    assert (status['status'], status['title'], status['detail']) == (
        'unverified', 'Component verification is incomplete', INCOMPLETE)
    assert status['actionRequired'] is True and status['action'] == 'check'


def during_verification(monkeypatch, change):
    """Run `change` after the reader has read setup's record and before it compares the packages."""
    real = qualification.verify_recorded

    def verify(project, receipt, **options):
        change(Path(receipt) / 'runtime-installed.json')
        return real(project, receipt, **options)
    monkeypatch.setattr(qualification, 'verify_recorded', verify)


RECORD_RACES = {
    'removed': lambda record: record.unlink(),
    'replaced': lambda record: record.write_text(json.dumps([{'name': 'amplifier-core', 'version': '2.0.2'}])),
}


def test_the_status_reader_compares_the_packages_with_the_record_that_it_read(setup_record, monkeypatch):
    # The reader of `runtime status` and of setup itself, with no second guard behind it.
    replace_package(setup_record, 'PyYAML', '6.0.3', '6.0.4')
    during_verification(monkeypatch, RECORD_RACES['removed'])
    assert installation_runtime.status(setup_record.home)['ok'] is False


@pytest.mark.parametrize('race', sorted(RECORD_RACES))
@pytest.mark.parametrize('package', ['changed', 'as-recorded'])
def test_a_record_that_changes_during_verification_gives_no_proof(setup_record, monkeypatch, race, package):
    if package == 'changed':
        # The comparison must not be skipped: this package is not what setup recorded.
        replace_package(setup_record, 'PyYAML', '6.0.3', '6.0.4')
    during_verification(monkeypatch, RECORD_RACES[race])
    proof = evidence(setup_record)  # The one read during which the record changed.
    assert proof == {}
    assert health.current(CHECKED, proof)['title'] == 'Component verification is incomplete'


def test_a_generation_that_is_selected_during_verification_gives_no_proof(setup_record, monkeypatch):
    real = installation_runtime.status

    def status(home):
        observed = real(home)  # Setup's record verifies ...
        (Path(home) / 'updates' / 'active.json').write_text(json.dumps({'current': 'b' * 32, 'at': 1}))
        return observed        # ... and a first generation is selected before the answer returns.
    monkeypatch.setattr(installation_runtime, 'status', status)
    assert evidence(setup_record) == {}


def test_an_installation_that_went_back_from_its_first_generation_keeps_the_earlier_report(setup_record):
    # After a rollback to setup's components the pointer names no generation, but it had one.
    (setup_record.home / 'updates' / 'active.json').write_text(json.dumps(
        {'current': None, 'previous': 'b' * 32, 'at': 1}))
    assert installation_runtime.status(setup_record.home)['ok'] is True  # The record itself still verifies.
    assert evidence(setup_record) == {}
    assert reported(setup_record)['title'] == 'Component verification is incomplete'


def cached(install, proof, release=None):
    """A manager whose last health refresh found `proof`; `release` is the published copy of the selection."""
    state = {**CHECKED, 'release': release}
    return SimpleNamespace(home=install.home, _active_update_evidence=proof,
                           service=SimpleNamespace(state={'updates': state}))


POINTERS = {
    'selected': json.dumps({'current': 'b' * 32, 'previous': None, 'at': 1}),   # An activation wrote the pointer.
    'went-back': json.dumps({'current': None, 'previous': 'b' * 32, 'at': 2}),  # A rollback to setup's components.
    'unreadable': 'not a pointer',
}


@pytest.mark.parametrize('pointer', sorted(POINTERS))
def test_a_cached_setup_proof_applies_only_while_the_pointer_names_no_generation(setup_record, pointer):
    proof = evidence(setup_record)
    assert proof['setupRecord'] is True
    manager = cached(setup_record, proof)
    assert health.cached_evidence(manager) == proof
    health.publish(manager)
    assert manager.service.state['updates']['currentStatus']['detail'] == UP_TO_DATE
    # The pointer changes after that refresh. The published copy `release` is still empty: an
    # activation publishes it only after the promotion, and a failed promotion never publishes it.
    # A refresh that started before the change and ends after it stores the same earlier proof.
    (setup_record.home / 'updates' / 'active.json').write_text(POINTERS[pointer])
    assert not manager.service.state['updates']['release']
    assert health.cached_evidence(manager) == {}
    health.publish(manager)
    status = manager.service.state['updates']['currentStatus']
    assert (status['status'], status['title']) == ('unverified', 'Component verification is incomplete')


def test_the_cached_proof_of_a_generation_is_not_touched_and_costs_no_pointer_read(setup_record, monkeypatch):
    from amplifier_web import updates
    generation = {'generation': 'b' * 32, 'startedAt': 8, 'verifiedAt': 9, 'attemptId': 'x',
                  'configuration': 'c' * 64, 'packages': set(), 'components': set()}
    manager = cached(setup_record, generation, 'b' * 32)
    monkeypatch.setattr(updates, 'active_release', lambda home: pytest.fail('Only a setup proof reads the pointer'))
    assert health.cached_evidence(manager) == generation
    health.publish(manager)
    assert manager.service.state['updates']['currentStatus']['detail'] == QUALIFIED
    assert health.cached_evidence(cached(setup_record, {})) == {}


@pytest.mark.parametrize('restored', ['after-the-reader-returns', 'before-the-reader-returns'])
def test_a_record_that_is_replaced_for_the_comparison_and_restored_gives_no_proof(
        setup_record, monkeypatch, restored):
    record = setup_record.receipt / 'runtime-installed.json'
    original = record.read_bytes()
    replace_package(setup_record, 'PyYAML', '6.0.3', '6.0.4')  # Not what setup recorded.
    matching = json.dumps(qualification.installed_graph(setup_record.project))
    real = installation_runtime.status

    def status(home):
        record.write_text(matching)    # The reader compares the packages with this list ...
        observed = real(home)
        record.write_bytes(original)   # ... and setup's own record is back when it returns.
        return observed
    monkeypatch.setattr(installation_runtime, 'status', status)
    if restored == 'before-the-reader-returns':
        # Setup's record is back as soon as the reader has read the other list:
        # a hash that the reader takes from a second read would be of setup's record.
        during_verification(monkeypatch, lambda path: path.write_bytes(original))
    assert evidence(setup_record) == {}


TOOL_FAULTS = {
    'tool-missing': FileNotFoundError(2, 'git'),  # A PATH with no Git: the read cannot start its tool.
    'tool-denied': PermissionError(13, 'git'),
    'tool-failed': subprocess.CalledProcessError(128, ['git', 'rev-parse', 'HEAD']),
    'tool-timeout': subprocess.TimeoutExpired(['git', 'status'], 10),
}


@pytest.mark.parametrize('fault', sorted(TOOL_FAULTS))
def test_a_package_read_that_cannot_run_keeps_the_incomplete_report(setup_record, monkeypatch, fault):
    def unreadable(project):
        raise TOOL_FAULTS[fault]
    monkeypatch.setattr(qualification, 'installed_graph', unreadable)
    assert evidence(setup_record) == {}  # No error leaves the reader.
    assert reported(setup_record)['title'] == 'Component verification is incomplete'


def local_checkout(install, name='amplifier-module-local'):
    """A recorded package that was installed from a local source checkout.

    The installed-package reader runs Git for such a package (revision, status
    and tracked files), so the proof needs Git on the PATH of the reading process.
    """
    checkout = install.home / 'foundation' / 'cache' / name
    checkout.mkdir(parents=True)
    (checkout / 'module.py').write_bytes(b'value = 1\n')
    (checkout / '.amplifier_cache_meta.json').write_text(json.dumps(
        {'git_url': 'https://example.invalid/' + name, 'ref': 'main'}))
    for command in (['init', '-q', '-b', 'main'], ['add', 'module.py'],
                    ['-c', 'user.email=test@example.invalid', '-c', 'user.name=Fixture', 'commit', '-q', '-m', 'one']):
        subprocess.run(['git', *command], cwd=checkout, check=True, capture_output=True, timeout=60)
    folder = distribution(install.project, name, '0.1.0')
    (folder / 'direct_url.json').write_text(json.dumps({'url': checkout.as_uri(), 'dir_info': {'editable': True}}))
    graph = qualification.installed_graph(install.project)
    assert next(row for row in graph if row['name'] == name)['cacheSource']['revision']  # Git was read.
    (install.receipt / 'runtime-installed.json').write_text(json.dumps(graph))
    return checkout


def test_the_proof_needs_git_on_the_path_when_a_package_comes_from_a_local_checkout(
        setup_record, tmp_path, monkeypatch):
    checkout = local_checkout(setup_record)
    assert evidence(setup_record) == {'setupRecord': True, 'packageCount': len(PACKAGES) + 1}
    assert reported(setup_record)['detail'] == UP_TO_DATE
    # A process whose PATH holds no Git cannot make the read: no proof, and no crash.
    empty = tmp_path / 'no-tools'
    empty.mkdir()
    with monkeypatch.context() as patch:
        patch.setenv('PATH', str(empty))
        assert shutil.which('git') is None
        assert installation_runtime.status(setup_record.home)['ok'] is False
        assert reported(setup_record)['title'] == 'Component verification is incomplete'
    assert reported(setup_record)['detail'] == UP_TO_DATE  # With Git again, the same record verifies.
    # A changed tracked file of that checkout is a changed component.
    (checkout / 'module.py').write_bytes(b'value = 2\n')
    assert reported(setup_record)['title'] == 'Component verification is incomplete'


def test_another_revision_of_a_local_checkout_is_a_changed_component(setup_record):
    checkout = local_checkout(setup_record)
    assert reported(setup_record)['detail'] == UP_TO_DATE
    # Same tracked files, another commit: only the revision differs from the record.
    subprocess.run(['git', '-c', 'user.email=test@example.invalid', '-c', 'user.name=Fixture',
                    'commit', '-q', '--allow-empty', '-m', 'two'], cwd=checkout, check=True,
                   capture_output=True, timeout=60)
    assert evidence(setup_record) == {}
    assert reported(setup_record)['title'] == 'Component verification is incomplete'


def test_the_path_that_setup_gives_the_host_finds_the_installations_own_git(setup_record, tmp_path, monkeypatch):
    """The host reads the proof with the PATH of its sign-in task, not with a person's PATH.

    The real functions build that PATH: setup's launcher environment, then the
    tool path that the task saves, then the host start. The person's own PATH
    holds no Git here, as on a PC that has no Git of its own.
    """
    from amplifier_web import windows_consumer, windows_files
    local_checkout(setup_record)
    root = setup_record.home.parent
    if os.name == 'nt':
        own_git = Path(shutil.which('git'))  # This machine's Git folder stands in for <root>\git\cmd.
    else:
        own_git = root / 'git' / 'cmd' / 'git'  # Only this folder of the installation holds Git.
        own_git.parent.mkdir(parents=True)
        own_git.symlink_to(shutil.which('git'))

    @contextmanager
    def directory(path, **kwargs):  # The Windows private-folder check; not a part of this question.
        yield
    monkeypatch.setattr(windows_files, 'directory', directory)
    person = tmp_path / 'person'
    (person / 'bin').mkdir(parents=True)
    monkeypatch.setenv('HOME', str(person))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(setup_record.home))
    monkeypatch.setenv('NoDefaultCurrentDirectoryInExePath', '1')  # The host start sets it; restore it after.
    monkeypatch.setenv('PATH', str(person / 'bin'))
    assert shutil.which('git') is None
    assert reported(setup_record)['title'] == 'Component verification is incomplete'

    launcher = windows_consumer.environment(root, {'uv': str(root / 'uv' / 'uv'), 'git': str(own_git)})
    saved = host_dependencies.tool_path(launcher['PATH'])  # The task's saved tool path (`service install`).
    assert str(own_git.parent) in saved.split(os.pathsep)
    monkeypatch.setenv('PATH', saved)                      # The host start sets PATH to exactly that.
    host_dependencies.prepare_environment()                # ... and then prepares it once more.
    found = shutil.which('git')
    assert found and os.path.normcase(os.path.dirname(found)) == os.path.normcase(str(own_git.parent))
    assert reported(setup_record)['detail'] == UP_TO_DATE


def test_a_selected_generation_is_never_verified_by_setups_record(setup_record):
    # Its receipts are absent, so the generation has no proof. Setup's intact record must not stand in.
    (setup_record.home / 'updates' / 'active.json').write_text(json.dumps({'current': 'b' * 32, 'at': 1}))
    manager = SimpleNamespace(home=setup_record.home, directory=setup_record.home / 'updates',
                              diagnostics=SimpleNamespace(state={}))
    assert health.active_evidence(manager, explain_configuration=True) == {}
    assert health.setup_evidence(manager) == {}
    assert reported(setup_record)['title'] == 'Component verification is incomplete'


@pytest.mark.parametrize('scope', [{'package': 'amplifier-core'}, {'component': 'amplifier-core'}, {}])
def test_setups_record_does_not_supersede_a_recorded_failure(setup_record, scope):
    failure = {'kind': 'ecosystem', 'status': 'failed', 'at': 5, 'phase': 'ecosystem-validation', **scope}
    state = {**CHECKED, 'diagnostics': {'activeFailures': {'one': {'latest': failure}}}}
    status = reported(setup_record, state)
    assert status['actionRequired'] is True and status['status'] != 'current'
    assert status['title'] == 'Update could not finish'
    assert status['component'] == (scope.get('package') or scope.get('component'))
    # Control: a later qualified and activated generation that holds it does supersede the failure.
    generation = {'generation': 'a' * 32, 'startedAt': 8, 'verifiedAt': 9, 'attemptId': 'x',
                  'configuration': 'c' * 64, 'packages': {'amplifier-core'}, 'components': {'amplifier-core'}}
    assert health.current(state, generation)['detail'] == QUALIFIED


def test_the_proof_costs_one_package_read_and_none_when_a_generation_is_selected(setup_record, monkeypatch):
    """The work bound: an installation with a generation already makes one such read for its own proof."""
    reads = []
    real = qualification.installed_graph

    def counted(project):
        reads.append(Path(project))
        return real(project)
    monkeypatch.setattr(qualification, 'installed_graph', counted)
    assert evidence(setup_record)['setupRecord'] is True
    assert reads == [setup_record.project]
    reads.clear()
    (setup_record.home / 'updates' / 'active.json').write_text(json.dumps({'current': 'b' * 32, 'at': 1}))
    assert evidence(setup_record) == {} and reads == []


@pytest.mark.parametrize('stage', ['application', 'included', 'other'])
def test_setups_record_does_not_replace_an_incomplete_check(setup_record, stage):
    state = {**CHECKED, 'sequence': {'stage': stage}}
    status = reported(setup_record, state)
    assert (status['status'], status['title'], status['action']) == ('unchecked', 'Check for updates', 'check')


def test_setups_record_does_not_hide_a_source_that_cannot_be_checked(setup_record):
    row = {'id': 'runtime:amplifier-core', 'label': 'amplifier-core', 'kind': 'runtime dependency',
           'status': 'check_failed'}
    status = reported(setup_record, {**CHECKED, 'items': [row]})
    assert status['title'] == 'A source could not be checked' and status['component'] == 'amplifier-core'
    assert status['actionRequired'] is True and status['action'] == 'sources'
