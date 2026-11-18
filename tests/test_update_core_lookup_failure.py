"""What the Updates page says after the registry lookup for app-managed Core.

The real update manager, application check and current-status projection run.
Only external answers are injected: release metadata, the release tag lookup,
the host's installed-component list and the registry request for Core.
"""
from contextlib import asynccontextmanager
import json
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from amplifier_web import __version__, app_component_graph, app_updates
from amplifier_web import runtime_registry_updates as registry
from amplifier_web import update_health
from amplifier_web.service import AppService
from amplifier_web.update_checks import LOOKUP_REASONS, RegistryLookupFailure, lookup_guidance
from amplifier_web.updates import UpdateManager
from test_runtime_registry_updates import core, worker  # noqa: F401
from test_service import Runtime

REVISION = 'c' * 40
SECRET = 'SECRET registry endpoint'


def certificate_failure():
    """The aiohttp error for a server chain that this PC's trust store rejects."""
    key = SimpleNamespace(host='pypi.org', port=443, ssl=True)
    return aiohttp.ClientConnectorCertificateError(
        key, ssl.SSLCertVerificationError(1, SECRET))


def page_status(state):
    """The page's overall status for a qualified installation.

    The worker fixture has no qualification receipts, so the published status
    would say "Component verification is incomplete" for every outcome. The
    projection is pure; stand-in evidence isolates the effect of the lookup.
    """
    return update_health.current(state, {'qualifiedAt': 1})


@pytest.fixture
async def current_install(worker, monkeypatch):  # noqa: F811 -- pytest fixture reuse
    """An install on the newest release whose worker uses the app-managed Core wheel."""
    async with checked_install(worker.home, core(worker), monkeypatch) as install:
        yield install


@asynccontextmanager
async def checked_install(home, row, monkeypatch):
    """The real service and update manager for `home`; only external answers are injected."""
    # The product names its data folder as the app home before it builds the service (cli.py).
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    service = AppService(home, Runtime(), workspace=home.parent)
    manager = UpdateManager(service)
    service.update_manager = manager
    row = dict(row)
    row['updateTier'] = 'included'
    # The real inventory stamps every row with the checked app selection.
    from amplifier_web.update_sequence import app_selection
    row['appSelection'] = app_selection(manager.home)['witness']
    monkeypatch.setattr(manager, 'inventory_sources',
                        AsyncMock(side_effect=lambda **kwargs: [dict(row)]))
    monkeypatch.setattr(app_updates, 'github_read',
                        AsyncMock(return_value=json.dumps({'tag_name': 'v' + __version__})))

    async def release_tag(*args, **kwargs):
        assert args[:3] == ('git', 'ls-remote', app_updates.SOURCE), 'Only the release tag uses Git here'
        return REVISION + '\trefs/tags/v' + __version__ + '\n'

    monkeypatch.setattr(app_updates, 'process', release_tag)
    # A consumer host installs Core from its registry wheel: no Git source.
    monkeypatch.setattr(app_component_graph, 'installed_graph',
                        lambda: [{'name': registry.CORE, 'version': '2.0.2'}])
    index = AsyncMock(return_value='2.0.2')
    monkeypatch.setattr(manager.check_cache, 'index', index)
    try:
        yield SimpleNamespace(service=service, manager=manager, index=index,
                              state=service.state['updates'])
    finally:
        await service.close()


async def test_current_install_with_a_working_lookup_is_up_to_date(current_install):
    c = current_install
    await c.manager.check()
    assert c.state['application']['status'] == 'current'
    assert not c.state['application'].get('releaseNotesWarning')
    assert [row['status'] for row in c.manager.inventory] == ['current']
    assert c.state['phase'] == 'checked' and not c.state['error']
    status = page_status(c.state)
    assert (status['status'], status['title']) == ('current', 'You\u2019re up to date')
    assert not status['actionRequired'] and status['component'] is None


FAILURES = {
    'certificate': certificate_failure,
    'timeout': TimeoutError,
    'unavailable': lambda: aiohttp.ClientConnectorError(SimpleNamespace(host='pypi.org', port=443, ssl=True),
                                                        OSError(101, SECRET)),
    'invalid_response': lambda: KeyError(SECRET),
    'roots_unavailable': lambda: RegistryLookupFailure('roots_unavailable'),
}


def release_directories(manager):
    return sorted((manager.directory / 'releases').glob('*'))


@pytest.mark.parametrize('reason', sorted(FAILURES))
async def test_a_failed_core_lookup_is_reported_in_plain_words_and_never_offered(current_install, reason):
    """Before the trust fix, a certificate failure here printed the clean-room defect text."""
    c = current_install
    c.index.side_effect = FAILURES[reason]()
    before = release_directories(c.manager)  # The fixture's own receipt folder.
    await c.manager.check()

    application = c.state['application']
    assert application['status'] == 'check_failed'
    assert (application['checkStage'], application['checkReason']) == ('components', reason)
    assert application['detail'] == (lookup_guidance(reason, application=True)
                                     + ' No update was installed by this check.')
    assert application['releaseNotesWarning'] == (
        'One component of the installed app could not be checked. '
        'The newest app release is installed; its saved release notes are shown.')

    checked = c.manager.inventory[0]
    assert checked['package'] == registry.CORE and checked['status'] == 'check_failed'
    assert checked['registryManaged'] is True and 'latest' not in checked
    assert checked['checkReason'] == reason and checked['detail'] == lookup_guidance(reason)

    assert c.state['currentStatus'] == page_status(c.state)  # Evidence does not change it.
    status = c.state['currentStatus']
    assert status['title'] == 'A source could not be checked'
    assert status['detail'] == lookup_guidance(reason)
    assert status['component'] == 'amplifier-core'
    assert status['actionRequired'] and status['action'] == 'sources'

    # Reported, and not offered: nothing is available and nothing can be prepared.
    assert c.state['phase'] == 'error' and c.state['error']
    assert c.state['available'] == 0 and not c.state.get('includedCanProceed')
    await c.manager.tick()  # The automatic installer finds nothing to install.
    assert release_directories(c.manager) == before and not c.state.get('pendingRelease')
    assert SECRET not in json.dumps(c.state)


async def test_a_failed_core_lookup_blocks_an_available_sibling_update(current_install, monkeypatch):
    """Mixed result: another included source has an update, and Core cannot be checked."""
    c = current_install
    core_row = (await c.manager.inventory_sources(indexed=True))[0]
    sibling = {'id': 'fixture-sibling', 'label': 'example.invalid/sibling', 'kind': 'bundle / module',
               'path': 'cache/sibling', 'url': 'https://example.invalid/sibling', 'ref': 'main',
               'current': 'a' * 40, 'eligible': True, 'status': 'not_checked', 'usage': 'configured',
               'usageEvidence': [], 'updateTier': 'included', 'appSelection': core_row['appSelection']}
    monkeypatch.setattr(c.manager, 'inventory_sources',
                        AsyncMock(side_effect=lambda **kwargs: [dict(core_row), dict(sibling)]))
    monkeypatch.setattr(c.manager.check_cache, 'git_policy',
                        AsyncMock(return_value={'kind': 'branch', 'revision': 'd' * 40}))
    c.service.state['settings'].setdefault('updates', {})['autoInstall'] = True

    def statuses():
        return {row['id']: row['status'] for row in c.manager.inventory}

    # Control: with a working Core lookup the sibling update is offered.
    await c.manager.check()
    assert statuses() == {'runtime:amplifier-core': 'current', 'fixture-sibling': 'update'}
    assert c.state['phase'] == 'available' and c.state['includedCanProceed'] is True

    c.index.side_effect = certificate_failure()
    before = release_directories(c.manager)
    await c.manager.check()
    assert statuses() == {'runtime:amplifier-core': 'check_failed', 'fixture-sibling': 'update'}
    assert c.state['phase'] == 'error' and c.state['error']
    assert c.state['includedCanProceed'] is False
    assert c.state['sequence']['stage'] == 'included' and c.state['sequence']['included']['issues'] == 1
    status = c.state['currentStatus']
    assert status['title'] == 'A source could not be checked' and status['component'] == 'amplifier-core'
    assert status['actionRequired'] and status['action'] == 'sources'

    stage_sources = AsyncMock(wraps=c.manager.stage_cache_sources)
    monkeypatch.setattr(c.manager, 'stage_cache_sources', stage_sources)
    await c.manager.tick()      # Automatic installation is on: it must not start.
    await c.manager.install()   # A person's explicit request is refused too.
    stage_sources.assert_not_called()
    assert release_directories(c.manager) == before and not c.state.get('pendingRelease')
    assert c.state['detail'] == 'Resolve the included component issues and check again before installing.'
    assert statuses()['runtime:amplifier-core'] == 'check_failed'


class ForeignError(ValueError):
    """Not a registry lookup failure, although it carries a `reason` of the closed set."""
    reason = 'certificate'


async def test_a_foreign_error_with_a_reason_attribute_gets_no_lookup_guidance(current_install, monkeypatch):
    c = current_install
    monkeypatch.setattr(registry, 'check', AsyncMock(side_effect=ForeignError(SECRET)))             # The row.
    monkeypatch.setattr(app_component_graph, 'updates', AsyncMock(side_effect=ForeignError(SECRET)))  # The app release.
    await c.manager.check()
    application = c.state['application']
    assert application['status'] == 'check_failed'
    assert (application['checkStage'], application['checkReason']) == ('components', 'invalid_response')
    assert application['detail'] == ('The published application release was verified, but its component check failed. '
                                     'Review source details, then check for updates again. '
                                     'No update was installed by this check.')
    checked = c.manager.inventory[0]
    assert checked['status'] == 'check_failed' and 'checkReason' not in checked and 'detail' not in checked
    assert page_status(c.state)['detail'] == (
        'The installed source is preserved. Review its source details before updating.')
    assert SECRET not in json.dumps(c.state)


async def test_a_core_release_that_cannot_be_verified_keeps_the_generic_report(current_install):
    """Not a lookup failure: the registry answers, but with a release below the app's minimum."""
    c = current_install
    c.index.return_value = '2.0.1'
    before = release_directories(c.manager)
    await c.manager.check()
    checked = c.manager.inventory[0]
    assert checked['status'] == 'check_failed' and 'latest' not in checked
    assert 'checkReason' not in checked and 'detail' not in checked
    status = page_status(c.state)
    assert status['title'] == 'A source could not be checked' and status['component'] == 'amplifier-core'
    assert status['detail'] == 'The installed source is preserved. Review its source details before updating.'
    assert c.state['available'] == 0 and not c.state.get('includedCanProceed')
    await c.manager.tick()
    assert release_directories(c.manager) == before and not c.state.get('pendingRelease')


def test_every_lookup_reason_is_tested_and_can_be_named_in_saved_diagnostics():
    assert set(FAILURES) == LOOKUP_REASONS <= app_updates.RELEASE_CHECK_REASONS
