"""Real AppService HTTP/SSE delivery with finite transport/runtime barriers.

Only the runtime is synthetic. Normal actions, selected-only browser frames,
input publication, command deduplication and passive delivery evidence retain
their production owners. Adversarial receipt controls run AFTER the real action.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import signal
import tempfile
import uuid
from aiohttp import web
import settings_ui_server as fixture


class Barriers:
    def __init__(self):
        self.rows = {}

    def arm(self, payload):
        key = payload['key']
        if key in self.rows:
            raise web.HTTPConflict(text='Barrier keys are single-use')
        if payload['action'] not in {'message.edit', 'runtime.send', 'conversation.send',
                                     'conversation.delivery', 'session.select'}:
            raise web.HTTPBadRequest(text='Unsupported fixture barrier')
        self.rows[key] = {**payload, 'arrived': False, 'released': False,
                          'event': asyncio.Event(), 'receipt': None, 'forge': None}

    async def pause(self, action, payload, receipt=None):
        args = payload.get('args', {})
        row = next((row for row in self.rows.values() if not row['arrived']
                    and row['action'] == action
                    and row.get('sessionId') == (args.get('sessionId') or args.get('id'))
                    and (not row.get('id') or row['id'] == payload.get('id'))), None)
        if row is None:
            return None
        row.update(arrived=True, payload=copy.deepcopy(payload), receipt=receipt)
        await asyncio.wait_for(row['event'].wait(), 45)
        return row

    def snapshot(self):
        return {key: {field: row.get(field) for field in
                      ('action', 'arrived', 'released', 'payload', 'receipt', 'forge')}
                for key, row in self.rows.items()}

    def close(self):
        for row in self.rows.values():
            row['event'].set()


class Runtime(fixture.Runtime):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.checked = []
        self.retried = []
        self.known = set()
        self.cases = {}
        self.barriers = Barriers()
        self.service = None

    async def send(self, session, text, input_id, emit):
        if input_id == 'original-fixture-input':
            raise RuntimeError('Synthetic lost reply')
        saved = self.service._session(session['id'])
        # Observe, do not replace, AppService's saved-input path. Browser checks
        # separately wait for its real published frame before releasing the ACK.
        saved_input = any(row.get('inputId') == input_id for row in saved['messages'])
        command = self.service.db.execute('SELECT receipt FROM commands WHERE id=?',
                                          (input_id,)).fetchone()
        self.sent.append({'sessionId': session['id'], 'inputId': input_id, 'text': text,
                          'savedBeforeInput': saved_input and command is not None})
        await self.barriers.pause('runtime.send', {
            'id': input_id, 'args': {'sessionId': session['id'], 'text': text}})
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
        if self.cases.get(session['id']) == 'lost_ack':
            from amplifier_web.runtime import RuntimeOperationPending
            raise RuntimeOperationPending('send')
        self.known.add(input_id)
        return {'accepted': True}

    async def delivery(self, session, input_id):
        self.checked.append(input_id)
        return 'accepted' if input_id in self.known else 'unknown'

    async def retry(self, session, text, input_id, emit):
        self.retried.append(input_id)
        if input_id in self.known:
            return {'accepted': True, 'duplicate': True}
        self.known.add(input_id)
        self.sent.append({'sessionId': session['id'], 'inputId': input_id, 'text': text})
        await emit('assistant.message', {'sessionId': session['id'], 'inputId': input_id, 'text': 'Recovery succeeded: one synthetic submission received.'})
        await emit('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
        return {'accepted': True}


fixture.Runtime = Runtime


async def main(home):
    app = await fixture.main(home)
    service = app['service']
    service.runtime.service = service
    barriers = service.runtime.barriers
    posts = []
    original_secret = app['session_secret']
    original_instance = service.instance_id
    original_sources = {}
    service.state['updates'].update(items=[], available=0)
    await service.dispatch('session.rename', {'id': service._session()['id'], 'title': 'Delivery recovery fixture'})
    attachment = await service.dispatch('attachment.add', {'name': 'reference.txt', 'base64': 'UmV0YWluIHRoaXMgYXR0YWNobWVudC4='})
    attachment_id = attachment['state']['sessions'][0]['draftAttachments'][0]['id']
    try:
        await service.dispatch('conversation.send', {'text': 'Recover this synthetic message with its attachment.', 'attachmentIds': [attachment_id]}, command_id='original-fixture-input')
    except RuntimeError:
        pass
    @web.middleware
    async def observe_actions(request, handler):
        if request.path != '/api/actions' or request.method != 'POST':
            return await handler(request)
        payload = await request.json()
        if payload.get('action') in {'conversation.send', 'conversation.retry',
                                     'conversation.delivery', 'session.select', 'message.edit'}:
            posts.append(copy.deepcopy(payload))
        # All admission, persistence, passive checks and publication occur in
        # the real handler, before any response is held or corrupted.
        response = await handler(request)
        if payload.get('action') not in {'message.edit', 'conversation.send',
                                         'conversation.delivery', 'session.select'}:
            return response
        receipt = json.loads(response.body) if isinstance(response, web.Response) and response.body else None
        row = await barriers.pause(payload['action'], payload, {
            'status': response.status, 'body': receipt})
        if row is None or not row.get('forge'):
            return response
        forged = copy.deepcopy(receipt)
        kind = row['forge']
        if kind == 'selection':
            # Keep the real navigation target/receipt intact. Extra purported
            # input acceptance on a selection action is not send evidence.
            forged.update(delivery='accepted', inputId=row['inputId'])
            forged['result'] = {'delivery': 'accepted', 'sessionId': row['ownerSessionId'],
                                'inputId': row['inputId']}
        else:
            # Start from real UNKNOWN evidence, not a fabricated positive
            # producer. Each corrupt positive must remain unusable by clients.
            forged['result']['delivery'] = 'accepted'
            if kind == 'wrong_input':
                forged['result']['inputId'] = 'wrong-fixture-input'
            elif kind == 'wrong_session':
                forged['result']['sessionId'] = row['wrongSessionId']
            elif kind == 'wrong_source':
                forged['result']['source']['nativeIdentity'] = 'wrong-native-identity'
            elif kind == 'wrong_host':
                forged['hostInstanceId'] = 'wrong-host-instance'
            elif kind == 'generic_acceptance':
                forged.pop('result', None)
            elif kind == 'refused':
                forged.update(accepted=False, error='Synthetic contradictory refusal', status=409)
            else:
                raise web.HTTPBadRequest(text='Unsupported fixture receipt corruption')
        return web.json_response(forged, status=response.status, headers={
            key: value for key, value in response.headers.items()
            if key.lower() not in {'content-type', 'content-length'}})

    app.middlewares.insert(0, observe_actions)

    async def inspect(request):
        return web.json_response({'sent': service.runtime.sent, 'checked': service.runtime.checked,
                                  'retried': service.runtime.retried, 'posts': posts,
                                  'barriers': barriers.snapshot()})

    async def new_case(request):
        payload = await request.json()
        if payload.get('outcome', 'accept') not in {'accept', 'lost_ack'}:
            raise web.HTTPBadRequest(text='Unsupported runtime outcome')
        ids = []
        for suffix in ('A', 'B'):
            before = {row['id'] for row in service.state['sessions']}
            await service.dispatch('session.create', {
                'title': payload['label'] + ' ' + suffix, 'select': False,
                'workspace': str(home / 'workspace'), 'bundle': 'anchors'})
            session = next(row for row in service.state['sessions'] if row['id'] not in before)
            ids.append(session['id'])
            service.runtime.cases[session['id']] = payload.get('outcome', 'accept')
            original_sources[session['id']] = {key: session.get(key) for key in
                                               ('workspace', 'runtimeSessionId', 'nativeIdentity')}
        return web.json_response({'a': ids[0], 'b': ids[1]})

    async def arm(request):
        barriers.arm(await request.json())
        return web.json_response({'armed': True})

    async def release(request):
        payload = await request.json()
        row = barriers.rows[payload['key']]
        if not row['arrived'] or row['released']:
            raise web.HTTPConflict(text='Release only an arrived, unreleased barrier')
        row.update({key: payload[key] for key in
                    ('forge', 'ownerSessionId', 'inputId', 'wrongSessionId') if key in payload})
        row['released'] = True
        row['event'].set()
        return web.json_response({'released': True})

    async def change_source(request):
        payload = await request.json()
        kind, sid = payload['kind'], payload['sessionId']
        async with service.lock:
            session = service._session(sid)
            if kind == 'host':
                # Finite generation-boundary simulation, not a claim that the
                # Python process or native worker was actually restarted.
                service.instance_id = str(uuid.uuid4())
            elif kind == 'account':
                # Reattachment recomputes the real authenticated namespace.
                app['session_secret'] = 'synthetic-new-account-secret'
            elif kind == 'native':
                session['nativeIdentity'] = str(uuid.uuid4())
                session.pop('runtimeSessionId', None)
            elif kind == 'workspace':
                other = home / 'other-workspace'
                other.mkdir(exist_ok=True)
                session['workspace'] = str(other)
            elif kind == 'restore':
                service.instance_id = original_instance
                app['session_secret'] = original_secret
                for key, value in original_sources[sid].items():
                    if value is None:
                        session.pop(key, None)
                    else:
                        session[key] = value
            else:
                raise web.HTTPBadRequest(text='Unsupported source transition')
            service._publish_changes(sessions={sid})
        return web.json_response({'changed': kind})

    async def confirm_native(request):
        payload = await request.json()
        # The next real passive check owns reconciliation/publication.
        # This endpoint never calls send/retry.
        service.runtime.known.add(payload['inputId'])
        return web.json_response({'evidenceAvailable': True})

    app.router.add_get('/fixture', inspect)
    app.router.add_post('/fixture/case', new_case)
    app.router.add_post('/fixture/barrier', arm)
    app.router.add_post('/fixture/release', release)
    app.router.add_post('/fixture/source', change_source)
    app.router.add_post('/fixture/native-evidence', confirm_native)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    app['allowed_origins'] = app['allowed_origins'] | {f'http://127.0.0.1:{port}'}
    print(json.dumps({'url': f'http://127.0.0.1:{port}'}), flush=True)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGTERM, signal.SIGINT) if os.name == 'posix' else ()
    for signum in signals:
        loop.add_signal_handler(signum, stopped.set)
    try:
        await stopped.wait()
    finally:
        barriers.close()
        try:
            await runner.cleanup()
        finally:
            for signum in signals:
                loop.remove_signal_handler(signum)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-delivery-ui-') as directory:
        asyncio.run(main(Path(directory)))
