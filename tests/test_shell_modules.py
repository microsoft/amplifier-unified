from copy import deepcopy
import asyncio
import json

import pytest

from amplifier_web.service import AppService, AppError
from amplifier_web.shell_modules import DEFAULT, API, PROFILE
from test_chat_navigation import state_fixture, chat


@pytest.fixture
def service(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    app.state.update(state_fixture())
    app.state['view']['draft'] = 'Keep this unsent message'
    app.state['sessions'] = [chat('alpha'), chat('beta', 'two')]
    yield app
    app.db.close()


async def command(app, name, **args):
    return await app.dispatch(name, args, origin='agent')


async def prepare(app, composition, client='browser-one', revision=0):
    result = await command(app, 'shell.changes.prepare', clientId=client, expectedRevision=revision, composition=composition)
    return result['result']['id']


async def stage(app, **overrides):
    manifest = {'id': 'test.navigator', 'version': '1.0.0', 'apiVersion': API, 'profile': PROFILE, 'stateSchema': 'navigation-v1', 'capabilities': ['navigation.read']}
    manifest.update(overrides)
    result = await command(app, 'shell.packages.stage', manifest=manifest, source='export default ({React})=>()=>React.createElement("p",null,"Hello")')
    return result['result']['digest']


def record_validation(app, digest):
    # Backend contract tests install a fixture receipt. The production browser
    # test exercises the real host subprocess and browser validation instead.
    record = app.shell.get('package', digest)
    record['validation'] = {'status': 'passed', 'hostFingerprint': app.shell.host_fingerprint()}
    app.shell.put('package', digest, record)


@pytest.mark.asyncio
async def test_independent_instances_clients_and_bounded_queries(service):
    composition = deepcopy(DEFAULT)
    composition['instances'].append({'id': 'beta', 'package': 'builtin.chats', 'slot': 'navigation', 'scope': {'mode': 'pinned', 'workspaceId': 'two'}})
    change = await prepare(service, composition)
    await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    await command(service, 'shell.view.update', clientId='browser-one', instanceId='chats', patch={'navFilter': 'alpha'})
    before = deepcopy(service.state)
    first = service.shell.inspect('browser-one', snapshots=True)
    second = service.shell.inspect('browser-two', snapshots=True)
    assert first['snapshots']['chats']['chatNavigation']['scope']['filter'] == 'alpha'
    assert first['snapshots']['beta']['chatNavigation']['items'][0]['id'] == 'beta'
    assert second['snapshots']['chats']['chatNavigation']['scope']['filter'] == ''
    assert service.state == before
    assert 'sessions' not in first['snapshots']['chats']
    assert 'draft' not in first['snapshots']['chats']['view']
    assert service.state['view']['draft'] == 'Keep this unsent message'


@pytest.mark.asyncio
async def test_prepare_preview_apply_report_revert_and_stale_revision(service):
    composition = deepcopy(DEFAULT)
    composition['instances'].reverse()
    composition['presentation'] = {'scheme': 'dark', 'layout': 'work', 'density': 'compact', 'accent': '#123456'}
    change = await prepare(service, composition)
    assert service.shell.client('browser-one')['composition'] == DEFAULT
    await command(service, 'shell.changes.preview', clientId='browser-one', changeId=change, expectedRevision=0)
    assert service.shell.client('browser-one')['preview']['composition'] == composition
    assert service.shell.client('browser-one')['composition'] == DEFAULT
    args = {'clientId': 'browser-one', 'changeId': change, 'expectedRevision': 0}
    first = await service.dispatch('shell.changes.apply', args, command_id='apply-one')
    retry = await service.dispatch('shell.changes.apply', args, command_id='apply-one')
    assert retry == first
    assert first['result']['status'] == 'awaiting-browser'
    with pytest.raises(AppError, match='changed'):
        await command(service, 'shell.changes.apply', **args)
    with pytest.raises(AppError, match='different arguments'):
        await service.dispatch('shell.changes.apply', {**args, 'expectedRevision': 1}, command_id='apply-one')
    await command(service, 'shell.report', clientId='browser-one', revision=1, instances={'workspaces': 'ready', 'chats': 'ready'})
    assert service.shell.client('browser-one')['lastGood'] == composition
    await command(service, 'shell.report', clientId='browser-one', revision=0, instances={'workspaces': 'error'})
    assert service.shell.client('browser-one')['reported']['status'] == 'ready'
    await command(service, 'shell.changes.revert', clientId='browser-one', changeId=change, expectedRevision=1)
    assert service.shell.client('browser-one')['composition'] == DEFAULT


@pytest.mark.asyncio
async def test_validation_digest_and_host_binding(service):
    digest = await stage(service)
    composition = deepcopy(DEFAULT)
    composition['instances'].append({'id': 'external', 'package': digest, 'slot': 'navigation'})
    with pytest.raises(AppError, match='validation'):
        await prepare(service, composition)
    record_validation(service, digest)
    change = await prepare(service, composition)
    path = service.shell.directory / (digest + '.mjs')
    path.write_text(path.read_text() + '\n// changed')
    with pytest.raises(AppError, match='contents changed'):
        await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    assert service.shell.client('browser-one')['composition'] == DEFAULT


@pytest.mark.asyncio
async def test_stale_host_receipt_rejected(service, monkeypatch):
    digest = await stage(service)
    record_validation(service, digest)
    monkeypatch.setattr(service.shell, 'host_fingerprint', lambda: 'different-host')
    with pytest.raises(AppError, match='validation'):
        service.shell.manifest(digest)


@pytest.mark.asyncio
async def test_dirty_removal_and_incompatible_replacement_defer(service):
    await command(service, 'shell.view.update', clientId='browser-one', instanceId='workspaces', patch={'workspaceDraft': {'mode': 'add', 'path': '/keep/me'}})
    composition = deepcopy(DEFAULT)
    composition['instances'] = composition['instances'][1:]
    change = await prepare(service, composition)
    result = await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    assert not result['accepted'] and result['result']['status'] == 'deferred'
    assert service.shell.client('browser-one')['views']['workspaces']['view']['workspaceDraft']['path'] == '/keep/me'
    digest = await stage(service, stateSchema='incompatible-v2')
    record_validation(service, digest)
    composition = deepcopy(DEFAULT)
    composition['instances'][1]['package'] = digest
    change = await prepare(service, composition)
    result = await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    assert result['result']['status'] == 'deferred'
    assert service.shell.client('browser-one')['composition'] == DEFAULT


@pytest.mark.asyncio
async def test_namespaces_and_capabilities(service):
    with pytest.raises(AppError, match='Unsupported'):
        await command(service, 'shell.view.update', clientId='browser-one', instanceId='chats', patch={'draft': 'overwrite'})
    with pytest.raises(AppError, match='capability'):
        await command(service, 'shell.command', clientId='browser-one', instanceId='chats', action='runtime.control', args={})
    with pytest.raises(AppError, match='reserved'):
        await stage(service, id='builtin.custom')
    assert 'shell.packages.validate' in {row['name'] for row in service.get_actions()}


@pytest.mark.asyncio
async def test_recovery_and_saved_composition_survive_restart(service):
    composition = deepcopy(DEFAULT)
    composition['instances'] = []
    change = await prepare(service, composition)
    await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    restored = AppService(service.data_dir, workspace=service.default_workspace)
    try:
        assert restored.shell.client('browser-one')['composition'] == composition
        await command(restored, 'shell.recover', clientId='browser-one', target='default', expectedRevision=1)
        assert restored.shell.client('browser-one')['composition'] == DEFAULT
    finally:
        restored.db.close()


@pytest.mark.asyncio
async def test_invalid_structure_rejected_before_commit(service):
    bad = deepcopy(DEFAULT)
    bad['instances'].append(deepcopy(bad['instances'][0]))
    with pytest.raises(AppError, match='unique'):
        await prepare(service, bad)
    bad = deepcopy(DEFAULT)
    bad['presentation']['css'] = 'body{display:none}'
    with pytest.raises(AppError, match='Additional properties'):
        await prepare(service, bad)
    assert service.shell.client('browser-one')['revision'] == 0


@pytest.mark.asyncio
async def test_unknown_package_unsupported_api_and_invalid_form_rejected(service):
    composition = deepcopy(DEFAULT)
    composition['instances'][0]['package'] = 'unavailable'
    with pytest.raises(AppError, match='Unknown shell package'):
        await prepare(service, composition)
    with pytest.raises(AppError, match='expected'):
        await stage(service, apiVersion='99.0')
    for value in [42, {'mode': 'unexpected'}, {'name': 'x' * 201}]:
        with pytest.raises(AppError):
            await command(service, 'shell.view.update', clientId='browser-one', instanceId='workspaces', patch={'workspaceDraft': value})


@pytest.mark.asyncio
async def test_pin_resets_only_originating_page_and_new_clients_keep_own_defaults(service):
    service.state['sessions'] = [{**chat(str(index)), 'workspace': '/projects/one/shared'} for index in range(250)]
    service.shell.client('browser-two')
    scope = service.shell.inspect('browser-one', snapshots=True)['snapshots']['chats']['chatNavigation']['scope']
    await command(service, 'shell.view.update', clientId='browser-one', instanceId='chats', patch={'navChatPage': {**scope, 'index': 2}})
    service.state['view']['navFilter'] = 'a changed legacy default'
    assert service.shell.inspect('browser-two', snapshots=True)['snapshots']['chats']['chatNavigation']['total'] == 250
    # The canonical command handler performs pinning; shell.command restores
    # the expected local reveal behavior without changing another client.
    await command(service, 'shell.command', clientId='browser-one', instanceId='chats', action='session.pin', args={'id': '249', 'pinned': True})
    assert service.shell.inspect('browser-one', snapshots=True)['snapshots']['chats']['chatNavigation']['index'] == 0
    assert service.shell.inspect('browser-two', snapshots=True)['snapshots']['chats']['chatNavigation']['scope']['filter'] == ''


@pytest.mark.asyncio
async def test_corrupt_optional_package_does_not_block_recovery_query(service):
    digest = await stage(service)
    record_validation(service, digest)
    composition = deepcopy(DEFAULT)
    composition['instances'].append({'id': 'optional', 'package': digest, 'slot': 'navigation'})
    change = await prepare(service, composition)
    await command(service, 'shell.changes.apply', clientId='browser-one', changeId=change, expectedRevision=0)
    (service.shell.directory / (digest + '.mjs')).unlink()
    state = service.shell.inspect('browser-one', snapshots=True)
    assert state['packages'][digest]['error']
    assert 'optional' not in state['snapshots']
    recovery = service.shell.inspect('browser-one', snapshots=True, recovery=True)
    assert recovery['effectiveComposition'] == DEFAULT
    assert set(recovery['snapshots']) == {'workspaces', 'chats'}

@pytest.mark.asyncio
async def test_paired_workspace_drill_in_is_shared_with_agents_and_client_scoped(service):
    paths = {workspace['id']: workspace['path'] for workspace in service.state['workspaces']}
    for session in service.state['sessions']:
        session['workspace'] = paths[session['workspaceId']]
    for client in ('browser-one', 'browser-two'):
        await command(service, 'shell.view.update', clientId=client, instanceId='chats',
                      patch={'navWorkspaceList': True, 'navStatusFilter': 'attention'})
    await command(service, 'shell.command', clientId='browser-one', instanceId='workspaces',
                  action='workspace.select', args={'id': 'two'})
    first = (await command(service, 'shell.query', clientId='browser-one', instanceId='chats'))['result']
    second = (await command(service, 'shell.query', clientId='browser-two', instanceId='chats'))['result']
    assert first['view']['navWorkspaceList'] is False
    assert first['view']['navStatusFilter'] == 'all'
    assert second['view']['navWorkspaceList'] is True
    assert second['view']['navStatusFilter'] == 'attention'


@pytest.mark.asyncio
async def test_shell_summaries_derive_current_unread_without_acknowledging_it(service):
    session = service.state['sessions'][0]
    session['completion'] = {'id': 'fresh', 'at': 123}
    before = deepcopy(service.state.get('attentionRead', {}))
    result = (await command(service, 'shell.query', clientId='browser-one', instanceId='chats'))['result']
    row = next(row for row in result['chatNavigation']['items'] if row['id'] == session['id'])
    assert row['activity']['kind'] == 'unread'
    assert result['attention']['sessions'][session['id']] == 1
    assert service.state.get('attentionRead', {}) == before
