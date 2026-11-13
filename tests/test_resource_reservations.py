"""Retired cooperative claims stay inert; real resource ownership remains live."""
import copy
import json
import sqlite3

import pytest

from amplifier_web import workspace_resources as resources
from amplifier_web.service import ACTION_DEFINITIONS, AppError
from test_collaborative_workspaces import app, agent_action
from test_task_resources import allocation

RETIRED_ACTIONS = tuple('workspace.resources.' + name for name in
                        ('reservations', 'reserve', 'release', 'uncertain'))


def seed_legacy_claims(home, workspace='workspace', root='root', count=2):
    """Historical upgrade fixture, deliberately not a replacement claim API."""
    folder = home / 'workspace-resources'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'reservations.sqlite3'
    with sqlite3.connect(target) as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS claims(id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
                resource_key TEXT NOT NULL, root TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
            INSERT OR IGNORE INTO metadata VALUES(1, 99);
        ''')
        for index in range(count):
            status = 'held' if index % 2 == 0 else 'uncertain'
            # Include malformed and private bodies: current reads must not even
            # decode these, let alone ask a person to reconcile them.
            body = 'malformed historical body' if index % 2 else json.dumps({
                'private': 'Private historical claim', 'status': status, 'revision': 7})
            db.execute('INSERT INTO claims VALUES(?,?,?,?,?,?)',
                       (f'legacy-{index}', workspace, f'key-{index}', root, status, body))
        db.execute('INSERT INTO commands VALUES(?,?,?)',
                   ('old-acquire', 'retained-fingerprint', '{"outcome":"reserved","private":"old receipt"}'))
    target.chmod(0o600)
    return target


def retained_rows(target):
    with sqlite3.connect(target) as db:
        return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
                for table in ('claims', 'commands', 'metadata')}


@pytest.mark.parametrize('origin', ['ui', 'agent'])
@pytest.mark.parametrize('action', RETIRED_ACTIONS)
async def test_retired_actions_are_undiscoverable_and_unsupported_not_replayed(app, origin, action):
    source = app.state['sessions'][0]
    workspace = app.state['workspaces'][0]['id']
    target = seed_legacy_claims(app.data_dir, workspace, source['id'])
    before = target.read_bytes()
    catalog = await app.app_bridge('list_actions', {'prefix': 'workspace.resources.'}, source['id'])
    assert not set(RETIRED_ACTIONS) & {item['name'] for item in catalog['items']}
    assert not set(RETIRED_ACTIONS) & ACTION_DEFINITIONS.keys()
    assert {'workspace.resources.managed', 'workspace.resources.provision',
            'workspace.resources.read', 'workspace.resources.retire'} <= ACTION_DEFINITIONS.keys()
    with pytest.raises(AppError, match='Unknown action:') as error:
        if origin == 'agent':
            await agent_action(app, source, action, {'workspaceId': workspace}, 'old-acquire')
        else:
            await app.dispatch(action, {'workspaceId': workspace}, command_id='old-acquire')
    assert error.value.status == 404
    assert target.read_bytes() == before
    assert not app.runtime.inputs


@pytest.mark.parametrize('count', [2, 1200])
async def test_context_and_recap_do_no_work_on_retired_claim_history(app, monkeypatch, count):
    source = app.state['sessions'][0]
    workspace = app.state['workspaces'][0]['id']
    target = seed_legacy_claims(app.data_dir, workspace, source['id'], count)
    before = retained_rows(target)
    view = copy.deepcopy(app.state['view'])
    original = sqlite3.connect
    queries = []
    def observed(*args, **kwargs):
        db = original(*args, **kwargs)
        db.set_trace_callback(queries.append)
        return db
    monkeypatch.setattr(sqlite3, 'connect', observed)
    for action in ('coordination.context', 'coordination.recap'):
        result = await agent_action(app, source, action, {'sessionId': source['id']}, action)
        assert 'resources' not in result['result']
        assert 'Private historical claim' not in json.dumps(result)
        assert 'Resource reservation' not in json.dumps(result['result'])
        assert 'cooperative-matching-keys' not in json.dumps(result['result'])
    assert not any('claims' in query.lower() for query in queries)
    assert retained_rows(target) == before
    assert app.state['view'] == view and not app.runtime.inputs
    assert not hasattr(resources, 'reservation_command')


def test_old_claims_and_mixed_receipts_do_not_gate_real_storage_or_inventory(tmp_path):
    home = tmp_path / 'home'
    target = seed_legacy_claims(home, count=1200)
    before = retained_rows(target)
    # The existing allocation owner still uses this same ledger. No migration,
    # TTL or synthetic release of old claims/commands is needed for admission.
    row = allocation(home, command='actual-storage')
    assert row['status'] == 'ready' and row['observation'] == 'present'
    item = resources.command(home, '00000000-0000-0000-0000-000000000001', 'workspace.resources.add',
        {'kind': 'preview', 'resourceId': 'key-0', 'owner': 'task', 'note': 'actual inventory'})
    assert item['status'] == 'active'
    after = retained_rows(target)
    assert after['claims'] == before['claims'] and after['metadata'] == before['metadata']
    assert after['commands'][:len(before['commands'])] == before['commands']
    assert len(after['commands']) > len(before['commands'])
    # A command ID from the retired mechanism cannot acquire real storage.
    with pytest.raises(ValueError, match='different'):
        allocation(home, command='old-acquire')
    assert retained_rows(target) == after
