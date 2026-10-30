"""Access-only cold startup preserves real pending work; physical effects are controlled."""
from argparse import Namespace
import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web import headless, server, task_environments as environments
from amplifier_web.service import AppService
from test_schedules import Runtime, attach, fixture as scheduled_fixture, schedule
from test_task_environments import backend, scope


@pytest.mark.parametrize('mode', ['status', 'full'])
async def test_cold_access_does_not_admit_due_schedule_or_queued_check(
        tmp_path, monkeypatch, capsys, backend, mode):
    original, _, now, sid = await scheduled_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(environments, 'capacity', lambda *args: {
        'available': True, 'counts': {'active': 0, 'stopped_retained': 0}})
    try:
        scheduled = await schedule(original, sid, now[0])
        # Save an ordinary accepted check while the host's work loop is stopped.
        monkeypatch.setattr(original.test_queue, 'start', lambda: None)
        await original.dispatch('workspace.tests.submit', {
            **scope(original), 'requestId': 'waiting-check', 'profileId': environments.PROFILE,
            'command': 'python -V', 'notify': False})
        check_id = original.test_queue.identity(sid, 'waiting-check')
        check_before = copy.deepcopy(original.test_queue.get(check_id))
        schedule_before = copy.deepcopy(original.schedules.store.get(sid, scheduled['id']))
        messages_before = copy.deepcopy(original._session(sid)['messages'])
        assert check_before['job']['status'] == 'queued'
        now[0] += 61
        assert schedule_before['nextDue'] < now[0]
        assert original.schedules.store.runs(sid) == []
        home = original.data_dir
    finally:
        await original.close()

    class ObservedRuntime(Runtime):
        def __init__(self):
            super().__init__()
            self.starts = []

        async def start(self, session, *args, **kwargs):
            self.starts.append(session['id'])

    created = []
    create_app = server.create_app

    async def cold_app(*args, **kwargs):
        runtime = ObservedRuntime()
        # Providers and physical commands are outside this deterministic test.
        kwargs.setdefault('preload_providers', False)
        kwargs.setdefault('voice', False)
        app = await create_app(*args, runtime=runtime, **kwargs)
        service = app['service']
        service.schedules.clock = lambda: now[0]
        service.management.ensure_runtime = AsyncMock()
        created.append((service, runtime))
        return app

    monkeypatch.setattr(headless, '_existing_host', AsyncMock(return_value=None))
    monkeypatch.setattr(server, 'create_app', cold_app)
    args = Namespace(port=18977, data_dir=str(home), workspace=str(tmp_path),
                     command='access', mode=mode, scope='app', session=None, json=True)
    assert await headless.run(args) == 0
    result = json.loads(capsys.readouterr().out)
    expected = 'full' if mode == 'full' else 'ask'
    assert result['mode'] == expected
    assert len(created) == 1
    service, runtime = created[0]
    assert service.closed, 'the access command cleans up its temporary host'
    assert runtime.starts == [] and runtime.inputs == []
    assert backend == [], 'access-only startup must not prepare or launch the queued check'

    # Reopen the durable owners without starting their loops. The command must
    # leave exactly the accepted work intact, not cancel or consume it to be quiet.
    resumed_runtime = ObservedRuntime()
    resumed = AppService(home, resumed_runtime, workspace=tmp_path)
    attach(resumed, resumed_runtime, now)
    try:
        assert resumed.test_queue.get(check_id) == check_before
        assert resumed.schedules.store.get(sid, scheduled['id']) == schedule_before
        assert resumed.schedules.store.runs(sid) == []
        assert resumed._session(sid)['messages'] == messages_before
        policy = (await resumed.dispatch('access.get', {'scope': 'app'}))['result']
        assert policy['mode'] == expected
        if mode == 'full':
            assert policy['source'] == 'app'

        # Positive control: these are real admissible records. Starting the
        # existing owners later runs each once through the controlled backend.
        await resumed.test_queue.tick()
        await asyncio.gather(*resumed.test_queue.tasks.values())
        assert resumed.test_queue.get(check_id)['job']['status'] == 'passed'
        assert [action for action, _ in backend].count('execute') == 1
        await resumed.schedules.tick()
        assert len(resumed_runtime.inputs) == 1
        assert resumed_runtime.inputs[0][0] == sid
        assert resumed.schedules.store.runs(sid)[0]['phase'] == 'accepted'
    finally:
        await resumed.close()
