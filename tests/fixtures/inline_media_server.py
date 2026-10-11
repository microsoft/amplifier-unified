"""Isolated synthetic host; source and installed modes never share import precedence."""
import asyncio
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
MODE = os.environ.get('INLINE_MEDIA_MODE', 'source')
if MODE not in {'source', 'installed'}:
    raise ValueError('INLINE_MEDIA_MODE must be source or installed')
if MODE == 'source':
    sys.path.insert(0, str(ROOT))
from aiohttp import web
from amplifier_web import auth
from amplifier_web.server import create_app
import amplifier_web

# The helper is loaded by filename, not tests/ROOT sys.path. Installed mode
# must exercise installed amplifier_web even when this fixture lives in source.
spec = importlib.util.spec_from_file_location('inline_media_samples', Path(__file__).with_name('inline_media_samples.py'))
samples_helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(samples_helper)
if MODE == 'installed' and Path(amplifier_web.__file__).resolve().is_relative_to(ROOT / 'amplifier_web'):
    raise AssertionError('Installed mode resolved source amplifier_web')

COUNTERS = Counter()
ERRORS = []


def forbidden(kind):
    COUNTERS['unexpected:' + kind] += 1
    ERRORS.append('Unexpected ' + kind)
    raise AssertionError('Unexpected ' + kind)


def isolate_probes():
    # Install guards BEFORE create_app can schedule any producer. Runtime mocks
    # alone do not prevent independent defaults/catalog/naming SDK probes.
    from amplifier_web import draft_defaults, automatic_naming, naming_backfill
    from amplifier_web.setup import SetupManager
    from amplifier_web.management import Management

    async def defaults(*args, **kwargs):
        COUNTERS['intercept:defaults'] += 1
        return {'bundle': 'fixture', 'providers': [], 'catalogs': {}, 'effective': {}}

    async def naming(*args, **kwargs):
        COUNTERS['intercept:naming'] += 1

    async def setup(self, action, args):
        COUNTERS['intercept:' + action] += 1
        if action == 'providers.list':
            return {'providers': []}
        return forbidden('provider-operation:' + action)

    original = Management.perform

    async def management(self, action, args, command_id=None):
        if action == 'runtime.control' and args.get('operation') == 'configuration.catalog':
            COUNTERS['intercept:catalog'] += 1
            result = {'phase': 'ready', 'providers': [], 'models': [], 'effective': {}, 'catalogs': {}}
            async with self.service.lock:
                sid = args.get('sessionId') or self.service._session()['id']
                self.service.state.setdefault('runtimeControl', {}).setdefault(sid, {})['configuration.catalog'] = result
                self.service._publish_changes(sessions={sid})
            return result
        return await original(self, action, args, command_id)

    draft_defaults.resolve_defaults = defaults
    automatic_naming.AutomaticNaming.schedule = naming
    naming_backfill.NamingBackfill.start = lambda *args, **kwargs: forbidden('naming-start')
    SetupManager.perform = setup
    SetupManager.provider_rows = lambda *args, **kwargs: []
    SetupManager.cached_probe = lambda *args, **kwargs: forbidden('provider-probe')
    Management.perform = management

    async def subprocess_async(*args, **kwargs):
        return forbidden('subprocess')

    asyncio.create_subprocess_exec = subprocess_async
    asyncio.create_subprocess_shell = subprocess_async
    subprocess.Popen = lambda *args, **kwargs: forbidden('subprocess')


class NoModel:
    async def start(self, *args, **kwargs):
        return forbidden('runtime-start')

    async def send(self, *args, **kwargs):
        return forbidden('runtime-send')

    async def control(self, *args, **kwargs):
        return forbidden('runtime-control')

    async def stop(self, *args, **kwargs):
        COUNTERS['runtime-stop'] += 1

    async def close(self):
        COUNTERS['runtime-close'] += 1


async def main():
    with tempfile.TemporaryDirectory(prefix='unified-inline-media-') as temporary:
        root = Path(temporary)
        os.environ.update(AMPLIFIER_HOME=str(root / 'native'), AMPLIFIER_WEB_HOME=str(root / 'app'),
                          AMPLIFIER_SESSION_STATE_HOME=str(root / 'locks'),
                          AMPLIFIER_UNIFIED_IMPORT_HOME=str(root / 'legacy'))
        isolate_probes()
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda loop, context: ERRORS.append(str(context.get('exception') or context.get('message'))))
        auth.authenticate_pam = lambda user, password: (user, password) == ('media-fixture', 'fixture-password')
        app = await create_app(root / 'app', workspace=root, runtime=NoModel(), voice=False,
                               preload_providers=False, background_updates=False)
        await serve(root, app)
    print(json.dumps({'phase': 'fixture-process', 'temporaryRemoved': not root.exists()}), flush=True)


async def serve(root, app):
    try:
        service = app['service']
        app['control_token'] = 'fixture-inline-media'
        await service.dispatch('view.update', {'patch': {'setupJourney': 'done'}})
        await service.dispatch('session.create', {'title': 'Inline media fixture'})
        sid = service._session()['id']
        # A real human input row is committed without running a model. The
        # assistant is then allocated by the ordinary runtime event boundary.
        async with service.lock:
            service._message(service._session(sid), 'user',
                'Earlier synthetic reading position.\n\n' * 45 + 'Keep this unsent-test conversation.', 'chat')
            service._publish_changes(sessions={sid})
        human_id = service._session(sid)['messages'][-1]['id']
        stale_generation = 'fixture-previous-generation'
        await service.on_runtime_event('runtime.generation', {
            'sessionId': sid, 'rootSessionId': sid, 'event': 'generation.started',
            'generation_id': stale_generation})
        await service.on_runtime_event('runtime.generation', {
            'sessionId': sid, 'rootSessionId': sid, 'event': 'generation.started',
            'generation_id': 'fixture-commentary'})
        await service.on_runtime_event('assistant.message', {
            'sessionId': sid, 'text': 'Saved media belongs inside this assistant article.',
            'generationId': 'fixture-commentary',
        })
        message_id = service._session()['messages'][-1]['id']
        await service.dispatch('session.create', {'title': 'Synthetic foreign root'})
        foreign_id = service._session()['id']
        await service.on_runtime_event('assistant.message', {
            'sessionId': foreign_id, 'text': 'A different root owns this assistant message.'})
        foreign_message = service._session(foreign_id)['messages'][-1]['id']
        # A native child is classified by the normal history owner from its
        # canonical ancestry metadata, never by assigning a test-only UI role.
        from amplifier_web.host.storage import SessionStore
        child_native = uuid.uuid4().hex
        SessionStore.for_app(service.data_dir, root).save(child_native, [
            {'role': 'user', 'content': 'Synthetic child input.'},
            {'role': 'assistant', 'content': 'Synthetic child saved response.'},
        ], {'name': 'Synthetic native child', 'parent_session_id': sid})
        await service.history.refresh()
        child = next(row for row in service.state['sessions'] if row.get('nativeIdentity') == child_native)
        if child.get('sessionKind') != 'worker':
            raise AssertionError('Native child was not classified by production history discovery')
        await service.dispatch('session.select', {'id': sid})
        normal, _ = samples_helper.animation()
        partial, expected = samples_helper.animation(partial=True)
        large, _ = samples_helper.animation(1280, 720, 26, target_bytes=19_100_000)
        samples = {'gif': normal, 'timing': normal, 'partial': partial, 'large': large}
        for name, variable in [('gif', 'INLINE_MEDIA_GIF'), ('mp4', 'INLINE_MEDIA_MP4'), ('large', 'INLINE_MEDIA_LARGE_GIF')]:
            if os.environ.get(variable):
                samples[name] = Path(os.environ[variable]).read_bytes()
        for name, data in samples.items():
            (root / name).write_bytes(data)
        png = samples_helper.png()
        (root / 'page.png').write_bytes(png)
        image_receipt = {'schema': 'amplifier.image.receipt.v1', 'status': 'completed',
            'requestId': 'synthetic-png', 'requestHash': 'a' * 64, 'backend': 'fixture', 'model': 'fixture',
            'operation': 'generate', 'inputs': [], 'artifact': {'path': 'page.png',
                'sha256': hashlib.sha256(png).hexdigest(), 'bytes': len(png),
                'mimeType': 'image/png', 'width': 4, 'height': 2, 'mode': 'RGB'}}
        (root / 'png-receipt.json').write_text(json.dumps(image_receipt))
        png_record = (await service.dispatch('outputs.attachImage', {'sessionId': sid, 'messageId': human_id,
            'title': 'Synthetic PNG gallery', 'receiptPath': 'png-receipt.json'}))['result']
        receipts = []
        attached = {}

        def private(request):
            if request.headers.get('X-Inline-Media-Fixture') != 'synthetic-control':
                raise web.HTTPForbidden(text='Synthetic fixture control required')

        async def agent(values, *, principal=sid, generation='fixture-commentary'):
            # Exact host transport context, not caller arguments that assert a
            # role. PRINCIPAL/BINDING are installed by the actual app bridge.
            return await service.app_bridge('dispatch', {'action': 'outputs.attachMedia', 'args': values,
                '_runtimeSessionId': principal, '_generationId': generation}, sid)

        async def control(request):
            private(request)
            args = await request.json()
            name = args.get('name', 'gif')
            if name not in samples:
                raise web.HTTPBadRequest(text='Requested verification fixture was not supplied')
            values = {
                'sessionId': sid, 'messageId': message_id, 'title': 'Synthetic/public ' + name,
                'path': name, 'expectedSha256': hashlib.sha256(samples[name]).hexdigest(),
            }
            instance = args.get('instance', 0)
            if type(instance) is not int or not 0 <= instance <= 2:
                raise web.HTTPBadRequest(text='Choose one of three fixture instances')
            key = name + ':' + str(instance)
            if key in attached:
                return web.json_response({'accepted': True, 'result': attached[key]})
            result = await agent(values)
            if result.get('accepted') is not True:
                raise AssertionError('Root media attach was not accepted')
            result = {key: result[key] for key in ('accepted', 'result')}
            attached[key] = result['result']
            receipts.append({'phase': 'attach', 'name': name, 'receipt': result})
            return web.json_response(result)

        async def matrix(request):
            private(request)
            from amplifier_web.service import AppError
            values = {'sessionId': sid, 'messageId': message_id, 'title': 'Authority matrix',
                'path': 'gif', 'expectedSha256': hashlib.sha256(samples['gif']).hexdigest()}
            results = []
            for label, overrides, principal, generation in [
                ('human-role', {'messageId': human_id}, sid, 'fixture-commentary'),
                ('child', {}, child_native, 'fixture-commentary'),
                ('foreign', {'sessionId': foreign_id, 'messageId': foreign_message}, sid, 'fixture-commentary'),
                ('stale', {}, sid, stale_generation),
            ]:
                before = service.db.execute('SELECT COUNT(*) FROM output_records').fetchone()[0]
                try:
                    result = await agent({**values, **overrides}, principal=principal, generation=generation)
                    rejected = result.get('accepted') is False
                    detail = result.get('error', '')
                except AppError as error:
                    rejected, detail = True, str(error)
                after = service.db.execute('SELECT COUNT(*) FROM output_records').fetchone()[0]
                results.append({'case': label, 'rejected': rejected, 'unchanged': before == after, 'detail': detail})
                if not rejected or before != after:
                    raise AssertionError('Attachment authority matrix failed: ' + label)
            receipts.append({'phase': 'authority', 'cases': results})
            return web.json_response({'cases': results})

        async def mutate(request):
            private(request)
            args = await request.json()
            name = args['name']
            if args['operation'] == 'remove-source':
                (root / name).unlink(missing_ok=True)
                result = {'removed': not (root / name).exists()}
            elif args['operation'] == 'unlink':
                record = attached[name + ':' + str(args.get('instance', 0))]
                result = await service.dispatch('outputs.unlink', {'sessionId': sid,
                    'id': record['id'], 'expectedRevision': record['revision']})
                result = {key: result[key] for key in ('accepted', 'result')}
            else:
                raise web.HTTPBadRequest(text='Unknown fixture operation')
            receipts.append({'phase': args['operation'], 'name': name, 'receipt': result})
            return web.json_response(result)

        def evidence():
            # Public output contains no temp path, product credentials or model
            # input. Linux RSS is a reading, not a native-decoder heap guarantee.
            rss = None
            try:
                rss = next(int(line.split()[1]) * 1024 for line in Path('/proc/self/status').read_text().splitlines()
                           if line.startswith('VmRSS:'))
            except (OSError, StopIteration):
                pass
            return {'mode': MODE, 'ids': {'sessionId': sid, 'messageId': message_id, 'humanId': human_id,
                                        'childNativeId': child_native, 'foreignId': foreign_id},
                    'counters': dict(COUNTERS), 'errors': ERRORS, 'receipts': receipts, 'rssBytes': rss,
                    'clientCount': len(service.clients.records), 'streamCount': len(service.queue_clients)}

        async def inspect(request):
            private(request)
            return web.json_response(evidence())

        stopped = asyncio.Event()

        async def shutdown(request):
            private(request)
            stopped.set()
            return web.json_response({'shutdownRequested': True})

        app.router.add_post('/api/fixture/media', control)
        app.router.add_post('/api/fixture/matrix', matrix)
        app.router.add_post('/api/fixture/mutate', mutate)
        app.router.add_get('/api/fixture/evidence', inspect)
        app.router.add_post('/api/fixture/shutdown', shutdown)
        runner = web.AppRunner(app)
        try:
            await runner.setup()
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            app['allowed_origins'] |= {f'http://127.0.0.1:{port}'}
            print(json.dumps({'url': f'http://127.0.0.1:{port}', 'sessionId': sid, 'messageId': message_id,
                              'mode': MODE, 'python': Path(sys.executable).name,
                              'samples': {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                                          for name, data in samples.items()},
                              'partialPixels': expected, 'pngId': png_record['id'],
                              'pngSha256': png_record['sha256']}), flush=True)
            for sig in (signal.SIGTERM, signal.SIGINT):
                asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
            await stopped.wait()
        finally:
            await runner.cleanup()
            closed = not runner.sites and service.closed and not service.queue_clients
            print(json.dumps({'phase': 'fixture-cleanup', 'closed': closed,
                              'evidence': evidence()}), flush=True)
            if not closed or ERRORS or any(key.startswith('unexpected:') for key in COUNTERS):
                raise AssertionError('Fixture cleanup or no-model guard failed')
    finally:
        # Covers failures while reading/seeding samples BEFORE a runner exists.
        # Normal runner cleanup already closes the service; close is idempotent.
        await app['service'].close()


if __name__ == '__main__':
    asyncio.run(main())