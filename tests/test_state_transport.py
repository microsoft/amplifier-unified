import asyncio
from copy import deepcopy
import json
import pytest

from amplifier_web.state_transport import delta
from amplifier_web.browser_detail import project
from amplifier_web.server import create_app
from test_service import Runtime


@pytest.mark.parametrize('publication', ['commit', 'failed-save', 'progress', 'progress-ready', 'peer-ready', 'save-only'])
async def test_work_invalidation_does_not_publish_uncommitted_state(authenticated_client, tmp_path, monkeypatch, publication):
    from amplifier_web.shell_resources import scope_for

    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=Runtime(), voice=False, background_updates=False)
    service = app['service']
    client = await authenticated_client(app)
    await client.post('/api/clients/attach', json={'clientId': 'work-reader', 'kind': 'web'})
    headers = {'X-Amplifier-Client': 'work-reader'}
    await client.post('/api/actions', headers=headers,
                      json={'id': 'create', 'action': 'session.create', 'args': {}})
    windows = service.event_log_view.windows
    peer_id = peer_queue = None
    if publication == 'peer-ready':
        await client.post('/api/clients/attach', json={'clientId': 'peer-reader', 'kind': 'web'})
        await client.post('/api/actions', headers={'X-Amplifier-Client': 'peer-reader'},
                          json={'id': 'peer-create', 'action': 'session.create', 'args': {}})
        with service.clients.bind('peer-reader'):
            peer_id = service.state['selectedSessionId']
            service.shell.resources.register('peer-reader', 1, ['recentNavigation'], scope_for(service.state))
            peer_queue = service.subscribe()
    with service.clients.bind('work-reader'):
        sid = service.state['selectedSessionId']
        service.shell.resources.register('work-reader', 1, ['recentNavigation'], scope_for(service.state))
        queue = service.subscribe()
    try:
        # Exclude unrelated startup/background publications from this exact
        # writer-boundary test while the bounded window worker finishes.
        async with service.lock:
            await windows.read(sid)
            assert windows.status[sid]['current']
            while not queue.empty():
                queue.get_nowait()
            previous = service._state['revision']
            service._session(sid)['streaming'] = 'Uncommitted sentinel'
            if publication == 'failed-save':
                def fail_save():
                    raise OSError('Synthetic commit failure')
                with monkeypatch.context() as patch:
                    patch.setattr(service, '_save', fail_save)
                    with pytest.raises(OSError, match='Synthetic commit failure'):
                        service._publish_changes(sessions={sid})
                assert service._state['revision'] == previous
                assert queue.empty(), 'Failed publication must not leak the invalidation snapshot'
            elif publication in {'progress', 'progress-ready', 'peer-ready'}:
                service._publish_progress(session_ids={sid}, record_only=True, detail_only=True)
                assert queue.empty(), 'Deferred progress must not publish through invalidation'
                assert not windows.status[sid]['current']
                if publication == 'progress-ready':
                    await windows.read(sid)
                    assert queue.empty(), 'Read completion must not expose deferred progress'
                elif publication == 'peer-ready':
                    while not peer_queue.empty():
                        peer_queue.get_nowait()
                    await windows.read(peer_id)
                    assert windows.status[peer_id]['current']
                    assert peer_queue.empty(), 'Peer readiness waits for the dirty writer commit'
            elif publication == 'save-only':
                service._save_changes(sessions={sid})
                assert queue.empty(), 'Saving without publication must not emit a Work snapshot'
            if publication not in {'commit', 'progress-ready'}:
                assert not windows.status[sid]['current'], 'Writer invalidation remains immediate'
            service._publish(session_ids={sid}, record_only=True, detail_only=True)
            assert not windows.status[sid]['current']
            if peer_queue is not None:
                peer_frames = []
                while not peer_queue.empty():
                    peer_frames.append(peer_queue.get_nowait())
                assert peer_frames, 'A narrow commit must deliver the unrelated retained ready notice'
                assert all(frame['revision'] == previous + 1 for frame in peer_frames)
                assert any(next(row for row in frame['sessions'] if row['id'] == peer_id)
                           ['workProjection']['current'] for frame in peer_frames)
            frames = []
            while not queue.empty():
                frames.append(queue.get_nowait())
            assert frames
            assert all(frame['revision'] == previous + 1 for frame in frames)
            assert all(next(row for row in frame['sessions'] if row['id'] == sid)['streaming'] ==
                       'Uncommitted sentinel' for frame in frames)
            # A real completed read must still notify without another action.
            await windows.read(sid)
            assert windows.status[sid]['current']
            ready = []
            while not queue.empty():
                ready.append(queue.get_nowait())
            assert ready
            assert any(next(row for row in frame['sessions'] if row['id'] == sid)
                       ['workProjection']['current'] for frame in ready)
    finally:
        service.unsubscribe(queue)
        if peer_queue is not None:
            service.unsubscribe(peer_queue)


@pytest.mark.parametrize('compact', [False, True])
@pytest.mark.parametrize('peer_after_completion', [False, True])
async def test_send_receipt_observes_consumption_after_admission(authenticated_client, tmp_path, compact, peer_after_completion, monkeypatch):
    import base64

    class ObservedRuntime(Runtime):
        async def send(self, session, text, input_id, emit):
            self.admission = service.browser_state()
            return await super().send(session, text, input_id, emit)

    runtime = ObservedRuntime()
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=runtime, voice=False, background_updates=False)
    service = app['service']
    await service.history.close()
    await service.event_log_view.close()
    client = await authenticated_client(app)
    await client.post('/api/clients/attach', json={'clientId': 'sender'})
    headers = {'X-Amplifier-Client': 'sender'}
    if compact:
        headers['X-Amplifier-State-Transport'] = 'delta-v1'

    async def command(identity, action, args):
        response = await client.post('/api/actions', headers=headers,
                                     json={'id': identity, 'action': action, 'args': args})
        assert response.status == 200, await response.text()
        return await response.json()

    await command('peer', 'session.create', {})
    with service.clients.bind('sender'):
        peer = service._session()
    await command('create', 'session.create', {})
    await command('file', 'attachment.add', {'name': 'sent.txt',
                  'base64': base64.b64encode(b'Exact uploaded bytes').decode()})
    with service.clients.bind('sender'):
        sid = service.state['selectedSessionId']
        attachment = deepcopy(service.clients.attachments(service._session(sid))[0])
        queue = service.subscribe()
    if peer_after_completion:
        create_task = service._task

        def task_with_peer_publication(coro):
            first = (coro.cr_code.co_name == 'dispatch'
                     and coro.cr_frame.f_locals.get('command_id') == 'first')
            task = create_task(coro)
            if first:
                # Registered before shield's callback: this real uninterested
                # publication runs after dispatch completes but before HTTP resumes.
                def publish_peer(completed):
                    if completed.cancelled() or completed.exception():
                        return
                    assert not service.lock.locked()
                    peer['streaming'] = 'Unrelated peer progress'
                    service._publish_changes(sessions={peer['id']}, detail_only=True)
                task.add_done_callback(publish_peer)
            return task

        monkeypatch.setattr(service, '_task', task_with_peer_publication)
    try:
        args = {'sessionId': sid, 'text': 'First', 'attachmentIds': [attachment['id']]}
        receipt = await command('first', 'conversation.send', args)
        assert receipt['delivery'] == 'accepted'
        frames = []
        while not queue.empty():
            frame = queue.get_nowait()
            if 'shellClientId' not in frame:
                frames.append(frame)
        admission = runtime.admission
        assert admission['revision'] == receipt['revision']
        admitted = next(row for row in admission['sessions'] if row['id'] == sid)
        assert admitted['draftAttachments'] == [attachment]
        assert next(row for row in admitted['messages'] if row.get('inputId') == 'first')['delivery']['status'] == 'sending'
        if compact:
            assert 'state' not in receipt
            assert receipt['stateRevision'] > receipt['revision']
            assert receipt['stateRevision'] in {frame['revision'] for frame in frames}, 'Receipt must name a frame emitted to the sender, not unrelated peer progress'
            observed = next(frame for frame in frames if frame['revision'] == receipt['stateRevision'])
            if peer_after_completion:
                assert service._state['revision'] > receipt['stateRevision']
        else:
            assert 'stateRevision' not in receipt
            observed = receipt['state']
        settled = next(row for row in observed['sessions'] if row['id'] == sid)
        assert settled['draftAttachments'] == []
        sent = next(row for row in settled['messages'] if row.get('inputId') == 'first')
        assert sent['delivery']['status'] == 'accepted' and sent['attachments'] == [attachment]
        duplicate = await command('first', 'conversation.send', args)
        assert duplicate['duplicate'] and duplicate['revision'] == receipt['revision']
        if compact:
            assert duplicate['stateRevision'] == receipt['stateRevision']
        assert '_deliveryStateRevision' not in receipt and '_deliveryStateRevision' not in duplicate
        assert len(runtime.sent) == 1
        second = await command('second', 'conversation.send', {'sessionId': sid, 'text': 'Adjacent cycle'})
        assert second['delivery'] == 'accepted' and len(runtime.sent) == 2
        rejected = await client.post('/api/actions', headers=headers, json={'id': 'stale',
            'action': 'conversation.send', 'args': {'sessionId': sid, 'text': 'Stale',
                                                   'attachmentIds': [attachment['id']]}})
        assert rejected.status == 400
        assert (await rejected.json())['error'] == 'An attachment is no longer in this draft. Refresh and retry.'
        assert len(runtime.sent) == 2
    finally:
        service.unsubscribe(queue)


@pytest.mark.parametrize('evidence', ['runtime.delivery', 'input.delivered'])
@pytest.mark.parametrize('rollback', [False, True])
async def test_duplicate_send_keeps_early_consumption_revision(authenticated_client, tmp_path, evidence, rollback, monkeypatch):
    import base64

    class EarlyRuntime(Runtime):
        def __init__(self):
            super().__init__()
            self.accepted, self.release = asyncio.Event(), asyncio.Event()

        async def send(self, session, text, input_id, emit):
            self.sent.append(input_id)
            if evidence == 'runtime.delivery':
                event = (evidence, {'sessionId': session['id'], 'inputId': input_id, 'delivery': 'accepted'})
            else:
                from amplifier_web.runtime import normalize_event
                event = normalize_event({'type': evidence, 'input_id': input_id}, session['id'], input_id)
            if rollback:
                from amplifier_web import state_records
                save = state_records.save_working
                before = service._state['revision']

                def fail_commit(*args, **kwargs):
                    save(*args, **kwargs)
                    raise OSError('Controlled publication failure')

                with monkeypatch.context() as patch:
                    patch.setattr(state_records, 'save_working', fail_commit)
                    with pytest.raises(OSError, match='Controlled publication failure'):
                        await emit(*event)
                durable = json.loads(service.db.execute('SELECT receipt FROM commands WHERE id=?', (input_id,)).fetchone()[0])
                assert durable['delivery'] == 'sending' and '_deliveryStateRevision' not in durable
                assert service._state['revision'] == before
            await emit(*event)
            self.acceptance_revision = service._state['revision']
            self.accepted.set()
            await self.release.wait()

        async def close(self):
            self.release.set()

    runtime = EarlyRuntime()
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=runtime, voice=False, background_updates=False)
    service = app['service']
    await service.history.close()
    await service.event_log_view.close()
    client = await authenticated_client(app)
    await client.post('/api/clients/attach', json={'clientId': 'sender'})
    headers = {'X-Amplifier-Client': 'sender', 'X-Amplifier-State-Transport': 'delta-v1'}

    async def command(identity, action, args):
        response = await client.post('/api/actions', headers=headers,
                                     json={'id': identity, 'action': action, 'args': args})
        assert response.status == 200, await response.text()
        return await response.json()

    await command('peer', 'session.create', {})
    with service.clients.bind('sender'):
        peer = service._session()
    await command('create', 'session.create', {})
    await command('file', 'attachment.add', {'name': 'sent.txt', 'base64': base64.b64encode(b'Exact').decode()})
    with service.clients.bind('sender'):
        sid = service.state['selectedSessionId']
        file_id = service.clients.attachments(service._session(sid))[0]['id']
        queue = service.subscribe()
    args = {'sessionId': sid, 'text': 'First', 'attachmentIds': [file_id]}
    pending = asyncio.create_task(command('first', 'conversation.send', args))
    try:
        await asyncio.wait_for(runtime.accepted.wait(), 5)
        assert not pending.done(), 'Early acceptance must precede the original RPC response'
        async with service.lock:
            peer['streaming'] = 'Unrelated progress after acceptance'
            service._publish_changes(sessions={peer['id']}, detail_only=True)
        duplicate = await command('first', 'conversation.send', args)
        assert duplicate['duplicate'] and duplicate['delivery'] == 'accepted'
        assert duplicate['stateRevision'] == runtime.acceptance_revision
        assert duplicate['revision'] < duplicate['stateRevision'] < service._state['revision']
        assert runtime.sent == ['first'] and not pending.done()
        frames = []
        while not queue.empty():
            frame = queue.get_nowait()
            if 'shellClientId' not in frame:
                frames.append(frame)
        observed = next(frame for frame in frames if frame['revision'] == duplicate['stateRevision'])
        row = next(row for row in observed['sessions'] if row['id'] == sid)
        assert row['draftAttachments'] == []
        assert next(row for row in row['messages'] if row.get('inputId') == 'first')['delivery']['status'] == 'accepted'
        runtime.release.set()
        original = await asyncio.wait_for(pending, 5)
        assert original['stateRevision'] == duplicate['stateRevision']
        assert original['revision'] == duplicate['revision'] and runtime.sent == ['first']
        assert '_deliveryStateRevision' not in original and '_deliveryStateRevision' not in duplicate
    finally:
        runtime.release.set()
        await asyncio.wait_for(pending, 5)
        service.unsubscribe(queue)
    await client.close()
    restored_runtime = Runtime()
    restored = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                                runtime=restored_runtime, voice=False, background_updates=False)
    client = await authenticated_client(restored)
    await client.post('/api/clients/attach', json={'clientId': 'sender'})
    replay = await command('first', 'conversation.send', args)
    assert replay['duplicate'] and replay['delivery'] == 'accepted'
    assert replay['revision'] == original['revision'] and replay['stateRevision'] == original['stateRevision']
    assert restored_runtime.sent == []


@pytest.mark.parametrize('disposition', ['queued', 'applied', 'next_turn', 'early-applied', 'held', 'unknown'])
async def test_steering_receipt_observes_exact_attachment_consumption(authenticated_client, tmp_path, disposition):
    import base64
    from amplifier_web.execution import ensure_turn

    class SteeringRuntime(Runtime):
        def __init__(self):
            super().__init__()
            self.observed, self.release = asyncio.Event(), asyncio.Event()

        async def steer(self, session, text, input_id, emit):
            self.sent.append(input_id)
            if disposition == 'unknown':
                raise TimeoutError('Controlled lost acknowledgement')
            if disposition == 'early-applied':
                await emit('runtime.steering', {'sessionId': session['id'], 'input_id': input_id,
                    'target_generation_id': 'generation', 'event': 'steering.applied'})
                self.acceptance_revision = service._state['revision']
                self.observed.set()
                await self.release.wait()
                return {'accepted': True, 'disposition': 'queued'}
            return {'accepted': disposition not in {'held', 'unknown'}, 'disposition': disposition}

        async def close(self):
            self.release.set()

    runtime = SteeringRuntime()
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=runtime, voice=False, background_updates=False)
    service = app['service']
    await service.history.close()
    await service.event_log_view.close()
    client = await authenticated_client(app)
    await client.post('/api/clients/attach', json={'clientId': 'sender'})
    headers = {'X-Amplifier-Client': 'sender', 'X-Amplifier-State-Transport': 'delta-v1'}

    async def command(identity, action, args):
        response = await client.post('/api/actions', headers=headers,
                                    json={'id': identity, 'action': action, 'args': args})
        assert response.status == 200, await response.text()
        return await response.json()

    await command('create', 'session.create', {})
    await command('file', 'attachment.add', {'name': 'steered.txt', 'base64': base64.b64encode(b'Exact').decode()})
    with service.clients.bind('sender'):
        session = service._session()
        sid = session['id']
        attachment = deepcopy(service.clients.attachments(session)[0])
        session['status'] = 'working'
        ensure_turn(session, 'original', 'Original task')
    await service.on_runtime_event('runtime.generation', {'sessionId': sid,
        'event': 'generation.started', 'generation_id': 'generation', 'initial_input_id': 'original'})
    with service.clients.bind('sender'):
        queue = service.subscribe()
    args = {'sessionId': sid, 'text': 'Correction', 'expectedGenerationId': 'generation',
            'allowNextTurn': True, 'attachmentIds': [attachment['id']]}
    pending = asyncio.create_task(command('correction', 'conversation.send', args))
    try:
        if disposition == 'early-applied':
            await asyncio.wait_for(runtime.observed.wait(), 5)
            assert not pending.done()
            receipt = await command('correction', 'conversation.send', args)
            assert receipt['duplicate'] and receipt['stateRevision'] == runtime.acceptance_revision
        else:
            receipt = await asyncio.wait_for(asyncio.shield(pending), 5)
        frames = []
        while not queue.empty():
            frame = queue.get_nowait()
            if 'shellClientId' not in frame:
                frames.append(frame)
        positive = disposition not in {'held', 'unknown'}
        durable = json.loads(service.db.execute('SELECT receipt FROM commands WHERE id=?', ('correction',)).fetchone()[0])
        if positive:
            assert receipt['delivery'] == 'accepted' and receipt['stateRevision'] > receipt['revision']
            observed = next(frame for frame in frames if frame['revision'] == receipt['stateRevision'])
            row = next(row for row in observed['sessions'] if row['id'] == sid)
            assert row['draftAttachments'] == []
            sent = next(row for row in row['messages'] if row.get('inputId') == 'correction')
            assert sent['delivery']['status'] == 'accepted' and sent['attachments'] == [attachment]
            assert durable['_deliveryStateRevision'] == receipt['stateRevision']
        else:
            assert receipt['delivery'] == ('failed' if disposition == 'held' else 'unknown')
            assert '_deliveryStateRevision' not in durable
            with service.clients.bind('sender'):
                assert service.clients.attachments(session) == [attachment]
        runtime.release.set()
        original = await asyncio.wait_for(pending, 5)
        duplicate = await command('correction', 'conversation.send', args)
        assert original['stateRevision'] == duplicate['stateRevision'] == receipt['stateRevision']
        assert original['steering']['disposition'] == ('applied' if disposition == 'early-applied' else disposition)
        assert '_deliveryStateRevision' not in original and '_deliveryStateRevision' not in duplicate
        assert runtime.sent == ['correction'] and session['execution']['currentTurnId'] == 'original'
    finally:
        runtime.release.set()
        await asyncio.wait_for(pending, 5)
        service.unsubscribe(queue)


@pytest.mark.parametrize('compact', [False, True])
async def test_noop_layout_receipt_does_not_wait_for_other_client_revision(authenticated_client, tmp_path, compact):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=Runtime(), voice=False, background_updates=False)
    service = app['service']
    await service.history.close()
    await service.event_log_view.close()
    # Startup diagnostics publishes shared state after an asynchronous read.
    # Complete that initial snapshot before observing only client-local changes;
    # its normal background reader remains active throughout the assertions.
    await service.diagnostics.publish()
    client = await authenticated_client(app)
    for identity in ('layout-one', 'layout-two'):
        await client.post('/api/clients/attach', json={'clientId': identity})
    transport = {'X-Amplifier-State-Transport': 'delta-v1'} if compact else {}

    async def layout(identity, width, **extra):
        return await client.post('/api/actions', headers={**transport, 'X-Amplifier-Client': identity},
                                 json={'action': 'view.update', 'args': {'patch': {'navWidth': width}}, **extra})

    await layout('layout-one', 301)
    with service.clients.bind('layout-one'):
        baseline = service.browser_state()
        queue = service.subscribe()
    try:
        await layout('layout-two', 302)
        assert service._state['revision'] > baseline['revision'] and queue.empty()
        revision = service._state['revision']
        receipt = await (await layout('layout-one', 301, id='unchanged-layout')).json()
        assert receipt['accepted'] and receipt['revision'] == revision
        assert 'stateRevision' not in receipt and '_stateIndependent' not in receipt
        assert service._state['revision'] == revision and queue.empty()
        if not compact:
            assert receipt['state']['view']['navWidth'] == 301
        saved = json.loads(service.db.execute('SELECT receipt FROM commands WHERE id=?',
                                             ('unchanged-layout',)).fetchone()[0])
        assert saved['_stateIndependent'] is True

        # A later unrelated change cannot turn an idempotent no-op into a wait.
        await layout('layout-two', 303)
        duplicate = await (await layout('layout-one', 301, id='unchanged-layout')).json()
        assert duplicate['duplicate'] and duplicate['revision'] == revision
        assert 'stateRevision' not in duplicate and '_stateIndependent' not in duplicate
        assert queue.empty()
        # Stale CAS and reused IDs still reject before the no-op shortcut.
        assert (await layout('layout-one', 301, expectedRevision=baseline['revision'])).status == 409
        assert (await layout('layout-one', 302, id='unchanged-layout')).status == 409
        # Actual changes retain their state acknowledgement; caller-supplied
        # markers cannot override the server's validated decision.
        changed = await (await layout('layout-one', 304, _stateIndependent=True)).json()
        assert queue.get_nowait()['view']['navWidth'] == 304
        if compact:
            assert changed['stateRevision'] == changed['revision']
        else:
            assert changed['state']['view']['navWidth'] == 304
        assert '_stateIndependent' not in changed
    finally:
        service.unsubscribe(queue)


@pytest.mark.parametrize('compact', [False, True])
async def test_plan_receipt_has_no_unrelated_state_ack(authenticated_client, tmp_path, compact):
    from amplifier_web.event_log_view import event_path
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=Runtime(), voice=False, background_updates=False)
    service = app['service']
    await service.history.close()
    await service.event_log_view.close()
    made = await service.dispatch('session.create', {'title': 'Exact plan'})
    session = service._session(made['sessionId'])
    identity = session.get('runtimeSessionId') or session['id']
    path = event_path(session, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'event': 'tool:post', 'timestamp': 1, 'data': {
        'session_id': identity, 'tool_name': 'todo', 'tool_call_id': 'exact-plan',
        'result': {'success': True, 'output': {'todos': [
            {'content': 'Retain the actual plan', 'status': 'completed'}]}}}}) + '\n')
    original = path.read_bytes()
    client = await authenticated_client(app)
    headers = {'X-Amplifier-State-Transport': 'delta-v1'} if compact else {}
    # No publication can acknowledge this unrelated private revision. A pure
    # read must return its own result instead of assigning it to that domain.
    service._state['revision'] += 100
    result = await (await client.post('/api/actions', headers=headers, json={
        'action': 'session.plans', 'args': {'ids': [session['id']], 'includeItems': True}})).json()
    assert result['accepted'] and 'stateRevision' not in result
    assert '_stateIndependent' not in result
    plan = result['result']['items'][0]['plan']
    assert (plan['done'], plan['total']) == (1, 1)
    assert plan['items'] == [{'content': 'Retain the actual plan', 'status': 'completed'}]
    assert path.read_bytes() == original and service._session()['id'] == session['id']
    # Mutations still retain their authoritative state acknowledgement, even
    # when the caller tries to supply the internal read-only marker.
    mutation = await (await client.post('/api/actions', headers=headers, json={
        'action': 'view.update', 'args': {'patch': {'navWidth': 301}},
        '_stateIndependent': True})).json()
    if compact:
        assert mutation['stateRevision'] == mutation['revision']
    else:
        assert mutation['state']['view']['navWidth'] == 301
    assert '_stateIndependent' not in mutation
    # The shell does not declare this capability; no wider delegation grant
    # is introduced merely to optimize the ordinary workspace's direct read.
    await client.post('/api/clients/attach', json={'clientId': 'plan-reader'})
    denied = await client.post('/api/actions', headers={**headers, 'X-Amplifier-Client': 'plan-reader'}, json={
        'action': 'shell.command', 'args': {'clientId': 'plan-reader', 'instanceId': 'chats',
            'action': 'session.plans', 'args': {'ids': [session['id']]}}})
    assert denied.status == 403


def apply(previous, patch):
    result = deepcopy(previous)
    for change in patch['changes']:
        parent = result
        for key in change['path'][:-1]:
            parent = parent[key]
        key = change['path'][-1]
        if change.get('remove'):
            del parent[key]
        else:
            parent[key] = deepcopy(change['value'])
    return result


def test_small_changes_do_not_retransmit_large_assets_or_history():
    state = {'revision': 10, 'theme': {'css': 'x' * 140_000},
             'setup': {'catalog': ['model' * 10_000] * 20},
             'sessions': [{'id': str(i), 'messages': [{'id': 'm', 'text': 'words' * 1000}], 'status': 'idle'} for i in range(100)],
             'view': {'canvasControlsExpanded': False}}
    after = deepcopy(state)
    after['revision'] += 1
    after['view']['canvasControlsExpanded'] = True
    after['sessions'][17]['status'] = 'working'
    patch = delta(state, after)
    assert len(json.dumps(patch)) < 400
    assert apply(state, patch) == after
    after['sessions'].pop(3)
    del after['view']['canvasControlsExpanded']
    after['view']['new'] = None
    assert apply(state, delta(state, after)) == after


def test_accounting_ledger_stays_authoritative_but_out_of_browser_projection():
    session = {'id': 's', 'messages': [], 'execution': {'nodes': [], 'turns': [],
        'retiredUsageNodes': [{'id': str(i), 'detail': 'x' * 1000} for i in range(1000)],
        'aggregateUsage': {'inputTokens': 12345, 'cost': 1.25, 'calls': 1000}}}
    browser = project(session)
    assert 'retiredUsageNodes' not in browser['execution']
    assert browser['execution']['aggregateUsage'] == session['execution']['aggregateUsage']
    assert len(session['execution']['retiredUsageNodes']) == 1000
    assert len(json.dumps(browser)) < 1500


async def read_event(response):
    lines = []
    while True:
        line = await asyncio.wait_for(response.content.readline(), 3)
        assert line
        if line == b'\n':
            break
        lines.append(line.decode().strip())
    return next(line[7:] for line in lines if line.startswith('event: ')), json.loads(next(line[6:] for line in lines if line.startswith('data: ')))


async def test_real_http_receipt_delta_reconnect_and_legacy_client(authenticated_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=Runtime(), voice=False, background_updates=False)
    service = app['service']
    await service.history.close()
    await service.event_log_view.close()
    client = await authenticated_client(app)
    await client.post('/api/clients/attach', json={'clientId': 'one'})
    client.session.headers['X-Amplifier-Client'] = 'one'
    stream = await client.get('/api/events?transport=delta-v1')
    kind, baseline = await read_event(stream)
    assert kind == 'state'
    client.session.headers['X-Amplifier-State-Transport'] = 'delta-v1'
    response = await client.post('/api/actions', json={'id': 'width', 'action': 'view.update', 'args': {'patch': {'navWidth': 301}}})
    body = await response.json()
    assert response.status == 200
    assert 'state' not in body and len(json.dumps(body)) < 400
    assert body['hostInstanceId'] == service.instance_id
    while True:
        kind, patch = await read_event(stream)
        if kind == 'shell':
            continue
        assert kind == 'state-delta'
        baseline = apply(baseline, patch)
        if baseline['revision'] >= body['stateRevision']:
            break
    assert baseline['view']['navWidth'] == 301
    assert len(json.dumps(patch)) < 1500
    duplicate = await (await client.post('/api/actions', json={'id': 'width', 'action': 'view.update', 'args': {'patch': {'navWidth': 301}}})).json()
    assert duplicate['duplicate'] and duplicate['revision'] == body['revision']
    stream.close()
    fresh = await client.get('/api/events?transport=delta-v1')
    kind, current = await read_event(fresh)
    assert kind == 'state' and current['view']['navWidth'] == 301
    fresh.close()
    del client.session.headers['X-Amplifier-State-Transport']
    old = await (await client.post('/api/actions', json={'action': 'view.update', 'args': {'patch': {'navWidth': 302}}})).json()
    assert old['state']['view']['navWidth'] == 302
