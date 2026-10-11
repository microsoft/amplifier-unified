import json
from pathlib import Path
import pytest
from amplifier_unified_workspaces.owner import Owner, WorkspaceError, identity
from amplifier_unified_workspaces import workspace_navigation as nav
from test_owner import config, action, CatalogFixture


async def pin(owner, row, value, command):
    state = await action(owner, 'pins', {})
    return await action(owner, 'pin', {'id': row['id'], 'pinned': value,
                        'expectedRevision': state['revision']}, command)


async def test_shared_order_empty_missing_rename_remove_and_restart(tmp_path):
    cfg = config(tmp_path); catalog = CatalogFixture(); owner = Owner(cfg, catalog)
    try:
        rows = []
        for name in ('Alpha', 'Beta', 'Gamma'):
            folder = Path(owner.roots[0]) / name; folder.mkdir()
            rows.append((await action(owner, 'add', {'path': str(folder)}, 'add-'+name))['workspace'])
        original = {row['id']: owner.local(row['id']) for row in rows}
        for row in rows:
            await pin(owner, row, True, 'pin-'+row['id'])
        pins = await action(owner, 'pins', {})
        assert pins['ids'] == [r['id'] for r in rows]
        assert catalog.session_queries == []
        assert original == {row['id']: owner.local(row['id']) for row in rows}
        args = {'ids': list(reversed(pins['ids'])), 'expectedRevision': pins['revision']}
        first = await action(owner, 'pinOrder', args, 'order')
        assert first['receipt']['status'] == 'completed'
        await owner.close(); owner = Owner(cfg, catalog)
        repeated = await action(owner, 'pinOrder', args, 'order')
        assert repeated['pinsRevision'] == first['pinsRevision'] and repeated['replayed'] is False
        Path(rows[1]['path']).rmdir()
        pins = await action(owner, 'pins', {})
        assert pins['ids'] == args['ids'] and pins['items'][1]['availability'] == 'missing'
        renamed = await action(owner, 'rename', {'id': rows[0]['id'], 'name': 'Renamed',
                              'expectedRevision': rows[0]['revision']}, 'rename')
        pins = await action(owner, 'pins', {})
        assert pins['items'][-1]['name'] == 'Renamed' and pins['ids'] == args['ids']
        await action(owner, 'remove', {'id': rows[0]['id'],
                     'expectedRevision': renamed['workspace']['revision']}, 'remove')
        assert (await action(owner, 'pins', {}))['ids'] == args['ids'][:-1]
        assert Path(rows[0]['path']).exists() and catalog.session_queries == []
    finally:
        await owner.close()


async def test_stale_duplicate_order_unknown_and_held_writes_refuse(tmp_path):
    owner = Owner(config(tmp_path), CatalogFixture())
    try:
        row = owner.record(Path(owner.roots[0])/'empty', 'Empty', False)
        stale = {'id': row['id'], 'pinned': False, 'expectedRevision': 0}
        await pin(owner, row, True, 'pin')
        with pytest.raises(WorkspaceError, match='changed') as error:
            await action(owner, 'pin', stale, 'stale')
        assert error.value.receipt['status'] == 'rejected'
        pins = await action(owner, 'pins', {})
        for index, ids in enumerate(([], [row['id'], row['id']], ['workspace:'+'a'*64], [None])):
            with pytest.raises(WorkspaceError, match='exactly once'):
                await action(owner, 'pinOrder', {'ids': ids, 'expectedRevision': pins['revision']}, 'bad-'+str(index))
        with owner.db() as db:
            args = {'id': row['id'], 'pinned': False, 'expectedRevision': pins['revision']}
            db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?)',
                       ('unknown', 'workspace.pin', json.dumps(args, sort_keys=True), 'unknown', '{}', 0))
        with pytest.raises(WorkspaceError, match='unresolved') as error:
            await action(owner, 'pin', args, 'unknown')
        assert error.value.executed is None
        context = {'fenceId':'pins-held','commandId':'hold','purpose':'recovery','instanceId':'fixture','dataScope':'pins'}
        await owner.request('quiescence/acquire', context)
        assert (await action(owner, 'pins', {}))['ids'] == [row['id']]
        with pytest.raises(WorkspaceError, match='held'):
            await action(owner, 'pin', args, 'held-pin')
        assert owner.public_receipt('held-pin') is None
    finally:
        await owner.close()


async def test_bounded_pages_authorization_and_stale_offset(tmp_path, monkeypatch):
    cfg = config(tmp_path); owner = Owner(cfg, CatalogFixture())
    try:
        ids = [owner.record(Path(owner.roots[0])/str(n), 'Workspace '+str(n), False)['id'] for n in range(123)]
        with owner.db() as db:
            nav.write(db, nav.saved(db), ids)
        calls = []
        original = owner.authorize
        def authorize(*args, **kwargs):
            calls.append(args[0]); return original(*args, **kwargs)
        monkeypatch.setattr(owner, 'authorize', authorize)
        first = await action(owner, 'pins', {'limit': 50})
        assert first['ids'] == ids and len(first['items']) == 50 and first['nextOffset'] == 50
        assert len(calls) == 50
        second = await action(owner, 'pins', {'offset': 100, 'expectedRevision': first['revision']})
        assert [row['id'] for row in second['items']] == ids[100:] and second['nextOffset'] is None
        assert len(calls) == 73
        await pin(owner, owner.local(ids[0]), False, 'unpin')
        with pytest.raises(ValueError, match='changed'):
            await action(owner, 'pins', {'offset': 50, 'expectedRevision': first['revision']})
        owner.roots = [str(Path(cfg['allowedRoots'][0])/'100')]
        scoped = await action(owner, 'pins', {})
        assert scoped['ids'] == [ids[100]]
        assert len(scoped['items']) == 1
        await action(owner, 'pinOrder', {'ids': scoped['ids'], 'expectedRevision': scoped['revision']}, 'scoped-order')
        with owner.db() as db:
            assert nav.saved(db)['ids'] == ids[1:]
    finally:
        await owner.close()


async def test_atomic_pin_receipt_and_corrupt_preferences_preserved(tmp_path, monkeypatch):
    cfg = config(tmp_path); owner = Owner(cfg, CatalogFixture())
    row = owner.record(Path(owner.roots[0])/'empty', 'Empty', False)
    original = nav.write
    def interrupted(db, previous, ids):
        original(db, previous, ids)
        raise RuntimeError('Crash before receipt')
    monkeypatch.setattr(nav, 'write', interrupted)
    try:
        with pytest.raises(RuntimeError, match='Crash'):
            await pin(owner, row, True, 'interrupted')
        assert (await action(owner, 'pins', {}))['ids'] == []
        assert owner.public_receipt('interrupted') is None
        monkeypatch.setattr(nav, 'write', original)
        await pin(owner, row, True, 'pin')
        with owner.db() as db:
            db.execute("DELETE FROM meta WHERE key='workspacePins'")
    finally:
        await owner.close()
    with pytest.raises(ValueError, match='preserved'):
        Owner(cfg, CatalogFixture())


async def test_full_size_order_is_bounded_and_not_truncated_at_command_limit(tmp_path):
    owner = Owner(config(tmp_path), CatalogFixture())
    try:
        with owner.db() as db:
            rows = [(identity(Path(owner.roots[0])/str(n)), str(Path(owner.roots[0])/str(n)), str(n), 0, n+1)
                    for n in range(nav.MAX_PINS)]
            db.executemany('INSERT INTO registrations VALUES(?,?,?,?,?)', rows)
            ids = [row[0] for row in rows]; nav.write(db, nav.saved(db), ids)
        pins = await action(owner, 'pins', {'limit': 1})
        assert len(pins['ids']) == 10000 and len(pins['items']) == 1
        order = list(reversed(ids))
        result = await action(owner, 'pinOrder', {'ids': order, 'expectedRevision': pins['revision']}, 'order')
        assert result['receipt']['status'] == 'completed'
        assert (await action(owner, 'pins', {'limit': 1}))['ids'] == order
        with pytest.raises(WorkspaceError, match='exactly once'):
            await action(owner, 'pinOrder', {'ids': order[:-1], 'expectedRevision': result['pinsRevision']}, 'omit')
    finally:
        await owner.close()


async def test_revision_limit_preserves_readable_preferences(tmp_path):
    owner = Owner(config(tmp_path), CatalogFixture())
    try:
        row = owner.record(Path(owner.roots[0])/'empty', 'Empty', False)
        with owner.db() as db:
            db.execute("INSERT INTO meta VALUES('workspacePins',?)",
                       (json.dumps({'revision': 9007199254740990, 'ids': []}),))
            db.execute("INSERT INTO meta VALUES('workspacePinsVersion','1')")
        await pin(owner, row, True, 'last-revision')
        pins = await action(owner, 'pins', {})
        assert pins['revision'] == 9007199254740991
        with pytest.raises(ValueError, match='revision limit'):
            await pin(owner, row, False, 'overflow')
        assert (await action(owner, 'pins', {}))['ids'] == [row['id']]
    finally:
        await owner.close()
