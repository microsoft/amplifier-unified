"""Saved client bodies must be usable by existing viewers after a host restart."""
import json

import pytest

from amplifier_web.service import AppService


@pytest.mark.parametrize('kind,body', [
    ('markdown', {'content': '# Retained Markdown\n\nReadable after restart.'}),
    ('text', {'content': 'Retained text'}),
    ('code', {'content': 'const retained = true;'}),
    ('json', {'content': '{"retained": true}'}),
    ('jsonl', {'content': '{"retained": true}\n'}),
    ('mermaid', {'content': 'graph TD; A-->B'}),
    ('dot', {'content': 'digraph { A -> B }'}),
    ('image', {'content': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aMZ8AAAAASUVORK5CYII='}),
    ('a2ui', {'surface': {'surfaceId': 'retained', 'root': 'text', 'components': [{'id': 'text', 'component': {'Text': {'text': {'literalString': 'Retained controls'}}}}]}}),
])
async def test_saved_client_canvas_body_restores_after_restart(tmp_path, kind, body):
    home = tmp_path / 'app'
    app = AppService(home, workspace=tmp_path)
    app.clients.attach('reader')
    with app.clients.bind('reader'):
        await app.dispatch('session.create', {})
        await app.dispatch('canvas.show', {'kind': kind, **body})
        await app.dispatch('view.update', {'patch': {'draft': 'Keep this unsent draft'}})
        identity, session = app.state['canvas']['id'], app.state['selectedSessionId']
        expected = {key: app.state['canvas'][key] for key in body}
        saved = json.loads(app.db.execute("SELECT value FROM client_views WHERE id='reader'").fetchone()[0])
        assert 'contentResource' in saved['canvas']
        assert not {'content', 'surface'} & saved['canvas'].keys()
        # Older restarts can have copied the compact client reference into an
        # artifact row. Selecting such a saved tab must work too.
        row = next(row for row in app.state['canvasArtifacts'] if row['id'] == identity)
        row['contentResource'] = row['body']
        app._save()
    await app.close()
    restored = AppService(home, workspace=tmp_path)
    try:
        with restored.clients.bind('reader'):
            state = restored.browser_state()
            assert state['canvas']['id'] == identity
            assert state['selectedSessionId'] == session
            assert state['view']['draft'] == 'Keep this unsent draft'
            assert 'contentResource' not in state['canvas']
            assert all(state['canvas'][key] == value for key, value in expected.items())
            # Existing source/copy/download controls need an inline ordinary
            # body, rather than the HTML-only source endpoint.
            if kind != 'a2ui':
                receipt = await restored.dispatch('canvas.copy', {'id': identity})
                assert receipt['effects'][0]['type'] == 'clipboard.write'
                assert receipt['effects'][0]['content'] == expected['content']
            await restored.dispatch('canvas.show', {'kind': 'text', 'content': 'Another artifact'})
            await restored.dispatch('canvas.select', {'id': identity})
            assert all(restored.state['canvas'][key] == value for key, value in expected.items())
        restored.clients.attach('copy', 'reader')
        with restored.clients.bind('copy'):
            assert restored.browser_state()['canvas']['id'] == identity
            assert restored.state['view']['draft'] == 'Keep this unsent draft'
        persisted = json.loads(restored.db.execute("SELECT value FROM client_views WHERE id='reader'").fetchone()[0])
        assert 'contentResource' in persisted['canvas']
        assert not {'content', 'surface'} & persisted['canvas'].keys()
    finally:
        await restored.close()


async def test_large_html_stays_indirect_when_client_is_restored(tmp_path):
    home = tmp_path / 'app'
    page = tmp_path / 'large.html'
    page.write_text('<h1>Large HTML</h1><!--' + 'x' * 1_200_000 + '-->')
    app = AppService(home, workspace=tmp_path)
    app.clients.attach('reader')
    with app.clients.bind('reader'):
        await app.dispatch('session.create', {})
        await app.dispatch('canvas.show', {'kind': 'html', 'path': str(page)})
        identity = app.state['canvas']['id']
    await app.close()
    page.unlink()
    restored = AppService(home, workspace=tmp_path)
    try:
        from amplifier_web.canvas_documents import raw_source
        with restored.clients.bind('reader'):
            canvas = restored.browser_state()['canvas']
            assert canvas['id'] == identity and 'content' not in canvas
            assert 'contentResource' in canvas
            assert len(raw_source(canvas, restored.db)) > 1_200_000
    finally:
        await restored.close()


async def test_unavailable_old_source_does_not_break_client_attachment(tmp_path):
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    app.clients.attach('reader')
    try:
        with app.clients.bind('reader'):
            await app.dispatch('session.create', {})
            await app.dispatch('canvas.show', {'kind': 'markdown', 'content': '# Old artifact'})
        saved = json.loads(app.db.execute("SELECT value FROM client_views WHERE id='reader'").fetchone()[0])
        reference = saved['canvas']['contentResource']
        app.db.execute('DELETE FROM state_resources WHERE id=?', (reference['$resource'],))
        app.clients.records['reader']['canvas'] = saved['canvas']
        app._client_snapshots.clear()
        with app.clients.bind('reader'):
            canvas = app.browser_state()['canvas']
            assert canvas['contentResource'] == reference
            assert canvas['renderReports']['stored-source']['status'] == 'error'
    finally:
        await app.close()


@pytest.mark.parametrize('missing', ['index', 'blob'])
async def test_reopen_and_select_unavailable_artifact_preserve_reference(tmp_path, missing):
    from amplifier_web.resource_files import root
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    app.clients.attach('reader')
    try:
        with app.clients.bind('reader'):
            await app.dispatch('session.create', {})
            sid = app.state['selectedSessionId']
            await app.dispatch('canvas.show', {'kind': 'markdown', 'content': '# Retained source'})
            original = app.state['canvasArtifacts'][0].copy()
            reference = original['body']['$resource']
            saved = app.db.execute('SELECT value FROM state_resources WHERE id=?', (reference,)).fetchone()[0]
            path = root(app.db) / (reference + '.json')
            content = path.read_bytes()
            if missing == 'index':
                app.db.execute('DELETE FROM state_resources WHERE id=?', (reference,))
            else:
                path.unlink()
            # A stale client selection must not block the drawer or chat switch.
            app.state['canvas'] = {'id': 'old-client', 'sessionId': 'other', 'open': False}
            await app.dispatch('canvas.reopen', {})
            assert app.state['canvas']['open']
            assert app.state['canvas']['id'] == original['id']
            assert app.state['canvas']['contentResource'] == original['body']
            assert app.browser_state()['canvas']['renderReports']['stored-source']['status'] == 'error'
            await app.dispatch('canvas.select', {'id': original['id']})
            assert app.state['canvasArtifacts'][0]['body'] == original['body']
            assert app.state['selectedSessionId'] == sid
            # Exact original source recovery remains possible; no blank snapshot replaces it.
            path.write_bytes(content)
            app.db.execute('INSERT OR REPLACE INTO state_resources VALUES (?,?)', (reference, saved))
            await app.dispatch('canvas.select', {'id': original['id']})
            assert app.state['canvas']['content'] == '# Retained source'
    finally:
        await app.close()


@pytest.mark.parametrize('missing', ['index', 'blob'])
async def test_unavailable_retained_source_suspends_pruning_unknown_nested_references(tmp_path, missing):
    from amplifier_web.resource_files import collect, put, root
    app = AppService(tmp_path / 'app', workspace=tmp_path)
    try:
        nested = put(app.db, {'content': 'Nested source retained by the missing parent'})
        parent = put(app.db, {'surface': nested})
        app.state['canvasArtifacts'] = [{'id': 'retained', 'body': parent}]
        if missing == 'index':
            app.db.execute('DELETE FROM state_resources WHERE id=?', (parent['$resource'],))
        else:
            (root(app.db) / (parent['$resource'] + '.json')).unlink()
        assert collect(app.db, app.state) == []
        assert app.db.execute('SELECT 1 FROM state_resources WHERE id=?', (nested['$resource'],)).fetchone()
        assert (root(app.db) / (nested['$resource'] + '.json')).exists()
        app._last_storage_sweep = 0
        await app.dispatch('canvas.reopen', {})
        assert app.state['canvas']['open']
    finally:
        await app.close()


@pytest.mark.parametrize('missing', ['index', 'blob'])
@pytest.mark.parametrize('kind', ['markdown', 'html'])
async def test_missing_global_canvas_body_does_not_prevent_host_restart(tmp_path, missing, kind):
    import sqlite3
    from amplifier_web.resource_files import root
    home = tmp_path / 'app'
    app = AppService(home, workspace=tmp_path)
    await app.dispatch('session.create', {})
    await app.dispatch('canvas.show', {'kind': kind, 'content': '# Preserve its reference'})
    aid = app.state['canvas']['id']
    sid = app.state['selectedSessionId']
    body = app.state['canvasArtifacts'][0]['body'].copy()
    blob = root(app.db) / (body['$resource'] + '.json')
    await app.close()
    if missing == 'index':
        with sqlite3.connect(home / 'app.sqlite3') as db:
            db.execute('DELETE FROM state_resources WHERE id=?', (body['$resource'],))
    else:
        blob.unlink()
    restored = AppService(home, workspace=tmp_path)
    try:
        await restored.dispatch('canvas.reopen', {})
        assert restored.state['selectedSessionId'] == sid
        assert restored.state['canvas']['id'] == aid
        assert restored.state['canvas']['open']
        assert restored.state['canvas']['contentResource'] == body
        assert restored.state['canvas']['renderReports']['stored-source']['status'] == 'error'
        assert restored.state['canvasArtifacts'][0]['body'] == body
    finally:
        await restored.close()
