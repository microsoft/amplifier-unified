"""Catalog reads stay responsive without weakening receipts or progress."""
from amplifier_web.state_records import load as load_saved_state
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

    def measured(**kwargs):
        publish(**kwargs)
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
    saved = load_saved_state(app.db)
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


async def test_independent_refresh_cancel_preserves_active_mutation(app, monkeypatch):
    entered = asyncio.Event()

    async def read(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(BundleManager, 'perform', read)
    active = {'phase': 'working', 'operation': 'bundles.add', 'error': None}
    app.state['management'] = active.copy()
    await app.management.lock.acquire()
    task = asyncio.create_task(app.management.command('bundles.list', {}, 'independent'))
    try:
        # Saved catalog reads enter independently of the mutation lock.
        await asyncio.wait_for(entered.wait(), 5)
        assert app.state['actionStatus']['bundles.list']['phase'] == 'working'
        assert app.state['management'] == active
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        app.management.lock.release()
    assert app.state['management'] == active
    assert app.state['managementResults']['independent']['phase'] == 'error'
    assert app.state['actionStatus']['bundles.list']['phase'] == 'error'


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


@pytest.mark.parametrize('older_outcome', ['ready', 'error', 'cancelled'])
@pytest.mark.parametrize('anonymous', [False, True])
async def test_older_completion_cannot_replace_current_read(app, monkeypatch, older_outcome, anonymous):
    entered = [asyncio.Event(), asyncio.Event()]
    finish = [asyncio.Event(), asyncio.Event()]
    calls = 0

    async def read(*args, **kwargs):
        nonlocal calls
        index = calls
        calls += 1
        entered[index].set()
        await finish[index].wait()
        if index == 0 and older_outcome == 'error':
            raise ValueError('Older read failed')
        name = ('older', 'newer')[index]
        return {'bundles': [{'id': name}], 'registeredBundles': [{'name': name}]}

    monkeypatch.setattr(BundleManager, 'perform', read)
    identities = [None, None] if anonymous else ['older', 'newer']
    tasks = []
    try:
        tasks.append(asyncio.create_task(app.management.command('bundles.list', {}, identities[0])))
        await asyncio.wait_for(entered[0].wait(), 5)
        older_identity = app.state['actionStatus']['bundles.list']['commandId']
        tasks.append(asyncio.create_task(app.management.command('bundles.list', {}, identities[1])))
        await asyncio.wait_for(entered[1].wait(), 5)
        current_identity = app.state['actionStatus']['bundles.list']['commandId']
        assert current_identity != older_identity
        if older_outcome == 'ready':
            finish[1].set()
            await asyncio.wait_for(tasks[1], 5)
            assert app.state['bundles'] == [{'id': 'newer'}]
            assert app.state['registeredBundles'] == [{'name': 'newer'}]
        before = copy.deepcopy({key: app.state.get(key) for key in
                                ('bundles', 'registeredBundles', 'actionStatus', 'management')})
        if older_outcome == 'cancelled':
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
        else:
            finish[0].set()
            await asyncio.wait_for(tasks[0], 5)
        assert {key: app.state.get(key) for key in before} == before
        if not anonymous:
            assert app.state['managementResults']['older']['phase'] == ('ready' if older_outcome == 'ready' else 'error')
        if older_outcome != 'ready':
            finish[1].set()
            await asyncio.wait_for(tasks[1], 5)
        assert app.state['bundles'] == [{'id': 'newer'}]
        assert app.state['registeredBundles'] == [{'name': 'newer'}]
        saved = load_saved_state(app.db)
        assert saved['bundles'] == app.state['bundles']
        assert saved['registeredBundles'] == app.state['registeredBundles']
        if not anonymous:
            assert saved['managementResults']['newer'] == {'phase': 'ready', 'error': None}
            assert saved['managementResults']['older'] == app.state['managementResults']['older']
        else:
            assert not app.state.get('managementResults')
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_anonymous_reads_and_completion_retention(app):
    await app.management.command('bundles.list', {})
    assert None not in app.state.get('managementResults', {})
    app.state['managementResults'] = {f'old-{i}': {'phase': 'ready', 'error': None} for i in range(100)}
    await app.management.command('bundles.list', {}, 'latest')
    assert len(app.state['managementResults']) == 100
    assert 'old-0' not in app.state['managementResults']
    assert app.state['managementResults']['latest']['phase'] == 'ready'
