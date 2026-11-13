"""Saved external choices use the existing host config, authority and chat owners."""
import copy
import json

import pytest

from amplifier_web import external_agent_settings
from amplifier_web.deployment import config_path, load_server_config, save_server_config
from amplifier_web.errors import AppError
from test_acp_participant import operations, peer
from test_automatic_history import app_factory
from test_live_clients import command, snapshot
from test_configuration_authority import app, agent
from test_external_conversation import send, until


async def save(service, row, **kwargs):
    return (await service.dispatch('externalAgents.save', {'agent': row, **kwargs}))['result']


async def detail(service, identity):
    return (await service.dispatch('externalAgents.get', {'id': identity}))['result']


async def test_saved_choice_is_live_private_persistent_and_replayable(app_factory, tmp_path):
    service = app_factory()
    service.server_config = load_server_config(service.data_dir)
    # A newer unrelated file change must survive editing the connection.
    saved = load_server_config(service.data_dir)
    saved['futureExtension'] = {'keep': True}
    saved['runtime']['idle_timeout_hours'] = 5
    save_server_config(service.data_dir, saved)
    row = peer(tmp_path)
    row.pop('id')
    args = {'agent': row}
    receipt = await service.dispatch('externalAgents.save', args, command_id='add-external')
    result = receipt['result']
    identity = result['savedId']
    assert result['agents'] == [{'id': identity, 'label': row['label'], 'revision': result['agents'][0]['revision']}]
    assert (await service.dispatch('externalAgents.save', args, command_id='add-external'))['duplicate']
    assert len(load_server_config(service.data_dir)['externalAgents']) == 1
    assert load_server_config(service.data_dir)['futureExtension'] == {'keep': True}
    assert load_server_config(service.data_dir)['runtime'] == saved['runtime']
    assert config_path(service.data_dir).stat().st_mode & 0o777 == 0o600
    inspected = await detail(service, identity)
    assert inspected['agent'] == {'id': identity, **row}
    read_receipt = await service.dispatch('externalAgents.get', {'id': identity}, command_id='private-read')
    assert 'state' not in read_receipt
    assert service.db.execute('SELECT 1 FROM commands WHERE id=?', ('private-read',)).fetchone() is None
    public = json.dumps([receipt, (await service.dispatch('externalAgents.list'))['result'], service.browser_state()])
    assert row['command'] not in public and row['args'][0] not in public
    stored_receipt = service.db.execute('SELECT receipt FROM commands WHERE id=?', ('add-external',)).fetchone()[0]
    assert row['args'][0] not in stored_receipt
    created = await service.dispatch('session.create', {'workspace': service.default_workspace, 'externalAgentId': identity})
    session = service._session(created['sessionId'])
    await send(service, session, 'first', 'settings-first')
    remote = session['externalAgent']['sessionId']
    await service.close()
    reopened = app_factory()
    reopened.server_config = load_server_config(reopened.data_dir)
    restored = reopened._session(session['id'])
    await send(reopened, restored, 'second', 'settings-second')
    assert restored['externalAgent']['sessionId'] == remote
    assert [row['kind'] for row in operations(tmp_path)] == ['new', 'prompt', 'resume', 'prompt']


async def test_stale_editor_cannot_overwrite_or_remove_newer_entry(app_factory, tmp_path):
    service = app_factory()
    await save(service, peer(tmp_path))
    first = await detail(service, 'fixture')
    await save(service, {**first['agent'], 'label': 'Renamed'}, expectedRevision=first['revision'])
    for action, args in [
        ('externalAgents.save', {'agent': first['agent'], 'expectedRevision': first['revision']}),
        ('externalAgents.remove', {'id': 'fixture', 'expectedRevision': first['revision']}),
    ]:
        with pytest.raises(AppError, match='changed') as error:
            await service.dispatch(action, args)
        assert error.value.status == 409
    assert (await detail(service, 'fixture'))['agent']['label'] == 'Renamed'


@pytest.mark.parametrize('patch', [{'command': 'relative-command'}, {'cwd': 'relative-folder'},
                                  {'sessionCwd': 'relative-folder'}, {'label': '   '}, {'args': [1]}])
async def test_invalid_launch_does_not_save_or_start(app_factory, tmp_path, patch):
    service = app_factory()
    before = load_server_config(service.data_dir)
    with pytest.raises(AppError):
        await save(service, {**peer(tmp_path), **patch})
    assert load_server_config(service.data_dir) == before
    assert not (tmp_path / 'peer').exists() and not service.runtime.started


async def test_failed_private_write_keeps_live_selection_and_saved_config(app_factory, tmp_path, monkeypatch):
    service = app_factory()
    await save(service, peer(tmp_path))
    current = await detail(service, 'fixture')
    before = config_path(service.data_dir).read_bytes()
    live = copy.deepcopy(service.server_config)

    def fail(*_args, **_kwargs):
        raise OSError('fixture write failure')

    monkeypatch.setattr(external_agent_settings, 'save_server_config', fail)
    with pytest.raises(AppError, match='could not save'):
        await save(service, {**current['agent'], 'label': 'Not saved'}, expectedRevision=current['revision'])
    assert service.server_config == live and config_path(service.data_dir).read_bytes() == before


async def test_label_edit_preserves_binding_and_launch_edit_never_rebinds(app_factory, tmp_path):
    service = app_factory()
    await save(service, peer(tmp_path))
    created = await service.dispatch('session.create', {'workspace': service.default_workspace, 'externalAgentId': 'fixture'})
    session = service._session(created['sessionId'])
    await send(service, session, 'first', 'label-first')
    binding = copy.deepcopy(session['_externalAgentSource'])
    remote = session['externalAgent']['sessionId']
    first = await detail(service, 'fixture')
    await save(service, {**first['agent'], 'label': 'New display name'}, expectedRevision=first['revision'])
    await send(service, session, 'second', 'label-second')
    latest = await detail(service, 'fixture')
    await save(service, {**latest['agent'], 'args': latest['agent']['args'] + ['--no-resume']}, expectedRevision=latest['revision'])
    with pytest.raises(AppError, match='endpoint changed'):
        await service.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'must not send'}, command_id='changed-launch')
    assert session['_externalAgentSource'] == binding and session['externalAgent']['sessionId'] == remote
    assert [row['text'] for row in operations(tmp_path) if row['kind'] == 'prompt'] == ['first', 'second']


async def test_removal_keeps_active_turn_and_history_then_prevents_later_send(app_factory, tmp_path):
    service = app_factory()
    await save(service, peer(tmp_path))
    created = await service.dispatch('session.create', {'workspace': service.default_workspace, 'externalAgentId': 'fixture'})
    session = service._session(created['sessionId'])
    await service.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'wait-for-stop'}, command_id='remove-running')
    await until(lambda: bool(session.get('streaming')))
    remote = session['externalAgent']['sessionId']
    current = await detail(service, 'fixture')
    receipt = await service.dispatch('externalAgents.remove', {'id': 'fixture', 'expectedRevision': current['revision']}, command_id='remove-choice')
    assert receipt['result'] == {'agents': []}
    assert session['id'] in service.external_conversations.turns and session['status'] == 'working'
    await service.dispatch('conversation.stop', {'sessionId': session['id']})
    await until(lambda: session['id'] not in service.external_conversations.turns)
    messages = copy.deepcopy(session['messages'])
    with pytest.raises(AppError, match='not configured'):
        await service.dispatch('conversation.send', {'sessionId': session['id'], 'text': 'later'}, command_id='removed-later')
    assert session['messages'] == messages and session['externalAgent']['sessionId'] == remote
    assert service._session(session['id']) is session


async def test_private_get_and_writes_use_existing_full_authority(app, tmp_path):
    source = app.state['sessions'][0]
    await save(app, peer(tmp_path))
    assert (await agent(app, source, 'externalAgents.list', {}, 'list-choice'))['accepted']
    with pytest.raises(AppError, match='Configuration authority is required'):
        await agent(app, source, 'externalAgents.get', {'id': 'fixture'}, 'private-no-authority')
    access = (await app.dispatch('access.get', {'sessionId': source['id']}))['result']
    await app.dispatch('access.set', {'sessionId': source['id'], 'scope': 'session', 'mode': 'full', 'expectedRevision': access['revision']})
    current = (await agent(app, source, 'externalAgents.get', {'id': 'fixture'}, 'private-full'))['result']
    result = await agent(app, source, 'externalAgents.save', {'agent': {**current['agent'], 'label': 'Full saved'}, 'expectedRevision': current['revision']}, 'full-save')
    assert result['accepted'] and result['result']['agents'][0]['label'] == 'Full saved'
    assert not source['approvals']


async def test_private_new_chat_draft_carries_external_choice_without_native_configuration(app_factory, tmp_path):
    service = app_factory()
    service.clients.attach('web', kind='web')
    await save(service, peer(tmp_path))
    setup = {'title': 'External draft', 'workspace': service.default_workspace,
             'externalAgentId': 'fixture', 'location': {'kind': 'workspace'}}
    await command(service, 'web', 'session.draft')
    await command(service, 'web', 'view.update', {'patch': {'newSessionDraft': setup, 'draft': 'Later'}})
    assert snapshot(service, 'web')['view']['newSessionDraft'] == setup
    assert not service.state['sessions'] and not (tmp_path / 'peer').exists()
    created = await command(service, 'web', 'session.create', {**setup, 'fromDraft': True})
    session = service._session(created['sessionId'])
    assert session['externalAgent']['id'] == 'fixture' and session['bundle'] is None
    assert not {'runtimeSessionId', 'nativeProject', 'nativeIdentity', 'selection'} & session.keys()
    assert not service.runtime.started and not (tmp_path / 'peer').exists()


@pytest.mark.parametrize('location', ['managed', 'workspace'])
async def test_external_creation_never_allocates_a_new_folder(app_factory, tmp_path, location):
    service = app_factory()
    await save(service, peer(tmp_path))
    path = tmp_path / 'must-not-create'
    args = {'externalAgentId': 'fixture', 'location': {'kind': location}}
    if location == 'workspace':
        args['workspace'] = str(path)
    with pytest.raises(AppError, match='existing workspace'):
        await service.dispatch('session.create', args)
    assert not path.exists() and not service.state['sessions']


async def test_add_recovers_saved_config_after_command_receipt_failure(app_factory, tmp_path, monkeypatch):
    service = app_factory()
    row = peer(tmp_path)
    row.pop('id')
    args = {'agent': row}
    writes = []
    original_save = external_agent_settings.save_server_config

    def record_save(*values, **kwargs):
        result = original_save(*values, **kwargs)
        writes.append(copy.deepcopy(result['externalAgents']))
        return result

    def fail_receipt():
        raise RuntimeError('injected post-config receipt failure')

    monkeypatch.setattr(external_agent_settings, 'save_server_config', record_save)
    monkeypatch.setattr(service.shell, 'stage_action_receipt', fail_receipt)
    with pytest.raises(RuntimeError, match='post-config receipt failure'):
        await service.dispatch('externalAgents.save', args, command_id='recover-external-add')
    published = load_server_config(service.data_dir)['externalAgents']
    assert len(published) == 1 and len(writes) == 1
    assert service.db.execute('SELECT 1 FROM commands WHERE id=?', ('recover-external-add',)).fetchone() is None
    await service.close()
    reopened = app_factory()
    with pytest.raises(AppError, match='changed') as conflict:
        await reopened.dispatch('externalAgents.save', {'agent': {**row, 'label': 'Different'}},
                                command_id='recover-external-add')
    assert conflict.value.status == 409
    receipt = await reopened.dispatch('externalAgents.save', args, command_id='recover-external-add')
    assert receipt['accepted'] and receipt['result']['recovered']
    assert receipt['result']['savedId'] == published[0]['id']
    assert load_server_config(reopened.data_dir)['externalAgents'] == published
    assert reopened.server_config['externalAgents'] == published and len(writes) == 1
    assert (await reopened.dispatch('externalAgents.save', args, command_id='recover-external-add'))['duplicate']
    assert reopened.db.execute('SELECT 1 FROM commands WHERE id=?', ('recover-external-add',)).fetchone()
    assert not (tmp_path / 'peer').exists() and not reopened.runtime.started


async def test_external_settings_page_is_discoverable_and_opens_on_its_browser(app_factory):
    from amplifier_web.settings_navigation import settings_state

    service = app_factory()
    service.clients.attach('web', kind='web')
    pages = settings_state(service.state)['pages']
    assert {'id': 'external-agents', 'title': 'External agents'} in pages
    receipt = await command(service, 'web', 'settings.open',
                            {'page': 'external-agents', 'surface': 'dialog'})
    assert receipt['accepted']
    assert receipt['result']['page'] == 'external-agents'
    browser = snapshot(service, 'web')
    assert browser['view']['panel'] == 'settings'
    assert browser['view']['settingsExpanded'] == ['external-agents']
    assert not service.runtime.started and not load_server_config(service.data_dir).get('externalAgents')
