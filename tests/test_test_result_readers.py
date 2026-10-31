"""Review saved peer checks through shared actions, without executing the peer."""
from copy import deepcopy

import pytest

from amplifier_web.service import AppError
from amplifier_web import task_environments as environments
from test_access_mode import save
from test_configuration_authority import app
from test_task_environments import backend, scope
from test_test_queue import queue, submit, drive


async def read_as(app, reader, action, args):
    return await app.app_bridge('dispatch', {'action': action, 'args': args}, reader['id'])


async def test_full_reviewer_reads_existing_check_without_new_effects(app, queue, backend, monkeypatch):
    producer, reviewer = app.state['sessions']
    await submit(app)
    await drive(queue)
    args = {**scope(app), 'requestId': 'check'}
    expected = (await app.dispatch('workspace.tests.result', args))['result']
    with pytest.raises(AppError, match='calling root'):
        await read_as(app, reviewer, 'workspace.tests.result', args)
    await save(app, reviewer)
    before = deepcopy(backend), deepcopy(app.runtime.calls), deepcopy(app.state['view'])
    original = app._session
    def session(identity=None, *, hydrate=True):
        assert not hydrate, 'Evidence review must not hydrate a transcript'
        return original(identity, hydrate=hydrate)
    monkeypatch.setattr(app, '_session', session)
    rows = (await read_as(app, reviewer, 'workspace.tests.read', scope(app)))['result']['items']
    assert len(rows) == 1 and rows[0]['status'] == 'passed'
    result = (await read_as(app, reviewer, 'workspace.tests.result', args))['result']
    assert result == expected and result['exitCode'] == 0 and 'check passed' in result['text']
    assert (backend, app.runtime.calls, app.state['view']) == before
    monkeypatch.setattr(app, '_session', original)
    for action, payload in [('workspace.tests.cancel', args), ('workspace.tests.submit', {
            **scope(app), 'requestId': 'peer-submit', 'profileId': environments.PROFILE, 'command': 'true'})]:
        with pytest.raises(AppError):
            await read_as(app, reviewer, action, payload)
    await save(app, reviewer, mode='ask')
    with pytest.raises(AppError, match='calling root'):
        await read_as(app, reviewer, 'workspace.tests.result', args)


async def test_peer_test_scope_requires_explicit_all_and_current_registered_workspace(app, queue, backend, tmp_path):
    producer, reviewer = app.state['sessions']
    await submit(app)
    await drive(queue)
    args = {**scope(app), 'requestId': 'check'}
    reviewer['workspace'] = str(tmp_path / 'other')
    (tmp_path / 'other').mkdir()
    await save(app, reviewer)
    for action, payload in [('workspace.tests.read', scope(app)), ('workspace.tests.result', args)]:
        with pytest.raises(AppError, match='history scope'):
            await read_as(app, reviewer, action, payload)
        assert (await read_as(app, reviewer, action, {**payload, 'scope': 'all'}))['accepted']
        with pytest.raises(AppError, match='current workspace'):
            await read_as(app, reviewer, action, {**payload, 'scope': 'all', 'workspaceId': 'unregistered'})
    producer['_deleting'] = True
    with pytest.raises(AppError, match='being deleted'):
        await read_as(app, reviewer, 'workspace.tests.result', {**args, 'scope': 'all'})


async def test_ask_owner_can_still_read_own_checks_and_discovery_explains_peer_selection(app, queue, backend):
    source = app.state['sessions'][0]
    await submit(app)
    await drive(queue)
    assert not app.access_mode.full(source)
    result = await read_as(app, source, 'workspace.tests.result', {**scope(app), 'requestId': 'check'})
    assert result['result']['exitCode'] == 0
    actions = (await app.app_bridge('list_actions', {'prefix': 'workspace.tests'}, source['id']))['items']
    for action in actions:
        if action['name'] in {'workspace.tests.read', 'workspace.tests.result'}:
            assert action['inputSchema']['properties']['scope']['enum'] == ['workspace', 'all']
            assert 'scope=all' in action['description']
