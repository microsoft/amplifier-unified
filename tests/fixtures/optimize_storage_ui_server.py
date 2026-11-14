"""Real Optimize host/owners; synthetic private captures and text-only runtime.

Never substitutes an eligibility adapter or writes operation/authority SQL.
--offline-check uses in-process dispatch only (no listener, browser or model).
The browser runner must use an independently selected, built client.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import struct
import zlib

TOKEN = 'fixture-browser-control-token'
PREFIX = 'maintenance.optimize.'
# Older than the manual 12-hour cutoff, but inside the ordinary diagnostics
# index's retention window: identity/export assertions must not be vacuous.
OLD = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat().replace('+00:00', 'Z')
REQUEST_SECRET = 'OPTIMIZE-SYNTHETIC-REQUEST-ONLY'
RESPONSE_SECRET = 'OPTIMIZE-SYNTHETIC-RESPONSE-KEEP'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def historical_pixel():
    """Valid synthetic 1x1 PNG, deliberately without owner/TTL admission."""
    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload
            + struct.pack('>I', zlib.crc32(kind + payload)))
    png = (b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(b'\x00\x12\x34\x56'))
        + chunk(b'IEND', b''))
    return base64.b64encode(png).decode('ascii')


def seed_capture(directory, native):
    """Small, explicit synthetic source; missing image provenance stays missing."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    (directory / 'metadata.json').write_text(json.dumps({
        'format': 'context-intelligence', 'version': '1.0.0', 'session_id': native}))
    records = []
    for direction, secret in [('request', REQUEST_SECRET), ('response', RESPONSE_SECRET)]:
        body = json.dumps({'synthetic': secret}, sort_keys=True).encode()
        sha = hashlib.sha256(body).hexdigest()
        assets = directory / 'provider-payloads'
        assets.mkdir(exist_ok=True)
        (assets / (sha + '.json')).write_bytes(body)
        records.append({'event': 'llm:' + direction, 'timestamp': OLD, 'session_id': native,
            'data': {'event_id': 'optimize-' + direction, 'provider': 'fixture',
                'request_id': 'optimize-call',
                'model': 'optimize-fixture', 'raw': {'synthetic': secret},
                'provider_payloads': {'raw': {'sha256': sha, 'bytes': len(body),
                    'redacted': True, 'truncated': False}},
                'usage': {'input_tokens': 7, 'output_tokens': 3, 'cost': 0.01}}})
    path = directory / 'events.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in records))
    path.chmod(0o600)
    os.utime(path, (1, 1))  # Genuine idle eligibility, not a mocked retention clock.
    return path


def dependency_check():
    """Fail closed, before starting a host, when the selected CI owner is old."""
    from context_intelligence.prune.policy import Policy
    from context_intelligence.prune.operations import PruneRequest
    Policy(diagnostic_sidecars=True)
    if 'before_commit' not in PruneRequest.__dataclass_fields__:
        raise RuntimeError('Selected CI lacks guarded before_commit retention admission')


async def main(home):
    # Inherited capture configuration must never redirect this fixture to real data.
    os.environ['AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH'] = str(home / 'native' / 'projects')
    os.environ['AMPLIFIER_SESSION_STATE_HOME'] = str(home / 'session-state')
    os.environ['HOME'] = str(home)
    import settings_ui_server as fixture
    from aiohttp import web
    from amplifier_web.capture_index import index_shared
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.session_files import capture_dir, sessions_dir

    class Runtime(fixture.Runtime):
        def __init__(self):
            super().__init__()
            self.calls = []
            self.boundaries = RuntimeManager()

        def stopped_boundary(self, session):
            # Real Foundation ownership acquisition; never an AlwaysEligible lease.
            return self.boundaries.stopped_boundary(session)

        async def start(self, session, emit, **kwargs):
            self.calls.append({'operation': 'start', 'sessionId': session['id']})
            await super().start(session, emit, **kwargs)

        async def stop(self, sid, **kwargs):
            self.calls.append({'operation': 'stop', 'sessionId': sid})

        async def close(self):
            await self.boundaries.close()

    fixture.Runtime = Runtime
    app = await fixture.main(home)
    service = app['service']
    source = service._session()
    native = source.get('runtimeSessionId') or source['id']
    path = seed_capture(capture_dir(source['workspace'], native), native)
    canonical = sessions_dir(source['workspace']) / native
    canonical.mkdir(parents=True, exist_ok=True, mode=0o700)
    (canonical / 'metadata.json').write_text(json.dumps({
        'session_id': native, 'host': 'amplifier-unified', 'cwd': source['workspace']}))
    # Deliberately unknown historic image origin/TTL; no fabricated owner grant.
    transcript = canonical / 'transcript.jsonl'
    transcript.write_text(json.dumps({'role': 'tool', 'name': 'historical-observation',
        'content': [{'type': 'image', 'source': {'type': 'base64',
            'media_type': 'image/png', 'data': historical_pixel()}}]}) + '\n')
    transcript.chmod(0o600)
    original_transcript = digest(transcript)
    # The ordinary committed session/create path owns native registration.
    await service.diagnostics.flush()
    with service.diagnostics._db() as db:
        index_shared(db, [(source['workspace'], native)], {'streams': ['usage']})
    # Initial session/create diagnostics may have appended during flush above.
    # Only this private synthetic fixture is declared genuinely idle.
    os.utime(path, (1, 1))
    initial_runtime_calls = list(service.runtime.calls)
    audit = []
    gates = {}
    # Timing-only test checkpoint AFTER the real apply receipt is committed and
    # BEFORE the unchanged production worker begins its first file. No status,
    # eligibility, file outcome, authority, adapter or prune result is replaced.
    from amplifier_web.storage_optimize import owner
    optimize = owner(service)
    production_work = optimize._work
    work_gate = {'held': False, 'entered': asyncio.Event(), 'release': asyncio.Event()}

    async def checkpointed_work(identity, caller, phase):
        if phase == 'apply' and work_gate['held']:
            work_gate['entered'].set()
            await asyncio.wait_for(work_gate['release'].wait(), 20)
        return await production_work(identity, caller, phase)

    optimize._work = checkpointed_work
    app['optimize_fixture_checkpoint'] = work_gate

    def authorize(request):
        if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
            raise web.HTTPForbidden()

    def observations():
        from amplifier_web.storage_cleanup import policy as cleanup_policy
        with service.diagnostics._db() as db:
            ids = [row[0] for row in db.execute(
                'SELECT id FROM records WHERE session=? ORDER BY id', (native,))]
            deliveries = [list(row) for row in db.execute(
                'SELECT record_id,destination,status,payload FROM deliveries ORDER BY record_id,destination')]
        # Bounded known synthetic records only. Never return their diagnostic bodies.
        facts = {}
        with path.open() as stream:
            for line in stream:
                if len(line) > 8192:
                    raise AssertionError('Synthetic fixture record exceeded its bound')
                record = json.loads(line)
                event_id = record.get('data', {}).get('event_id')
                if event_id in {'optimize-request', 'optimize-response'}:
                    data = record['data']
                    facts[event_id] = {'rawPresent': 'raw' in data,
                        'expired': data.get('provider_payloads_expired', {}).get('raw'),
                        'usage': data.get('usage')}
        return {'nativeId': native, 'sessionId': source['id'], 'eventIds': ids,
            'deliveries': deliveries, 'canonicalUnchanged': digest(transcript) == original_transcript,
            'sourceSha256': digest(path), 'runtimeCalls': list(service.runtime.calls),
            'diagnosticFacts': facts,
            'automaticCleanupPolicy': cleanup_policy(service),
            'diagnosticsConfigSha256': hashlib.sha256(json.dumps(
                service.diagnostics.config, sort_keys=True).encode()).hexdigest(),
            'initialRuntimeCalls': initial_runtime_calls, 'requests': list(audit),
            'workCheckpoint': {'entered': work_gate['entered'].is_set(),
                'released': work_gate['release'].is_set()},
            'responseGates': {key: {'entered': value['entered'].is_set(),
                'released': value['release'].is_set()} for key, value in gates.items()}}

    @web.middleware
    async def transport_gate(request, handler):
        if request.path != '/api/actions' or request.method != 'POST':
            return await handler(request)
        body = await request.json()
        if not str(body.get('action', '')).startswith(PREFIX):
            return await handler(request)
        # Observe the exact public envelope. Do not call the action a second time.
        row = {key: body.get(key) for key in ('action', 'args', 'id')}
        audit.append(row)
        response = await handler(request)
        action = body['action'].removeprefix(PREFIX)
        gate = gates.get(action)
        if gate and not gate['entered'].is_set():
            gate['entered'].set()  # handler has returned its actual durable receipt.
            await asyncio.wait_for(gate['release'].wait(), 20)
        return response

    app.middlewares.append(transport_gate)

    async def observe(request):
        authorize(request)
        return web.json_response(observations())

    async def control(request):
        authorize(request)
        body = await request.json()
        if body.get('op') == 'hold-before-first-file' and not work_gate['held']:
            work_gate['held'] = True
            return web.json_response({'ok': True})
        if body.get('op') == 'release-first-file' and work_gate['held']:
            work_gate['release'].set()
            return web.json_response({'ok': True})
        action = body.get('action')
        if action not in {'preview', 'apply', 'cancel', 'resume', 'policy.save'}:
            raise web.HTTPBadRequest(text='Unknown fixed transport gate')
        if body.get('op') == 'hold-response' and action not in gates:
            gates[action] = {'entered': asyncio.Event(), 'release': asyncio.Event()}
        elif body.get('op') == 'release-response' and action in gates:
            gates[action]['release'].set()
        else:
            raise web.HTTPBadRequest(text='Invalid gate transition')
        return web.json_response({'ok': True})

    async def browser_session(request):
        authorize(request)
        from amplifier_web.auth import SESSION_COOKIE, new_session
        response = web.json_response({'issued': True})
        response.set_cookie(SESSION_COOKIE, new_session(app['session_secret']),
            httponly=True, samesite='Strict', path='/')
        return response

    async def cleanup(_):
        work_gate['release'].set()
        for gate in gates.values():
            gate['release'].set()

    app.router.add_get('/fixture/optimize', observe)
    app.router.add_post('/fixture/optimize', control)
    app.router.add_post('/fixture/browser-session', browser_session)
    app.on_cleanup.append(cleanup)
    # In-process qualification uses these read-only closures, never a listener.
    return app, observations


async def check_public_usage_export(service, native):
    """Exercise the same inspected-page export actions as the browser, unmodified."""
    inspected = await service.dispatch('diagnostics.records',
        {'stream': 'usage', 'sessionId': native}, include_state=False)
    assert inspected['accepted'] is True, inspected
    page = service.state['diagnostics']['lastResult']
    calls = [row for row in page['items'] if row['event'] in {'llm:request', 'llm:response'}
        and row['data'].get('model') == 'optimize-fixture']
    assert {row['event'] for row in calls} == {'llm:request', 'llm:response'}, calls
    safe = {'provider': 'fixture', 'model': 'optimize-fixture',
        'usage': {'input_tokens': 7, 'output_tokens': 3, 'cost': 0.01}}
    assert all(row['data'] == safe for row in calls), calls
    print(json.dumps({'phase': 'public-usage-export',
        'inspectedEvents': [row['event'] for row in calls],
        'safeData': safe, 'privateEventIdsExcluded': True,
        'metadataTimestampExcluded': True, 'exportOutcome': 'not_yet_observed'}), flush=True)
    # Do not restore timestamp/event_id/raw into sanitized data to make this pass.
    exported = await service.dispatch('diagnostics.export', {}, include_state=False)
    assert exported['accepted'] is True, exported
    downloads = [effect for effect in exported.get('effects', []) if effect['type'] == 'download']
    assert len(downloads) == 1 and downloads[0]['mime'] == 'application/x-ndjson', exported
    content = downloads[0]['content']
    assert REQUEST_SECRET not in content and RESPONSE_SECRET not in content
    rows = [json.loads(line) for line in content.splitlines()]
    assert len(rows) == len(page['items']) and len(rows) <= 30
    actual = [row for row in rows if row['event'] in {'llm:request', 'llm:response'}
        and row['data'].get('model') == 'optimize-fixture']
    assert len(actual) == 2
    for row in actual:
        assert set(row) == {'event', 'workspace', 'timestamp', 'data'}, row
        datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
        assert row['data'] == safe
    return {'publicUsageExport': True, 'exportedEvents': [row['event'] for row in actual],
        'exportedData': [row['data'] for row in actual],
        'exportSha256': hashlib.sha256(content.encode()).hexdigest()}


async def offline_check(*, export=False):
    """Focused real-service proof; explicitly not browser or installed evidence."""
    dependency_check()
    from amplifier_web.storage_optimize import owner
    from aiohttp import web
    with tempfile.TemporaryDirectory(prefix='optimize-offline-') as temporary:
        app, observe = await main(Path(temporary))
        runner = web.AppRunner(app)
        await runner.setup()
        service = app['service']
        async def dispatch(name, args, identity=None):
            result = await service.dispatch(PREFIX + name, args,
                command_id=identity, include_state=False)
            assert result['accepted'] is True, result
            return result['result']
        async def settle(identity):
            async with asyncio.timeout(15):
                while True:
                    value = await dispatch('read', {'operationId': identity})
                    if value['status'] not in {'previewing', 'applying', 'cancelling'}:
                        return value
                    await asyncio.sleep(.01)
        try:
            before = observe()
            assert len(before['eventIds']) >= 2, 'Identity preservation needs nonempty real indexed records'
            default = await dispatch('policy.read', {})
            assert default['requestKeepHours'] is None and default['responseKeepHours'] is None
            saved = await dispatch('policy.save', {'expectedRevision': default['revision'],
                'requestKeepHours': 12, 'responseKeepHours': None}, 'offline-save')
            images = await dispatch('preview', {'scope': 'all', 'extractImages': True,
                'policyRevision': saved['revision']}, 'offline-images-preview')
            images = await settle(images['operationId'])
            assert images['summary']['coverage'].get('eligible', 0) > 0, images
            assert any(row['owner'] == 'foundation' and row['status'] == 'held'
                and 'provenance' in row['reason'].lower() for row in images['rows']), images
            preview = await dispatch('preview', {'scope': 'all', 'extractImages': False,
                'policyRevision': saved['revision']}, 'offline-preview')
            preview = await settle(preview['operationId'])
            assert preview['summary']['coverage'].get('eligible', 0) > 0, preview
            frozen = preview['cutoffs'].copy()
            assert frozen['requestBefore'].endswith('Z') and frozen['responseBefore'] is None
            assert preview['canApply'] is True
            gate = app['optimize_fixture_checkpoint']
            gate['held'] = True
            await dispatch('apply', {'operationId': preview['operationId'],
                'expectedRevision': preview['revision'], 'confirmExpiry': True}, 'offline-apply')
            await asyncio.wait_for(gate['entered'].wait(), 5)
            cancelled = await dispatch('cancel', {'operationId': preview['operationId'],
                'expectedRevision': preview['revision']}, 'offline-cancel')
            assert cancelled['status'] == 'cancelling'
            gate['release'].set()
            cancelled = await settle(preview['operationId'])
            assert cancelled['status'] == 'cancelled' and cancelled['canResume'], cancelled
            assert observe()['sourceSha256'] == before['sourceSha256']
            # Passive receipt reads do not resume or expire anything.
            await dispatch('read', {'commandId': 'offline-apply'})
            assert observe()['sourceSha256'] == before['sourceSha256']
            await dispatch('resume', {'operationId': preview['operationId'],
                'expectedRevision': cancelled['revision']}, 'offline-resume')
            finished = await settle(preview['operationId'])
            assert finished['summary']['coverage'].get('completed', 0) > 0, finished
            command = await dispatch('read', {'commandId': 'offline-apply'})
            assert command['status'] == 'recorded' and command['operationId'] == preview['operationId']
            assert finished['cutoffs'] == frozen
            after = observe()
            assert before['eventIds'] == after['eventIds']
            assert before['deliveries'] == after['deliveries']
            assert after['canonicalUnchanged'] and after['runtimeCalls'] == before['runtimeCalls']
            assert after['automaticCleanupPolicy'] == before['automaticCleanupPolicy']
            assert after['diagnosticsConfigSha256'] == before['diagnosticsConfigSha256']
            assert not after['diagnosticFacts']['optimize-request']['rawPresent']
            assert after['diagnosticFacts']['optimize-request']['expired']
            assert after['diagnosticFacts']['optimize-response']['rawPresent']
            assert after['diagnosticFacts']['optimize-response']['usage'] == before['diagnosticFacts']['optimize-response']['usage']
            source = service._session()
            tree = service.event_log_view.read(source)
            models = [node for node in tree['nodes'] if node.get('model') == 'optimize-fixture']
            assert len(models) == 1, models
            model = models[0]
            assert model.get('requestRecordingExpired') and not model.get('requestDetail'), model
            assert model.get('responseDetail') and model.get('responseRecording'), model
            assert owner(service).adapter.__class__.__name__ == 'OwnerAdapters'
            export_result = await check_public_usage_export(service, before['nativeId']) if export else {}
            print(json.dumps({'passed': True, 'browser': False, 'listener': False,
                'realService': True, 'realRetentionOwner': True, 'syntheticSource': True,
                'operationId': finished['operationId'], 'status': finished['status'],
                'cutoffs': frozen, 'coverage': finished['summary']['coverage'],
                'cancelledBeforeEffect': True, 'explicitResumeOnly': True,
                'requestExpiredResponseKept': True, 'eventIdsAndDeliveriesPreserved': True,
                'actualReaderMarksRequestExpiredAndResponseAvailable': True,
                'realImagePreviewHoldsMissingHistoricalProvenance': True,
                'indexedRecordCount': len(before['eventIds']),
                'deliveryRows': len(before['deliveries']), **export_result}))
        finally:
            await runner.cleanup()


async def serve():
    dependency_check()
    from aiohttp import web
    with tempfile.TemporaryDirectory(prefix='optimize-storage-ui-') as temporary:
        app, _ = await main(Path(temporary))
        runner = web.AppRunner(app, shutdown_timeout=5)
        await runner.setup()
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        signals = (signal.SIGTERM, signal.SIGINT) if os.name == 'posix' else ()
        for value in signals:
            loop.add_signal_handler(value, stopped.set)
        try:
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            base = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
            app['allowed_origins'] = app['allowed_origins'] | {base}
            print(json.dumps({'fixture': 'optimize-storage-ui', 'base': base,
                'pid': os.getpid()}), flush=True)
            await stopped.wait()
        finally:
            await runner.cleanup()
            for value in signals:
                loop.remove_signal_handler(value)


def source_check():
    """Pure source-fixture invariants, not evidence of owner/browser execution."""
    with tempfile.TemporaryDirectory(prefix='optimize-source-check-') as temporary:
        path = seed_capture(Path(temporary) / 'synthetic', 'fixture-root')
        records = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(records) == 2 and len({row['data']['event_id'] for row in records}) == 2
        assert [row['event'] for row in records] == ['llm:request', 'llm:response']
        for row in records:
            ref = row['data']['provider_payloads']['raw']
            asset = path.parent / 'provider-payloads' / (ref['sha256'] + '.json')
            assert digest(asset) == ref['sha256'] and asset.stat().st_size == ref['bytes']
            assert row['timestamp'] == OLD and row['session_id'] == 'fixture-root'
        assert path.stat().st_mtime < 3600
        print(json.dumps({'passed': True, 'checks': [
            'two independent synthetic diagnostic directions',
            'stable unique event identities', 'exact real sidecar hashes and sizes',
            'genuinely old timestamp and filesystem idle time'],
            'productionOwnerExecuted': False, 'browserExecuted': False}))


if __name__ == '__main__':
    if not os.environ.get('AMPLIFIER_TEST_INSTALLED_PACKAGE'):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    if '--source-check' in sys.argv:
        source_check()
    else:
        asyncio.run(offline_check(export='--export-check' in sys.argv)
            if '--offline-check' in sys.argv or '--export-check' in sys.argv else serve())