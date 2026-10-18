"""Cold binding uses real observer/diagnostic receipts, never receipt-shaped trust.

Canonical tool bodies below are synthetic disk-contract data. Ownership records
are emitted by ExecutionEvents -> Diagnostics -> its actual append/index path.
The separate native-producer baseline covers actual kernel/capture production.
"""
from types import SimpleNamespace
import copy
import json
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

    def call(identity, turn_id='turn'):
        if not any(t['id'] == turn_id for t in session['execution']['turns']):
            turn(turn_id)
        interim = {'id': identity + '-interim-' + str(len(calls)), 'role': 'assistant',
            'inputId': turn_id, 'createdAt': time.time() - .001, 'timestampKnown': True,
            'text': 'Progress', 'response': {'status': 'completed', 'inputIds': [turn_id],
                                          'phase': 'interim'}}
        session['messages'].append(interim)
        body = {'text': 'INPUT:漢字é𝄞' * 1800 + ':END'}
        result = {'success': True, 'output': 'OUTPUT:漢字é𝄞' * 1800 + ':END'}
        refs = []
        for event, fields in [('tool:pre', {'tool_input': body}), ('tool:post', {'result': result})]:
            data = {'session_id': 'root', 'tool_call_id': identity, 'tool_name': 'read_file',
                    'timestamp': time.time(), **fields}
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
                                  'disabled', 'reused-id', 'cross-session', 'partial', 'invalid'])
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
    elif kind == 'copied-receipt':
        # Same authentic bytes at a different occurrence are not another receipt.
        raw = path.read_bytes()
        path.write_bytes(raw + raw.splitlines(keepends=True)[-1])
    elif kind == 'cross-session':
        append_event(source.workspace, 'root', 'tool:post', {'timestamp': time.time(),
            'session_id': 'other', 'tool_call_id': 'one', 'result': 'other'})
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
            assert node['inputDetail']['id'] == node['id']
            reply = await owner.read_text('root', node['inputDetail'])
            assert reply['value']
            sync = owner.nodes('root', group['id'], known={node['id']: node['detailVersion']})
            assert sync['items'] == [] and sync['order'] == [node['id']]
        assert source.session == original
    finally:
        await owner.close()
