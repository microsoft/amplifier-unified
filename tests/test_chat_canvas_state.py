from copy import deepcopy
import json

import pytest

from amplifier_web.service import AppService, AppError


async def test_chat_canvas_roundtrip_restart_and_client_isolation(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title': 'One'})
        one = app.state['selectedSessionId']
        app.clients.attach('browser')
        app.clients.attach('other')
        app.clients.save()
        other = deepcopy(app.clients.records['other'])
        with app.clients.bind('browser'):
            await app.dispatch('canvas.show', {'kind': 'text', 'title': 'First', 'content': 'unique saved body'})
            first = app.state['canvas']['id']
            await app.dispatch('canvas.show', {'kind': 'text', 'title': 'Second', 'content': 'second body'})
            await app.dispatch('canvas.select', {'id': first, 'version': 1})
            await app.dispatch('canvas.view', {'id': first, 'patch': {'zoom': 1.5, 'source': True}})
            await app.dispatch('view.update', {'patch': {'canvasWidth': 680, 'canvasControlsPinned': True, 'canvasDraft': {'filter': 'First', 'library': True}}})
            await app.dispatch('session.create', {'title': 'Two'})
            two = app.state['selectedSessionId']
            assert not app.state['canvas']['open']
            assert 'canvasWidth' not in app.state['view']
            assert 'canvasDraft' not in app.state['view']
            await app.dispatch('canvas.reopen', {})
            await app.dispatch('view.update', {'patch': {'canvasWidth': 420}})
            await app.dispatch('canvas.close', {})
            await app.dispatch('session.select', {'id': one})
            assert app.state['canvas']['open']
            assert app.state['canvas']['id'] == first
            assert app.state['canvas']['selectedVersion'] == 1
            assert app.state['canvas']['view']['zoom'] == 1.5
            assert app.state['view']['canvasWidth'] == 680
            assert app.state['view']['canvasDraft']['filter'] == 'First'
            assert app.state['view']['canvasControlsPinned']
            await app.dispatch('session.select', {'id': two})
            assert not app.state['canvas']['open']
            assert app.state['view']['canvasWidth'] == 420
            with pytest.raises(AppError, match='chat changed'):
                await app.dispatch('view.update', {'sessionId': one, 'patch': {'canvasWidth': 1000}})
            stored = json.loads(app.db.execute("SELECT value FROM client_views WHERE id='browser'").fetchone()[0])
            assert 'unique saved body' not in json.dumps(stored['chatCanvas'])
            assert 'chatCanvas' not in app.browser_state()
        assert app.clients.records['other'] == other
        await app.close()
        app = AppService(tmp_path / 'app', workspace=tmp_path)
        app.clients.attach('resumed', resume='browser')
        with app.clients.bind('resumed'):
            assert not app.state['canvas']['open']
            await app.dispatch('session.select', {'id': one})
            assert app.state['canvas']['id'] == first
            assert app.state['canvas']['open']
            assert app.state['view']['canvasWidth'] == 680
            assert app.state['canvas']['view']['zoom'] == 1.5
            await app.dispatch('session.draft', {})
            assert not app.state['canvas']['open']
            assert 'canvasWidth' not in app.state['view']
    finally:
        await app.close()


async def test_legacy_selected_canvas_is_saved_before_first_navigation(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        one = app.state['selectedSessionId']
        await app.dispatch('canvas.show', {'kind': 'text', 'content': 'Legacy'})
        app.clients.attach('browser')
        with app.clients.bind('browser'):
            app.clients.record().pop('chatCanvas', None)
            await app.dispatch('session.create', {})
            assert not app.state['canvas']['open']
            await app.dispatch('session.select', {'id': one})
            assert app.state['canvas']['open']
            assert app.state['canvas']['content'] == 'Legacy'
    finally:
        await app.close()
