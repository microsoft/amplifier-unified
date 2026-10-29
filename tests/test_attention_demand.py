"""Notice demand bounds body work, while global review facts remain exact."""
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from amplifier_web import attention, browser_demand
from amplifier_web.state_projections import StateProjections
from test_history_directory_navigation import directory_fixture


def legacy(directory, state):
    return attention.snapshot({**state, 'sessions': directory.attention_rows(state)})


def browser(projections, directory, state):
    return directory.read_frame(lambda: browser_demand.project({**state,
        **projections.browser(state, selected_context=True)})['attention'])


def synthetic_notices(number):
    # Match the observed shape, never its contents: historical resolved
    # questions and coordination metadata dwarf a small current notice.
    return [{'id': f'notice-{i}', 'title': f'Conversation {i}', 'status': 'idle',
             'workspace': '/projects/alpha', 'sessionKind': 'root',
             'completion': {'id': f'response-{i}', 'at': i + 1},
             'questions': [{'id': f'q-{i}-{j}', 'status': 'answered', 'required': True,
                            'revision': 1, 'prompt': 'Synthetic resolved question. ' * 10}
                           for j in range(90 if i < 5 else 20)],
             'coordinationReference': {'notes': 'Synthetic retained reference. ' * 120}}
            for i in range(number)]


def test_browser_notice_work_does_not_decode_dormant_question_history(tmp_path, monkeypatch):
    state, directory = directory_fixture(tmp_path, 0)
    residents = [{'id': 'selected', 'title': 'Selected', 'status': 'idle', 'completion': {'id': 'selected-response'}},
                 {'id': 'other', 'title': 'Other', 'status': 'idle'}]
    state.update(sessions=residents, selectedSessionId='selected')
    state['view']['panel'] = 'chat-work'
    state['notificationSettings'] = {'completionBadge': True}
    projections = StateProjections(service=SimpleNamespace(directory=directory))
    decoded, slots = [], []
    metrics = {}
    def increment(name, amount=1):
        metrics[name] = metrics.get(name, 0) + amount
    directory.service.performance_diagnostics = SimpleNamespace(increment=increment)
    original_decode, original_item = directory.decode, attention.session_item_at
    monkeypatch.setattr(directory, 'decode', lambda row: (decoded.append(1), original_decode(row))[1])
    monkeypatch.setattr(attention, 'session_item_at', lambda row, *args:
                        (slots.append(row['id']), original_item(row, *args))[1])
    work = []
    for number in (10, 300):
        rows = synthetic_notices(number)
        state['attentionRead'] = {
            item['id']: item['fingerprint'] for row in rows
            if (item := original_item(row, {}, len(row['questions']) + 1))}
        directory.sync(rows, state)
        directory.db.commit()
        before = list(directory.db.execute('SELECT id,summary FROM history_local_rows ORDER BY id'))
        expected = legacy(directory, state)
        expected['items'] = [item for item in expected['items'] if item.get('sessionId') == 'selected']
        expected['workspaces'] = {}
        observations = []
        for _ in range(2):
            # Rebuild on each saved generation, not a warm cache-only check.
            projections.invalidate(state=state, session_ids=set())
            decoded.clear(); slots.clear(); metrics.clear()
            vm = 0
            def progress():
                nonlocal vm
                vm += 1000
                return 0
            directory.db.set_progress_handler(progress, 1000)
            started = time.thread_time_ns()
            try:
                actual = browser(projections, directory, state)
            finally:
                elapsed = (time.thread_time_ns() - started) / 1e6
                directory.db.set_progress_handler(None, 0)
            assert actual == expected
            assert decoded == [], 'ordinary view decoded dormant session summaries'
            assert set(slots) <= {'selected', 'other'} and len(slots) <= 6
            assert metrics.get('directory.notices.decoded', 0) == 1  # Selected completion only.
            observations.append({'vm': vm, 'cpuMs': elapsed, 'summaryDecodes': len(decoded),
                                 'noticeDecodes': metrics.get('directory.notices.decoded', 0), 'slots': len(slots)})
        assert list(directory.db.execute('SELECT id,summary FROM history_local_rows ORDER BY id')) == before
        assert state['sessions'] == residents and len(residents) == 2
        work.append({'owners': number, 'summaryBytes': sum(len(row[1]) for row in before),
                     'observations': observations})
    # Scalar global aggregation may grow linearly. Reject accidental nested
    # notice/receipt scans; the small input has fixed setup work as well.
    assert max(row['vm'] for row in work[1]['observations']) < max(row['vm'] for row in work[0]['observations']) * 40
    print(json.dumps({'attentionDemand': work}))


@pytest.mark.parametrize('completion_badge', [False, True])
def test_counts_selected_details_activity_and_visible_receipts_match_legacy(tmp_path, completion_badge):
    state, directory = directory_fixture(tmp_path, 0)
    rows = synthetic_notices(5)
    rows[0].update(manualUnread='visit', error='Selected error', errorAt=1,
                   approvals=[{'id': 'approve', 'status': 'pending', 'title': 'Permission'}])
    rows[1].update(error='Off-page reviewed error', errorAt=2)
    rows[2].update(error='Off-page fresh error', errorAt=3)
    for row, status in ((rows[1], 'unknown'), (rows[2], 'rejected')):
        row['questions'].append({'id': 'delivery-' + status, 'status': 'answered', 'required': True,
                                 'prompt': 'Saved answer', 'revision': 3,
                                 'delivery': {'status': status, 'error': 'Synthetic admission failure'}})
    rows[3]['questions'].append({'id': 'pending', 'status': 'pending', 'required': True,
                                'prompt': 'Answer this', 'revision': 2})
    rows[4]['workspace'] = '/projects/beta'
    state.update(selectedSessionId=rows[0]['id'], notificationSettings={'completionBadge': completion_badge},
                 notificationError='Global failure')
    for row in rows:
        for position in range(attention.session_item_count(row)):
            item = attention.session_item_at(row, {}, position)
            if item and (item['id'].startswith('completion:') or item['id'] == 'session:notice-1'):
                state['attentionRead'][item['id']] = item['fingerprint']
    directory.sync(rows, state); directory.db.commit()
    projections = StateProjections(service=SimpleNamespace(directory=directory))
    expected = legacy(directory, state)
    assert projections.attention(state) == expected
    scoped = browser(projections, directory, state)
    assert scoped == browser_demand.project({**state, 'attention': expected})['attention']
    assert scoped['sessions'] == {'notice-0': 1, 'notice-1': 1, 'notice-2': 1, 'notice-3': 1}
    state['view']['panel'] = 'activity'
    assert browser(projections, directory, state)['items'] == expected['items']
    visible = {'notice-0', 'notice-1', 'notice-4'}
    assert projections.attention_receipts(state, visible) == [
        {key: item[key] for key in ('id', 'sessionId', 'fingerprint', 'read')}
        for item in expected['items'] if item.get('sessionId') in visible
        and item['id'].startswith(('session:', 'completion:', 'unread:'))]
    assert attention.reviewed_session_errors(projections.attention(state, session_ids=())) == {'notice-1'}


def test_native_title_workspace_and_suppression_remain_canonical(tmp_path):
    from amplifier_web.directory_attention import snapshot
    from test_history_directory_exact_ids import native_fixture, change_native
    state, directory, catalog, project, workspace = native_fixture(tmp_path, 2, 0)
    row = {'id': 'app-owner', 'nativeIdentity': 'saved-00000', 'nativeProject': project,
           'historyManaged': True, 'title': 'Old title', 'workspace': '/old', 'status': 'idle',
           'completion': {'id': 'response'}, 'manualUnread': 'visit',
           'approvals': [{'id': 'approval', 'status': 'pending', 'title': 'Allow fixture'}]}
    directory.sync([row], state); directory.db.commit()
    prior = legacy(directory, state)
    state['attentionRead'] = {item['id']: item['fingerprint'] for item in prior['items']}
    changed = catalog.get(project, 'saved-00000')
    changed.update(title='Renamed canonical owner', workspace='/new', workspaceId='new',
                   nameRevision=100, nameSource='manual')
    change_native(catalog, project, workspace, [changed])
    actual = snapshot(directory, state)
    assert actual == legacy(directory, state)
    approval = next(item for item in actual['items'] if item['id'].startswith('approval:'))
    assert approval['title'] == 'Approval requested · Renamed canonical owner' and not approval['read']
    assert {item['workspace'] for item in actual['items']} == {'/new'}
    assert {item['sessionId'] for item in actual['items']} == {'app-owner'}
    state['hiddenNativeSessions'] = [directory.db.execute(
        'SELECT hidden_id FROM history_local_rows WHERE id=?', ('app-owner',)).fetchone()[0]]
    assert snapshot(directory, state) == legacy(directory, state)
    assert snapshot(directory, state)['items'] == []


def test_multi_select_read_is_pinned_and_does_not_commit_outer_transaction(tmp_path, monkeypatch):
    from amplifier_web.directory_attention import snapshot, DirectoryAttention
    from test_history_directory_exact_ids import native_fixture
    state, directory, catalog, project, _ = native_fixture(tmp_path, 1, 0)
    row = {'id': 'app-owner', 'nativeIdentity': 'saved-00000', 'nativeProject': project,
           'historyManaged': True, 'title': 'Old', 'status': 'idle',
           'approvals': [{'id': 'a', 'title': 'Allow', 'status': 'pending'}]}
    directory.sync([row], state); directory.db.commit()
    expected = legacy(directory, state)
    original = DirectoryAttention.items
    def concurrent_rename(self, *args):
        with catalog.connect() as other:
            other.execute('UPDATE native_rows SET title=?,name_revision=100 WHERE identity=?',
                          ('Concurrent title', 'saved-00000'))
        return original(self, *args)
    monkeypatch.setattr(DirectoryAttention, 'items', concurrent_rename)
    assert snapshot(directory, state) == expected
    monkeypatch.setattr(DirectoryAttention, 'items', original)
    assert snapshot(directory, state) == legacy(directory, state)
    assert next(iter(snapshot(directory, state)['items']))['title'] == 'Approval requested · Concurrent title'
    assert not directory.db.in_transaction
    directory.db.execute('CREATE TABLE pending_test(value TEXT)'); directory.db.commit()
    directory.db.execute("INSERT INTO pending_test VALUES('uncommitted')")
    assert snapshot(directory, state) == legacy(directory, state)
    with sqlite3.connect(tmp_path / 'app.sqlite3') as other:
        assert other.execute('SELECT count(*) FROM pending_test').fetchone()[0] == 0
    def fail(*args):
        raise RuntimeError('injected projection failure')
    monkeypatch.setattr(DirectoryAttention, 'items', fail)
    with pytest.raises(RuntimeError, match='injected'):
        snapshot(directory, state)
    assert directory.db.in_transaction
    assert directory.db.execute('SELECT value FROM pending_test').fetchone()[0] == 'uncommitted'
    directory.db.rollback()
    assert directory.db.execute('SELECT count(*) FROM pending_test').fetchone()[0] == 0


def test_live_alias_mutation_and_rollback_never_reuse_old_notice_facts(tmp_path):
    from amplifier_web.directory_attention import snapshot
    state, directory = directory_fixture(tmp_path, 0)
    row = {'id': 'live', 'title': 'Live', 'status': 'idle', 'completion': {'id': 'one'}}
    state['sessions'] = [row]
    directory.sync([row], state); directory.db.commit()
    item = snapshot(directory, state)['items'][0]
    state['attentionRead'][item['id']] = item['fingerprint']
    assert snapshot(directory, state)['unread'] == 0
    row['completion']['id'] = 'two'
    assert snapshot(directory, state)['unread'] == 1
    assert snapshot(directory, state) == legacy(directory, state)
    state['sessions'] = []
    directory.sync([row], state)
    assert snapshot(directory, state)['unread'] == 1
    directory.db.rollback()
    assert snapshot(directory, state)['unread'] == 0
    assert not state['sessions']


def test_visible_receipts_do_not_decode_unrelated_approval_payloads(tmp_path, monkeypatch):
    from amplifier_web.directory_attention import receipts, DirectoryAttention
    from test_history_directory_exact_ids import native_fixture
    state, directory, _, project, _ = native_fixture(tmp_path, 1, 0)
    row = {'id': 'owner', 'nativeIdentity': 'saved-00000', 'nativeProject': project,
           'historyManaged': True, 'title': 'Superseded local title', 'status': 'idle',
           'completion': {'id': 'response'}, 'error': 'Failure', 'errorAt': 1,
           'approvals': [{'id': 'a', 'title': 'Allow', 'status': 'pending'}]}
    directory.sync([row], state); directory.db.commit()
    expected = [{key: item[key] for key in ('id', 'sessionId', 'fingerprint', 'read')}
                for item in legacy(directory, state)['items'] if not item['id'].startswith('approval:')]
    monkeypatch.setattr(DirectoryAttention, 'decode', lambda *args: pytest.fail('receipt-only read decoded a body'))
    assert receipts(directory, state, {'owner'}) == expected
    assert receipts(directory, state, set()) == []
