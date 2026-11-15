"""What the Updates page says when the registry lookup for app-managed Core fails.

The real update manager, application check and current-status projection run.
Only external answers are injected: release metadata, the release tag lookup,
the host's installed-component list and the registry request for Core.
"""
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
    service = AppService(worker.home, Runtime(), workspace=worker.home.parent)
    manager = UpdateManager(service)
    service.update_manager = manager
    row = core(worker)
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


async def test_failed_core_lookup_prints_the_clean_room_page_text(current_install):
    """Reproduction: one failed request gives both failure texts of the defect report."""
    c = current_install
    c.index.side_effect = certificate_failure()
    await c.manager.check()

    application = c.state['application']
    assert application['status'] == 'check_failed'
    assert (application['checkStage'], application['checkReason']) == ('components', 'invalid_response')
    assert application['releaseNotesWarning'] == (
        'The update check failed. Showing saved release notes; check again to confirm the latest release.')

    checked = c.manager.inventory[0]
    assert checked['package'] == registry.CORE and checked['status'] == 'check_failed'
    assert checked['registryManaged'] is True and 'detail' not in checked and 'latest' not in checked

    assert c.state['currentStatus'] == page_status(c.state)  # Evidence does not change it.
    status = c.state['currentStatus']
    assert status['title'] == 'A source could not be checked'
    assert status['detail'] == 'The installed source is preserved. Review its source details before updating.'
    assert status['component'] == 'amplifier-core'
    assert status['actionRequired'] and status['action'] == 'sources'
    assert SECRET not in json.dumps(c.state)
