"""Exercise the registered-browser boundary, not a hand-populated exchange tree."""
import copy
import asyncio
import hashlib
import json
import os
from pathlib import Path
import traceback
from types import SimpleNamespace

import pytest

from amplifier_web.execution import ingest
from amplifier_web.execution_events import ExecutionEvents
from amplifier_web.runtime import normalize_event
from amplifier_web.session_projection import stored_execution
from test_work_windows import FakeProjector, fixture


def trace_windows(monkeypatch, path):
    """Bounded metadata-only evidence for the formerly intermittent cold page."""
    from amplifier_web.work_windows import WorkWindows, source_identity
    from amplifier_web.cold_display import raw
    records = []
    def note(kind, windows, identity=None, **extra):
        if len(records) >= 160:
            return
        owner = windows._owner(identity) if identity else None
        stat = path.stat()
        records.append({'event': kind, 'epoch': windows.epochs.get(identity),
                        'revision': windows.service._state.get('revision'),
                        'source': source_identity(owner) if owner is not None else None,
                        'coldExecution': raw(owner).get('_coldFields', {}).get('execution') if owner is not None else None,
                        'residentExecution': 'execution' in raw(owner) if owner is not None else None,
                        'sourceStamp': [stat.st_dev, stat.st_ino, stat.st_size,
                                        stat.st_mtime_ns, stat.st_ctime_ns],
                        **extra})
    invalidate, check, read = WorkWindows.invalidate, WorkWindows._check_current, WorkWindows.read
    def traced_invalidate(windows, identities=None, **kwargs):
        stack = [f'{Path(frame.filename).name}:{frame.name}:{frame.lineno}'
                 for frame in traceback.extract_stack(limit=8)[:-1]]
        for identity in tuple(windows.epochs):
            if identities is None or identity in identities:
                note('invalidate', windows, identity, publication=stack)
        return invalidate(windows, identities, **kwargs)
    def traced_check(windows, identity, owner, source, fence):
        try:
            return check(windows, identity, owner, source, fence)
        except ValueError as error:
            note('rejected', windows, identity, capturedFence=fence,
                 sameOwner=windows._owner(identity) is owner,
                 capturedSource=source, error=str(error),
                 guard=[f'{Path(frame.filename).name}:{frame.name}:{frame.lineno}'
                        for frame in traceback.extract_tb(error.__traceback__)])
            raise
    async def traced_read(windows, identity, before=None, **kwargs):
        note('read', windows, identity, older=bool(before))
        try:
            result = await read(windows, identity, before, **kwargs)
            note('accepted', windows, identity, older=bool(before))
            return result
        except ValueError as error:
            note('read-error', windows, identity, older=bool(before), error=str(error))
            raise
    monkeypatch.setattr(WorkWindows, 'invalidate', traced_invalidate)
    monkeypatch.setattr(WorkWindows, '_check_current', traced_check)
    monkeypatch.setattr(WorkWindows, 'read', traced_read)
    return records


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
@pytest.mark.parametrize('trailing_tools,mode,controlled_publication', [
    (count, mode, False) for count in (0, 140)
    for mode in ('resident', 'persisted-projection', 'service-reopen')
] + [(140, 'service-reopen', True)], ids=[
    f'{size}-{mode}' for size in ('recent', 'paged')
    for mode in ('resident', 'persisted-projection', 'service-reopen')
] + ['paged-service-reopen-controlled-publication'])
async def test_registered_browser_reads_recorded_delegate_exchange_without_body_injection(
        authenticated_client, tmp_path, monkeypatch, mode, trailing_tools, controlled_publication):
    from amplifier_web.server import create_app
    from amplifier_web.work_windows import WorkWindows
    from test_service import Runtime

    _, source, path, _ = fixture(tmp_path, monkeypatch, count=0)
    trace = trace_windows(monkeypatch, path) if mode == 'service-reopen' and trailing_tools else None
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
    placement_keys = ('id', 'turnId', 'sessionId', 'rootSessionId',
                      'toolCallId', 'toolObservationId')
    expected_placements = [tuple(row[key] for key in placement_keys)
                           for row in session['execution']['nodes'] if row.get('kind') == 'tool']
    assert len(expected_placements) == trailing_tools + 1

    def assert_saved_placement():
        # Cold history now retains host-proven placement, but never the saved
        # canonical request/reply bodies. Reopen must preserve exact bindings.
        rows = session['execution']['nodes']
        assert len(rows) == trailing_tools + 1
        assert [tuple(row[key] for key in placement_keys) for row in rows] == expected_placements
        assert all(row.get('liveObservation') is True for row in rows)
        allowed = {*placement_keys, 'parentId', 'kind', 'phase', 'label', 'tool',
                   'revision', 'startedAt', 'endedAt', 'runId', 'lifecycle', 'liveObservation'}
        assert all(set(row) <= allowed for row in rows)
        assert all(not set(row) & {'input', 'output', 'exchange', 'delegate'} for row in rows)

    if mode == 'persisted-projection':
        # Exercise the real persistence projection and a fresh window cache.
        # This is not yet the separate process-restart/large-history acceptance.
        session['execution'] = json.loads(json.dumps(stored_execution(session['execution'])))
        assert_saved_placement()
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
        assert_saved_placement()

    windows = service.event_log_view.windows
    windows.projector = FakeProjector(service.data_dir)
    service.clients.attach('exchange-browser')
    client = await authenticated_client(app)
    client.session.headers['X-Amplifier-Client'] = 'exchange-browser'
    if mode == 'service-reopen':
        # Startup catalog reconciliation can publish even with update polling
        # disabled. Finish that real barrier before expecting a stable read.
        # A separate controlled-publication case below retains the race guard.
        await service.history.refresh(force=True)
    original = service._session
    before = copy.deepcopy(session['execution'])

    def bounded(identity=None, *, hydrate=True):
        assert hydrate is False, 'A Work read must not hydrate the full execution graph'
        return original(identity, hydrate=False)

    monkeypatch.setattr(service, '_session', bounded)
    try:
        if controlled_publication:
            # Compare completed startup plus one controlled publication, not a
            # longer sleep or a silent retry of an intermittently rejected read.
            entered, release = asyncio.Event(), asyncio.Event()
            first = await windows.read(identity)
            assert first['before'], 'Controlled rejection must exercise an older page'
            project = windows.projector.project
            async def held(inputs, *, key):
                entered.set()
                await release.wait()
                return await project(inputs, key=key)
            windows.projector.project = held
            pending = asyncio.create_task(windows.read(identity, first['before']))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                service._publish_full(reason='Controlled catalog publication during older Work read')
                release.set()
                with pytest.raises(ValueError, match='requested conversation changed'):
                    await pending
                rejected = [item for item in trace if item['event'] == 'rejected'][-1]
                assert rejected['sameOwner']
                assert rejected['capturedSource'] == rejected['source']
                assert rejected['capturedFence'] != rejected['epoch']
                assert len({tuple(item['sourceStamp']) for item in trace}) == 1
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
                windows.projector.project = project
            # This new explicit read follows the deliberate rejected read.
            # It is not a retry loop and must not relax its invalidation fence.
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
        if trace is not None:
            destination = Path(os.environ.get('AMPLIFIER_TEST_OUTPUT_DIR', str(tmp_path)))
            destination.mkdir(parents=True, exist_ok=True)
            (destination / f'delegate-paged-reopen-{"controlled" if controlled_publication else "settled"}-trace.json').write_text(json.dumps(trace, indent=2))
