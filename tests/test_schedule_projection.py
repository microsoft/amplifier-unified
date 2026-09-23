"""Schedule display stays fresh without querying every historical session."""
from types import SimpleNamespace

import pytest

from amplifier_scheduling import ScheduleStore
from amplifier_web.schedules import Schedules


def record(identity, owner):
    return {'id': identity, 'sessionId': owner, 'revision': 1, 'status': 'active',
            'prompt': 'Check the saved task', 'authorization': {'messageId': 'user-request'}}


def run(identity, schedule, owner, due, **extra):
    return {'id': identity, 'scheduleId': schedule, 'sessionId': owner,
            'dueAt': due, 'phase': 'completed', 'revision': 1, **extra}


@pytest.mark.parametrize('schedule_count', [0, 30])
def test_large_catalog_uses_one_bounded_read_with_exact_owned_run_history(tmp_path, schedule_count):
    sessions = [{'id': f'session-{i}', 'schedules': [{'id': 'stale'}]} for i in range(23000)]
    app = SimpleNamespace(data_dir=tmp_path, state={'sessions': sessions})
    schedules = Schedules(app)
    try:
        for i in range(schedule_count):
            owner = sessions[i // 2]['id']
            schedules.store.put(record(f'schedule-{i}', owner))
            for due in range(15):
                schedules.store.put_run(run(f'run-{i}-{due}', f'schedule-{i}', owner, due,
                    phase='unknown' if due == 14 else 'completed',
                    destinationSessionId='session-22000', report={'values': {'count': due}}))
        # A saved schedule outside this catalog must not appear on another row.
        schedules.store.put(record('orphan', 'not-in-catalog'))
        expected = {session['id']: [{**item, 'runs': schedules.store.runs(session['id'], item['id'])[:10]}
                                   for item in schedules.store.list(session['id'])]
                    for session in sessions[:max(1, schedule_count // 2)]}
        queries = []
        schedules.store.db.set_trace_callback(queries.append)
        schedules.sync()
        schedules.store.db.set_trace_callback(None)
        assert len(queries) == 1
        assert queries[0].lstrip().upper().startswith('SELECT')
        for session in sessions:
            assert session['schedules'] == expected.get(session['id'], [])
        if schedule_count:
            latest = sessions[0]['schedules'][0]['runs']
            assert len(latest) == 10
            assert [row['dueAt'] for row in latest] == list(range(14, 4, -1))
            assert latest[0]['phase'] == 'unknown'
        assert sessions[22000]['schedules'] == []  # Destination is not the schedule owner.
    finally:
        schedules.store.close()


def test_projection_reads_external_mutations_and_does_not_change_authority(tmp_path):
    app = SimpleNamespace(data_dir=tmp_path, state={'sessions': [{'id': 'owner'}, {'id': 'other'}]})
    schedules = Schedules(app)
    other = ScheduleStore(tmp_path / 'schedules.sqlite3')
    try:
        schedules.store.put(record('one', 'owner'))
        schedules.sync()
        first = app.state['sessions'][0]['schedules'][0]
        first['authorization']['messageId'] = 'display-edit'
        first['runs'].append({'id': 'display-only'})
        def pause(value):
            value['status'] = 'paused'
            return value
        other.mutate('schedule.pause', {'sessionId': 'owner', 'id': 'one', 'expectedRevision': 1}, 'pause', pause)
        other.put_run(run('run-one', 'one', 'owner', 2, phase='unknown'))
        schedules.sync()
        current = app.state['sessions'][0]['schedules'][0]
        assert current['status'] == 'paused' and current['revision'] == 2
        assert current['authorization']['messageId'] == 'user-request'
        assert [row['id'] for row in current['runs']] == ['run-one']
        assert current['runs'][0]['phase'] == 'unknown'
        assert app.state['sessions'][1]['schedules'] == []
        with pytest.raises(ValueError, match='revision'):
            schedules.store.mutate('schedule.pause', {'sessionId': 'owner', 'id': 'one', 'expectedRevision': 1}, 'stale', pause)
        with pytest.raises(ValueError, match='belong'):
            schedules.store.get('other', 'one')
        other.transition('owner', 'run-one', ['unknown'], 'abandoned', 3, detail='Reviewed; do not replay')
        schedules.sync()
        assert app.state['sessions'][0]['schedules'][0]['runs'][0]['phase'] == 'abandoned'
    finally:
        other.close()
        schedules.store.close()


async def test_visible_schedule_and_unknown_run_survive_restart_without_replay(tmp_path, monkeypatch):
    from test_schedules import fixture, schedule, Runtime, attach
    from amplifier_web.service import AppService

    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        created = await schedule(app, sid, now[0])
        assert app._session(sid)['schedules'] == [{**created, 'runs': []}]
        runtime.failure = RuntimeError('Connection ended after submission')
        now[0] += 61
        await app.schedules.tick()
        assert len(runtime.inputs) == 1
        visible = app._session(sid)['schedules'][0]
        assert visible['runs'][0]['phase'] == 'unknown'
        run_id = visible['runs'][0]['id']
    finally:
        await app.close()
    restored_runtime = Runtime()
    restored = AppService(tmp_path / 'app', restored_runtime, workspace=tmp_path)
    attach(restored, restored_runtime, now)
    try:
        now[0] += 1000
        await restored.schedules.tick()
        visible = restored._session(sid)['schedules'][0]
        assert visible['status'] == 'needs_review'
        assert visible['runs'][0]['id'] == run_id and visible['runs'][0]['phase'] == 'unknown'
        assert restored_runtime.inputs == []
        original_run = restored.schedules.store.run(sid, run_id)
        await restored.dispatch('schedule.reconcile', {'sessionId': sid, 'runId': run_id,
            'expectedRevision': original_run['revision'], 'resolution': 'abandoned',
            'evidence': 'Reviewed the original input; do not repeat it.'}, command_id='reconcile-projection')
        assert restored._session(sid)['schedules'][0]['runs'][0]['phase'] == 'abandoned'
        await restored.schedules.tick()
        assert restored_runtime.inputs == []
    finally:
        await restored.close()
