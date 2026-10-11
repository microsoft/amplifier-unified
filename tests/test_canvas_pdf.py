"""PDF snapshots keep binary bytes out of app state and preserve file boundaries."""
import copy

import pytest

from amplifier_web.canvas_downloads import filename, pdf_bytes
from amplifier_web.state_storage import resource
from amplifier_web.service import AppError
from test_canvas_files import args
from test_canvas_views import app, command, view


async def test_pdf_snapshot_original_bytes_and_private_view(app, tmp_path):
    original = b'%PDF-1.7\n\x00\xff\noriginal bytes'
    path = tmp_path / 'Quarterly report.pdf'
    path.write_bytes(original)
    await command(app, 'view.update', {'patch': {'draft': 'Keep draft'}})
    other = copy.deepcopy(app.clients.records['two'])
    await command(app, 'canvas.openFile', args(app, path.name))
    with app.clients.bind('one'):
        canvas = app.state['canvas']
        assert canvas['kind'] == 'pdf'
        assert 'content' not in canvas
        row = next(r for r in app.state['canvasArtifacts'] if r['id'] == canvas['id'])
        saved = {**row, **resource(app.db, row['body']['$resource'])}
        assert pdf_bytes(saved) == (original, 'application/pdf')
        assert filename(saved) == path.name
        identity = canvas['id']
    assert app.clients.records['two'] == other
    path.write_bytes(b'replaced on disk')
    await command(app, 'canvas.select', {'id': identity})
    with app.clients.bind('one'):
        assert 'content' not in app.state['canvas']
    result = await command(app, 'canvas.download', {'id': identity})
    assert result['effects'][0]['type'] == 'download.url'
    assert pdf_bytes(saved)[0] == original
    with pytest.raises(AppError, match='Select text in the PDF'):
        await command(app, 'canvas.copy', {'id': identity})


async def test_pdf_escape_and_size_bound(app, tmp_path):
    from amplifier_web.workspace_canvas import MAX_PDF
    outside = tmp_path.parent / (tmp_path.name + '-outside.pdf')
    outside.write_bytes(b'%PDF-secret')
    try:
        (tmp_path / 'escape.pdf').symlink_to(outside)
        for path in [str(outside), 'escape.pdf']:
            result = await command(app, 'canvas.openFile', args(app, path))
            assert result['result']['status'] == 'unavailable'
        with (tmp_path / 'large.pdf').open('wb') as handle:
            handle.truncate(MAX_PDF + 1)
        result = await command(app, 'canvas.openFile', args(app, 'large.pdf'))
        assert result['result']['status'] == 'unavailable'
    finally:
        outside.unlink()
