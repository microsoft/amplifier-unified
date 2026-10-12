import asyncio
import copy

import pytest

from amplifier_web.management import Management
from amplifier_web.preferences import SettingsStore
from amplifier_web.service import AppError, AppService
from amplifier_web.setup import SetupManager
from test_service import Runtime


@pytest.fixture
async def app(tmp_path, monkeypatch):
    async def no_warm(*args, **kwargs): pass
    monkeypatch.setattr(Management, 'warm_providers', no_warm)
    monkeypatch.setattr(SetupManager, 'ensure_routing_catalog', no_warm)
    selected = tmp_path/'selected'; selected.mkdir()
    edited = tmp_path/'edited'; edited.mkdir()
    service = AppService(tmp_path/'app', Runtime(), workspace=selected)
    service.management = Management(service)
    await service.dispatch('session.create', {'workspace': str(selected), 'bundle': 'anchors'})
    service.state['workspaces'].append({'id': 'edited', 'path': str(edited), 'name': 'Edited workspace'})
    store = SettingsStore(service.data_dir)
    for scope, workspace, model in [('global', selected, 'global-model'), ('local', selected, 'selected-model'), ('local', edited, 'edited-model')]:
        store.update(workspace, scope, lambda settings, model=model: settings.update(config={'providers': [
            {'id': 'one', 'module': 'provider-openai', 'config': {'default_model': model}}]}))
    yield service
    await service.close()


async def run(app, action, args):
    await app.dispatch(action, args)
    for _ in range(200):
        status = app.state.get('actionStatus', {}).get(action, {})
        if status.get('phase') in {'ready', 'error'}:
            assert status['phase'] == 'ready', status
            return
        await asyncio.sleep(.01)
    pytest.fail('Settings command did not complete')


async def test_global_reads_and_image_save_ignore_selected_workspace(app):
    store = SettingsStore(app.data_dir)
    workspace = app._session()['workspace']
    before = copy.deepcopy(store.read(workspace, 'local'))
    target = {'configurationTarget': {'kind': 'global'}}
    await run(app, 'providers.list', target)
    assert app.state['setup']['providers'][0]['config']['default_model'] == 'global-model'
    await run(app, 'providers.configureImages', {**target, 'mode': 'off'})
    assert store.read(workspace, 'local') == before
    assert app.state['setup']['imagePreference'] == 'off'
    assert app.state['setup']['providers'][0]['config']['default_model'] == 'global-model'


async def test_workspace_editor_targets_workspace_even_with_another_chat_selected(app):
    store = SettingsStore(app.data_dir)
    selected = app._session()['workspace']
    edited = next(w['path'] for w in app.state['workspaces'] if w['id'] == 'edited')
    before = [copy.deepcopy(store.read(selected, scope)) for scope in ('global', 'local')]
    target = {'configurationTarget': {'kind': 'workspace', 'workspaceId': 'edited'}}
    await run(app, 'providers.list', target)
    assert app.state['setup']['providers'][0]['config']['default_model'] == 'edited-model'
    await run(app, 'providers.configureImages', {**target, 'mode': 'off'})
    await run(app, 'modules.save', {**target, 'section': 'tools', 'module': 'tool-fixture', 'config': {}})
    assert [store.read(selected, scope) for scope in ('global', 'local')] == before
    assert store.read(edited, 'local') != before[1]
    assert app.state['registry']['scope'] == 'local'
    assert app.state['registry']['modules'][0]['module'] == 'tool-fixture'
    assert app._session()['workspace'] == selected


@pytest.mark.parametrize('args', [
    {'configurationTarget': {'kind': 'global'}, 'scope': 'local'},
    {'configurationTarget': {'kind': 'workspace', 'workspaceId': 'edited'}, 'scope': 'global'},
    {'configurationTarget': {'kind': 'workspace', 'workspaceId': 'missing'}},
    {'configurationTarget': {'kind': 'global'}, 'sessionId': 'a-chat'},
    {'configurationTarget': {'kind': 'global'}, 'nativeProject': '/project'},
])
async def test_conflicting_or_missing_targets_rejected_before_mutation(app, args):
    with pytest.raises(AppError):
        await app.dispatch('providers.configureImages', {**args, 'mode': 'off'})


def test_switching_owner_cannot_relabel_previous_results(app):
    from amplifier_web.configuration_target import state_for
    app.state['setup'] = {'providers': [{'id': 'workspace-private'}], 'matrix': {'name': 'workspace-rule'}}
    current = state_for(app, 'setup', {'configurationTarget': {'kind': 'global'}})
    assert 'providers' not in current and 'matrix' not in current


async def test_concurrent_settings_reads_keep_each_owners_receipt(app):
    from amplifier_web.configuration_target import operation_key
    targets = [{'configurationTarget': {'kind': 'global'}},
               {'configurationTarget': {'kind': 'workspace', 'workspaceId': 'edited'}}]
    await asyncio.gather(*(app.management.list_providers(args, 'read-'+str(index))
                           for index, args in enumerate(targets)))
    operations = app.state['setup']['operations']
    for index, args in enumerate(targets):
        receipt = operations[operation_key(args, 'providers.list:')]
        assert receipt['phase'] == 'ready'
        assert receipt['commandId'] == 'read-'+str(index)


def test_workspace_settings_resources_and_global_dialog_have_distinct_demand():
    from amplifier_web.browser_demand import project
    state = {'view': {'workSurface': 'workspace', 'workWorkspaceTab': 'details',
                      'workWorkspaceSettings': 'registries'}, 'registry': {'modules': ['fixture']}}
    assert project(copy.deepcopy(state))['registry'] == state['registry']
    state['view'].update(panel='settings', settingsExpanded=['updates'])
    assert 'registry' not in project(copy.deepcopy(state))
