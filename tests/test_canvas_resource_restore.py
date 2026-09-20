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
