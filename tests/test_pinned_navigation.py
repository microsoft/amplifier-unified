"""Mixed shortcuts preserve identities, complete order and passive storage."""
from copy import deepcopy
import json
import sqlite3

import pytest

from amplifier_web.service import AppError, AppService
from amplifier_web.pinned_navigation import ordered_ids, reorder, reorder_kind
from amplifier_web.state_records import load
from test_workspace_experience import app


def test_legacy_order_migration_and_kind_reordering_preserve_other_slots():
    value = {'workspaces': [{'id': 'same'}, {'id': 'hidden'}],
             'pinnedSessionIds': ['same', 'other'], 'pinnedWorkspaceIds': ['same', 'hidden']}
    assert ordered_ids(value) == ['session:same', 'session:other', 'workspace:same', 'workspace:hidden']
    mixed = ['workspace:same', 'session:other', 'workspace:hidden', 'session:same']
    reorder(value, mixed)
    reorder_kind(value, 'session', ['same', 'other'])
    value['pinnedSessionIds'] = ['same', 'other']
    assert ordered_ids(value) == ['workspace:same', 'session:same', 'workspace:hidden', 'session:other']
    before = deepcopy(value)
    for invalid in [mixed[:-1], mixed + [mixed[0]], mixed[:-1] + ['workspace:unknown']]:
        with pytest.raises(ValueError):
            reorder(value, invalid)
        assert value == before


def test_combined_page_is_bounded_after_merging_and_keeps_hidden_membership():
    from amplifier_web.pinned_navigation import snapshot
    workspaces = [{'id': str(i)} for i in range(55)]
    value = {'workspaces': workspaces, 'pinnedWorkspaceIds': [str(i) for i in range(55)],
             'pinnedSessionIds': ['same', 'hidden'], 'view': {'navPinnedPage': 0}}
    ids = ['workspace:0', 'session:same', 'session:hidden'] + [f'workspace:{i}' for i in range(1, 55)]
    reorder(value, ids)
    chats = [{'id': 'same', 'pinned': True}]
    rows = [{'workspaceId': str(i), 'path': f'/projects/{i}'} for i in range(55)]
    first = snapshot(value, chats, rows)
    assert first['total'] == 56 and first['pages'] == 2
    assert len(first['items']) == 50
    assert [item['id'] for item in first['items'][:2]] == ['workspace:0', 'session:same']
    value['view']['navPinnedPage'] = 1
    second = snapshot(value, chats, rows)
    assert [item['id'] for item in second['items']] == [f'workspace:{i}' for i in range(49, 55)]
    assert ordered_ids(value) == ids
    # A shrinking page clamps without losing stored IDs or collapse state.
    value['view']['navSectionsCollapsed'] = ['pinned']
    empty = snapshot(value, [], [])
    assert empty['index'] == 0 and empty['total'] == 0
    assert value['view']['navSectionsCollapsed'] == ['pinned']


def test_workspace_pins_are_not_limited_to_shortcuts_or_explorer_search():
    from amplifier_web.state_projections import StateProjections
    value = {'sessions': [], 'workspaces': [
        {'id': str(i), 'name': str(i), 'path': f'/projects/{i:03}', 'available': True}
        for i in range(115)], 'pinnedSessionIds': [],
        'pinnedWorkspaceIds': [str(i) for i in range(110)],
        'selectedWorkspaceId': '114', 'view': {'navChatScope': 'all',
        'navWorkspaceMode': 'folders', 'navWorkspaceFilter': 'missing', 'navWorkspacePage': 7}}
    before = deepcopy(value)
    projections = StateProjections()
    mixed = projections.pinned_navigation(value)
    assert mixed['total'] == 110 and len(mixed['items']) == 50
    assert [item['id'] for item in mixed['items'][:7]] == [f'workspace:{i}' for i in range(7)]
    # Ordinary lists retain pins and their bounded first page.
    shortcuts = projections.workspace_shortcuts(value)
    assert len(shortcuts) == 100
    assert [row['workspaceId'] for row in shortcuts] == [str(i) for i in range(100)]
    assert [row['workspaceId'] for row in projections.workspace_shortcuts(value, workspace_id='114')] == ['114']
    assert value == before


async def test_scoped_shortcuts_retain_pinned_alias_of_same_registered_path(app, tmp_path):
    path = str(tmp_path / 'project')
    app.state['workspaces'] = [
        {'id': identity, 'path': path, 'name': 'Project', 'available': True}
        for identity in ['legacy', 'current']]
    app.state['selectedWorkspaceId'] = 'current'
    app.state['pinnedWorkspaceIds'] = ['legacy']
    app.projections.invalidate()
    rows = app.projections.workspace_shortcuts(app.state, workspace_id='current')
    assert [(row['workspaceId'], row['path'], row['pinned']) for row in rows] == [('legacy', path, True)]
    client = app.shell.client('alias-reader')
    instance = {'id': 'alias-chats', 'package': 'builtin.chats', 'slot': 'navigation',
                'scope': {'mode': 'pinned', 'workspaceId': 'current'}}
    result = app.shell.snapshot(client, instance, identity='alias-reader',
                               navigation_resources=frozenset({'workspaceShortcuts'}))
    assert result['workspaceShortcuts'] == rows
    assert result['selectedWorkspaceId'] == 'current'


async def populate(app, tmp_path):
    ids = []
    for name in ('one', 'two'):
        path = tmp_path / name
        path.mkdir()
        await app.dispatch('workspace.add', {'path': str(path)})
        await app.dispatch('session.create', {'workspace': str(path), 'title': name})
        ids.append((app.state['selectedWorkspaceId'], app.state['selectedSessionId']))
    return ids


async def test_mixed_pins_order_actions_projection_and_client_preservation(app, tmp_path, monkeypatch):
    (one, a), (two, b) = await populate(app, tmp_path)
    app.clients.attach('reader'); app.clients.attach('other')
    with app.clients.bind('reader'):
        await app.dispatch('view.update', {'patch': {'draft': 'Retain unsent text'}})
        await app.dispatch('canvas.show', {'kind': 'text', 'content': 'Retain Canvas'})
        await app.dispatch('session.pin', {'id': a, 'pinned': True})
        await app.dispatch('workspace.pin', {'id': two, 'pinned': True})
        await app.dispatch('session.pin', {'id': b, 'pinned': True})
        initial = [f'session:{a}', f'workspace:{two}', f'session:{b}']
        assert ordered_ids(app.state) == initial
        before = deepcopy(app.clients.record())
        sessions = deepcopy(app.state['sessions'])
        other = deepcopy(app.clients.records['other'])
        ids = [f'workspace:{two}', f'session:{b}', f'session:{a}']
        def forbidden(*_args, **_kwargs):
            pytest.fail('Mixed reorder must not flush conversations or Canvas')
        with monkeypatch.context() as patch:
            patch.setattr(app, '_save', forbidden)
            result = await app.dispatch('shell.command', {'clientId': 'reader', 'instanceId': 'chats',
                                       'action': 'navigation.pinOrder', 'args': {'ids': ids}},
                                       command_id='mixed-order')
        assert result['accepted']
        assert app.clients.record() == before
        assert app.clients.records['other'] == other
        assert app.state['sessions'] == sessions
        query = app.shell.inspect('reader', snapshots=True)['snapshots']['chats']
        assert query['pinnedItemIds'] == ids
        assert [row['id'] for row in query['pinnedNavigation']['items']] == ids
        assert [row['id'] for row in query['sidebarNavigation']['pinned']['items']] == [b, a]
        assert two in [row['workspaceId'] for row in query['workspaceShortcuts']]
        assert {a, b} <= {row['id'] for row in query['recentNavigation']['items']}
        assert load(app.db)['pinnedItemIds'] == ids
        # Old clients change only the slots for their kind.
        await app.dispatch('session.pinOrder', {'ids': [a, b]})
        assert ordered_ids(app.state) == [f'workspace:{two}', f'session:{a}', f'session:{b}']
        await app.dispatch('workspace.pin', {'id': one, 'pinned': True})
        await app.dispatch('workspace.pinOrder', {'ids': [one, two]})
        assert ordered_ids(app.state) == [f'workspace:{one}', f'session:{a}', f'session:{b}', f'workspace:{two}']


async def test_mixed_pins_survive_backup_export_restart_and_hidden_restoration(app, tmp_path):
    (one, a), (two, b) = await populate(app, tmp_path)
    for action, identity in [('session.pin', a), ('workspace.pin', two), ('session.pin', b)]:
        await app.dispatch(action, {'id': identity, 'pinned': True})
    ids = [f'session:{a}', f'workspace:{two}', f'session:{b}']
    await app.dispatch('navigation.pinOrder', {'ids': ids})
    row = next(row for row in app.state['workspaces'] if row['id'] == two)
    row['available'] = False
    app.projections.invalidate()
    app.clients.attach('reader')
    query = app.shell.inspect('reader', snapshots=True)['snapshots']['chats']
    assert f'workspace:{two}' not in [item['id'] for item in query['pinnedNavigation']['items']]
    assert query['pinnedItemIds'] == ids
    row['available'] = True
    app.projections.invalidate()
    assert [item['id'] for item in app.shell.inspect('reader', snapshots=True)['snapshots']['chats']['pinnedNavigation']['items']] == ids
    app._save()
    assert load(app.db)['pinnedItemIds'] == ids
    exported = await app.dispatch('state.export', {})
    assert json.loads(exported['effects'][0]['content'])['pinnedItemIds'] == ids
    backup = sqlite3.connect(tmp_path / 'backup.sqlite3')
    app.db.backup(backup)
    assert load(backup)['pinnedItemIds'] == ids
    backup.close()
    data_dir, workspace = app.data_dir, app.state['settings']['workspace']
    await app.close()
    restored = AppService(data_dir, workspace=workspace)
    try:
        assert ordered_ids(restored.state) == ids
        await restored.dispatch('workspace.remove', {'id': two})
        assert ordered_ids(restored.state) == [f'session:{a}', f'session:{b}']
    finally:
        await restored.close()


async def test_mixed_reorder_rejects_stale_membership_and_rolls_back_transaction(app, tmp_path, monkeypatch):
    import amplifier_web.state_records as records
    (one, a), _ = await populate(app, tmp_path)
    await app.dispatch('session.pin', {'id': a, 'pinned': True})
    await app.dispatch('workspace.pin', {'id': one, 'pinned': True})
    before = deepcopy(app._state)
    with pytest.raises(AppError):
        await app.dispatch('navigation.pinOrder', {'ids': [f'session:{a}']})
    assert app._state == before
    def fail(*_args, **_kwargs):
        raise OSError('Synthetic record failure')
    with monkeypatch.context() as patch:
        patch.setattr(records, 'save', fail)
        with pytest.raises(OSError):
            await app.dispatch('navigation.pinOrder', {'ids': [f'workspace:{one}', f'session:{a}']},
                               command_id='failed-mixed')
    assert app._state == before
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='failed-mixed'").fetchone()


async def test_adjacent_mixed_reorders_receipt_idempotence_conflict_and_safe_read(app, tmp_path):
    (one, a), (two, b) = await populate(app, tmp_path)
    for action, identity in [('session.pin', a), ('workspace.pin', two), ('session.pin', b)]:
        await app.dispatch(action, {'id': identity, 'pinned': True})
    app.clients.attach('pin-reader')
    first = [f'workspace:{two}', f'session:{a}', f'session:{b}']
    second = [f'session:{b}', f'workspace:{two}', f'session:{a}']
    receipt = await app.dispatch('navigation.pinOrder', {'ids': first}, command_id='first-pin-move')
    stale_revision = app.state['revision']
    await app.dispatch('navigation.pinOrder', {'ids': second}, command_id='second-pin-move')
    # Re-reading the same command receipt cannot replay the first order over B.
    again = await app.dispatch('navigation.pinOrder', {'ids': first}, command_id='first-pin-move')
    assert again['revision'] == receipt['revision']
    assert ordered_ids(app.state) == second
    # The same total order under a fresh intent has no membership/order effect.
    await app.dispatch('navigation.pinOrder', {'ids': second}, command_id='same-pin-order')
    with pytest.raises(AppError):
        await app.dispatch('navigation.pinOrder', {'ids': first},
                           command_id='stale-pin-move', expected_revision=stale_revision)
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='stale-pin-move'").fetchone()
    current = await app.dispatch('shell.query', {'clientId': 'pin-reader', 'instanceId': 'chats'})
    assert current['result']['pinnedItemIds'] == second
    assert load(app.db)['pinnedItemIds'] == second