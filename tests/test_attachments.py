import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from amplifier_web import attachments
from amplifier_web.runtime import RuntimeManager
from amplifier_web.server import create_app
from amplifier_web.service import AppError, AppService
from test_service import Runtime

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


def upload(home, name='tiny.png', data=PNG):
    return attachments.save(home, name, base64.b64encode(data).decode())


def test_private_storage_sniffs_content_and_bounds_data(tmp_path):
    row = upload(tmp_path, '../../metadata.json')
    path, saved = attachments.file_path(tmp_path, row['id'])
    assert row == saved and row['mime'] == 'image/png'
    assert row['name'] == 'metadata.json' and path.name == 'content'
    assert path.read_bytes() == PNG
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert upload(tmp_path, 'photo.svg', b'<svg><script>alert(1)</script></svg>')['mime'] == 'text/plain'
    for encoded in ('', 'not base64!', 'A' * (((attachments.MAX_BYTES + 2) // 3) * 4 + 4)):
        with pytest.raises(ValueError):
            attachments.save(tmp_path, 'bad', encoded)
    for identity in ('../outside', '', None, 'z' * 32):
        with pytest.raises(ValueError):
            attachments.file_path(tmp_path, identity)


@pytest.mark.parametrize('part', ['content', 'metadata.json', 'directory', 'root'])
def test_symbolic_links_cannot_escape_private_store(tmp_path, part):
    home = tmp_path / 'home'
    row = upload(home)
    directory = attachments.location(home, row['id'])
    target = directory if part == 'directory' else directory.parent if part == 'root' else directory / part
    moved = tmp_path / 'outside'
    target.rename(moved)
    target.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises(ValueError):
        attachments.file_path(home, row['id'])


def test_encoder_reads_authoritative_metadata_and_limits_inline_text(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    rows = [upload(tmp_path), upload(tmp_path, 'notes.txt', b'a' * 60000), upload(tmp_path, 'more.txt', b'b' * 60000)]
    content = attachments.encode(SimpleNamespace(text='Review these', attachments=rows))
    assert content[0]['text'] == 'Review these'
    image = next(item for item in content if item['type'] == 'image')
    assert base64.b64decode(image['source']['data']) == PNG
    assert image['source']['media_type'] == 'image/png'
    assert sum(item.get('text', '').startswith('Attached file contents:') for item in content) == 1
    assert 'appropriate tool' in content[-1]['text']
    path, _ = attachments.file_path(tmp_path, rows[0]['id'])
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='unavailable'):
        attachments.encode(SimpleNamespace(text='Review', attachments=[rows[0]]))


async def test_draft_attachment_send_is_durable_and_private(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        await app.dispatch('session.create', {})
        first = app.state['selectedSessionId']
        encoded = base64.b64encode(PNG).decode()
        result = await app.dispatch('attachment.add', {'name': 'tiny.png', 'base64': encoded}, command_id='upload')
        row = result['state']['sessions'][0]['draftAttachments'][0]
        assert encoded not in json.dumps(result)
        await app.dispatch('session.create', {})
        with pytest.raises(AppError, match='draft'):
            await app.dispatch('conversation.send', {'text': '', 'attachmentIds': [row['id']]})
        await app.dispatch('conversation.send', {'sessionId': first, 'text': '', 'attachmentIds': [row['id']]}, command_id='image-only')
        await asyncio.gather(*app.tasks)
        session = next(item for item in app.get_state()['sessions'] if item['id'] == first)
        assert not session['draftAttachments']
        assert session['messages'][0]['attachments'] == [row]
        assert session['messages'][0]['text'] == 'Please review the attached files.'
        await app.dispatch('attachment.remove', {'sessionId': first, 'id': row['id']})
        assert attachments.file_path(tmp_path, row['id'])[0].exists()
        assert encoded not in json.dumps(app.get_state())
    finally:
        await app.close()
    restored = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        session = next(item for item in restored.get_state()['sessions'] if item['id'] == first)
        assert session['messages'][0]['attachments'] == [row]
    finally:
        await restored.close()


async def test_runtime_routes_only_matching_message_attachments():
    runtime = RuntimeManager()
    async def start(*args): pass
    async def request(*args, **kwargs): return kwargs
    runtime.start, runtime._request = start, request
    result = await runtime.send({'id': 'session', 'messages': [
        {'inputId': 'old', 'attachments': [{'id': 'old-file'}]},
        {'inputId': 'current', 'attachments': [{'id': 'current-file'}]}]}, 'Review', 'current', None)
    assert result['attachments'] == [{'id': 'current-file'}]


async def test_attachment_http_images_inline_documents_download(authenticated_client, tmp_path):
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path, runtime=Runtime(), voice=False, background_updates=False)
    client = await authenticated_client(app)
    for row, data, disposition in [(upload(tmp_path), PNG, 'inline'), (upload(tmp_path, 'note.html', b'<script>bad</script>'), b'<script>bad</script>', 'attachment')]:
        response = await client.get(row['url'])
        assert response.status == 200 and await response.read() == data
        assert response.headers['Content-Type'] == row['mime']
        assert response.headers['Content-Disposition'].startswith(disposition)
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert response.headers['Cache-Control'] == 'no-store'
        rejected = await client.get(row['url'], headers={'Sec-Fetch-Site': 'cross-site'})
        assert rejected.status == 403
    assert (await client.get('/api/attachments/' + '0' * 32)).status == 404


def test_real_core_loop_live_receives_multimodal_message():
    python = os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python:
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON for real Core/loop-live multimodal verification')
    result = subprocess.run([python, str(Path(__file__).parent / 'fixtures/attachments_runtime_probe.py')], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"image_received": true' in result.stdout
