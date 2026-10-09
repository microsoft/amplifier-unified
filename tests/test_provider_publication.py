import asyncio
from collections import Counter

import pytest

from amplifier_web.management import Management
from amplifier_web.provider_catalog import ProviderCatalog
from amplifier_web.service import AppService
from amplifier_web.setup import SetupManager


class Runtime:
    async def close(self):
        pass


@pytest.fixture
async def app(tmp_path):
    service = AppService(tmp_path / 'app', Runtime(), workspace=tmp_path)
    service.management = Management(service)
    yield service
    await service.close()


def configured(monkeypatch, count=14):
    rows = [{'id': str(i), 'module': 'provider-fixture', 'enabled': True} for i in range(count)]
    monkeypatch.setattr(SetupManager, 'provider_rows', lambda self, workspace: rows)
    monkeypatch.setattr(SetupManager, 'catalog_key', lambda self, args, workspace: str((workspace, self.global_only, args['id'])))
    return rows


async def seed(manager, workspace, rows):
    for row in rows:
        async def result(row=row):
            return {'models': [{'id': 'model-' + row['id']}], 'modelsProviderId': row['id'],
                    'providerMetadata': {'module': row['module'], 'info': {'display_name': 'Fixture'}}}
        await manager.catalog.get(('providers.models', manager.catalog_key({'id': row['id']}, workspace)), result)


async def test_fourteen_cached_providers_publish_once_and_do_not_probe(app, monkeypatch):
    rows = configured(monkeypatch)
    manager = SetupManager(app.data_dir, catalog=app.management.provider_catalog)
    workspace = app.default_workspace
    await seed(manager, workspace, rows)
    app.state['setup'] = {'providersWorkspace': workspace, 'providersLocation': {'kind': 'workspace'}}
    original = app._publish
    publications = []
    def publish(*args, **kwargs):
        publications.append(len(app.state['setup'].get('providerCatalogs', {})))
        original(*args, **kwargs)
    monkeypatch.setattr(app, '_publish', publish)
    async def unexpected(*args):
        raise AssertionError('Fresh provider catalogs must not probe again')
    monkeypatch.setattr(manager, 'perform', unexpected)
    await app.management.warm_providers(manager, workspace)
    assert publications == [14]
    assert all(row['phase'] == 'ready' for row in app.state['setup']['providerCatalogs'].values())
    await app.management.warm_providers(manager, workspace)
    assert publications == [14]


async def test_list_has_atomic_status_receipt_and_bounded_cached_publications(app, monkeypatch):
    rows = configured(monkeypatch)
    manager = SetupManager(app.data_dir, catalog=app.management.provider_catalog)
    await seed(manager, app.default_workspace, rows)
    count = 0
    original = app._publish
    def publish(*args, **kwargs):
        nonlocal count
        count += 1
        original(*args, **kwargs)
    monkeypatch.setattr(app, '_publish', publish)
    await app.management.command('providers.list', {}, 'list-cached')
    await asyncio.gather(*list(app.tasks))
    assert count == 3
    assert app.state['actionStatus']['providers.list']['phase'] == 'ready'
    assert app.state['setup']['operations']['providers.list:']['commandId'] == 'list-cached'
    assert app.state['managementResults']['list-cached'] == {'phase': 'ready', 'error': None}


async def test_refresh_failures_keep_saved_models_and_successes_finish_together(app, monkeypatch):
    rows = configured(monkeypatch)
    now = [1000]
    app.management.provider_catalog = ProviderCatalog(ttl=10, clock=lambda: now[0])
    manager = SetupManager(app.data_dir, catalog=app.management.provider_catalog)
    workspace = app.default_workspace
    await seed(manager, workspace, rows)
    now[0] += 11
    entered = Counter()
    async def probe(self, action, args, workspace):
        entered[args['id']] += 1
        await asyncio.sleep(.005)
        if args['id'] == '0':
            raise ValueError('Unavailable')
        return {'models': [{'id': 'refreshed-' + args['id']}], 'modelsProviderId': args['id']}
    monkeypatch.setattr(SetupManager, 'probe', probe)
    app.state['setup'] = {'providersWorkspace': workspace, 'providersLocation': {'kind': 'workspace'}}
    publications = 0
    original = app._publish
    def publish(*args, **kwargs):
        nonlocal publications
        publications += 1
        original(*args, **kwargs)
    monkeypatch.setattr(app, '_publish', publish)
    await app.management.warm_providers(manager, workspace)
    assert entered == {str(i): 1 for i in range(14)}
    catalogs = app.state['setup']['providerCatalogs']
    assert catalogs['0']['phase'] == 'error'
    assert catalogs['0']['models'] == [{'id': 'model-0'}]
    assert catalogs['1']['models'] == [{'id': 'refreshed-1'}]
    assert publications == 2


async def test_old_managed_listing_cannot_replace_new_workspace_receipt(app, monkeypatch):
    configured(monkeypatch, 0)
    original = SetupManager.perform
    entered = asyncio.Event()
    finish = asyncio.Event()
    async def perform(self, action, args):
        if action == 'providers.list' and self.global_only:
            entered.set()
            await finish.wait()
        return await original(self, action, args)
    monkeypatch.setattr(SetupManager, 'perform', perform)
    old = asyncio.create_task(app.management.command('providers.list', {'location': {'kind': 'managed'}, 'workspace': ''}, 'old'))
    await entered.wait()
    await app.management.command('providers.list', {'workspace': app.default_workspace}, 'new')
    finish.set()
    await old
    await asyncio.gather(*list(app.tasks))
    assert app.state['setup']['providersLocation'] == {'kind': 'workspace'}
    assert app.state['setup']['providersRequestedWorkspace'] == app.default_workspace
    assert app.state['actionStatus']['providers.list']['commandId'] == 'new'
    assert app.state['managementResults']['old']['phase'] == 'ready'
    assert app.state['managementResults']['new']['phase'] == 'ready'


async def test_old_managed_model_completion_cannot_overwrite_workspace_catalog(app, monkeypatch):
    configured(monkeypatch, 1)
    entered = asyncio.Event()
    finish = asyncio.Event()
    async def probe(self, action, args, workspace):
        entered.set()
        await finish.wait()
        return {'models': [{'id': 'old-managed'}]}
    monkeypatch.setattr(SetupManager, 'probe', probe)
    workspace = str(app.data_dir)
    manager = SetupManager(app.data_dir, catalog=app.management.provider_catalog, global_only=True)
    app.state['setup'] = {'providersWorkspace': workspace, 'providersLocation': {'kind': 'managed'}}
    old = asyncio.create_task(app.management.warm_providers(manager, workspace))
    await entered.wait()
    app.state['setup'].update(providersLocation={'kind': 'workspace'}, providerCatalogs={'new': {'phase': 'ready'}})
    finish.set()
    await old
    assert app.state['setup']['providerCatalogs'] == {'new': {'phase': 'ready'}}


async def test_listing_error_finishes_status_and_receipt(app, monkeypatch):
    async def fail(*args):
        raise ValueError('Provider configuration is invalid')
    monkeypatch.setattr(SetupManager, 'perform', fail)
    await app.management.command('providers.list', {}, 'failed')
    assert app.state['actionStatus']['providers.list']['phase'] == 'error'
    assert app.state['setup']['operations']['providers.list:']['phase'] == 'error'
    assert app.state['managementResults']['failed'] == {'phase': 'error', 'error': 'Provider configuration is invalid'}


async def test_token_rotation_discards_old_catalog_and_finishes_under_new_identity(app, monkeypatch):
    rows = configured(monkeypatch, 1)
    generation = [0]
    monkeypatch.setattr(SetupManager, 'catalog_key', lambda self, args, workspace: str((workspace, args['id'], generation[0])))
    manager = SetupManager(app.data_dir, catalog=app.management.provider_catalog)
    workspace = app.default_workspace
    app.state['setup'] = {'providersWorkspace': workspace, 'providersLocation': {'kind': 'workspace'}}
    calls = []
    async def probe(self, action, args, workspace):
        calls.append(generation[0])
        if generation[0] == 0:
            generation[0] = 1
            return {'models': [{'id': 'obsolete'}]}
        return {'models': [{'id': 'renewed'}]}
    monkeypatch.setattr(SetupManager, 'probe', probe)
    await app.management.warm_providers(manager, workspace)
    assert calls == [0, 1]
    catalog = app.state['setup']['providerCatalogs']['0']
    assert catalog['phase'] == 'ready'
    assert catalog['models'] == [{'id': 'renewed'}]
    assert catalog['sharedCatalogKey'] == str((workspace, '0', 1))
    assert app.state['setup']['providers'] == rows
