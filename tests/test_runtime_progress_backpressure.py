"""Buffered worker telemetry must not delay control messages or HTTP work."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from amplifier_web.runtime import RuntimeManager
from test_automatic_history import app_factory


@pytest.mark.parametrize('terminal', ['ready', 'stopped', 'error'])
async def test_module_preparation_burst_keeps_latest_progress_and_durable_terminal(
    app_factory, monkeypatch, terminal,
):
    app = app_factory()
    await app.dispatch('session.create', {})
    sid = app.state['selectedSessionId']
    await app.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'starting'})
    revision = app.state['revision']
    saves = []
    original = app._save

    def save():
        saves.append(app._session(sid)['status'])
        original()

    monkeypatch.setattr(app, '_save', save)
    manager = RuntimeManager()
    row = {'emit': app.on_runtime_event, 'started_at': time.monotonic(),
           'phase': 'bundle-preparation', 'detail': ''}
    for number in range(55):
        row['detail'] = f'Preparing configured module {number}'
        await manager._emit_progress(sid, row)
    assert saves == [], 'Module callbacks are progress, not 55 durable transitions'
    assert app.state['revision'] == revision
    assert app._session(sid)['progress']['detail'] == 'Preparing configured module 54'
    # Revision-sensitive readers can explicitly flush the latest progress.
    await app._flush_pending_progress()
    assert saves == ['starting']
    assert app.browser_state()['sessions'][0]['progress']['detail'].endswith('54')
    row['detail'] = 'Final setup observation'
    await manager._emit_progress(sid, row)
    if terminal == 'error':
        await app.on_runtime_event('runtime.error', {'sessionId': sid, 'error': 'Fixture setup failed'})
    else:
        await app.on_runtime_event('runtime.status', {'sessionId': sid, 'status': terminal})
    assert saves == ['starting', terminal]
    assert not app._progress_dirty
    saved_revision = app.state['revision']
    persisted = json.loads(app.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
    assert persisted['revision'] == saved_revision
    await asyncio.sleep(.3)
    assert app._session(sid)['status'] == terminal
    assert app.state['revision'] == saved_revision, 'A stale timer must not republish terminal work'


async def test_buffered_worker_events_yield_to_bridge_without_reordering_events():
    order, replies = [], []

    async def bridge(operation, args, sid):
        order.append('bridge')
        return {'accepted': True}

    async def emit(kind, payload):
        if kind == 'assistant.delta':
            order.append(payload['text'])

    async def drain():
        pass

    async def wait():
        return 0

    stream = asyncio.StreamReader()
    messages = [{'op': 'bridge', 'id': 'capacity', 'operation': 'capacity.admit', 'args': {}}]
    messages += [{'type': 'assistant.delta', 'text': str(number)} for number in range(55)]
    stream.feed_data(b''.join(json.dumps(row).encode() + b'\n' for row in messages))
    stream.feed_eof()
    process = SimpleNamespace(stdout=stream, returncode=None, wait=wait,
                              stdin=SimpleNamespace(write=replies.append, drain=drain))
    row = {'process': process, 'emit': emit, 'closing': True, 'pending': {},
           'inputId': 'input', 'bridge_tasks': set(), 'backgroundCalls': set()}
    manager = RuntimeManager(bridge)
    await manager._read('session', row)
    await asyncio.gather(*row['bridge_tasks'])
    assert [item for item in order if item != 'bridge'] == [str(number) for number in range(55)]
    assert order.index('bridge') < order.index('54'), 'Buffered telemetry must not monopolize the reader'
    assert len(replies) == 1
    assert json.loads(replies[0]) == {'op': 'bridge.result', 'id': 'capacity', 'result': {'accepted': True}}
