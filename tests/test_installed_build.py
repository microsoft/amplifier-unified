"""The bound web baseline advertises one immutable, host-scoped build receipt."""
import json
from pathlib import Path

import pytest

from amplifier_web import __version__, installed_build
from amplifier_web.service import AppService


@pytest.mark.parametrize('raw', [None, b'{', b'[]', b'null', b' ' * 4097,
                                      b'{"version":3,"id":[]}', b'\xff'])
def test_missing_or_invalid_packaged_identity_is_bounded_unknown(tmp_path, monkeypatch, raw):
    path = tmp_path / 'build.json'
    if raw is not None:
        path.write_bytes(raw)
    monkeypatch.setattr(installed_build, 'BUILD_FILE', path)
    assert installed_build.capture().project() == {
        'serverVersion': __version__, 'frontend': {'version': 'unknown', 'id': 'unknown'}}


async def test_web_baselines_reconnect_and_publication_share_host_capture(tmp_path, monkeypatch):
    path = tmp_path / 'build.json'
    first = {'version': '0.20.146', 'id': '1234567890abcdef'}
    second = {'version': '0.20.147', 'id': 'abcdef1234567890'}
    path.write_text(json.dumps(first))
    monkeypatch.setattr(installed_build, 'BUILD_FILE', path)
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    await app.history.close()
    expected = {'serverVersion': __version__, 'frontend': first}
    host = app.instance_id
    queue = None
    try:
        app.clients.attach('web')
        with app.clients.bind('web'):
            baseline = app.browser_state()
            assert baseline['client']['build'] == expected
            assert baseline['client']['hostInstanceId'] == host
            queue = app.subscribe()
        path.write_text(json.dumps(second))
        original_open = Path.open

        def guarded_open(self, *args, **kwargs):
            assert self != path, 'State publication must not reopen packaged build metadata'
            return original_open(self, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, 'open', guarded_open)
            with app.clients.bind('web'):
                await app.dispatch('view.update', {'patch': {'navExpanded': True}})
                assert queue.get_nowait()['client']['build'] == expected
            app.clients.attach('reconnected', resume='web')
            with app.clients.bind('reconnected'):
                frame = app.browser_state()
                assert frame['client']['build'] == expected
                assert frame['client']['hostInstanceId'] == host
            # Projection values cannot alter the immutable host capture.
            frame['client']['build']['frontend']['id'] = 'caller-mutated'
            assert app.installed_build.project() == expected
            for kind in ('tui', 'native', 'api'):
                app.clients.attach(kind, kind=kind)
                with app.clients.bind(kind):
                    assert 'build' not in app.browser_state()['client']
            assert 'client' not in app.browser_state()
    finally:
        if queue is not None:
            app.unsubscribe(queue)
        await app.close()

    restored = AppService(tmp_path / 'app', workspace=tmp_path)
    await restored.history.close()
    try:
        assert restored.instance_id != host
        with restored.clients.bind('web'):
            frame = restored.browser_state()
            assert frame['client']['hostInstanceId'] == restored.instance_id
            assert frame['client']['build'] == {'serverVersion': __version__, 'frontend': second}
    finally:
        await restored.close()
