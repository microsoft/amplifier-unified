import asyncio
import base64
import json
import hashlib
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


async def test_full_size_upload_crosses_http_schema_and_storage_boundaries(authenticated_client, tmp_path):
    # Exceeds both the original 8 MiB intake cap and 13 MB HTTP body cap.
    data = PNG + bytes(attachments.MAX_BYTES - len(PNG))
    encoded = base64.b64encode(data).decode()
    app = await create_app(tmp_path, preload_providers=False, workspace=tmp_path,
                           runtime=Runtime(), voice=False, background_updates=False)
    await app['service'].dispatch('session.create', {})
    client = await authenticated_client(app)
    response = await client.post('/api/actions', json={
        'action': 'attachment.add', 'args': {'name': 'camera.png', 'base64': encoded},
    })
    assert response.status == 200, await response.text()
    result = await response.json()
    assert encoded not in json.dumps(result)
    rows = list((tmp_path / 'attachments').glob('*/metadata.json'))
    assert len(rows) == 1
    row = attachments.metadata(tmp_path, rows[0].parent.name)
    assert row['size'] == 32 * 1024 * 1024
    downloaded = await client.get(row['url'])
    assert downloaded.status == 200
    assert hashlib.sha256(await downloaded.read()).digest() == hashlib.sha256(data).digest()
    with pytest.raises(ValueError, match='32 MB'):
        upload(tmp_path, 'too-large.png', data + b'!')


async def test_feedback_keeps_its_independent_file_limit(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        with pytest.raises(AppError, match='8 MB'):
            await app.dispatch('feedback.attachment.add', {
                'requestId': 'oversize-feedback', 'name': 'report.bin',
                'base64': base64.b64encode(bytes(8 * 1024 * 1024 + 1)).decode(),
            })
        assert not (tmp_path / 'attachments').exists()
    finally:
        await app.close()


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


def test_pasted_text_metadata_and_content_survive_reload(tmp_path):
    from amplifier_web.attachments import save, metadata, file_path
    import base64
    text = 'Diagnostic report 📋\r\n' * 1000
    row = save(tmp_path, 'Pasted text.txt', base64.b64encode(text.encode()).decode(), source='clipboard-text')
    assert row['source'] == 'clipboard-text'
    assert len(row['preview']) <= 120
    assert metadata(tmp_path, row['id']) == row
    path, _ = file_path(tmp_path, row['id'])
    assert path.read_bytes() == text.encode()


@pytest.mark.parametrize('private', [False, True], ids=['legacy', 'private-client'])
@pytest.mark.parametrize('evidence', ['input-delivered', 'runtime-delivery', 'delivery-check'])
async def test_accepted_attachments_settle_before_rpc_return(tmp_path, private, evidence):
    """Exercise the real settlement/publication boundary without a provider."""
    from amplifier_web.runtime import normalize_event

    class HeldRuntime(Runtime):
        def __init__(self):
            super().__init__()
            self.entered, self.release = asyncio.Event(), asyncio.Event()
            self.calls = []

        async def send(self, session, text, input_id, emit):
            self.calls.append(input_id)
            self.entered.set()
            await asyncio.wait_for(self.release.wait(), 15)
            return {'accepted': True}

        async def delivery(self, session, input_id):
            return 'accepted'

        async def close(self):
            self.release.set()

    runtime = HeldRuntime()
    app = AppService(tmp_path, runtime, workspace=tmp_path)
    sender = 'sender' if private else None
    pending = entered = None

    async def action(identity, name, args, **kwargs):
        with app.clients.bind(identity):
            return await asyncio.wait_for(app.dispatch(name, args, **kwargs), 10)

    async def add(identity, sid):
        # Same name and bytes deliberately yield different upload identities.
        await action(identity, 'attachment.add', {
            'sessionId': sid, 'name': 'same.png', 'base64': base64.b64encode(PNG).decode()})
        # Action snapshots include the selected chat, not every nonselected
        # upload target. Read the explicit caller's actual private draft owner.
        with app.clients.bind(identity):
            return dict(app.clients.attachments(app._session(sid))[-1])

    def draft(identity, session):
        with app.clients.bind(identity):
            return list(app.clients.attachments(session))

    try:
        await action(None, 'session.create', {})
        owner = app._session()
        await action(None, 'session.create', {})
        other = app._session()
        if private:
            app.clients.attach(sender)
        app.clients.attach('independent')
        await action(sender, 'session.select', {'id': owner['id']})
        sent = await add(sender, owner['id'])
        independent = await add('independent', owner['id'])
        other_file = await add(sender, other['id'])
        pending = asyncio.create_task(action(sender, 'conversation.send', {
            'sessionId': owner['id'], 'text': 'Sent A', 'attachmentIds': [sent['id']]}, command_id='sent-A'))
        entered = asyncio.create_task(runtime.entered.wait())
        done, _ = await asyncio.wait({pending, entered}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        if pending in done:
            await pending
            pytest.fail('The synthetic RPC returned before its release')
        assert entered in done, 'Send did not reach the bounded synthetic runtime'
        newer = await add(sender, owner['id'])
        await action(sender, 'view.update', {'patch': {'draft': 'Newer text B'}})
        if private:
            # A real page reload copies the pending private draft. Delivery must
            # settle both copies, not whichever client happens to be bound now.
            app.clients.attach('resumed', resume=sender)
        await action(sender, 'session.select', {'id': other['id']})

        # Neither another chat/input nor negative/uncertain evidence consumes A.
        async with app.lock:
            app._delivery(other, 'sent-A', 'accepted')
            app._delivery(owner, 'not-this-input', 'accepted')
            for status in ('unknown', 'failed'):
                app._delivery(owner, 'sent-A', status)
                assert draft(sender, owner) == [sent, newer]
        assert not pending.done()

        # Deliberately deliver from an unrelated client context. The callback
        # must use the accepted message's chat and upload IDs, not this context.
        with app.clients.bind('independent'):
            if evidence == 'input-delivered':
                event = normalize_event({'type': 'input.delivered', 'input_id': 'sent-A'}, owner['id'], 'sent-A')
                await asyncio.wait_for(app.on_runtime_event(*event), 5)
            elif evidence == 'runtime-delivery':
                await asyncio.wait_for(app.on_runtime_event('runtime.delivery', {
                    'sessionId': owner['id'], 'inputId': 'sent-A', 'delivery': 'accepted'}), 5)
            else:
                result = await asyncio.wait_for(app.dispatch('conversation.delivery', {
                    'sessionId': owner['id'], 'inputId': 'sent-A'}), 5)
                assert result['result']['delivery'] == 'accepted'

        assert not pending.done(), 'Assertions must precede the RPC acknowledgement'
        assert owner['messages'][0]['delivery']['status'] == 'accepted'
        assert owner['messages'][0]['attachments'] == [sent]
        assert draft(sender, owner) == [newer]
        assert draft('independent', owner) == [independent]
        assert draft(sender, other) == [other_file]
        if private:
            assert draft('resumed', owner) == [newer]
            assert app.clients.records[sender]['drafts'][owner['id']] == 'Newer text B'
            assert app.clients.records['resumed']['drafts'][owner['id']] == 'Newer text B'
            for identity in (sender, 'resumed'):
                saved = json.loads(app.db.execute('SELECT value FROM client_views WHERE id=?', (identity,)).fetchone()[0])
                assert saved['attachments'][owner['id']] == [newer]
        else:
            assert owner['draft'] == 'Newer text B'
        assert attachments.file_path(tmp_path, sent['id'])[0].read_bytes() == PNG

        # Repeated acceptance and a late weaker reply cannot erase the new draft.
        async with app.lock:
            for status in ('accepted', 'unknown', 'failed'):
                app._delivery(owner, 'sent-A', status)
            assert owner['messages'][0]['delivery']['status'] == 'accepted'
        runtime.release.set()
        await asyncio.wait_for(pending, 5)
        assert draft(sender, owner) == [newer]
        assert runtime.calls == ['sent-A']
    finally:
        runtime.release.set()
        for task in (pending, entered):
            if task is not None and not task.done():
                task.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(*(task for task in (pending, entered) if task is not None),
                                                  return_exceptions=True), 5)
        finally:
            await asyncio.wait_for(app.close(), 5)

    restored = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        session = restored._session(owner['id'])
        with restored.clients.bind(sender):
            assert restored.clients.attachments(session) == [newer]
        if private:
            with restored.clients.bind('resumed'):
                assert restored.clients.attachments(session) == [newer]
        assert session['messages'][0]['attachments'] == [sent]
        assert attachments.file_path(tmp_path, sent['id'])[0].read_bytes() == PNG
    finally:
        await asyncio.wait_for(restored.close(), 5)


async def test_unnamed_upload_identity_follows_only_its_exact_creation(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        app.clients.attach('upload-owner')
        with app.clients.bind('upload-owner'):
            await app.dispatch('session.draft', {'workspace': str(tmp_path)})
            identity = app.clients.attachment_draft_id()
            args = {'sessionId': None, 'expectedDraftId': identity, 'name': 'first.png',
                    'base64': base64.b64encode(PNG).decode()}
            await app.dispatch('attachment.add', args)
            original = list(app.clients.attachments(None))
            created = await app.dispatch('session.create', {'fromDraft': True, 'workspace': str(tmp_path)})
            owner = app._session(created['sessionId'])
            await app.dispatch('session.create', {'title': 'Unrelated chat', 'workspace': str(tmp_path)})
            other = app._session()
            await app.dispatch('attachment.add', {**args, 'name': 'late.png'})
            assert app.state['selectedSessionId'] == other['id']
            assert len(app.clients.attachments(owner)) == 2
            assert app.clients.attachments(owner)[0] == original[0]
            assert app.clients.attachments(other) == []
            assert app.clients.attachments(None) == []
    finally:
        await asyncio.wait_for(app.close(), 5)


async def test_replaced_unnamed_location_rejects_late_upload_without_losing_new_draft(tmp_path):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    try:
        app.clients.attach('upload-owner')
        app.clients.attach('independent')
        with app.clients.bind('upload-owner'):
            await app.dispatch('session.draft', {'workspace': str(tmp_path)})
            identity = app.clients.attachment_draft_id()
            await app.dispatch('session.draft', {'location': {'kind': 'managed'}})
            await app.dispatch('view.update', {'patch': {'draft': 'Keep newer text'}})
            args = {'sessionId': None, 'expectedDraftId': identity, 'name': 'late.png',
                    'base64': base64.b64encode(PNG).decode()}
            with pytest.raises(AppError, match='original new-chat draft changed'):
                await app.dispatch('attachment.add', args)
            assert app.clients.attachments(None) == []
            assert app.state['view']['draft'] == 'Keep newer text'
        with app.clients.bind('independent'):
            with pytest.raises(AppError, match='original new-chat draft changed'):
                await app.dispatch('attachment.add', args)
            assert app.clients.attachments(None) == []
        assert not (tmp_path / 'attachments').exists()
    finally:
        await asyncio.wait_for(app.close(), 5)


async def test_slow_creation_cannot_bind_a_newer_unnamed_draft(tmp_path, monkeypatch):
    app = AppService(tmp_path, Runtime(), workspace=tmp_path)
    started, release = asyncio.Event(), asyncio.Event()
    pending = None
    original_thread = app.worktrees.finish_thread

    async def hold_creation(function, *args):
        if function.__name__ == '_create_workspace_folder':
            started.set()
            await asyncio.wait_for(release.wait(), 5)
        return await original_thread(function, *args)

    monkeypatch.setattr(app.worktrees, 'finish_thread', hold_creation)
    try:
        app.clients.attach('upload-owner')
        with app.clients.bind('upload-owner'):
            await app.dispatch('session.draft', {'workspace': str(tmp_path)})
            identity = app.clients.attachment_draft_id()
            pending = asyncio.create_task(app.dispatch('session.create', {
                'workspace': str(tmp_path), 'fromDraft': True, 'expectedDraftId': identity,
            }))
            await asyncio.wait_for(started.wait(), 3)
            await app.dispatch('session.draft', {'location': {'kind': 'managed'}})
            newer_id = app.clients.attachment_draft_id()
            await app.dispatch('view.update', {'patch': {'draft': 'Keep newer draft'}})
            await app.dispatch('attachment.add', {
                'sessionId': None, 'expectedDraftId': newer_id, 'name': 'newer.png',
                'base64': base64.b64encode(PNG).decode(),
            })
            newer = list(app.clients.attachments(None))
            release.set()
            with pytest.raises(AppError, match='original new-chat draft changed'):
                await asyncio.wait_for(pending, 5)
            assert app.state['selectedSessionId'] is None
            assert app.state['view']['draft'] == 'Keep newer draft'
            assert app.clients.attachments(None) == newer
            assert app.clients.attachment_target(None, newer_id) is None
            assert not app.state['sessions']
    finally:
        release.set()
        if pending is not None:
            await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), 5)
        await asyncio.wait_for(app.close(), 5)
