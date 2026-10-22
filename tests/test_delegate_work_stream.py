"""Exercise the registered-browser boundary, not a hand-populated exchange tree."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from amplifier_web.execution import ingest
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import normalize_event
from amplifier_web.session_projection import stored_execution
from test_work_windows import FakeProjector, fixture


def recorded_delegate(session, path, monkeypatch, *, trailing_tools=0):
    """The same hook feeds canonical recording and the metadata-only observer."""
    tick = [10.0]
    # Keep deterministic observer stamps without freezing the service's shared
    # time module, HTTP timers, or background task clocks.
    monkeypatch.setattr('amplifier_web.execution_events.time',
                        SimpleNamespace(time=lambda: tick[0]))
    root = session['nativeIdentity']
    request = 'Review the launch checklist: café.\n' * 600 + 'REQUEST-END'
    reply = 'EXCHANGE-CHECK: the returned answer.\n' * 600 + 'REPLY-END'
    session['execution']['turns'] = [
        {'id': 'turn', 'anchorMessageId': 'user', 'phase': 'running', 'startedAt': 10}
    ]

    def emit(event):
        normalized = normalize_event(event, root)
        if normalized and normalized[0] == 'execution.event':
            ingest(session, normalized[1])

    observer = ExecutionEvents(root, emit)
    observer.lifecycle({'type': 'input.delivered', 'input_id': 'turn', 'source': 'user'})

    def hook(name, data):
        tick[0] += 1
        captured = {'session_id': root, **data}
        if name.startswith('tool:'):
            captured = observer.hook(root, name, captured)
            assert captured['unified_tool_observation_id'] == (
                observer.calls[(root, captured['tool_call_id'])]['toolObservationId'])
        record = {'event': name, 'timestamp': tick[0], 'session_id': root, 'data': captured}
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')

    arguments = {'agent': 'review', 'instruction': request}
    hook('tool:pre', {'tool_call_id': 'delegate-one', 'tool_name': 'delegate',
                      'tool_input': arguments})
    hook('delegate:agent_spawned', {'tool_call_id': 'delegate-one',
                                    'parent_session_id': root, 'sub_session_id': 'child'})
    observer.lifecycle({'type': 'child.updated', 'sessionId': 'child',
                        'parentSessionId': root, 'callId': 'delegate-one',
                        'runId': 'run-one', 'agent': 'review', 'status': 'running'})
    hook('tool:post', {'tool_call_id': 'delegate-one', 'tool_name': 'delegate',
                       'tool_input': arguments,
                       'result': {'success': True, 'output': {'response': reply,
                                                             'session_id': 'child'}}})
    observer.lifecycle({'type': 'child.updated', 'sessionId': 'child',
                        'parentSessionId': root, 'callId': 'delegate-one',
                        'runId': 'run-one', 'agent': 'review', 'status': 'completed',
                        'report': 'A retained summary is not the original reply.'})
    # Move both delegate records outside the recent event and resident tails.
    # These are actual metadata-only observer calls, not fabricated display rows.
    for index in range(trailing_tools):
        data = {'tool_call_id': f'later-{index}', 'tool_name': 'read_file',
                'tool_input': {'path': f'/fixture/later-{index}.txt'}}
        hook('tool:pre', data)
        hook('tool:post', {**data, 'result': f'OTHER-RESULT-{index}'})
    session['execution']['turns'][0].update(phase='completed', endedAt=tick[0])
    # Crucial distinction from the older browser fixture: do not inject bodies.
    assert all('input' not in row and 'output' not in row
               for row in session['execution']['nodes'])
    return request, reply


def exchanges(rows):
    found = {}
    for row in rows:
        # Saved record-local fields are useful even without proven worker/turn
        # association. This test must not require the resolver to invent one.
        for descriptor in (row, row.get('exchange'), (row.get('delegate') or {}).get('exchange')):
            if descriptor and any(key.startswith('delegateRequest') or key.startswith('delegateResponse')
                                  for key in descriptor):
                found.setdefault(descriptor['id'], {}).update(descriptor)
    return list(found.values())


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['resident', 'persisted-projection', 'service-reopen'])
@pytest.mark.parametrize('trailing_tools', [0, 140], ids=['recent', 'paged'])
async def test_registered_browser_reads_recorded_delegate_exchange_without_body_injection(
        authenticated_client, tmp_path, monkeypatch, mode, trailing_tools):
    from amplifier_web.server import create_app
    from amplifier_web.work_windows import WorkWindows
    from test_service import Runtime

    _, source, path, _ = fixture(tmp_path, monkeypatch, count=0)
    async def open_app():
        return await create_app(tmp_path / 'web', workspace=tmp_path, runtime=Runtime(),
                                voice=False, background_updates=False, preload_providers=False)

    app = await open_app()
    service = app['service']
    await service.dispatch('session.create', {})
    session = service._session()
    session.update({key: value for key, value in source.items() if key != 'id'})
    identity = session['id']
    expected = recorded_delegate(session, path, monkeypatch, trailing_tools=trailing_tools)
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()

    if mode == 'persisted-projection':
        # Exercise the real persistence projection and a fresh window cache.
        # This is not yet the separate process-restart/large-history acceptance.
        session['execution'] = json.loads(json.dumps(stored_execution(session['execution'])))
        assert session['execution']['nodes'] == []
        await service.event_log_view.windows.close()
        service.event_log_view.windows = WorkWindows(service)
    elif mode == 'service-reopen':
        # Close the actual HTTP application/store and open a fresh service from
        # saved disk state, not a copied execution dict or browser reload.
        # This is an in-process service restart; OS-process restart is separate.
        service._publish()
        previous = service
        previous_client = await authenticated_client(app)
        await previous_client.close()
        assert previous.closed
        app = await open_app()
        service = app['service']
        assert service is not previous
        session = service._session(identity, hydrate=False)
        assert session['nativeIdentity'] == source['nativeIdentity']
        assert session['execution']['nodes'] == []

    windows = service.event_log_view.windows
    windows.projector = FakeProjector(service.data_dir)
    service.clients.attach('exchange-browser')
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'exchange-browser'
    original = service._session
    before = copy.deepcopy(session['execution'])

    def bounded(identity=None, *, hydrate=True):
        assert hydrate is False, 'A Work read must not hydrate the full execution graph'
        return original(identity, hydrate=False)

    monkeypatch.setattr(service, '_session', bounded)
    try:
        group = 'turn@user'
        pages = []
        cursor = None
        seen_cursors = set()
        for _ in range(8):
            query = {'sessionId': identity, 'part': 'groups' if mode != 'resident' else 'nodes'}
            if mode == 'resident':
                query['group'] = group
            if cursor:
                query['before'] = cursor
            response = await client.get('/api/conversation/detail', params=query)
            assert response.status == 200, await response.text()
            result = await response.json()
            assert result['coverage']['historyComplete'] is False
            if mode == 'resident':
                pages.append(result)
            else:
                for item in result['items']:
                    response = await client.get('/api/conversation/detail', params={
                        'sessionId': identity, 'part': 'nodes', 'group': item['id']})
                    assert response.status == 200, await response.text()
                    pages.append(await response.json())
            cursor = result.get('before')
            if not cursor:
                break
            assert cursor not in seen_cursors, 'Older-page cursor must advance'
            seen_cursors.add(cursor)
        else:
            pytest.fail('Synthetic history exceeded the declared eight-page budget')
        if trailing_tools:
            assert seen_cursors, 'The out-of-tail case must demand older work explicitly'
        assert all(len(page['items']) <= 100 for page in pages)
        rows = [row for page in pages for row in page['items']]
        if mode == 'resident':
            target = next(row for row in rows if row.get('kind') == 'tool'
                          and row.get('toolCallId') == 'delegate-one')
            continuation = {}
            for _ in range(8):
                response = await client.get('/api/conversation/detail', params={
                    **target['toolDetail'], **continuation})
                assert response.status == 200, await response.text()
                loaded = await response.json()
                if loaded['before'] is None:
                    break
                continuation = {'lookupKey': loaded['lookupKey'], 'before': loaded['before']}
            else:
                pytest.fail('Saved delegate lookup exceeded eight pages')
            assert loaded['item']['contentAvailability']['status'] == 'linked'
            assert loaded['item']['toolObservationId'] == target['toolObservationId']
            descriptors = exchanges([loaded['item']])
        else:
            descriptors = exchanges(rows)

        async def read_exact(reference):
            value, offset, reads = '', 0, 0
            for _ in range(64):
                response = await client.get('/api/conversation/detail', params={
                    **reference, 'sessionId': identity, 'offset': offset, 'limit': 1024})
                assert response.status == 200, await response.text()
                chunk = await response.json()
                reads += 1
                value += chunk['value']
                next_offset = chunk['nextOffset']
                if next_offset is None:
                    assert reads >= 2, 'Long exchange body must exercise chunked reads'
                    return value
                assert next_offset > offset, 'Lazy field reads must make progress'
                offset = next_offset
            pytest.fail('Lazy field exceeded the declared chunk budget')

        issued = []
        for field, exact in zip(('delegateRequest', 'delegateResponse'), expected):
            candidates = [entry for entry in descriptors
                          if field in entry or entry.get(field + 'Detail')]
            assert candidates, f'{field} missing from registered-browser {mode} page'
            reference = candidates[-1].get(field + 'Detail')
            assert reference, 'Long exchange bodies must remain lazy, not inline'
            assert len(candidates[-1].get(field, '')) < len(exact)
            assert await read_exact(reference) == exact
            issued.append((reference, exact))
        # The explicit tool lookup admits exact field leases. The first sync may
        # publish that newly enriched projection (OpenWorkFields.retain), even
        # though the underlying execution graph is unchanged. Consume it, verify
        # identity and exact fields, then require a second quiet sync to settle.
        by_group = {}
        for page in pages:
            by_group.setdefault(page['group'], {}).update(
                (row['id'], row['detailVersion']) for row in page['items'])
        for group, known in by_group.items():
            original_rows = {row['id']: row for page in pages if page['group'] == group
                             for row in page['items']}
            # Pages arrive newest first; derive chronology independently from
            # source position, not from the sync response's own order.
            if mode == 'resident':
                expected_order = [row['id'] for row in before['nodes'] if row['id'] in known]
            else:
                expected_order = [row['id'] for page in pages if page['group'] == group
                                  for row in page['items']]
            assert len(expected_order) == len(set(expected_order)) == len(known)
            response = await client.post('/api/conversation/work-sync', json={
                'sessionId': identity, 'group': group, 'known': known})
            assert response.status == 200, await response.text()
            result = await response.json()
            assert result['order'] == expected_order
            changed_ids = [row['id'] for row in result['items']]
            assert len(changed_ids) == len(set(changed_ids))
            if mode == 'resident':
                assert changed_ids == [target['id']], 'Only the resolved delegate gains field leases'
                updated = result['items'][0]
                prior = original_rows[updated['id']]
                for key in ('id', 'kind', 'sessionId', 'rootSessionId', 'toolCallId',
                            'toolObservationId', 'parentId', 'turnId', 'phase', 'startedAt', 'endedAt'):
                    assert updated.get(key) == prior.get(key), key
                assert updated['detailVersion'] != known[updated['id']]
                assert updated['contentAvailability']['status'] == 'linked'
                for field, (reference, exact) in zip(('delegateRequest', 'delegateResponse'), issued):
                    candidates = [row for row in exchanges([updated]) if row.get(field + 'Detail')]
                    assert candidates, f'Sync lost {field}'
                    current = candidates[-1][field + 'Detail']
                    assert current == reference, 'Sync must retain the exact admitted source lease'
                    assert await read_exact(current) == exact
            else:
                assert result['items'] == []
            known.update((row['id'], row['detailVersion']) for row in result['items'])
            response = await client.post('/api/conversation/work-sync', json={
                'sessionId': identity, 'group': group, 'known': known})
            assert response.status == 200, await response.text()
            settled = await response.json()
            assert settled['items'] == [], 'Accepted field leases must not churn on quiet sync'
            assert settled['order'] == expected_order
        for reference, exact in issued:
            assert await read_exact(reference) == exact
        assert session['execution'] == before
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source_hash
    finally:
        monkeypatch.setattr(service, '_session', original)
