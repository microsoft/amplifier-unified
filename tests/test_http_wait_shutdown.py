"""Real HTTP drain owns passive waits, not the effects they observe.

No browser, provider, process signal, or replacement service dispatch verdict.
The baseline discriminator links a known HTTP request to its actual Event wait.
"""
import asyncio
import json
import time
from contextlib import asynccontextmanager

import pytest
from aiohttp import ClientConnectionError, ClientSession, web
from aiohttp.web_protocol import RequestHandler

from amplifier_web.server import create_app
from amplifier_web.service import AppService
from test_service import Runtime


WAIT_ACTIONS = ('workspace.work.wait', 'assignment.tasks.wait', 'coordination.wait',
                'operations.wait', 'recall.wait')


async def until(predicate, timeout=3):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.001)


def chain(task):
    result, current = [], task.get_coro()
    while current is not None and len(result) < 24:
        code = getattr(current, 'cr_code', None) or getattr(current, 'gi_code', None)
        if code:
            result.append(code.co_qualname)
        current = getattr(current, 'cr_await', None) or getattr(current, 'gi_yieldfrom', None)
    return result


@asynccontextmanager
async def host(tmp_path, monkeypatch, runtime=None):
    app = await create_app(tmp_path / 'data', workspace=tmp_path, runtime=runtime or Runtime(),
                           voice=False, background_updates=False, preload_providers=False)
    service = app['service']
    await service.dispatch('session.create', {'title': 'Synthetic shutdown scope'}, command_id='setup')
    records = {}
    dispatch = service.dispatch

    async def observed(action, *args, **kwargs):
        identity = kwargs.get('command_id')
        if identity:
            records[identity] = asyncio.current_task()
        return await dispatch(action, *args, **kwargs)

    monkeypatch.setattr(service, 'dispatch', observed)
    runner = web.AppRunner(app)  # Production default: no shorter aiohttp timeout.
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    client = ClientSession(headers={'Authorization': 'Bearer ' + app['control_token']})
    state = {'app': app, 'service': service, 'runner': runner, 'port': port,
             'url': f'http://127.0.0.1:{port}/api/actions', 'client': client,
             'records': records, 'requests': [], 'cleanup': None}
    try:
        yield state
    finally:
        # Only test recovery after an assertion: cancel the exact captured reads,
        # never arbitrary service tasks or the accepted effectful command.
        if runtime and hasattr(runtime, 'release'):
            runtime.release.set()
        for identity, task in records.items():
            if identity.startswith('read-') and not task.done():
                task.cancel()
        await asyncio.gather(*(task for identity, task in records.items()
                               if identity.startswith('read-')), return_exceptions=True)
        await asyncio.gather(*state['requests'], return_exceptions=True)
        await client.close()
        if state['cleanup'] is None:
            state['cleanup'] = asyncio.create_task(runner.cleanup())
        await asyncio.wait_for(asyncio.shield(state['cleanup']), 10)


async def post(h, action, args, identity):
    response = await h['client'].post(h['url'], json={'action': action, 'args': args, 'id': identity})
    return response.status, await response.json()


async def wait_args(h, action):
    service = h['service']
    sid = service.state['selectedSessionId']
    if action == 'workspace.work.wait':
        wid = next(row['id'] for row in service.state['workspaces'] if row['path'] == str(service.default_workspace))
        args = {'workspaceId': wid}
        result = await service.dispatch('workspace.work.list', args)
        args['afterVersion'] = result['result']['observationVersion']
    elif action == 'assignment.tasks.wait':
        args = {'sessionId': sid}
        result = await service.dispatch('assignment.tasks.list', args)
        args['afterVersion'] = result['result']['version']
    elif action == 'coordination.wait':
        result = await service.dispatch(action, {'targets': [{'sessionId': sid}], 'waitMs': 0})
        args = {'targets': [{'sessionId': sid, 'afterCursor': result['result']['targets'][0]['nextCursor']}]}
    elif action == 'operations.wait':
        # Existing journal observation, not a real subprocess or mutated verdict.
        service.operations.journal.ingest(sid, 'synthetic-runtime', {
            'schemaVersion': 1, 'eventId': 'observed:1', 'sequence': 1,
            'operationId': 'observed', 'ownerId': 'synthetic-owner', 'source': 'tool-bash',
            'kind': 'process', 'phase': 'started', 'at': 1})
        args = {'sessionId': sid, 'id': 'observed'}
        result = await service.dispatch('operations.read', args)
        args['afterRevision'] = result['result']['revision']
    else:
        args = {'sessionId': sid, 'afterRevision': service.recall.state['revision']}
    return {**args, 'waitMs': 30000}


async def pending_read(h, action='workspace.work.wait', identity='read-pending'):
    args = await wait_args(h, action)
    request = asyncio.create_task(post(h, action, args, identity))
    h['requests'].append(request)
    await until(lambda: identity in h['records'] or request.done())
    if request.done():
        pytest.fail(f'Known read never entered its wait: {await request}')
    task = h['records'][identity]
    await until(lambda: 'Event.wait' in chain(task) or task.done())
    assert not task.done(), (action, await request)
    assert task in h['service'].tasks
    return request, task, args


@pytest.mark.parametrize('action', WAIT_ACTIONS)
async def test_http_read_wait_drains_within_ten_seconds(tmp_path, monkeypatch, action):
    async with host(tmp_path, monkeypatch) as h:
        request, task, _ = await pending_read(h, action)
        before = chain(task)
        started = time.monotonic()
        h['cleanup'] = asyncio.create_task(h['runner'].cleanup())
        done, _ = await asyncio.wait({h['cleanup']}, timeout=10)
        duration = time.monotonic() - started
        print(json.dumps({'action': action, 'knownDispatch': id(task), 'awaitChain': before,
                          'drainSeconds': duration, 'drained': bool(done),
                          'sameDispatchPending': not task.done()}))
        # Deliberately fails on the old server for the measured ownership defect,
        # not a missing helper/attribute, external process, or fabricated result.
        assert done, 'READ_WAIT_BLOCKED_HTTP_DRAIN_10S'
        await h['cleanup']
        status, result = await request
        assert (status, result['accepted'], result['code']) == (503, False, 'host_shutdown')
        assert task.cancelled() and task not in h['service'].tasks
        assert duration < 10


@pytest.mark.parametrize('action', WAIT_ACTIONS)
async def test_shutdown_fence_refuses_late_read_without_dispatch(tmp_path, monkeypatch, action):
    async with host(tmp_path, monkeypatch) as h:
        args = await wait_args(h, action)
        await h['app'].shutdown()  # Listener remains for a deterministic late arrival.
        status, body = await post(h, action, args, 'read-too-late')
        assert (status, body['accepted'], body['code']) == (503, False, 'host_shutdown')
        assert 'read-too-late' not in h['records']


async def test_body_crossing_shutdown_fence_cannot_open_wait(tmp_path, monkeypatch):
    async with host(tmp_path, monkeypatch) as h:
        args = await wait_args(h, 'workspace.work.wait')
        entered = asyncio.Event()
        original_json = web.Request.json

        async def observe_body(request, *args, **kwargs):
            if request.headers.get('X-Test-Body') == 'partial':
                entered.set()
            return await original_json(request, *args, **kwargs)

        monkeypatch.setattr(web.Request, 'json', observe_body)
        reader, writer = await asyncio.open_connection('127.0.0.1', h['port'])
        body = json.dumps({'action': 'workspace.work.wait', 'args': args, 'id': 'read-late-body'}).encode()
        headers = (f'POST /api/actions HTTP/1.1\r\nHost: 127.0.0.1:{h["port"]}\r\n'
                   f'Authorization: Bearer {h["app"]["control_token"]}\r\nX-Test-Body: partial\r\n'
                   f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode()
        try:
            writer.write(headers + body[:1]); await writer.drain()
            await asyncio.wait_for(entered.wait(), 3)
            await h['app'].shutdown()
            writer.write(body[1:]); await writer.drain()
            response = await asyncio.wait_for(reader.read(), 3)
            assert b'503 Service Unavailable' in response and b'"accepted": false' in response
            assert b'host_shutdown' in response
            assert 'read-late-body' not in h['records']
        finally:
            writer.close(); await writer.wait_closed()


@pytest.mark.parametrize('order', ['completion-first', 'same-turn', 'shutdown-first'])
async def test_normal_completion_and_cancel_races_keep_honest_receipts(tmp_path, monkeypatch, order):
    async with host(tmp_path, monkeypatch) as h:
        # A zero wait is an immediate observation, not a timeout (ChangeSignal).
        args = await wait_args(h, 'workspace.work.wait')
        status, body = await post(h, 'workspace.work.wait', {**args, 'waitMs': 0}, 'read-immediate')
        assert status == 200 and body['accepted'] and not body['timedOut'] and not body['changed']
        # A genuine bounded wait still expires normally without cancellation.
        status, body = await post(h, 'workspace.work.wait', {**args, 'waitMs': 10}, 'read-natural')
        assert status == 200 and body['accepted'] and body['timedOut'] and not body['changed']
        await until(lambda: h['records']['read-natural'] not in h['service'].tasks)
        assert not h['records']['read-natural'].cancelled()
        request, task, args = await pending_read(h, identity='read-changed')
        # Use the real durable version owner and rotating event, not a replaced
        # dispatch result or cancellation-resistant synthetic sleep.
        workspace = next(row for row in h['service'].state['workspaces'] if row['id'] == args['workspaceId'])
        if order != 'shutdown-first':
            h['service'].workspace_work.advance(workspace)
            h['service'].db.commit()
            h['service'].coordination.notify()
        if order == 'completion-first':
            await asyncio.wait_for(asyncio.shield(task), 3)
        await h['app'].shutdown()
        status, result = await request
        assert status in (200, 503)
        if order == 'completion-first':
            assert status == 200
        if order == 'shutdown-first':
            assert status == 503
        if status == 200:
            assert result['accepted'] and result['changed'] and not result['timedOut']
        else:
            assert result['accepted'] is False and result['code'] == 'host_shutdown'
        assert task.done() and task not in h['service'].tasks
        # Repeated shutdown cannot recancel natural completions or leak a waiter.
        await h['app'].shutdown()
        assert not h['records']['read-natural'].cancelled()


async def test_nonshutdown_read_cancellation_is_not_a_success(tmp_path, monkeypatch):
    async with host(tmp_path, monkeypatch) as h:
        request, task, _ = await pending_read(h)
        task.cancel()
        with pytest.raises(ClientConnectionError):
            await request
        await until(lambda: task not in h['service'].tasks)
        assert task.cancelled() and not h['service'].closed


class GatedRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.cancelled = False

    async def send(self, session, text, input_id, emit):
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        await super().send(session, text, input_id, emit)


@pytest.mark.parametrize('disconnect', [False, True])
async def test_accepted_effect_stays_shielded_during_read_wait_shutdown(tmp_path, monkeypatch, disconnect):
    runtime = GatedRuntime()
    async with host(tmp_path, monkeypatch, runtime) as h:
        request, read_task, read_args = await pending_read(h)
        sid = h['service'].state['selectedSessionId']
        args = {'sessionId': sid, 'text': 'Exactly one durable synthetic input'}
        observed, stages = {}, []
        disconnected = asyncio.Event()

        def stage(name, **detail):
            task = h['records'].get('effect-once')
            row = {'stage': name, 'at': time.monotonic(),
                   'gateOpen': runtime.release.is_set(),
                   'effectDone': task.done() if task else None,
                   'effectCancelling': task.cancelling() if task else None,
                   'runtimeCancelled': runtime.cancelled, **detail}
            stages.append(row)
            return row

        original_json = web.Request.json

        async def observe_request(http_request, *values, **options):
            payload = await original_json(http_request, *values, **options)
            if payload.get('id') == 'effect-once':
                # Bind the actual HTTP handler/protocol, not the client future.
                observed['protocol'] = http_request.protocol
                observed['request_task'] = asyncio.current_task()
                stage('effect_request_parsed', protocol=id(http_request.protocol),
                      requestTask=id(observed['request_task']))
            return payload

        original_lost = RequestHandler.connection_lost

        def observe_disconnect(protocol, error):
            result = original_lost(protocol, error)
            if protocol is observed.get('protocol'):
                stage('server_connection_lost', protocol=id(protocol),
                      transportGone=protocol.transport is None)
                disconnected.set()
            return result

        original_shutdown = RequestHandler.shutdown

        async def observe_request_shutdown(protocol, *values, **options):
            if protocol is observed.get('protocol'):
                observed['drain_task'] = asyncio.current_task()
                stage('effect_request_shutdown_enter', protocol=id(protocol),
                      drainTask=id(observed['drain_task']))
            return await original_shutdown(protocol, *values, **options)

        server = h['runner'].server
        original_drain = server.shutdown

        async def observe_http_drain(*values, **options):
            stage('http_drain_enter')
            try:
                return await original_drain(*values, **options)
            finally:
                stage('http_drain_exit')

        original_close = h['service'].close

        async def observe_service_close():
            observed['service_close_entered'] = True
            stage('service_close_enter')
            return await original_close()

        # All observers delegate unchanged; none suspends/reorders cleanup or
        # opens the gate. The real methods choose their wait/cancellation policy.
        monkeypatch.setattr(web.Request, 'json', observe_request)
        monkeypatch.setattr(RequestHandler, 'connection_lost', observe_disconnect)
        monkeypatch.setattr(RequestHandler, 'shutdown', observe_request_shutdown)
        monkeypatch.setattr(server, 'shutdown', observe_http_drain)
        monkeypatch.setattr(h['service'], 'close', observe_service_close)
        # Separate connection so the read and effect are genuinely concurrent.
        effect_client = ClientSession(headers=h['client'].headers)
        effect = asyncio.create_task(effect_client.post(h['url'], json={
            'action': 'conversation.send', 'args': args, 'id': 'effect-once'}))
        try:
            await asyncio.wait_for(runtime.entered.wait(), 3)
            stored = h['service'].db.execute('SELECT receipt FROM commands WHERE id=?', ('effect-once',)).fetchone()
            assert stored and json.loads(stored[0])['accepted'] is True
            effect_task = h['records']['effect-once']
            assert not effect_task.done()
            stage('durable_effect_receipt_observed', effectDispatch=id(effect_task),
                  requestTask=id(observed['request_task']))
            effect_task.add_done_callback(lambda task: stage(
                'effect_dispatch_settled', cancelled=task.cancelled()))
            assert observed['request_task'] is not effect_task
            if disconnect:
                await effect_client.close()
                await asyncio.gather(effect, return_exceptions=True)
                await asyncio.wait_for(disconnected.wait(), 3)
                assert observed['protocol'].transport is None
                assert not effect_task.cancelled() and not runtime.cancelled
            await h['app'].shutdown()
            stage('on_shutdown_returned', passiveCancelled=read_task.cancelled(),
                  passiveDone=read_task.done())
            # This assertion is specifically about on_shutdown's cancellation
            # list, before any later AppService.close policy is reached.
            assert not effect_task.done() and not effect_task.cancelling()
            assert not runtime.cancelled and not runtime.release.is_set()
            assert not observed.get('service_close_entered')

            # This same two-case effect oracle is runnable against the baseline.
            # Baseline leaves the passive read alive; settle it by a real version
            # change so it cannot mask whether the EFFECT is awaited by drain.
            # The five drain tests above still require cancellation/503; this
            # explicit stimulus does not waive their passive-wait falsifier.
            if not read_task.done():
                workspace = next(row for row in h['service'].state['workspaces']
                                 if row['id'] == read_args['workspaceId'])
                h['service'].workspace_work.advance(workspace)
                h['service'].db.commit()
                h['service'].coordination.notify()
                stage('passive_read_released_by_version_change')
            status, result = await asyncio.wait_for(request, 3)
            if read_task.cancelled():
                assert (status, result['accepted'], result['code']) == (503, False, 'host_shutdown')
            else:
                assert status == 200 and result['accepted'] and result['changed'] and not result['timedOut']
            assert read_task.done() and not effect_task.done()

            # Observe the actual RequestHandler.shutdown suspension for THIS
            # protocol, not merely runner.cleanup being scheduled for one turn.
            # aiohttp's default disconnect policy must be measured, not assumed.
            def effect_drain_waiting():
                protocol = observed['protocol']
                drain_task = observed.get('drain_task')
                waiter = protocol._handler_waiter
                return bool(drain_task and not drain_task.done()
                            and protocol._request_in_progress
                            and waiter is not None and not waiter.done()
                            and getattr(drain_task, '_fut_waiter', None) is waiter
                            and 'RequestHandler.shutdown' in chain(drain_task))

            h['cleanup'] = asyncio.create_task(h['runner'].cleanup())
            await until(lambda: effect_drain_waiting()
                        or observed.get('service_close_entered')
                        or effect_task.done() or effect_task.cancelling()
                        or h['cleanup'].done())
            stage('pre_release_boundary', exactEffectDrainWait=effect_drain_waiting(),
                  serviceCloseEntered=bool(observed.get('service_close_entered')),
                  requestPending=not observed['request_task'].done(),
                  serverDisconnectObserved=disconnected.is_set(),
                  handlerWaiter=id(observed['protocol']._handler_waiter),
                  drainAwaitChain=chain(observed['drain_task']) if observed.get('drain_task') else [])
            # An early transition to service.close/cancellation is counterevidence,
            # not permission to release first and call the effect durable. Run
            # this identical oracle on baseline before attributing such a failure.
            assert not observed.get('service_close_entered'), 'SERVICE_CLOSE_BEFORE_EFFECT_DRAIN'
            assert not effect_task.done() and not effect_task.cancelling() and not runtime.cancelled
            assert not observed['request_task'].done() and effect_drain_waiting(), 'EFFECT_NOT_OWNED_BY_HTTP_DRAIN'
            assert not runtime.release.is_set()
            if disconnect:
                assert disconnected.is_set()
            stage('effect_gate_release')
            runtime.release.set()
            receipt = await asyncio.wait_for(asyncio.shield(effect_task), 3)
            assert receipt['accepted'] and receipt['delivery'] == 'accepted'
            if not disconnect:
                response = await effect
                assert response.status == 200 and (await response.json())['accepted']
            await asyncio.wait_for(asyncio.shield(h['cleanup']), 10)
            assert not runtime.cancelled and len(runtime.sent) == 1
        finally:
            stage('test_finally_before_gate_release',
                  serviceCloseEntered=bool(observed.get('service_close_entered')))
            runtime.release.set()
            await effect_client.close()
            await asyncio.gather(effect, return_exceptions=True)
            print(json.dumps({'effectDisconnect': disconnect,
                              'aiohttpHandlerCancellation': server.handler_cancellation,
                              'stages': stages}))
    # Reopen only the synthetic store: exact retry returns durable receipt, no send.
    restored = AppService(tmp_path / 'data', Runtime(), workspace=tmp_path)
    try:
        result = await restored.dispatch('conversation.send', args, command_id='effect-once')
        assert result['duplicate'] and result['accepted'] and result['delivery'] == 'accepted'
        assert restored.runtime.sent == []
        assert restored.db.execute('SELECT count(*) FROM commands WHERE id=?', ('effect-once',)).fetchone()[0] == 1
    finally:
        await restored.close()


async def test_wait_auth_validation_and_scope_remain_enforced(tmp_path, monkeypatch):
    async with host(tmp_path, monkeypatch) as h:
        args = await wait_args(h, 'workspace.work.wait')
        response = await h['client'].post(h['url'], headers={'Origin': 'https://untrusted.example'},
            json={'action': 'workspace.work.wait', 'args': args})
        assert response.status == 403
        status, result = await post(h, 'workspace.work.wait', {**args, 'waitMs': 60001}, 'read-invalid')
        assert status == 400 and result['accepted'] is False
        status, result = await post(h, 'workspace.work.wait', {**args, 'workspaceId': 'missing'}, 'read-wrong-scope')
        assert status >= 400 and result['accepted'] is False
        status, result = await post(h, 'invented.wait', {}, 'read-invented')
        assert status == 404 and result['accepted'] is False
        await h['app'].shutdown()
        status, result = await post(h, 'invented.wait', {}, 'read-still-invented')
        assert status == 404  # A suffix never opts a command into cancellation.
