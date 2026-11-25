"""Due occurrence receipts when a new task requires configuration review."""
import asyncio
import json

import pytest

from amplifier_web.service import AppService
from test_schedule_new_tasks import ConfiguredRuntime, create, fixture
from test_schedules import attach


@pytest.mark.parametrize('legacy', [False, True])
async def test_changed_configuration_records_one_blocked_due_run_and_notice(tmp_path, monkeypatch, legacy):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record, _ = await create(app, sid, now[0], notificationPolicy='failures_only')
        if legacy:
            record['newTaskConfiguration'].pop('configurationParts')
            app.schedules.store.put(record)
        path = app.data_dir / 'sessions' / sid / 'effective-configuration.json'
        plan = json.loads(path.read_text())
        secret = 'private-fixture-setting'
        plan.update(agents={'new-agent': {}}, hooks=[{'module': 'new-hook', 'config': {'key': secret}}],
                    tools=[{'module': 'new-tool'}])
        path.write_text(json.dumps(plan))
        app.browser_state()
        now[0] += 61
        await asyncio.gather(app.schedules.tick(), app.schedules.tick())
        await app.schedules.tick()
        run, = app.schedules.store.runs(sid)
        assert run['dueAt'] == record['nextDue']
        assert run['phase'] == 'skipped' and run['blockedBeforeStart'] is True
        assert run['notificationDecision']['notify']
        detail = run['detail']
        assert ('plan.agents, plan.hooks, plan.tools' in detail) is (not legacy)
        assert 'Review' in detail
        assert secret not in json.dumps(run) and 'new-hook' not in detail
        assert app.schedules.store.get(sid, record['id'])['status'] == 'needs_review'
        notice, = app.state['scheduleNotifications']
        assert notice['id'] == run['id'] + ':notice' and notice['text'] == detail
        assert [notice['id']] == [row['id'] for row in app.browser_state()['notificationMessages']]
        app.management.notifications.send.assert_called_once()
        assert not runtime.inputs and len(app.directory.select(limit=-1)) == 1
        assert 'destinationSessionId' not in run and not runtime.workers
    finally:
        await app.close()

    runtime = ConfiguredRuntime()
    reopened = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    attach(reopened, runtime, now)
    try:
        now[0] += 600
        await reopened.schedules.tick()
        assert reopened.schedules.store.runs(sid) == [run]
        assert not runtime.inputs and not runtime.workers
        assert reopened.state['scheduleNotifications'] == [notice]
        assert [notice['id']] == [row['id'] for row in reopened.browser_state()['notificationMessages']]
        reopened.management.notifications.send.assert_not_called()
    finally:
        await reopened.close()


async def test_stop_precedes_prestart_configuration_review(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record, _ = await create(app, sid, now[0], notificationPolicy='failures_only')
        source = app._session(sid)
        source['selection']['effort'] = 'low'
        source['interruptionRevision'] = record['interruptionRevision'] + 1
        now[0] += 61
        await app.schedules.tick()
        assert 'stopped' in app.schedules.store.get(sid, record['id'])['reviewReason']
        assert not app.schedules.store.runs(sid) and not app.state.get('scheduleNotifications')
        assert not runtime.inputs and not runtime.workers
    finally:
        await app.close()


async def test_restarted_scheduler_decides_pending_prestart_notice_without_waking_worker(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        await create(app, sid, now[0], notificationPolicy='failures_only')
        app._session(sid)['selection']['effort'] = 'low'
        now[0] += 61
        def interrupted(*args):
            raise RuntimeError('test interruption before notification')
        monkeypatch.setattr(app.schedules, 'decide_notification', interrupted)
        with pytest.raises(RuntimeError, match='before notification'):
            await app.schedules.tick()
        run, = app.schedules.store.runs(sid)
        assert run['blockedBeforeStart'] and 'notificationDecision' not in run
        assert not runtime.workers and not runtime.inputs
    finally:
        await app.close()

    runtime = ConfiguredRuntime()
    reopened = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    attach(reopened, runtime, now)
    try:
        now[0] += 60
        async with reopened.lock:
            first = asyncio.create_task(reopened.schedules.tick())
            await asyncio.sleep(0)
            assert not first.done()
            await reopened.schedules.tick()
        await first
        await reopened.schedules.tick()
        recovered, = reopened.schedules.store.runs(sid)
        assert recovered['id'] == run['id'] and recovered['notificationDecision']['notify']
        reopened.management.notifications.send.assert_called_once()
        assert len(reopened.browser_state()['notificationMessages']) == 1
        assert not runtime.workers and not runtime.inputs
    finally:
        await reopened.close()


async def test_coincident_missed_policy_skip_stays_quiet_and_holds_future_work(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record, _ = await create(app, sid, now[0], notificationPolicy='failures_only', missedRunPolicy='skip')
        app._session(sid)['selection']['effort'] = 'low'
        now[0] += 600
        await app.schedules.tick()
        run, = app.schedules.store.runs(sid)
        assert run['phase'] == 'skipped' and 'blockedBeforeStart' not in run
        assert app.schedules.store.get(sid, record['id'])['status'] == 'needs_review'
        assert not app.state.get('scheduleNotifications') and not runtime.workers and not runtime.inputs
        app.management.notifications.send.assert_not_called()
    finally:
        await app.close()
