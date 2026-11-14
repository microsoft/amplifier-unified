"""Real AppService HTTPS health joined to the production read-only worker.

Native ownership/process/install effects use the maintained worker scaffold.
Health route, TLS connection, leaf pin, bearer and reconciliation are real.
This is not additional native-manager or published-artifact qualification.
"""
import asyncio
import hashlib
import math
from pathlib import Path
import ssl
from unittest.mock import patch

from aiohttp import web
import pytest

import test_installation_repair_worker as scaffold
from test_service import Runtime
from amplifier_web.server import create_app
from amplifier_web import installation_repair as controller
from amplifier_web.deployment import load_server_config
from amplifier_web.tls import setup_local_ca, ssl_context


@pytest.mark.asyncio
async def test_original_lifetime_reconcile_joins_real_pinned_health_and_service_uuid():
    fixture = scaffold.RepairFixture()
    real_health_get = scaffold.worker._health_get
    fixture.setUp()
    runner = None
    try:
        # Remove only deliberately invalid scaffold placeholders before creating
        # the real app. No user data or installed environment is involved.
        for name in ('app.sqlite3', 'settings.yaml'):
            (fixture.home / name).unlink()
        config = setup_local_ca(fixture.home, load_server_config(fixture.home))
        app = await create_app(fixture.home, workspace=fixture.root,
                               runtime=Runtime(), voice=False,
                               preload_providers=False, background_updates=False,
                               background_work=False, server_config=config)
        service = app['service']
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0,
                          ssl_context=ssl_context(fixture.home, config))
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        leaf = ssl.PEM_cert_to_DER_cert(
            (fixture.home / 'config/tls/leaf.crt').read_text())
        fixture.plan.update(hostInstanceId=service.instance_id,
                            runningIdentity=dict(service.update_manager.running_identity))
        fixture.plan['health'] = {'urls': [f'https://127.0.0.1:{port}/api/health'],
                                  'pin': hashlib.sha256(leaf).hexdigest()}
        # Join a controller-valid admission, not just the worker scaffold's
        # arbitrary operation name, so the real hold can be inspected too.
        operation = fixture.receipt()
        fixture.folder = fixture.folder.rename(fixture.namespace / ('a' * 32))
        fixture.artifact['path'] = str(fixture.folder / Path(fixture.artifact['path']).name)
        fixture.plan.update(operationId=fixture.folder.name, previewId='b' * 32)
        operation.update(operationId=fixture.folder.name, previewId='b' * 32,
                         hostInstanceId=service.instance_id, status='unknown')
        scaffold.worker.write(fixture.folder / 'operation.json', operation)
        fixture.save_plan()
        scaffold.worker.write(fixture.namespace / 'admissions.json',
                              {'schema': 1, 'current': fixture.folder.name,
                               'previews': {'b' * 32: fixture.folder.name}})
        controller.initialize(service)
        assert controller.held(service)
        before = (fixture.folder / 'operation.json').read_bytes()
        admission = (fixture.namespace / 'admissions.json').read_bytes()
        expected = service.instance_id
        # Restore the real transport that the all-mocked worker scaffold replaces.
        with patch.object(scaffold.worker, '_health_get', real_health_get):
            health = await asyncio.to_thread(scaffold.worker.authenticated_health, fixture.plan)
            assert health['hostInstanceId'] == expected
            assert health['instanceId'] == fixture.plan['runningIdentity']['instanceId']
            assert expected != health['instanceId']
            for bad in ('different-service-uuid', '', None):
                # The actual server emits this field; no route/response adapter
                # or worker authentication function is substituted.
                service.instance_id = bad
                with pytest.raises(ValueError, match='confirmed host'):
                    await asyncio.to_thread(scaffold.worker.reconcile, fixture.folder, expected)
                assert (fixture.folder / 'operation.json').read_bytes() == before
                assert (fixture.namespace / 'admissions.json').read_bytes() == admission
                assert controller.held(service)
            service.instance_id = expected
            with pytest.raises(ValueError, match='confirmed host'):
                await asyncio.to_thread(scaffold.worker.reconcile, fixture.folder, 'wrong-confirmed-uuid')
            assert (fixture.folder / 'operation.json').read_bytes() == before
            assert controller.held(service)
            result = await asyncio.to_thread(scaffold.worker.reconcile, fixture.folder, expected)
        assert result['status'] == 'failed' and result['stage'] == 'reconciled-no-effect'
        assert result['reconciliation']['noReplacement'] is True
        assert result['reconciliation']['hostInstanceId'] == expected
        assert result['reconciliation']['verifiedAt'] > 0
        assert math.isfinite(result['reconciliation']['verifiedAt'])
        assert result['reinstalled'] is False and result['healthVerified'] is False
        assert result['serviceState'] == 'running'
        assert (fixture.namespace / 'admissions.json').read_bytes() == admission
        assert not {'stop', 'start', 'candidate-install', 'fresh-install'} & set(fixture.names())
        assert fixture.receipt() == result
        assert controller.held(service) is False
    finally:
        if runner is not None:
            await runner.cleanup()
        fixture.doCleanups()
