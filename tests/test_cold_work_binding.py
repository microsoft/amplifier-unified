"""Cold binding uses real observer/diagnostic receipts, never receipt-shaped trust.

Canonical tool bodies below are synthetic disk-contract data. Ownership records
are emitted by ExecutionEvents -> Diagnostics -> its actual append/index path.
The separate native-producer baseline covers actual kernel/capture production.
"""
from types import SimpleNamespace
from contextlib import asynccontextmanager
from contextvars import ContextVar
import asyncio
import copy
import json
import sys
import time

import pytest

from amplifier_web.diagnostics import Diagnostics
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web import event_window
from amplifier_web.session_files import append_event
from amplifier_web.work_windows import WorkWindows
from amplifier_web.event_window_worker import WindowModel


@pytest.fixture
async def source(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.delenv('AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH', raising=False)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    session = {'id': 'root', 'workspace': str(workspace), 'messages': [],
               'execution': {'turns': [], 'nodes': []}, 'status': 'idle'}
    service = SimpleNamespace(data_dir=tmp_path / 'app', default_workspace=str(workspace),
        state={'sessions': [session]}, closed=False, instance_id='fixture-host',
        projections=SimpleNamespace(detail_bodies={}), event_log_view=None)
    service._session = lambda identity, hydrate=False: session if identity == 'root' else None
    diagnostics = Diagnostics(service)
    emitted = []
    observer = ExecutionEvents('root', lambda envelope: emitted.append(envelope['event']))
    calls = []

    def turn(identity):
        observer.lifecycle({'type': 'input.delivered', 'input_id': identity})
        at = time.time() - .02
        session['messages'].append({'id': identity + '-user', 'role': 'user',
            'inputId': identity, 'createdAt': at, 'timestampKnown': True, 'text': 'Question'})
        session['execution']['turns'].append({'id': identity, 'phase': 'completed',
            'anchorMessageId': identity + '-user', 'startedAt': at, 'endedAt': at + 10})

    def call(identity, turn_id='turn', *, body_metadata=None):
        if not any(t['id'] == turn_id for t in session['execution']['turns']):
            turn(turn_id)
        interim = {'id': identity + '-interim-' + str(len(calls)), 'role': 'assistant',
            'inputId': turn_id, 'createdAt': time.time(), 'timestampKnown': True,
            'text': 'Progress', 'response': {'status': 'completed', 'inputIds': [turn_id],
                                          'phase': 'interim'}}
        session['messages'].append(interim)
        body = {'text': 'INPUT:漢字é𝄞' * 1800 + ':END'}
        result = {'success': True, 'output': 'OUTPUT:漢字é𝄞' * 1800 + ':END'}
        refs = []
        for event, fields in [('tool:pre', {'tool_input': body}), ('tool:post', {'result': result})]:
            data = {'session_id': 'root', 'tool_call_id': identity, 'tool_name': 'read_file',
                    'timestamp': time.time(), **fields, **(body_metadata or {})}
            refs.append(append_event(workspace, 'root', event, data))
            observer.hook('root', event, data)
            row = emitted.pop()
            assert not {'input', 'output', '_eventFields'}.intersection(row)
            diagnostics.runtime_event('execution.event', row, session)
        calls.append((identity, interim['id'], body, result, refs))
        return calls[-1]

    async def page(**kwargs):
        await diagnostics.flush()
        return event_window.read_window(session, window_key='test-window', cursor_key=b'k' * 32,
                                        cache_home=service.data_dir, **kwargs)

    yield SimpleNamespace(session=session, service=service, diagnostics=diagnostics,
                          observer=observer, emitted=emitted, call=call, turn=turn,
                          page=page, calls=calls, workspace=workspace)
    await diagnostics.close()


@pytest.mark.asyncio
async def test_real_emitter_index_attests_complete_root_tool_pairs(source):
    call, anchor, body, result, _ = source.call('one')
    page = await source.page()
    assert page['bindingCoverage'] == {'status': 'complete-root', 'bound': 1, 'unassociated': 0}
    bound = page['_boundWork'][0]
    assert bound['turnId'] == 'turn' and bound['toolCallId'] == call
    assert bound['rootSessionId'] == bound['sessionId'] == 'root'
    assert all('rootSessionId' not in row for row in page['items'])
    assert bound['bindingProvenance'] == 'host-diagnostics-receipt'
    assert bound['sourceOccurrenceIds'] and bound['id'] in bound['sourceOccurrenceIds']
    assert len(page['items']) == 4  # Original pre/post and host receipt records retained.
    for field, expected in [('input', body), ('output', result)]:
        chunks, offset = [], 0
        while True:
            chunk = event_window.read_field(source.session, bound['_eventFields'][field], offset=offset, limit=4096)
            assert len(chunk['text']) <= 4096
            chunks.append(chunk['text'])
            offset = chunk['nextOffset']
            if offset is None:
                break
        assert json.loads(''.join(chunks)) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['missing-index', 'copied-receipt', 'wrong-host', 'suppressed',
                                  'disabled', 'reused-id', 'cross-session', 'partial', 'invalid',
                                  'conflicting-alias', 'wrong-index-workspace', 'duplicate-body',
                                  'missing-receipt-hash'])
async def test_missing_provenance_or_incomplete_collision_scope_never_binds(source, kind, tmp_path):
    if kind == 'suppressed':
        source.session['runtimeReport'] = {'contextIntelligence': {'enabled': False}}
    if kind == 'disabled':
        source.diagnostics.config['enabled'] = False
    source.call('one')
    if kind == 'reused-id':
        source.turn('other')
        source.call('one', 'other')  # Actual observer reuses old call state; must NOT pass.
    await source.diagnostics.flush()
    path, _, _ = event_window._source(source.session)
    if kind == 'missing-index':
        with source.diagnostics._db() as db:
            db.execute('DELETE FROM records')
    elif kind == 'wrong-index-workspace':
        with source.diagnostics._db() as db:
            db.execute("UPDATE records SET workspace='/different-owner'")
    elif kind == 'missing-receipt-hash':
        with source.diagnostics._db() as db:
            for row in db.execute('SELECT id,data FROM records').fetchall():
                ref = json.loads(row['data'])
                ref.pop('sha256', None)
                db.execute('UPDATE records SET data=? WHERE id=?', (json.dumps(ref), row['id']))
    elif kind == 'duplicate-body':
        append_event(source.workspace, 'root', 'tool:post', {'timestamp': time.time(),
            'session_id': 'root', 'tool_call_id': 'one', 'result': 'duplicate'})
    elif kind == 'copied-receipt':
        # Same authentic bytes at a different occurrence are not another receipt.
        raw = path.read_bytes()
        path.write_bytes(raw + raw.splitlines(keepends=True)[-1])
    elif kind == 'cross-session':
        append_event(source.workspace, 'root', 'tool:post', {'timestamp': time.time(),
            'session_id': 'other', 'tool_call_id': 'one', 'result': 'other'})
    elif kind == 'conflicting-alias':
        append_event(source.workspace, 'root', 'tool:post', {'timestamp': time.time(),
            'session_id': 'root', 'tool_call_id': 'other', 'call_id': 'one', 'result': 'other'})
    elif kind == 'partial':
        with path.open('ab') as f:
            f.write(b'{')
    elif kind == 'invalid':
        with path.open('ab') as f:
            f.write(b'[]\n')
    home = tmp_path / 'unrelated-app' if kind == 'wrong-host' else source.service.data_dir
    page = event_window.read_window(source.session, window_key='test', cursor_key=b'k' * 32,
                                    cache_home=home)
    assert not page['_boundWork']
    assert len(page['items']) >= 2


@pytest.mark.asyncio
async def test_latest_or_older_single_page_cannot_claim_whole_source_scope(source):
    source.call('one')
    await source.diagnostics.flush()
    latest = event_window.read_window(source.session, window_key='test', cursor_key=b'k' * 32,
                                      cache_home=source.service.data_dir, limit=2)
    older = event_window.read_window(source.session, window_key='test', cursor_key=b'k' * 32,
        cache_home=source.service.data_dir, limit=2, before=latest['before'])
    assert older['coverage']['rootExhausted']
    assert not latest['_boundWork'] and not older['_boundWork']
    assert latest['bindingCoverage']['status'] == older['bindingCoverage']['status'] == 'partial-source'


@pytest.mark.asyncio
async def test_tool_supplied_ownership_shape_cannot_mint_host_binding(source):
    source.call('one')
    await source.diagnostics.flush()
    path, _, _ = event_window._source(source.session)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        if row['data'].get('app') == 'amplifier-unified':
            row['data']['turnId'] = 'forged'
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    assert not (await source.page())['_boundWork']


@pytest.mark.asyncio
async def test_real_model_lifecycle_receipts_bind_without_inventing_payloads(source):
    source.turn('turn')
    for event, fields in [('llm:request', {'provider': 'fixture', 'model': 'local'}),
                          ('llm:response', {'provider': 'fixture', 'model': 'local',
                                            'usage': {'input_tokens': 1, 'output_tokens': 1}})]:
        source.observer.hook('root', event, {'session_id': 'root', **fields})
        source.diagnostics.runtime_event('execution.event', source.emitted.pop(), source.session)
    page = await source.page()
    assert len(page['_boundWork']) == 1
    model = page['_boundWork'][0]
    assert model['kind'] == 'llm' and model['turnId'] == 'turn'
    assert model['rootSessionId'] == model['sessionId'] == 'root'
    assert all('rootSessionId' not in row for row in page['items'])
    assert model['provider'] == 'fixture' and model['model'] == 'local'
    assert model['phase'] == 'completed' and model['bindingProvenance'] == 'host-diagnostics-receipt'
    assert not model['_eventFields']  # Lifecycle evidence cannot manufacture raw provider bodies.


@pytest.mark.asyncio
async def test_page_groups_route_original_fields_and_keep_final_outside(source):
    source.call('one')
    source.call('two')
    final = {'id': 'final', 'role': 'assistant', 'inputId': 'turn', 'createdAt': time.time() + .01,
             'timestampKnown': True, 'text': 'Final', 'response': {'status': 'completed', 'phase': 'final'}}
    source.session['messages'].append(final)
    await source.diagnostics.flush()
    original = copy.deepcopy(source.session)
    owner = WorkWindows(source.service)
    class Direct:
        model = WindowModel(source.service.data_dir)
        async def project(self, inputs, *, key):
            return self.model.project(inputs, check_budget=lambda: None)
        async def close(self):
            pass
    owner.projector = Direct()
    try:
        page = await owner.read('root')
        groups = [g for g in page['items'] if g.get('bindingProvenance')]
        assert [g['anchorMessageId'] for g in groups] == [c[1] for c in source.calls]
        assert all(g['turnId'] == 'turn' and g['detailGroup'] == g['id'] for g in groups)
        assert all(g['anchorMessageId'] != final['id'] for g in groups)
        for group, call in zip(groups, source.calls):
            node = owner.nodes('root', group['id'])['items'][0]
            assert node['toolCallId'] == call[0] and node['turnId'] == 'turn'
            assert node['rootSessionId'] == node['sessionId'] == 'root'
            assert node['inputDetail']['id'] == node['id']
            reply = await owner.read_text('root', node['inputDetail'])
            assert reply['value']
            sync = owner.nodes('root', group['id'], known={node['id']: node['detailVersion']})
            assert sync['items'] == [] and sync['order'] == [node['id']]
        assert source.session == original
    finally:
        await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['append-conflict', 'invalidate', 'invalidate-quiet-refresh'])
async def test_bound_field_revalidates_source_and_epoch_after_await(source, change):
    source.call('one')
    await source.diagnostics.flush()
    owner = WorkWindows(source.service)
    entered, release = asyncio.Event(), asyncio.Event()
    class Direct:
        model = WindowModel(source.service.data_dir)
        async def project(self, inputs, *, key):
            result = self.model.project(inputs, check_budget=lambda: None)
            if inputs['operation'] == 'field':
                entered.set()
                await release.wait()
            return result
        async def close(self):
            pass
    owner.projector = Direct()
    read = barrier = None
    try:
        page = await owner.read('root')
        group = next(g for g in page['items'] if g.get('bindingProvenance'))
        ref = owner.nodes('root', group['id'])['items'][0]['inputDetail']
        read = asyncio.create_task(owner.read_text('root', ref))
        barrier = asyncio.create_task(entered.wait())
        done, _ = await asyncio.wait(
            {read, barrier}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        if read in done:
            # Preserve an early failure instead of hiding it behind a timeout
            # or accepting it as the expected post-invalidation rejection.
            await read
            pytest.fail('Field read completed before the release barrier')
        assert barrier in done, 'Field read did not reach the barrier within 5 seconds'
        if change == 'append-conflict':
            append_event(source.workspace, 'root', 'tool:post', {'timestamp': time.time(),
                'session_id': 'root', 'tool_call_id': 'one', 'result': 'conflict'})
        else:
            owner.invalidate({'root'})
            if change == 'invalidate-quiet-refresh':
                await owner.read('root', _initial=True)
        release.set()
        with pytest.raises(ValueError, match='expired'):
            await asyncio.wait_for(read, timeout=5)
    finally:
        release.set()
        tasks = [task for task in (read, barrier) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await owner.close()


@pytest.mark.asyncio
async def test_same_capture_reconsiders_binding_after_existing_index_commit(source):
    source.call('one')
    await source.diagnostics.flush()
    with source.diagnostics._db() as db:
        records = list(db.execute('SELECT * FROM records'))
        db.execute('DELETE FROM records')
    owner = WorkWindows(source.service)
    class Direct:
        model = WindowModel(source.service.data_dir)
        async def project(self, inputs, *, key):
            return self.model.project(inputs, check_budget=lambda: None)
        async def close(self):
            pass
    owner.projector = Direct()
    try:
        first = await owner.read('root', _initial=True)
        assert not any(g.get('bindingProvenance') for g in first['items'])
        # Keep the WAL owner open across the second read: the dependency must
        # notice the WAL commit without relying on last-close checkpointing.
        import sqlite3
        db = sqlite3.connect(source.diagnostics.path)
        try:
            db.execute('PRAGMA wal_autocheckpoint=0')
            db.executemany('INSERT INTO records VALUES(?,?,?,?,?,?,?,?)', [tuple(r) for r in records])
            db.commit()
            second = await owner.read('root', _initial=True)
            assert len([g for g in second['items'] if g.get('bindingProvenance')]) == 1
        finally:
            db.close()
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_unavailable_index_revision_never_attests_existing_rows(source, monkeypatch):
    source.call('one')
    await source.diagnostics.flush()
    monkeypatch.setattr(event_window, 'binding_revision', lambda home: None)
    assert not (await source.page())['_boundWork']


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['initialization', 'authority-query', 'teardown'])
async def test_receipt_snapshot_observes_writer_without_relabelling_old_rows(source, monkeypatch, boundary):
    source.call('one')
    await source.diagnostics.flush()
    connect = event_window.sqlite3.connect
    committed, closed = [], []

    def commit():
        # Delete real indexed ownership records without changing canonical bytes.
        with source.diagnostics._db() as writer:
            writer.execute('DELETE FROM records')
        committed.append(boundary)

    class ObservedConnection:
        def __init__(self, db):
            self.db = db
        def __getattr__(self, name):
            return getattr(self.db, name)
        def execute(self, sql, *args):
            result = self.db.execute(sql, *args)
            if boundary == 'authority-query' and sql.startswith('SELECT event,stream') and not committed:
                commit()
            return result
        def rollback(self):
            self.db.rollback()
            if boundary == 'initialization' and not committed:
                commit()
        def close(self):
            self.db.close()
            closed.append(True)
            if boundary == 'teardown' and not committed:
                commit()

    def observed_connect(database, *args, **kwargs):
        db = connect(database, *args, **kwargs)
        return ObservedConnection(db) if str(database).endswith('?mode=ro') else db

    monkeypatch.setattr(event_window.sqlite3, 'connect', observed_connect)
    if boundary == 'initialization':
        # Commit before the fresh authority transaction: it must see the miss,
        # not reuse an earlier record under a newer physical revision.
        page = event_window.read_window(source.session, window_key='writer-boundary',
            cursor_key=b'k' * 32, cache_home=source.service.data_dir)
        assert page['_boundWork'] == []
        assert page['_bindingRevision'] == event_window.binding_revision(source.service.data_dir)
    else:
        with pytest.raises(event_window.StaleWindow, match='binding index changed'):
            event_window.read_window(source.session, window_key='writer-boundary',
                cursor_key=b'k' * 32, cache_home=source.service.data_dir)
    assert committed == [boundary] and closed == [True]


@pytest.mark.asyncio
async def test_partial_raw_page_never_consults_or_depends_on_ownership_index(source, monkeypatch):
    source.call('one')
    source.call('two')
    await source.diagnostics.flush()
    def forbidden(*args, **kwargs):
        raise AssertionError('Partial raw observation must not consult ownership evidence')
    monkeypatch.setattr(event_window, '_host_receipts', forbidden)
    monkeypatch.setattr(event_window, 'binding_revision', forbidden)
    page = event_window.read_window(source.session, window_key='partial-raw',
        cursor_key=b'k' * 32, limit=2, cache_home=source.service.data_dir)
    assert page['bindingCoverage']['status'] == 'partial-source'
    assert page['_boundWork'] == [] and '_bindingRevision' not in page
    assert len(page['items']) == 2 and page['before']
    with source.diagnostics._db() as writer:
        writer.execute('DELETE FROM records')
    # The raw source has not changed and never relied on that index.
    owner = SimpleNamespace(service=source.service)
    assert WorkWindows._binding_current(owner, page)
    assert all(row['association'] == 'unverified' for row in page['items'])


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['source', 'epoch'])
async def test_raw_evidence_keeps_source_and_after_await_fence(source, change):
    source.call('one')
    await source.diagnostics.flush()
    owner = WorkWindows(source.service)
    entered, release = asyncio.Event(), asyncio.Event()
    class Direct:
        model = WindowModel(source.service.data_dir)
        async def project(self, inputs, *, key):
            result = self.model.project(inputs, check_budget=lambda: None)
            if inputs['operation'] == 'field':
                entered.set()
                await release.wait()
            return result
        async def close(self):
            pass
    owner.projector = Direct()
    read = barrier = None
    try:
        page = await owner.read('root')
        evidence = page['evidence']
        raw = owner.observations('root', evidence['group'], evidence['revision'])
        ref = next(row['inputDetail'] for row in raw['items'] if 'inputDetail' in row)
        read = asyncio.create_task(owner.read_text('root', ref))
        barrier = asyncio.create_task(entered.wait())
        done, _ = await asyncio.wait({read, barrier}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        if read in done:
            await read
            pytest.fail('Raw field completed before its barrier')
        assert barrier in done
        if change == 'source':
            append_event(source.workspace, 'root', 'tool:post', {'timestamp': time.time(),
                'session_id': 'root', 'result': 'later'})
            with pytest.raises(ValueError, match='source changed'):
                owner.observations('root', evidence['group'], evidence['revision'])
        else:
            owner.invalidate({'root'})
        release.set()
        with pytest.raises(ValueError, match='expired'):
            await asyncio.wait_for(read, timeout=5)
    finally:
        release.set()
        tasks = [task for task in (read, barrier) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await owner.close()


@pytest.mark.asyncio
async def test_real_persisted_projection_and_new_window_owner_preserve_proven_groups(source):
    from amplifier_web.session_projection import stored_execution
    source.call('one')
    await source.diagnostics.flush()
    # Serialize through the real projection, not carry a private accepted page.
    source.session['execution'] = json.loads(json.dumps(stored_execution(source.session['execution'])))
    assert not source.session['execution']['nodes']
    async def read_once():
        owner = WorkWindows(source.service)
        class Direct:
            model = WindowModel(source.service.data_dir)
            async def project(self, inputs, *, key):
                return self.model.project(inputs, check_budget=lambda: None)
            async def close(self):
                pass
        owner.projector = Direct()
        try:
            page = await owner.read('root')
            group = next(g for g in page['items'] if g.get('bindingProvenance'))
            return group['turnId'], group['anchorMessageId'], owner.nodes('root', group['id'])['items'][0]['id']
        finally:
            await owner.close()
    assert await read_once() == await read_once()


@pytest.mark.asyncio
async def test_registered_web_routes_bound_group_nodes_sync_and_full_field(source, authenticated_client, monkeypatch):
    async with _ready_cold_web(source, authenticated_client, monkeypatch) as (service, client):
        trace = _ColdReadTrace(service.event_log_view.windows, 'root')
        with monkeypatch.context() as observation:
            trace.install(observation)
            try:
                response = await client.get('/api/conversation/detail', params={'sessionId': 'root', 'part': 'groups'})
            finally:
                trace.emit()
        assert response.status == 200, {'body': await response.text(), 'fence': trace.evidence}
        await _assert_cold_web_details(client, await response.json(), source)


class _InitialCatalogCompletion:
    """Observe the first scheduled refresh; never start another refresh."""
    def __init__(self, refresh, home=None):
        self.refresh = refresh
        self.home = home
        self.owner = None
        self.entry = self.completion = None
        self.finished = asyncio.get_running_loop().create_future()

    @staticmethod
    def _snapshot(owner):
        service = owner.service
        history = service._state.get('sharedHistory', {})
        phase = (history.get('reconciliation') or {}).get('phase')
        return {'loading': history.get('loading'), 'hasError': bool(history.get('error')),
                'hasIssues': bool(history.get('issues') or history.get('issueCount')),
                'phase': phase if phase in {'ready', 'complete', 'reconciling', 'recovering'} else 'other',
                'closed': service.closed,
                'rootCount': sum(row.get('id') == 'root' for row in service._state.get('sessions', []))}

    async def observe(self, owner, *args, **kwargs):
        first = self.owner is None and (self.home is None or owner.service.data_dir == self.home)
        if first:
            self.owner = owner
            try:
                self.entry = self._snapshot(owner)
            except Exception:
                self.entry = {'observationError': True}
        failure = None
        try:
            return await self.refresh(owner, *args, **kwargs)
        except BaseException as error:
            failure = error
            raise
        finally:
            if first:
                try:
                    self.completion = self._snapshot(owner)
                except Exception:
                    self.completion = {'observationError': True}
                self.finished.set_result(failure)

    async def wait(self, owner, timeout=10):
        failure = await asyncio.wait_for(asyncio.shield(self.finished), timeout)
        if failure is not None:
            raise failure
        assert self.owner is owner, 'The observed refresh belongs to another history owner'
        assert not self.entry.get('observationError') and not self.completion.get('observationError')
        assert self.entry['rootCount'] == self.completion['rootCount'] == 1
        assert self.completion['loading'] is False and not self.completion['hasError'], self.completion
        assert not self.completion['closed'] and not self.completion['hasIssues'], self.completion
        assert not owner.service.closed and self._snapshot(owner)['rootCount'] == 1


@asynccontextmanager
async def _ready_cold_web(source, authenticated_client, monkeypatch):
    from amplifier_web.server import create_app
    from amplifier_web.session_projection import stored_execution
    from amplifier_web.automatic_history import AutomaticHistory
    source.call('one')
    await source.diagnostics.flush()
    class Runtime:
        async def start(self, *args, **kwargs):
            raise AssertionError('A history read must not start a runtime')
        async def close(self):
            pass
    completion = _InitialCatalogCompletion(AutomaticHistory.refresh, home=source.service.data_dir)
    with monkeypatch.context() as observation:
        async def refresh(owner, *args, **kwargs):
            return await completion.observe(owner, *args, **kwargs)
        observation.setattr(AutomaticHistory, 'refresh', refresh)
        app = await create_app(source.service.data_dir, workspace=source.workspace,
            runtime=Runtime(), voice=False, background_updates=False, preload_providers=False)
        service, client, stream = app['service'], None, None
        try:
            row = copy.deepcopy(source.session)
            row['execution'] = stored_execution(row['execution'])
            service.state['sessions'].append(row)
            service.clients.attach('cold-web', kind='web')
            with service.clients.bind('cold-web'):
                await service.dispatch('session.select', {'id': row['id']})
            client = await authenticated_client(app)
            client.session.headers['X-Amplifier-Client'] = 'cold-web'
            stream = await client.ws_connect('/api/events/ws?transport=delta-v1')
            initial = await stream.receive_json(timeout=5)
            assert initial['event'] == 'state'
            # create_app starts this refresh. Await its actual return, not the
            # perpetual history loop or a repeated test-initiated refresh.
            await completion.wait(service.history)
            assert not service.closed
            print('COLD-INITIAL-REFRESH-COMPLETED', completion.completion)
            yield service, client
        finally:
            try:
                if stream is not None:
                    await stream.close()
            finally:
                if client is not None:
                    await client.close()
                else:
                    await service.close()


async def _assert_cold_web_details(client, page, source):
    group = next(g for g in page['items'] if g.get('bindingProvenance'))
    assert group['turnId'] == 'turn' and group['anchorMessageId'] == source.calls[0][1]
    response = await client.get('/api/conversation/detail', params={
        'sessionId': 'root', 'part': 'nodes', 'group': group['id'], 'revision': group['detailRevision']})
    assert response.status == 200, await response.text()
    nodes = (await response.json())['items']
    assert len(nodes) == 1
    node = nodes[0]
    assert node['rootSessionId'] == node['sessionId'] == 'root'
    assert node['turnId'] == 'turn' and node['toolCallId'] == source.calls[0][0]
    reference = node['outputDetail']
    chunks, offset = [], 0
    for _ in range(64):
        response = await client.get('/api/conversation/detail', params={**reference, 'offset': offset, 'limit': 4096})
        assert response.status == 200, await response.text()
        field = await response.json()
        assert len(field['value']) <= 4096
        chunks.append(field['value'])
        next_offset = field['nextOffset']
        if next_offset is None:
            break
        assert next_offset > offset
        offset = next_offset
    else:
        pytest.fail('Full output did not terminate within64 bounded field reads')
    assert json.loads(''.join(chunks)) == source.calls[0][3]
    response = await client.post('/api/conversation/work-sync', json={
        'sessionId': 'root', 'group': group['id'], 'known': {node['id']: node['detailVersion']}})
    assert response.status == 200, await response.text()
    assert (await response.json())['order'] == [node['id']]
    return group, node


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['root-publication', 'source-and-root-publication'])
async def test_obsolete_cold_http_refuses_then_one_current_request_reads_full_work(
        source, authenticated_client, monkeypatch, change):
    async with _ready_cold_web(source, authenticated_client, monkeypatch) as (service, client):
        windows = service.event_log_view.windows
        original_read, worker = windows.read, windows._worker()
        original_project = worker.project
        original_check = windows._check_current
        foreground = ContextVar('held-cold-http', default=False)
        entered, release, read_settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        evidence = {}
        async def observed_read(*args, **kwargs):
            is_foreground = not kwargs.get('_initial', False)
            token = foreground.set(is_foreground)
            if is_foreground:
                evidence['readStarted'] = True
            try:
                return await original_read(*args, **kwargs)
            finally:
                if is_foreground:
                    read_settled.set()
                foreground.reset(token)
        async def held_project(inputs, *, key):
            result = await original_project(inputs, key=key)
            if foreground.get() and inputs.get('operation') == 'window':
                assert result['status'] == 'complete', result.get('reason')
                evidence.update(epoch=windows.epochs.get('root'),
                                revision=result['page']['generation'])
                entered.set()
                await asyncio.wait_for(release.wait(), 5)
            return result
        def checked(identity, owner, source_identity, fence):
            if foreground.get() and identity == 'root':
                evidence.update(readFence=fence, checkedEpoch=windows.epochs.get('root'))
            return original_check(identity, owner, source_identity, fence)
        pending = barrier = None
        with monkeypatch.context() as observation:
            observation.setattr(windows, 'read', observed_read)
            observation.setattr(worker, 'project', held_project)
            observation.setattr(windows, '_check_current', checked)
            try:
                pending = asyncio.create_task(client.get('/api/conversation/detail',
                    params={'sessionId': 'root', 'part': 'groups'}))
                barrier = asyncio.create_task(entered.wait())
                done, _ = await asyncio.wait({pending, barrier}, timeout=5,
                                            return_when=asyncio.FIRST_COMPLETED)
                if pending in done:
                    early = await pending  # Preserve actual transport errors.
                    pytest.fail(f'Read finished before controlled publication: {early.status} {await early.text()}')
                assert barrier in done and entered.is_set(), 'Physical read never reached the publication barrier'
                row = service._session('root', hydrate=False)
                turn = next(t for t in row['execution']['turns'] if t['id'] == 'turn')
                previous_end = turn['endedAt']
                turn['endedAt'] = previous_end + 1  # A real relevant owner fact.
                path, _, _ = event_window._source(row)
                source_before = path.stat().st_size
                if change == 'source-and-root-publication':
                    append_event(source.workspace, 'root', 'prompt:submit', {
                        'session_id': 'root', 'timestamp': time.time(), 'input_id': 'later-input'})
                    assert path.stat().st_size > source_before
                # Synchronous return means this actual save/publication settled.
                service._publish_changes(sessions={'root'}, detail_only=True)
                assert turn['endedAt'] == previous_end + 1
                assert windows.epochs['root'] > evidence['epoch']
                published_epoch = windows.epochs['root']
                release.set()
                refused = await asyncio.wait_for(asyncio.shield(pending), 5)
                assert refused.status == 400, await refused.text()
                assert await refused.json() == {
                    'error': 'Invalid request: The requested conversation changed. Open it again.',
                    'accepted': False}
                assert evidence['readFence'] == evidence['epoch'], evidence
                assert evidence['checkedEpoch'] == published_epoch, evidence
                assert windows.epochs['root'] >= published_epoch
            finally:
                release.set()
                for task in (pending, barrier):
                    if task is not None and not task.done():
                        task.cancel()
                await asyncio.gather(*(t for t in (pending, barrier) if t is not None),
                                     return_exceptions=True)
                if evidence.get('readStarted'):
                    await asyncio.wait_for(read_settled.wait(), 5)
        # Exactly one new request after the completed publication. Never reuse
        # old group/detail receipts or retry an arbitrary refused response.
        response = await client.get('/api/conversation/detail', params={'sessionId': 'root', 'part': 'groups'})
        assert response.status == 200, await response.text()
        current = await response.json()
        assert windows.epochs['root'] >= published_epoch
        assert windows.status['root']['current'] is True
        group, node = await _assert_cold_web_details(client, current, source)
        assert group['turnId'] == node['turnId'] == 'turn'
        assert group['anchorMessageId'] == source.calls[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['returned', 'raised', 'reported-error', 'loading', 'timeout',
                                    'cancelled', 'wrong-owner', 'later-success', 'pending'])
async def test_catalog_completion_gate_requires_actual_success_without_extra_refresh(outcome):
    state = {'sharedHistory': {'loading': outcome == 'loading',
                               'error': 'synthetic failure' if outcome in {'reported-error', 'later-success'} else None},
             'sessions': [{'id': 'root'}]}
    owner = SimpleNamespace(service=SimpleNamespace(_state=state, closed=False))
    calls, expected = [], object()
    entered, release = asyncio.Event(), asyncio.Event()
    failure = asyncio.CancelledError() if outcome == 'cancelled' else ValueError('synthetic original')
    async def refresh(actual, *, force=False):
        calls.append((actual, force))
        if outcome == 'pending':
            entered.set()
            await release.wait()
        if outcome in {'raised', 'cancelled'}:
            raise failure
        return expected
    completion = _InitialCatalogCompletion(refresh)
    if outcome == 'pending':
        task = asyncio.create_task(completion.observe(owner, force=False))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert not completion.finished.done()
            release.set()
            assert await asyncio.wait_for(asyncio.shield(task), 1) is expected
            await completion.wait(owner)
            assert calls == [(owner, False)]
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return
    if outcome == 'timeout':
        with pytest.raises(TimeoutError):
            await completion.wait(owner, timeout=.01)
        assert not calls and not completion.finished.done()
        completion.finished.cancel()
        return
    if outcome in {'raised', 'cancelled'}:
        with pytest.raises(type(failure)) as original:
            await completion.observe(owner, force=False)
        assert original.value is failure
        with pytest.raises(type(failure)) as observed:
            await completion.wait(owner)
        assert observed.value is failure
    else:
        assert await completion.observe(owner, force=False) is expected
        if outcome == 'later-success':
            state['sharedHistory']['error'] = None
            assert await completion.observe(owner, force=False) is expected
            assert completion.completion['hasError'] is True
            with pytest.raises(AssertionError):
                await completion.wait(owner)
        elif outcome == 'wrong-owner':
            with pytest.raises(AssertionError, match='another history owner'):
                await completion.wait(SimpleNamespace(service=owner.service))
        elif outcome in {'reported-error', 'loading'}:
            with pytest.raises(AssertionError):
                await completion.wait(owner)
        else:
            await completion.wait(owner)
    assert calls == [(owner, False)] * (2 if outcome == 'later-success' else 1)


class _ColdReadTrace:
    """Test-only first-read facts; never retries, changes fences or reads bodies."""
    def __init__(self, windows, identity):
        self.windows, self.identity = windows, identity
        self.evidence = {'records': [], 'dropped': 0, 'observationFailures': 0}
        self._publication = None
        self._publication_sequence = 0
        self._full_reason = 'not-full'

    def _append(self, record):
        records = self.evidence['records']
        if len(records) == 24:
            records.pop(0)
            self.evidence['dropped'] += 1
        records.append(record)

    def _record(self, factory):
        try:
            self._append(factory())
        except Exception:
            # Observation failure cannot replace the original result/exception.
            self.evidence['observationFailures'] += 1

    def emit(self, write=print):
        try:
            write('COLD-FIRST-READ-FENCE', json.dumps(self.evidence, sort_keys=True))
        except Exception:
            self.evidence['observationFailures'] += 1

    def _facts(self, owner, source, fence, outcome):
        from amplifier_web.work_windows import IDENTITY_FIELDS
        windows, identity = self.windows, self.identity
        service = windows.service
        resident = service._state.get('sessions', [])
        rows = [row for row in resident[:8] if dict.get(row, 'id') == identity]
        changed = [key for key in IDENTITY_FIELDS
                   if (key in owner) != (key in source) or dict.get(owner, key) != source.get(key)]
        clients = service.clients.records
        return {'stage': 'check-current', 'outcome': outcome,
                'expectedEpoch': fence, 'actualEpoch': windows.epochs.get(identity),
                'windowClosed': windows.closed, 'serviceClosed': service.closed,
                'residentLookupComplete': len(resident) <= 8,
                'matchingResidentCount': len(rows),
                'originalOwnerResident': any(row is owner for row in rows),
                'sourceChangedFields': changed,
                'stateRevision': service._state.get('revision'),
                'selectedWebConnected': any(
                    clients.get(client, {}).get('kind') == 'web'
                    and clients.get(client, {}).get('selectedSessionId') == identity
                    for client in service.queue_clients.values())}

    def install(self, patch):
        service = self.windows.service
        full = getattr(service, '_publish_full', None)
        if callable(full):
            def full_observed(*args, **kwargs):
                previous = self._full_reason
                self._full_reason = 'unknown'
                try:
                    self._full_reason = self._reason(kwargs.get('reason'))
                except Exception:
                    self.evidence['observationFailures'] += 1
                try:
                    return full(*args, **kwargs)
                finally:
                    self._full_reason = previous
            patch.setattr(service, '_publish_full', full_observed)
        publish = getattr(service, '_publish', None)
        if callable(publish):
            def publish_observed(*args, **kwargs):
                previous = self._publication
                self._publication_sequence += 1
                self._publication = {'publication': self._publication_sequence,
                                     'publisher': 'unknown', 'fullReason': self._full_reason}
                try:
                    self._publication['publisher'] = self._publisher()
                except Exception:
                    self.evidence['observationFailures'] += 1
                try:
                    return publish(*args, **kwargs)
                finally:
                    self._publication = previous
            patch.setattr(service, '_publish', publish_observed)
        original = self.windows._check_current
        def checked(identity, owner, source, fence):
            try:
                result = original(identity, owner, source, fence)
            except BaseException:
                if identity == self.identity:
                    self._record(lambda: self._facts(owner, source, fence, 'raised'))
                raise
            if identity == self.identity:
                self._record(lambda: self._facts(owner, source, fence, 'returned'))
            return result
        patch.setattr(self.windows, '_check_current', checked)
        for name in ('invalidate', 'retain_interest'):
            method = getattr(self.windows, name)
            def observed(*args, _method=method, _name=name, **kwargs):
                before = self.windows.epochs.get(self.identity)
                try:
                    return _method(*args, **kwargs)
                finally:
                    if before != self.windows.epochs.get(self.identity):
                        self._record(lambda: {'stage': _name, 'beforeEpoch': before,
                            'afterEpoch': self.windows.epochs.get(self.identity),
                            'caller': self._caller(),
                            'effectiveScope': self._scope(args[0] if args else kwargs.get('identities')),
                            **(self._publication or {'publication': None, 'publisher': 'unknown',
                                                    'fullReason': 'not-full'})})
            patch.setattr(self.windows, name, observed)

    def _scope(self, identities):
        # Describe only the effective argument at the real invalidation call.
        # Never consume an iterator or stringify unknown caller-owned objects.
        if identities is None:
            return 'all'
        if type(identities) not in (set, frozenset, list, tuple) or len(identities) > 64:
            return 'unknown'
        if not all(type(item) is str for item in identities):
            return 'unknown'
        return 'root' if self.identity in identities else 'excludes-root'

    @staticmethod
    def _reason(reason):
        if type(reason) is not str:
            return 'unknown'
        return {
            'Native catalog membership and ownership reconciliation': 'native-catalog-membership',
            'Startup reconciles persisted stores and interrupted operations': 'startup-reconciliation',
            'Shutdown reconciliation of durable controller stores': 'shutdown-reconciliation',
        }.get(reason, 'unknown')

    @staticmethod
    def _origin_name(module, qualname):
        return {
            ('amplifier_web.automatic_history', 'AutomaticHistory.refresh'): 'automatic-history-refresh',
            ('amplifier_web.automatic_history', 'AutomaticHistory.load'): 'automatic-history-load',
            ('amplifier_web.automatic_history', 'AutomaticHistory.loop'): 'automatic-history-loop',
            ('amplifier_web.automatic_naming', 'AutomaticNaming.schedule'): 'automatic-naming-schedule',
        }.get((module, qualname), 'unknown')

    @classmethod
    def _publisher(cls):
        # Bounded code-identity walk above service wrappers: no frame locals,
        # paths, raw stack or caller-provided strings enter the observation.
        frame = sys._getframe(1)
        try:
            for _ in range(16):
                if frame is None:
                    break
                origin = cls._origin_name(frame.f_globals.get('__name__'), frame.f_code.co_qualname)
                if origin != 'unknown':
                    return origin
                frame = frame.f_back
            return 'unknown'
        finally:
            del frame

    @staticmethod
    def _caller():
        # Fixed source locations only, not exception text, locals or raw stacks.
        allowed = {'service.py': {'_save', '_publish', '_publish_progress', 'on_runtime_event'},
                   'event_log_view.py': {'loop', 'invalidate'}}
        frame = sys._getframe(1)
        fallback = 'other'
        try:
            for _ in range(8):
                if frame is None:
                    break
                name = frame.f_code.co_filename.rsplit('/', 1)[-1]
                method = frame.f_code.co_name
                if method in allowed.get(name, set()):
                    if name == 'service.py':
                        return name + ':' + method
                    fallback = name + ':' + method
                frame = frame.f_back
            return fallback
        finally:
            del frame


@pytest.mark.parametrize('mode', ['returned', 'raised', 'observation-failed'])
def test_cold_read_trace_preserves_original_once_and_bounds_evidence(monkeypatch, mode):
    owner = {'id': 'root', 'workspace': 'synthetic-private-marker'}
    service = SimpleNamespace(_state={'sessions': [owner], 'revision': 1}, closed=False,
        clients=SimpleNamespace(records={}), queue_clients={})
    failure, result, calls = ValueError('not-exported'), object(), []
    def original(*args):
        calls.append(args)
        if mode != 'returned':
            raise failure
        return result
    windows = SimpleNamespace(service=service, epochs={'root': 2}, closed=False,
        _check_current=original)
    mutations = []
    def mutate(values):
        mutations.append(values)
        windows.epochs['root'] += 1
        if mode != 'returned':
            raise failure
        return result
    windows.invalidate = windows.retain_interest = mutate
    trace = _ColdReadTrace(windows, 'root')
    if mode == 'observation-failed':
        monkeypatch.setattr(trace, '_append', lambda record: (_ for _ in ()).throw(OSError('not-exported')))
    trace.install(monkeypatch)
    args = ('root', owner, dict(owner), 1)
    if mode == 'returned':
        assert windows._check_current(*args) is result
    else:
        with pytest.raises(ValueError) as error:
            windows._check_current(*args)
        assert error.value is failure
    assert calls == [args]
    if mode == 'observation-failed':
        assert trace.evidence['observationFailures'] == 1 and not trace.evidence['records']
    else:
        record = trace.evidence['records'][0]
        assert record['expectedEpoch'] == 1 and record['actualEpoch'] == 2
        assert record['originalOwnerResident'] and record['sourceChangedFields'] == []
        for _ in range(30):
            trace._record(lambda: record)
        assert len(trace.evidence['records']) == 24 and trace.evidence['dropped'] == 7
    values = {'root'}
    for method in (windows.invalidate, windows.retain_interest):
        if mode == 'returned':
            assert method(values) is result
        else:
            with pytest.raises(ValueError) as error:
                method(values)
            assert error.value is failure
    assert len(mutations) == 2 and all(value is values for value in mutations)
    assert windows.epochs['root'] == 4  # Observation did not undo either mutation.
    encoded = json.dumps(trace.evidence)
    assert 'synthetic-private-marker' not in encoded and 'not-exported' not in encoded
    failures = trace.evidence['observationFailures']
    trace.emit(write=lambda *args: (_ for _ in ()).throw(OSError('not-exported')))
    assert trace.evidence['observationFailures'] == failures + 1


@pytest.mark.parametrize('scope,expected', [
    (None, 'all'), ({'root'}, 'root'), ({'other'}, 'excludes-root'),
    ('private-scope', 'unknown'), ({1}, 'unknown'),
])
def test_publisher_scope_and_unknown_origin_do_not_expose_inputs(scope, expected):
    trace = _ColdReadTrace(SimpleNamespace(), 'root')
    assert trace._scope(scope) == expected
    assert trace._scope({str(i) for i in range(65)}) == 'unknown'
    assert trace._publisher() == 'unknown'  # This test is not a production origin.
    assert trace._reason('private-reason') == trace._reason(object()) == 'unknown'
    assert trace._origin_name('untrusted.module', 'AutomaticHistory.refresh') == 'unknown'
    assert trace._origin_name('amplifier_web.automatic_history', 'unknown') == 'unknown'
    assert trace._origin_name('amplifier_web.automatic_history', 'AutomaticHistory.refresh') == 'automatic-history-refresh'


@pytest.mark.parametrize('mode', ['returned', 'raised', 'observer-failed'])
def test_one_publication_correlates_two_epoch_changes_without_changing_calls(monkeypatch, mode):
    service = SimpleNamespace()
    calls, failure, result = [], ValueError('private-exception'), object()
    windows = SimpleNamespace(service=service, epochs={'root': 1},
        _check_current=lambda *args: None, retain_interest=lambda *args: None)
    def invalidate(identities=None):
        calls.append(('invalidate', identities))
        if identities is None or 'root' in identities:
            windows.epochs['root'] += 1
    windows.invalidate = invalidate
    scope = {'root'}
    def publish(*, session_ids=None):
        calls.append(('publish', session_ids))
        windows.invalidate(session_ids)  # Original _publish invalidation.
        windows.invalidate(session_ids)  # Its synchronous _save invalidation.
        if mode != 'returned':
            raise failure
        return result
    def full(*, reason):
        calls.append(('full', reason))
        return service._publish(session_ids=scope)
    service._publish, service._publish_full = publish, full
    trace = _ColdReadTrace(windows, 'root')
    if mode == 'observer-failed':
        def broken():
            raise OSError('private-observer')
        monkeypatch.setattr(trace, '_publisher', broken)
    trace.install(monkeypatch)
    reason = 'Native catalog membership and ownership reconciliation'
    if mode == 'returned':
        assert service._publish_full(reason=reason) is result
    else:
        with pytest.raises(ValueError) as error:
            service._publish_full(reason=reason)
        assert error.value is failure
    assert calls == [('full', reason), ('publish', scope), ('invalidate', scope), ('invalidate', scope)]
    assert calls[1][1] is scope and calls[2][1] is scope and calls[3][1] is scope
    assert windows.epochs['root'] == 3
    assert trace._publication is None and trace._full_reason == 'not-full'
    records = trace.evidence['records']
    assert [(row['beforeEpoch'], row['afterEpoch']) for row in records] == [(1, 2), (2, 3)]
    assert all(row['publication'] == 1 and row['fullReason'] == 'native-catalog-membership'
               and row['effectiveScope'] == 'root' and row['publisher'] == 'unknown' for row in records)
    assert trace.evidence['observationFailures'] == int(mode == 'observer-failed')
    assert trace.evidence['dropped'] == 0
    # A later scoped/direct call must not inherit the previous full reason.
    if mode == 'returned':
        assert service._publish(session_ids=None) is result
    else:
        with pytest.raises(ValueError) as error:
            service._publish(session_ids=None)
        assert error.value is failure
    assert calls[-3:] == [('publish', None), ('invalidate', None), ('invalidate', None)]
    assert windows.epochs['root'] == 5 and trace._publication is None
    assert all(row['publication'] == 2 and row['fullReason'] == 'not-full'
               and row['effectiveScope'] == 'all' for row in records[-2:])
    assert trace.evidence['observationFailures'] == 2 * int(mode == 'observer-failed')
    encoded = json.dumps(trace.evidence)
    assert 'private-' not in encoded and reason not in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize('indexed', [True, False])
async def test_raw_root_metadata_cannot_replace_attested_root(source, indexed):
    source.call('one', body_metadata={'rootSessionId': 'forged-root'})
    await source.diagnostics.flush()
    if not indexed:
        with source.diagnostics._db() as db:
            db.execute('DELETE FROM records')
    page = await source.page()
    assert all('rootSessionId' not in row for row in page['items'])
    if indexed:
        assert len(page['_boundWork']) == 1
        assert page['_boundWork'][0]['rootSessionId'] == 'root'
    else:
        assert not page['_boundWork']


@pytest.mark.asyncio
async def test_attested_native_root_and_public_detail_routing_stay_distinct(source):
    source.session.update(id='public-root', nativeIdentity='root', runtimeSessionId='root')
    source.service._session = lambda identity, hydrate=False: source.session if identity == 'public-root' else None
    source.call('one')
    await source.diagnostics.flush()
    owner = WorkWindows(source.service)
    class Direct:
        model = WindowModel(source.service.data_dir)
        async def project(self, inputs, *, key):
            return self.model.project(inputs, check_budget=lambda: None)
        async def close(self):
            pass
    owner.projector = Direct()
    try:
        page = await owner.read('public-root')
        group = next(row for row in page['items'] if row.get('bindingProvenance'))
        node = owner.nodes('public-root', group['id'])['items'][0]
        assert node['sessionId'] == node['rootSessionId'] == 'root'
        assert node['inputDetail']['sessionId'] == node['outputDetail']['sessionId'] == 'public-root'
        raw = owner.observations('public-root', page['evidence']['group'], page['evidence']['revision'])
        assert all('rootSessionId' not in row for row in raw['items'])
        chunks, offset = [], 0
        while True:
            value = await owner.read_text('public-root', {**node['inputDetail'], 'offset': offset, 'limit': 4096})
            assert len(value['value']) <= 4096
            chunks.append(value['value'])
            offset = value['nextOffset']
            if offset is None:
                break
        assert json.loads(''.join(chunks)) == source.calls[0][2]
    finally:
        await owner.close()
