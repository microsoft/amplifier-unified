"""Update health of a new installation that has no component generation yet.

Setup (`amplifier-unified runtime prepare`, with no AI account) installs the
declared worker dependencies and records them. No component update has run, so
no generation is selected. The real status reader, the real installed-package
reader and the real update manager run here; the worker environment is a
directory of package metadata files.

Setup's record is a weaker proof than a qualified generation. These tests also
hold its limits: it cannot supersede a failure, hide a source that cannot be
checked, stand in for a selected generation, or authorize preparation.
"""
import hashlib
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from amplifier_web import installation_runtime
from amplifier_web import runtime_environment as environments
from amplifier_web import runtime_qualification as qualification
from amplifier_web import runtime_registry_updates as registry
from amplifier_web import update_health as health
from amplifier_web import update_report
from amplifier_web.updates import active_release
from test_runtime_registry_updates import BASE, lock
from test_update_core_lookup_failure import certificate_failure, checked_install

PACKAGES = (('amplifier-core', '2.0.2'), ('PyYAML', '6.0.3'))
UP_TO_DATE = ('No newer version of the app or its components was found. '
              'The components that setup installed are unchanged.')
QUALIFIED = ('The current application and qualified components are ready. Earlier attempts remain in history; '
             'configured fixed versions are kept.')
INCOMPLETE = ('The active component graph or configuration could not be verified. Current components are kept. '
              'Check for updates and inspect diagnostics if this remains.')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    manifest.write_bytes(BASE)
    monkeypatch.setattr(environments, 'manifest_path', lambda: manifest)
    home = tmp_path / 'app'
    receipt = environments.receipt_directory(home)
    receipt.mkdir(parents=True)
    (receipt / 'runtime.toml').write_bytes(BASE)
    (receipt / 'runtime.lock').write_text(lock())
    project = environments.prepare_project(home)
    for name, version in PACKAGES:
        distribution(project, name, version)
    (receipt / 'runtime-installed.json').write_text(json.dumps(qualification.installed_graph(project)))
    overrides = receipt / 'runtime-install-overrides.txt'
    overrides.write_text('amplifier-core==2.0.2\n')
    (receipt / 'runtime-install-policy.json').write_text(json.dumps(
        {'version': 2, 'lockSha256': digest(project / 'uv.lock'), 'overridesSha256': digest(overrides)}))
    (receipt / 'installation-prepared.json').write_text(json.dumps(
        {'schema': 1, 'lockSha256': digest(receipt / 'runtime.lock'), 'connectionRequired': True,
         'workerReady': False}))
    assert not active_release(home)  # No component generation is selected.
    return SimpleNamespace(home=home, receipt=receipt, project=project)


def core_row(install):
    return next(row for row in environments.inventory(install.home) if row.get('package') == registry.CORE)


def test_the_fixture_is_a_verified_setup_record(setup_record):
    status = installation_runtime.status(setup_record.home)
    assert status['ok'] and status['prepared'] and status['generation'] is None
    assert status['packageCount'] == len(PACKAGES) and status['workerReady'] is False
    assert core_row(setup_record)['registryManaged'] is True


async def test_a_new_installation_on_the_newest_release_is_up_to_date(setup_record, monkeypatch):
    async with checked_install(setup_record.home, core_row(setup_record), monkeypatch) as c:
        await c.manager.check()
        assert c.state['application']['status'] == 'current'
        assert [row['status'] for row in c.manager.inventory] == ['current']
        assert c.state['phase'] == 'checked' and c.state['sequence']['stage'] == 'complete'
        status = c.state['currentStatus']
        assert (status['status'], status['title'], status['detail']) == ('current', 'You\u2019re up to date', UP_TO_DATE)
        assert status['actionRequired'] is False and status['action'] is None and status['component'] is None
        assert not active_release(setup_record.home)  # The check selected and prepared nothing.
        # Saved diagnostics name the proof that this host process found.
        assert (await update_report.collect(c.manager))['updateState']['healthProof'] == 'setup-record'


# A complete check that found nothing newer, as the update manager publishes it.
CHECKED = {'phase': 'checked', 'sequence': {'stage': 'complete'}, 'application': {'status': 'current'}, 'items': []}


def reported(install, state=CHECKED):
    """The page status for `state`, with the proof that this installation can give."""
    return health.current(state, health.setup_evidence(SimpleNamespace(home=install.home)))


def replace_package(install, name, version, new_version=None):
    shutil.rmtree(next((install.project / '.venv').rglob(f"{name.replace('-', '_')}-{version}.dist-info")))
    if new_version:
        distribution(install.project, name, new_version)


def test_a_verified_setup_record_reports_up_to_date_and_does_not_say_qualified(setup_record):
    status = reported(setup_record)
    assert (status['status'], status['title'], status['detail']) == ('current', 'You\u2019re up to date', UP_TO_DATE)
    assert status['actionRequired'] is False and 'qualified' not in status['detail']
    assert health.setup_evidence(SimpleNamespace(home=setup_record.home)) == {
        'setupRecord': True, 'packageCount': len(PACKAGES)}


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
}


@pytest.mark.parametrize('change', sorted(CHANGES))
def test_a_missing_or_changed_setup_record_keeps_the_incomplete_report(setup_record, change):
    CHANGES[change](setup_record)
    assert health.setup_evidence(SimpleNamespace(home=setup_record.home)) == {}
    status = reported(setup_record)
    assert (status['status'], status['title'], status['detail']) == (
        'unverified', 'Component verification is incomplete', INCOMPLETE)
    assert status['actionRequired'] is True and status['action'] == 'check'


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
    assert health.setup_evidence(SimpleNamespace(home=setup_record.home)) == {}  # No error leaves the reader.
    assert reported(setup_record)['title'] == 'Component verification is incomplete'


def local_checkout(install, name='amplifier-module-local'):
    """A recorded package that was installed from a local source checkout.

    The installed-package reader runs Git for such a package (revision, status
    and tracked files), so the proof needs Git on the PATH of the reading process.
    """
    checkout = install.home / 'foundation/cache' / name
    checkout.mkdir(parents=True)
    (checkout / 'module.py').write_text('value = 1\n')
    (checkout / '.amplifier_cache_meta.json').write_text(json.dumps(
        {'git_url': 'https://example.invalid/' + name, 'ref': 'main'}))
    for command in (['init', '-q', '-b', 'main'], ['add', 'module.py'],
                    ['-c', 'user.email=test@example.invalid', '-c', 'user.name=Fixture', 'commit', '-q', '-m', 'one']):
        subprocess.run(['git', *command], cwd=checkout, check=True, capture_output=True, timeout=30)
    folder = distribution(install.project, name, '0.1.0')
    (folder / 'direct_url.json').write_text(json.dumps({'url': checkout.as_uri(), 'dir_info': {'editable': True}}))
    graph = qualification.installed_graph(install.project)
    assert next(row for row in graph if row['name'] == name)['cacheSource']['revision']  # Git was read.
    (install.receipt / 'runtime-installed.json').write_text(json.dumps(graph))
    return checkout


def test_the_proof_needs_git_on_the_path_when_a_package_comes_from_a_local_checkout(
        setup_record, tmp_path, monkeypatch):
    checkout = local_checkout(setup_record)
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
    (checkout / 'module.py').write_text('value = 2\n')
    assert reported(setup_record)['title'] == 'Component verification is incomplete'


def test_a_selected_generation_is_never_verified_by_setups_record(setup_record):
    # Its receipts are absent, so the generation has no proof. Setup's intact record must not stand in.
    (setup_record.home / 'updates/active.json').write_text(json.dumps({'current': 'b' * 32, 'at': 1}))
    manager = SimpleNamespace(home=setup_record.home, directory=setup_record.home / 'updates',
                              diagnostics=SimpleNamespace(state={}))
    assert health.active_evidence(manager, explain_configuration=True) == {}
    assert health.setup_evidence(manager) == {}
    assert reported(setup_record)['title'] == 'Component verification is incomplete'


def test_setups_record_does_not_supersede_a_recorded_failure(setup_record):
    failure = {'kind': 'ecosystem', 'status': 'failed', 'at': 5, 'phase': 'ecosystem-validation',
               'package': 'amplifier-core'}
    state = {**CHECKED, 'diagnostics': {'activeFailures': {'one': {'latest': failure}}}}
    status = reported(setup_record, state)
    assert status['actionRequired'] is True and status['status'] != 'current'
    assert status['title'] == 'Update could not finish' and status['component'] == 'amplifier-core'
    # Control: a later qualified and activated generation that holds the package does supersede it.
    generation = {'generation': 'a' * 32, 'startedAt': 8, 'verifiedAt': 9, 'attemptId': 'x',
                  'configuration': 'c' * 64, 'packages': {'amplifier-core'}, 'components': set()}
    assert health.current(state, generation)['detail'] == QUALIFIED


@pytest.mark.parametrize('stage', ['application', 'included', 'other'])
def test_setups_record_does_not_replace_an_incomplete_check(setup_record, stage):
    state = {**CHECKED, 'sequence': {'stage': stage}}
    status = reported(setup_record, state)
    assert (status['status'], status['title'], status['action']) == ('unchecked', 'Check for updates', 'check')


async def test_setups_record_does_not_hide_a_source_that_cannot_be_checked(setup_record, monkeypatch):
    async with checked_install(setup_record.home, core_row(setup_record), monkeypatch) as c:
        c.index.side_effect = certificate_failure()
        await c.manager.check()
        status = c.state['currentStatus']
        assert status['title'] == 'A source could not be checked' and status['component'] == 'amplifier-core'
        assert status['actionRequired'] is True and c.manager.inventory[0]['status'] == 'check_failed'


async def test_each_check_reads_the_record_again_and_preparation_never_uses_it(setup_record, monkeypatch):
    from amplifier_web.update_partial import Preparation
    async with checked_install(setup_record.home, core_row(setup_record), monkeypatch) as c:
        await c.manager.check()
        assert c.state['currentStatus']['detail'] == UP_TO_DATE
        # Setup's record is not a verified working generation for partial preparation.
        stage = c.manager.directory / 'releases' / ('c' * 32)
        with pytest.raises(ValueError, match='verified working component generation'):
            await Preparation(c.manager, stage, []).verify_baseline()
        assert not stage.exists() and not active_release(setup_record.home)

        replace_package(setup_record, 'PyYAML', '6.0.3', '6.0.4')
        await c.manager.check()
        status = c.state['currentStatus']
        assert (status['status'], status['title'], status['detail']) == (
            'unverified', 'Component verification is incomplete', INCOMPLETE)
        assert status['actionRequired'] is True
        assert (await update_report.collect(c.manager))['updateState']['healthProof'] == 'none'
