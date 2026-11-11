from copy import deepcopy

import pytest

from amplifier_web.service import AppError
from amplifier_web.shell_modules import DEFAULT
from amplifier_web.shell_components import PROFILE
from test_shell_modules import service as base_service, command, prepare, stage, record_validation


@pytest.fixture
def service(base_service, tmp_path):
    for row in base_service.state['sessions']:
        row.update(workspace=str(tmp_path), messages=[])
    return base_service


async def component(service, slot='app.actions', **changes):
    digest = await stage(service, profile=PROFILE, label='Demo component', stateSchema='shell-component-v1',
                         slots=[slot], capabilities=changes.pop('capabilities', ['shell.read']), **changes)
    record_validation(service, digest)
    return digest


async def install(service, digest, slot='app.actions', revision=0):
    composition = deepcopy(service.shell.client('browser-one')['composition'])
    composition['instances'] = [row for row in composition['instances'] if row['id'] != 'demo']
    composition['instances'].append({'id':'demo', 'package':digest, 'slot':slot})
    change = await prepare(service, composition, revision=revision)
    result = await command(service, 'shell.changes.apply', clientId='browser-one', expectedRevision=revision, changeId=change)
    return result, change


async def query(service):
    return (await command(service, 'shell.query', clientId='browser-one', instanceId='demo'))['result']


async def act(service, action, args, **extra):
    observed = await query(service)
    return await command(service, 'shell.command', clientId='browser-one', instanceId='demo', generation=observed['generation'], action=action, args=args, **extra)


async def test_catalog_inherits_builtins_without_rewriting_legacy_composition(service):
    original = deepcopy(service.shell.client('browser-one'))
    result = service.shell.inspect('browser-one', snapshots=True)
    assert result['composition'] == DEFAULT
    assert service.shell.client('browser-one') == original
    assert result['slots']['conversation.header']['maximum'] == 1
    assert 'settings.section' in result['slots']
    assert 'presentation.update' in result['componentCommands']
    assert any(row['package']=='builtin.app-actions' for row in result['resolvedInstances'])


async def test_snapshots_include_only_declared_summaries_and_module_state(service):
    service.state['selectedSessionId'] = 'alpha'
    service.state['sessions'][0].update(messages=[{'text':'Private transcript'}], draft='Private draft', secret='Credential')
    digest = await component(service)
    await install(service, digest)
    first = await query(service)
    assert 'conversation' not in first and 'canvas' not in first and 'attention' not in first
    assert first['view'] == {} and 'Private' not in str(first) and 'Credential' not in str(first)
    with pytest.raises(AppError, match='capability'):
        await act(service, 'session.rename', {'id':'alpha','title':'No permission'})
    richer = await component(service, capabilities=['shell.read','conversation.summary','canvas.summary','attention.summary'])
    await install(service, richer, revision=1)
    second = await query(service)
    assert second['conversation']['id'] == 'alpha'
    assert 'messages' not in second['conversation'] and 'draft' not in second['conversation']
    assert 'canvas' in second and 'attention' in second


async def test_slot_compatibility_limits_and_explicit_disabling(service):
    digest = await component(service)
    with pytest.raises(AppError, match='slot'):
        await install(service, digest, 'conversation.header')
    composition = deepcopy(DEFAULT)
    composition['disabledSlots'] = ['app.actions']
    change = await prepare(service, composition)
    await command(service, 'shell.changes.apply', clientId='browser-one', expectedRevision=0, changeId=change)
    assert not any(row['slot']=='app.actions' for row in service.shell.inspect('browser-one')['resolvedInstances'])
    composition['instances'].append({'id':'demo','package':digest,'slot':'app.actions'})
    with pytest.raises(AppError, match='disabled'):
        await prepare(service, composition, revision=1)
    header = await component(service, 'conversation.header')
    composition = deepcopy(DEFAULT)
    composition['instances'] += [{'id':str(i),'slot':'conversation.header','package':header} for i in range(2)]
    with pytest.raises(AppError, match='Too many'):
        await prepare(service, composition, revision=1)


async def test_dirty_swap_defers_and_stale_component_commands_are_rejected(service):
    first = await component(service, capabilities=['shell.read','panels.open'])
    await install(service, first)
    old = await query(service)
    target = {'clientId':'browser-one','instanceId':'demo','generation':old['generation']}
    await command(service, 'shell.view.update', **target, patch={'choice':'keep'}, dirty=True)
    second = await component(service, version='1.0.1', capabilities=['shell.read','panels.open'])
    result, _ = await install(service, second, revision=1)
    assert result['result']['status'] == 'deferred'
    assert (await query(service))['view']['choice'] == 'keep'
    await command(service, 'shell.view.update', **target, patch={}, dirty=False)
    await install(service, second, revision=1)
    assert (await query(service))['generation'] > old['generation']
    assert (await query(service))['view']['choice'] == 'keep'
    with pytest.raises(AppError, match='replaced'):
        await command(service, 'shell.command', **target, action='panel.open', args={'panel':'settings'})
    with pytest.raises(AppError, match='replaced'):
        await command(service, 'shell.view.update', **target, patch={'choice':'late edit'})


async def test_shared_presentation_path_is_idempotent_and_preserves_draft(service):
    digest = await component(service, capabilities=['shell.read','presentation.update'])
    await install(service, digest)
    observed = await query(service)
    arguments = {'clientId':'browser-one','instanceId':'demo','generation':observed['generation'],
                 'action':'presentation.update','args':{'expectedRevision':1,'patch':{'scheme':'light','decorations':False}}}
    first = await service.dispatch('shell.command', arguments, command_id='component-change')
    second = await service.dispatch('shell.command', arguments, command_id='component-change')
    assert first == second
    assert service.shell.client('browser-one')['revision'] == 2
    assert service.shell.client('browser-one')['composition']['presentation']['decorations'] is False
    assert service.state['view']['draft'] == 'Keep this unsent message'
    with pytest.raises(AppError, match='revision'):
        await act(service, 'presentation.update', arguments['args'])


async def test_conversation_commands_reject_a_changed_target(service):
    service.state['selectedSessionId'] = 'alpha'
    digest = await component(service, capabilities=['shell.read','conversation.summary','conversation.manage'])
    await install(service, digest)
    before = (await query(service))['conversation']['id']
    service.state['selectedSessionId'] = 'beta'
    with pytest.raises(AppError, match='selected conversation changed'):
        await act(service, 'session.rename', {'id':before,'title':'Wrong chat'})


async def test_module_state_is_bounded_in_total_and_cannot_change_host_draft(service):
    digest = await component(service)
    await install(service, digest)
    target = {'clientId':'browser-one','instanceId':'demo','generation':(await query(service))['generation']}
    await command(service, 'shell.view.update', **target, patch={'draft':'module-local','first':'x'*9000})
    assert service.state['view']['draft'] == 'Keep this unsent message'
    with pytest.raises(AppError, match='16 KB'):
        await command(service, 'shell.view.update', **target, patch={'second':'y'*9000})
    assert 'second' not in (await query(service))['view']


async def test_component_panel_receipt_does_not_return_global_app_state(service):
    digest = await component(service, capabilities=['shell.read','panels.open'])
    await install(service, digest)
    result = await act(service, 'panel.open', {'panel':'settings'})
    assert result['accepted'] and 'state' not in result
    assert service.state['view']['panel'] == 'settings'


async def test_component_commands_and_state_remain_client_local(service):
    service.state['selectedSessionId'] = 'alpha'
    service.clients.attach('browser-one')
    service.clients.attach('browser-two')
    digest = await component(service, capabilities=['shell.read','panels.open'])
    await install(service, digest)
    await act(service, 'panel.open', {'panel':'settings'})
    assert service.clients.records['browser-one']['view']['panel'] == 'settings'
    assert service.clients.records['browser-two']['view'].get('panel') != 'settings'
    assert not any(row['id']=='demo' for row in service.shell.inspect('browser-two')['resolvedInstances'])
    with service.clients.bind('browser-two'):
        with pytest.raises(AppError, match='different client'):
            await act(service, 'panel.open', {'panel':'activity'})


async def test_remove_and_readd_cannot_bypass_saved_state_schema(service):
    digest = await component(service)
    await install(service, digest)
    observed = await query(service)
    await command(service, 'shell.view.update', clientId='browser-one', instanceId='demo', generation=observed['generation'], patch={'keep':'my preference'})
    change = await prepare(service, deepcopy(DEFAULT), revision=1)
    await command(service, 'shell.changes.apply', clientId='browser-one', expectedRevision=1, changeId=change)
    replacement = await stage(service, profile=PROFILE, label='Different state', stateSchema='incompatible-state', slots=['app.actions'], capabilities=['shell.read'])
    record_validation(service, replacement)
    result, _ = await install(service, replacement, revision=2)
    assert result['result']['status'] == 'deferred'
    assert service.shell.client('browser-one')['views']['demo']['view']['keep'] == 'my preference'
