"""Standing choices follow retained task sources, never copied Full flags."""
import json
from types import SimpleNamespace

import pytest

from amplifier_web.access_mode import read_mode
from amplifier_web.host.config import read_config
from amplifier_web.host.session import _apply_host_policy
from amplifier_web.service import AppError, AppService
from test_access_mode import save
from test_configuration_authority import app
from test_coordination_generation import agent, app as collaboration_app, until
from test_schedule_new_tasks import create as create_schedule, fixture as schedule_fixture


def link(app, target, source, kind='commissioned'):
    if kind == 'commissioned':
        target['collaboration'] = {'creatorSessionId': source['id'], 'requestId': 'create-' + target['id']}
    else:
        target['scheduledOrigin'] = {'sessionId': source['id'], 'scheduleId': 'schedule', 'runId': 'run-' + target['id']}
    app._publish_changes(sessions={target['id']})


def both_modes(app, target):
    return (app.access_mode.snapshot(target)['mode'],
            read_mode(app.data_dir, target['workspace'], target.get('runtimeSessionId') or target['id']))


@pytest.mark.parametrize('kind', ['commissioned', 'scheduled'])
async def test_source_full_revocation_and_file_policy_use_one_resolver(app, kind):
    source, target = app.state['sessions']
    await save(app, source)
    link(app, target, source, kind)
    assert both_modes(app, target) == ('full', 'full')
    assert app.access_mode.snapshot(target)['source'] == 'inherited'
    assert not app.configuration_authority.rows(target['id'])
    bundle = SimpleNamespace(tools=[{'module': 'tool-filesystem', 'config': {
        'allowed_write_paths': [], 'denied_write_paths': [source['workspace']]}}], agents={})
    config = read_config(target['workspace'], home=app.data_dir, session_id=target['id'])
    _apply_host_policy(bundle, config)
    assert bundle.tools[0]['config']['denied_write_paths'] == []
    revision = app.access_mode.snapshot(target)['revision']
    await save(app, source, mode='ask')
    assert both_modes(app, target) == ('ask', 'ask')
    assert not app.access_mode.full(target, revision)
    config = read_config(target['workspace'], home=app.data_dir, session_id=target['id'])
    _apply_host_policy(bundle, config)
    assert bundle.tools[0]['config']['allowed_write_paths'] == []
    assert bundle.tools[0]['config']['denied_write_paths'] == [source['workspace']]


async def test_specific_source_choice_precedes_defaults_but_target_choice_is_independent(app):
    source, target = app.state['sessions']
    await save(app, scope='workspace', mode='ask')
    await save(app, source)
    link(app, target, source)
    assert both_modes(app, target) == ('full', 'full')
    await save(app, target, mode='ask')
    assert both_modes(app, target) == ('ask', 'ask')
    await save(app, target, mode='full')
    independent = app.access_mode.snapshot(target)['revision']
    await save(app, source, mode='ask')
    assert both_modes(app, target) == ('full', 'full')
    assert app.access_mode.full(target, independent)
    await save(app, target, mode='inherit')
    assert both_modes(app, target) == ('ask', 'ask')


async def test_app_full_does_not_override_source_or_receiving_ask(app):
    source, target = app.state['sessions']
    await save(app, scope='app')
    await save(app, source, mode='ask')
    link(app, target, source)
    assert both_modes(app, target) == ('ask', 'ask')
    await save(app, source, mode='inherit')
    await save(app, target, scope='workspace', mode='ask')
    assert both_modes(app, target) == ('ask', 'ask')


async def test_destination_workspace_choice_precedes_cross_workspace_source(app, tmp_path):
    source, target = app.state['sessions']
    workspace = tmp_path / 'destination'
    workspace.mkdir()
    target['workspace'] = str(workspace)
    target['workingDirectory'] = str(workspace)
    link(app, target, source, 'scheduled')
    await save(app, source)
    assert both_modes(app, target) == ('full', 'full')
    await save(app, target, scope='workspace', mode='ask')
    assert both_modes(app, target) == ('ask', 'ask')
    await save(app, target, scope='workspace', mode='full')
    independent = app.access_mode.snapshot(target)['revision']
    await save(app, source, mode='ask')
    assert app.access_mode.full(target, independent)
    await save(app, target, scope='workspace', mode='inherit')
    assert both_modes(app, target) == ('ask', 'ask')


async def test_source_stop_fences_captured_effect_without_changing_saved_mode(app):
    source, target = app.state['sessions']
    await save(app, source)
    link(app, target, source)
    authority = {'accessRevision': app.access_mode.snapshot(target)['revision'],
        'sessionId': target['id'], 'workspace': target['workspace'], 'interruptionRevision': 0,
        'effect': 'runtime_configuration', 'scope': 'session'}
    app.configuration_authority.check(authority)
    source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    app._publish_changes(sessions={source['id']})
    assert both_modes(app, target) == ('full', 'full')
    with pytest.raises(AppError, match='Full access changed'):
        app.configuration_authority.check(authority)


async def test_missing_and_cyclic_source_cannot_borrow_app_full(app):
    source, target = app.state['sessions']
    await save(app, scope='app')
    link(app, target, {'id': 'missing-source'})
    assert both_modes(app, target) == ('ask', 'ask')
    link(app, target, source)
    link(app, source, target)
    assert both_modes(app, target) == ('ask', 'ask')
    await save(app, target)
    assert both_modes(app, target) == ('full', 'full')


async def test_native_alias_locator_reads_canonical_source_not_directory_projection(app):
    source, target = app.state['sessions']
    await save(app, source)
    target['runtimeSessionId'] = 'target-native'
    link(app, target, source, 'scheduled')
    assert both_modes(app, target) == ('full', 'full')
    # scheduledOrigin deliberately is not in the history directory summary.
    summary = json.loads(app.db.execute('SELECT summary FROM history_local_rows WHERE id=?', (target['id'],)).fetchone()[0])
    assert 'scheduledOrigin' not in summary
    app.db.execute('UPDATE history_local_rows SET summary=? WHERE id=?', ('{}', target['id']))
    app.db.commit()
    assert both_modes(app, target) == ('full', 'full')


async def test_current_worker_alias_uses_verified_host_owner_for_file_policy(app):
    source, target = app.state['sessions']
    await save(app, source)
    target.update(nativeIdentity='older-native-alias', runtimeSessionId='current-native-root')
    link(app, target, source)
    assert app.access_mode.full(target)
    config = read_config(target['workspace'], home=app.data_dir, session_id='current-native-root',
        owner_session_id=target['id'])
    assert config.access_mode == 'full'
    assert read_mode(app.data_dir, target['workspace'], 'unrelated-native', owner_session_id=target['id']) == 'ask'
    await save(app, source, mode='ask')
    assert read_config(target['workspace'], home=app.data_dir, session_id='current-native-root',
        owner_session_id=target['id']).access_mode == 'ask'


async def test_real_commission_from_peer_origin_inherits_without_grant(collaboration_app):
    app = collaboration_app
    source = app.state['sessions'][0]
    await save(app, source)
    receipt = await agent(app, source, 'coordination.create',
        {'title': 'Inherited task', 'text': 'Continue the delegated work'}, 'full-create')
    await until(lambda: len(app.runtime.inputs) == 1)
    target = app._session(receipt['sessionId'])
    assert target['collaboration']['creatorSessionId'] == source['id']
    assert both_modes(app, target) == ('full', 'full')
    assert not app.configuration_authority.rows(target['id'])
    assert not source['messages']  # No fresh human message was fabricated.


async def test_scheduled_root_inherits_after_host_restart_without_source_residency(tmp_path, monkeypatch):
    app, runtime, now, sid = await schedule_fixture(tmp_path, monkeypatch)
    target_id = None
    try:
        await save(app, app._session(sid))
        await create_schedule(app, sid, now[0])
        now[0] += 61
        await app.schedules.tick()
        await app.schedules.tick()
        run = app.schedules.store.runs(sid)[0]
        assert run['phase'] == 'accepted', run.get('detail')
        target_id = run['destinationSessionId']
        assert both_modes(app, app._session(target_id)) == ('full', 'full')
    finally:
        await app.close()
    restored = AppService(tmp_path / 'app', None, workspace=tmp_path)
    try:
        # Reads use exact canonical source records even if the source is cold.
        restored.state['sessions'] = [row for row in restored.state['sessions'] if row['id'] != sid]
        target = restored._session(target_id, hydrate=False)
        assert both_modes(restored, target) == ('full', 'full')
        assert not restored.configuration_authority.rows(target_id)
    finally:
        await restored.close()
