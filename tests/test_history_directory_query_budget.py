"""Typed directory query work stays linear in retained metadata and live owners."""
import json
import time

import pytest

from amplifier_web.history_directory import COLUMNS
from test_automatic_history import app_factory


def measure_sql(app, query):
    instructions = 0
    def progress():
        nonlocal instructions
        instructions += 1000
        return 0
    app.db.set_progress_handler(progress, 1000)
    start = time.monotonic()
    try:
        result = query()
    finally:
        app.db.set_progress_handler(None, 0)
    return result, instructions, (time.monotonic() - start) * 1000


@pytest.mark.parametrize('count,live', [(27000, 6), (1000, 1000)])
async def test_local_catalog_navigation_and_children_avoid_quadratic_live_overlay_join(app_factory, count, live):
    app = app_factory()
    template = app._new_session({'title': 'Seed owner'})
    rows = [{**template, 'id': f'owner-{number:05}', 'title': f'Owner {number:05}',
             'messages': [], 'workers': [], 'recentActivityAt': number,
             'navigationActivityAt': number} for number in range(live)]
    app._state['sessions'] = rows
    app._state['selectedSessionId'] = rows[0]['id']
    app._save()
    scalar = dict(zip(COLUMNS, app.db.execute('SELECT ' + ','.join(COLUMNS) +
        ' FROM history_local_rows WHERE id=?', (rows[0]['id'],)).fetchone()))
    def dormant():
        for number in range(live, count):
            identity = f'owner-{number:05}'
            values = {**scalar, 'id': identity, 'native_id': identity, 'alias_id': identity,
                'title': f'Owner {number:05}', 'recent': number, 'ordinal': f'{number:020}',
                'summary': json.dumps({**template, 'id': identity, 'title': f'Owner {number:05}',
                                      'messages': [], 'workers': [], 'recentActivityAt': number})}
            yield tuple(values[name] for name in COLUMNS)
    app.db.executemany('INSERT INTO history_local_rows (' + ','.join(COLUMNS) + ') VALUES (' +
                      ','.join('?' for _ in COLUMNS) + ')', dormant())
    app.db.commit()
    state = {**app._state, 'view': {**app._state['view'], 'navChatScope': 'all'}}
    decoded = []
    original = app.directory.decode
    app.directory.decode = lambda raw: (decoded.append(1), original(raw))[1]
    result, instructions, elapsed = measure_sql(app, lambda: app.directory.chats(state))
    assert result['total'] == count
    assert len(decoded) == len(result['items']) <= 100
    assert instructions < count * 1200 + live * 2000
    children, child_work, child_ms = measure_sql(app, lambda: app.directory.children(state, rows[0]))
    assert children['total'] == 0 and children['items'] == []
    assert child_work < count * 700 + live * 2000
    assert len(app._state['sessions']) == live
    print(json.dumps({'catalog': count, 'live': live, 'navigationVm': instructions,
                      'navigationMs': elapsed, 'childrenVm': child_work, 'childrenMs': child_ms}))
