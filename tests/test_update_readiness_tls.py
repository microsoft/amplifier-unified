import asyncio

from aiohttp import web
import pytest

from amplifier_web import __version__, update_readiness
from amplifier_web.deployment import validate_server
from amplifier_web.server import create_app
from amplifier_web.tls import setup_local_ca, ssl_context
from test_service import Runtime


@pytest.mark.parametrize('certificate_matches', [True, False])
async def test_post_start_readiness_requires_the_configured_tls_certificate(
        tmp_path, unused_tcp_port, monkeypatch, certificate_matches):
    config = setup_local_ca(tmp_path, validate_server({'port': unused_tcp_port, 'bind': ['127.0.0.1']}))
    monkeypatch.setattr(update_readiness, 'running_identity', lambda: {
        'version': __version__, 'revision': 'a' * 40, 'instanceId': 'b' * 32})
    # Even a service answering with the correct JSON cannot confirm readiness
    # if it presents a certificate other than this deployment's configured one.
    if certificate_matches:
        secure = ssl_context(tmp_path, config)
    else:
        wrong_home = tmp_path / 'other-server'
        other_config = setup_local_ca(wrong_home, validate_server({'port': unused_tcp_port}))
        secure = ssl_context(wrong_home, other_config)
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           background_updates=False, preload_providers=False, server_config=config)
    manager = app['service'].update_manager
    marker = {'version': __version__, 'revision': 'a' * 40, 'attemptId': 'c' * 32, 'sourceInstanceId': 'd' * 32}
    app['service'].state['updates'].update(phase='activating', pendingRestart=marker)
    # Bound the negative case while keeping actual production probing/listeners.
    real_wait = update_readiness.wait_for_readiness
    async def short_wait(manager, token):
        return await real_wait(manager, token, timeout=.6, interval=.02)
    monkeypatch.setattr(update_readiness, 'wait_for_readiness', short_wait)
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        await asyncio.sleep(.05)
        assert app['service'].state['updates']['pendingRestart'] == marker
        assert not any(e['phase'] == 'restart-ack' for e in manager.diagnostics.state['events'])
        await web.TCPSite(runner, '127.0.0.1', unused_tcp_port, ssl_context=secure).start()
        await asyncio.wait_for(manager.readiness_task, 3)
        updates = app['service'].state['updates']
        if certificate_matches:
            assert updates['phase'] == 'installed' and updates['pendingRestart'] is None
            assert manager.diagnostics.state['latest']['phase'] == 'restart-ack'
            assert manager.diagnostics.state['latest']['observedRevision'] == 'a' * 40
        else:
            assert updates['phase'] == 'activating' and updates['pendingRestart'] == marker
            assert manager.diagnostics.state['lastFailure']['errorType'] == 'TimeoutError'
            assert not any(e['phase'] == 'restart-ack' for e in manager.diagnostics.state['events'])
    finally:
        await runner.cleanup()
