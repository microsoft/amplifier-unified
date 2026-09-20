"""Catalog reads stay responsive without weakening receipts or progress."""
import asyncio
import copy
import json

import pytest

from amplifier_web.bundles import BundleManager
from amplifier_web.management import Management
from amplifier_web.service import AppService


@pytest.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_UNIFIED_IMPORT_HOME', str(tmp_path / 'empty-legacy-home'))
    service = AppService(tmp_path / 'app', workspace=tmp_path)
    service.management = Management(service)
    await service.dispatch('session.create', {})
    yield service
    await service.close()


def publications(app, monkeypatch):
    snapshots = []
    publish = app._publish

    def measured():
        publish()
        snapshots.append(copy.deepcopy(app.browser_state()))

    monkeypatch.setattr(app, '_publish', measured)
    return snapshots


async def test_catalog_and_receipt_arrive_together_with_bounded_publications(app, monkeypatch):
    snapshots = publications(app, monkeypatch)
    await app.management.command('bundles.list', {}, 'refresh')
    assert len(snapshots) <= 2
    assert snapshots[0]['actionStatus']['bundles.list']['phase'] == 'working'
    final = snapshots[-1]
    assert 'bundles' in final and 'registeredBundles' in final
    assert final['management']['phase'] == 'ready'
    assert final['actionStatus']['bundles.list']['commandId'] == 'refresh'
    assert final['managementResults']['refresh'] == {'phase': 'ready', 'error': None}
    saved = json.loads(app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
    assert saved['managementResults']['refresh'] == final['managementResults']['refresh']
    assert saved['bundles'] == final['bundles']


@pytest.mark.parametrize('origin', ['ui', 'agent'])
async def test_ui_and_agent_retries_do_not_repeat_the_read(app, monkeypatch, origin):
    calls = 0
    real = BundleManager.perform

    async def perform(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(BundleManager, 'perform', perform)
    snapshots = publications(app, monkeypatch)

    async def dispatch():
        if origin == 'agent':
            return await app.app_bridge('dispatch', {'action': 'bundles.list', 'id': 'refresh'}, app.state['selectedSessionId'])
        return await app.dispatch('bundles.list', {}, command_id='refresh')

    assert (await dispatch())['accepted']
    await asyncio.gather(*tuple(app.tasks))
    assert len(snapshots) <= 3  # Admission, start, and completion.
    assert (await dispatch())['duplicate']
    await asyncio.gather(*tuple(app.tasks))
    assert calls == 1
    detail = await app.app_bridge('get_state', {'path': '/managementResults/refresh/phase'}, app.state['selectedSessionId'])
    assert detail['value'] == 'ready'


async def test_failed_refresh_preserves_catalog_and_publishes_matching_error(app, monkeypatch):
    app.state['bundles'] = [{'id': 'keep'}]
    app.state['registeredBundles'] = [{'name': 'keep'}]

    async def fail(*args, **kwargs):
        raise ValueError('Invalid bundle configuration')

    monkeypatch.setattr(BundleManager, 'perform', fail)
    snapshots = publications(app, monkeypatch)
    await app.management.command('bundles.list', {}, 'failed')
    assert len(snapshots) <= 2
    final = snapshots[-1]
    assert final['bundles'] == [{'id': 'keep'}]
    assert final['registeredBundles'] == [{'name': 'keep'}]
    assert final['managementResults']['failed']['phase'] == 'error'
    assert final['management']['error'] == final['actionStatus']['bundles.list']['error'] == 'Invalid bundle configuration'


async def test_slow_read_allows_navigation_and_cancel_has_a_terminal_receipt(app, monkeypatch):
    entered = asyncio.Event()

    async def slow(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(BundleManager, 'perform', slow)
    task = asyncio.create_task(app.management.command('bundles.list', {}, 'slow'))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert app.state['management']['phase'] == 'working'
        assert not app.lock.locked()
        await asyncio.wait_for(app.dispatch('view.update', {'patch': {'settingsSection': 'maintenance'}}), 1)
        assert app.state['view']['settingsSection'] == 'maintenance'
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert app.state['managementResults']['slow'] == {'phase': 'error', 'error': 'Bundle refresh cancelled.'}
    assert app.state['actionStatus']['bundles.list']['phase'] == 'error'
    assert not app.management.lock.locked()


async def test_cancel_queued_refresh_preserves_active_operation(app):
    app.state['management'] = {'phase': 'working', 'operation': 'bundles.add', 'error': None}
    await app.management.lock.acquire()
    task = asyncio.create_task(app.management.command('bundles.list', {}, 'queued'))
    try:
        await asyncio.sleep(0)
        assert app.state['actionStatus']['bundles.list']['phase'] == 'queued'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert app.state['management']['operation'] == 'bundles.add'
        assert app.state['management']['phase'] == 'working'
        assert app.state['managementResults']['queued']['phase'] == 'error'
        assert app.state['actionStatus']['bundles.list']['phase'] == 'error'
    finally:
        app.management.lock.release()


async def test_cancel_before_queue_publication_records_completion(app):
    app.state['management'] = {'phase': 'working', 'operation': 'bundles.add', 'error': None}
    await app.management.lock.acquire()
    await app.lock.acquire()
    task = asyncio.create_task(app.management.command('bundles.list', {}, 'cancel-before-queue'))
    try:
        await asyncio.sleep(0)
        task.cancel()
        app.lock.release()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert app.state['management']['operation'] == 'bundles.add'
        assert app.state['management']['phase'] == 'working'
        assert app.state['managementResults']['cancel-before-queue']['phase'] == 'error'
    finally:
        app.management.lock.release()


async def test_older_completion_preserves_newer_queued_status(app, monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()
    snapshots = publications(app, monkeypatch)
    calls = 0

    async def read(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await finish.wait()
        return {'bundles': [{'id': str(calls)}]}

    monkeypatch.setattr(BundleManager, 'perform', read)
    old = asyncio.create_task(app.management.command('bundles.list', {}, 'old'))
    await asyncio.wait_for(entered.wait(), 1)
    new = asyncio.create_task(app.management.command('bundles.list', {}, 'new'))
    try:
        await asyncio.sleep(0)
        assert app.state['actionStatus']['bundles.list']['commandId'] == 'new'
    finally:
        finish.set()
        await asyncio.gather(old, new)
    old_done = next(s for s in snapshots if 'old' in s.get('managementResults', {}))
    assert old_done['actionStatus']['bundles.list']['commandId'] == 'new'
    assert old_done['actionStatus']['bundles.list']['phase'] == 'queued'
    assert app.state['bundles'] == [{'id': '2'}]
    assert app.state['managementResults']['new']['phase'] == 'ready'


async def test_anonymous_reads_and_completion_retention(app):
    await app.management.command('bundles.list', {})
    assert None not in app.state.get('managementResults', {})
    app.state['managementResults'] = {f'old-{i}': {'phase': 'ready', 'error': None} for i in range(100)}
    await app.management.command('bundles.list', {}, 'latest')
    assert len(app.state['managementResults']) == 100
    assert 'old-0' not in app.state['managementResults']
    assert app.state['managementResults']['latest']['phase'] == 'ready'
