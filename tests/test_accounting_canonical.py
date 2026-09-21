"""Canonical display refreshes cannot erase or mint owned usage receipts."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.capacity import usage_snapshot, evaluate, admission
from amplifier_web.event_log_view import EventLogView, event_path
from amplifier_web.execution import ingest
from amplifier_web.history_revision import trim_execution
from amplifier_web.management import Management
from amplifier_web.service import AppService
from amplifier_web.session_projection import view_path


def receipt(root, identity, owner=None, **updates):
    return {'id': identity, 'sessionId': owner or root, 'rootSessionId': root,
            'kind': 'llm', 'phase': 'completed', 'revision': 2, 'producerId': 'producer',
            'budgetRevision': 1, 'admittedAt': 9, 'liveObservation': True,
            'provider': 'fixture', 'model': 'fixture-model', 'startedAt': 10, 'endedAt': 11.1,
            'usage': {'inputTokens': 8, 'outputTokens': 2, 'totalTokens': 10,
                      'cacheReadTokens': 3, 'cacheWriteTokens': 5, 'costUsd': .01,
                      'costType': 'reported'}, **updates}


def observed_tree(root):
    return {'nodes': [
        {'id': 'worker:child', 'kind': 'worker', 'sessionId': 'child',
         'rootSessionId': root, 'revision': 2, 'liveObservation': True, 'phase': 'completed'},
        receipt(root, 'root-call'), receipt(root, 'child-call', 'child'),
    ], 'turns': []}


def append(path, event, data, at):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        stream.write(json.dumps({'event': event, 'data': data, 'timestamp': at}) + '\n')


def populated_logs(session):
    root = session.get('runtimeSessionId') or session['id']
    root_path = event_path(session, root)
    append(root_path, 'tool:pre', {'session_id': root, 'tool_call_id': 'delegate', 'tool_name': 'delegate'}, 9)
    append(root_path, 'delegate:agent_spawned', {'session_id': root, 'sub_session_id': 'child', 'tool_call_id': 'delegate'}, 9.1)
    paths = []
    for owner, identity in ((root, 'root-call'), ('child', 'child-call')):
        path = event_path(session, owner)
        app_row = receipt(session['id'], identity, owner)
        append(path, 'provider:request', {**app_row, 'session_id': owner, 'phase': 'running', 'endedAt': None}, 10)
        append(path, 'llm:request', {'session_id': owner, 'model': 'fixture-model', 'provider': 'fixture',
               'raw': {'input': [{'role': 'user', 'content': 'PRIVATE REQUEST BODY'}]}}, 10.1)
        append(path, 'llm:response', {'session_id': owner, 'model': 'fixture-model', 'provider': 'fixture',
               'usage': {'input_tokens': 8, 'output_tokens': 2, 'cache_write_tokens': 5}}, 11)
        append(path, 'llm:response', {**app_row, 'session_id': owner}, 11.1)
        paths.append(path)
    return paths


def assert_usage(session, total=30):
    snapshot = usage_snapshot(session)
    assert snapshot['calls'] == 2
    assert snapshot['metrics']['grossTotalTokens']['value'] == total
    assert snapshot['metrics']['totalTokens']['value'] == total - 10
    assert {row['id'] for row in snapshot['receipts']} == {'root-call', 'child-call'}
    policy = {'enabled': True, 'maxTotalTokens': 25, 'unknownPolicy': 'pause'}
    assert evaluate(policy, snapshot)['allowed'] is False


async def test_populated_canonical_logs_restart_refresh_and_save_preserve_root_child_budget(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    await app.dispatch('session.create', {})
    sid = app._session()['id']
    paths = populated_logs(app._session())
    try:
        for row in observed_tree(sid)['nodes']:
            await app.on_runtime_event('execution.event', row)
        assert_usage(app._session(sid))
        # First reconcile while running, then restart with a populated log.
        await app.event_log_view.refresh(sid)
        assert_usage(app._session(sid))
        app._save()
        await app.close()
        # Closing flushes the original observer's queued diagnostic events.
        originals = {path: path.read_bytes() for path in paths}
        for _ in range(2):
            app = AppService(tmp_path / 'app', workspace=tmp_path)
            assert_usage(app._session(sid))
            await app.event_log_view.refresh(sid)
            current = app._session(sid)
            assert_usage(current)
            displayed = [row for row in current['execution']['nodes'] if row['kind'] == 'llm']
            assert len(displayed) == 2 and all(row.get('requestDetail') for row in displayed)
            assert all(row['producerId'] == 'producer' and row['revision'] == 2 for row in displayed)
            app._save()
            saved = json.loads(view_path(app.data_dir, current).read_text())
            serialized = json.dumps(saved['execution']['retiredUsageNodes'])
            assert len(saved['execution']['retiredUsageNodes']) == 3
            assert not saved['execution']['nodes']
            assert all(marker not in serialized for marker in ('PRIVATE REQUEST BODY', 'requestInfo', 'requestDetail', '_eventFields'))
            await app.close()
        assert all(path.read_bytes() == original for path, original in originals.items())
    finally:
        if not app.closed:
            await app.close()


def test_later_live_revision_on_canonical_hybrid_replaces_once_and_old_log_stays_stale(tmp_path):
    session = {'id': 'root', 'runtimeSessionId': 'native', 'workspace': str(tmp_path),
               'messages': [], 'workers': [], 'status': 'idle', 'execution': observed_tree('root')}
    populated_logs(session)
    view = EventLogView(None)
    session['execution'] = view.read(session)
    assert_usage(session)
    updated = receipt('root', 'root-call', revision=3,
                      usage={'inputTokens': 8, 'outputTokens': 7, 'totalTokens': 15, 'cacheWriteTokens': 5})
    ingest(session, updated)
    hybrid = next(row for row in session['execution']['nodes'] if row['id'] == 'root-call')
    assert hybrid['canonicalHistory'] and hybrid['liveObservation']
    for _ in range(3):
        session['execution'] = view.read(session)  # Canonical log still says revision 2 / total 10.
        assert_usage(session, 35)
        saved = next(row for row in session['execution']['retiredUsageNodes'] if row['id'] == 'root-call')
        assert saved['revision'] == 3 and 'costUsd' not in saved['usage']
        ingest(session, receipt('root', 'root-call'))  # Old reconnect observation.
        assert_usage(session, 35)
    # Display-only or other-producer mutations cannot replace a bound receipt.
    hybrid = next(row for row in session['execution']['nodes'] if row['id'] == 'root-call')
    hybrid.update(producerId='foreign-producer', revision=99, usage={'totalTokens': 9000})
    session['execution'] = view.read(session)
    assert_usage(session, 35)


def test_foreign_same_call_id_and_unbound_log_records_do_not_mint_budget_receipts(tmp_path):
    session = {'id': 'root', 'workspace': str(tmp_path), 'messages': [], 'workers': [],
               'status': 'idle', 'execution': observed_tree('root')}
    root_path, _ = populated_logs(session)
    foreign = receipt('root', 'root-call', 'foreign', revision=99, usage={'totalTokens': 9000})
    append(root_path, 'llm:response', {**foreign, 'session_id': 'foreign'}, 12)
    unbound = receipt('root', 'unbound-call', revision=99, usage={'totalTokens': 9000})
    append(root_path, 'llm:response', {**unbound, 'session_id': 'root'}, 13)
    session['execution'] = EventLogView(None).read(session)
    assert_usage(session)
    snapshot = usage_snapshot(session)
    assert snapshot['excludedUnboundCalls'] >= 2
    unbound_rows = [row for row in session['execution']['nodes']
                    if row.get('sessionId') == 'foreign' or row.get('_canonicalId') == 'unbound-call']
    assert len(unbound_rows) == 2 and all('rootSessionId' not in row for row in unbound_rows)
    assert {row['id'] for row in session['execution']['retiredUsageNodes']} == {'worker:child', 'root-call', 'child-call'}


def test_history_edit_retires_only_sanitized_accounting_in_memory(tmp_path):
    session = {'id': 'root', 'workspace': str(tmp_path), 'messages': [], 'workers': [],
               'status': 'idle', 'execution': observed_tree('root')}
    populated_logs(session)
    session['execution'] = EventLogView(None).read(session)
    for row in session['execution']['nodes']:
        row['turnId'] = 'removed'
    session['execution']['turns'] = [{'id': 'removed', 'anchorMessageId': 'old'}]
    trim_execution(session, [{'id': 'old', 'role': 'user'}], 'old')
    assert_usage(session)
    saved = json.dumps(session['execution']['retiredUsageNodes'])
    assert all(marker not in saved for marker in ('PRIVATE REQUEST BODY', 'requestInfo', 'requestDetail', '_eventFields'))


async def test_budget_control_demotes_catalog_history_before_bound_call_admission(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    await app.dispatch('session.create', {})
    session = app._session()
    sid = session['id']
    session.update(historyManaged=True, historyLoaded=False, nativeProject='fixture')
    loaded = []
    async def load(identity):
        loaded.append(identity)
        app._session(identity)['historyLoaded'] = True
    app.history.ensure_loaded = load
    async def start(snapshot, emit):
        assert snapshot['historyManaged'] is False and app._session(sid)['historyManaged'] is False
        row = receipt(sid, 'admitted', phase='running', endedAt=None, usage={})
        assert (await admission(app, sid, {'call': row}))['allowed']
        await emit('execution.event', {**row, 'phase': 'completed', 'endedAt': 12,
                                     'usage': {'inputTokens': 8, 'outputTokens': 2, 'totalTokens': 10}})
    app.runtime = SimpleNamespace(start=start, control=AsyncMock(return_value={'budget': {'revision': 1}}), close=AsyncMock())
    app.management = Management(app)
    try:
        await app.dispatch('capacity.set', {'sessionId': sid, 'expectedRevision': 0, 'maxTotalTokens': 25})
        assert loaded and session['historyManaged'] is False
        app._save()
        saved = json.loads(view_path(app.data_dir, session).read_text())
        assert usage_snapshot(saved)['calls'] == 1
        assert usage_snapshot(saved)['metrics']['totalTokens']['value'] == 10
    finally:
        await app.close()
