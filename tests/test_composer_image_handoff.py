"""Native forks and signed task transfers hand off detached, ordinary image blocks."""
import base64
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from amplifier_portability.capsule import read_capsule
from amplifier_web import attachments, portability_data
from amplifier_web.host.media import NAMESPACE, RequestOnlyRegistry
from amplifier_web.host.storage import SessionStore
from amplifier_web.session_store import fork_session
from test_output_images import png
from test_portability import export, host_env, setup_hosts

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Compact composer writes are POSIX-only')


async def seed(home, store, sid):
    directory = store.directory(sid)
    directory.mkdir(exist_ok=True, parents=True)
    media = store.media(sid)
    registry = RequestOnlyRegistry(directory / 'media-upload-issuance.json')
    rows = []
    uploads = [attachments.save(home, name, base64.b64encode(png(width=width)).decode())
               for name, width in (('first.png', 3), ('different.png', 5), ('another-name.png', 3))]

    async def add_message(row):
        rows.append(row)

    async def checkpoint():
        saved = store.save(sid, rows, {})
        store.acknowledge_media(sid, saved, media)

    coordinator = SimpleNamespace(get_capability={
        NAMESPACE: SimpleNamespace(enabled=True), 'web.media.composer': media,
        'web.media.store': media, 'web.media.request_only': registry,
        'session.durable_checkpoint': checkpoint}.get)
    context = attachments.MediaAttachmentContext(SimpleNamespace(add_message=add_message), coordinator)
    cmd = SimpleNamespace(id='original-input', kind='user', source='user',
                          text='Keep the order', attachments=uploads)
    try:
        content = attachments.encode(cmd, coordinator, image_references=True)
        metadata = {'amplifier_input': {'version': 1, 'kind': 'user', 'id': cmd.id}, 'original': True}
        await context.add_message({'role': 'user', 'metadata': metadata, 'content': content})
    finally:
        media.close()
    row = store.load(sid)[0][0]
    assert [ref['provenance']['id'] for ref in row['metadata'][NAMESPACE]['refs']] == [u['id'] for u in uploads]
    return row, uploads


def assert_inline(row, original, uploads):
    assert NAMESPACE not in row['metadata']
    assert row['metadata'] == {key: value for key, value in original['metadata'].items() if key != NAMESPACE}
    assert row['content'][0] == original['content'][0]
    for i, upload in enumerate(uploads):
        label, block = row['content'][1 + i * 2:3 + i * 2]
        assert label == original['content'][1 + i * 2] and upload['id'] in label['text']
        assert block['type'] == 'image'
        assert block['source']['media_type'] == 'image/png'
        assert base64.b64decode(block['source']['data'], validate=True) == png(width=(3, 5, 3)[i])


async def test_fork_and_in_place_edit_of_marked_chat(tmp_path, monkeypatch):
    home = tmp_path / 'app'
    monkeypatch.setenv('AMPLIFIER_HOME', str(home / 'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    store = SessionStore.for_app(home, tmp_path)
    src = {'id': 'source', 'workspace': str(tmp_path), 'bundle': 'fixture', 'messages': [],
           'composerImageReferences': True, 'status': 'idle'}
    original, uploads = await seed(home, store, src['id'])
    path = store.directory(src['id']) / 'transcript.jsonl'
    raw = path.read_bytes()
    preview = fork_session(home, src, 'same-source', prepare_only=True)
    assert preview['context'] == [original]
    fork = fork_session(home, src, 'detached')
    assert 'composerImageReferences' not in fork
    target, = store.load('detached')[0]
    assert_inline(target, original, uploads)
    assert not (store.directory('detached') / 'media-upload-issuance.json').exists()
    assert path.read_bytes() == raw
    for modification in ('substitute input', 'substitute role', 'duplicate', 'uncheckpointed'):
        altered = copy.deepcopy(original)
        if modification == 'substitute input':
            altered['metadata']['amplifier_input']['id'] = 'other'
        elif modification == 'substitute role':
            altered['role'] = 'assistant'
        elif modification == 'uncheckpointed':
            altered['metadata']['original'] = False
        live = [original, altered] if modification == 'duplicate' else [altered]
        with pytest.raises(ValueError, match='confirm the original'):
            fork_session(home, src, 'refused', live_messages=live)
        assert not store.directory('refused').exists() and path.read_bytes() == raw


async def test_handoff_preserves_tool_refs_and_refuses_unissued_or_oversize_images(tmp_path, monkeypatch):
    from amplifier_web.composer_handoff import inline_composer_uploads
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    store = SessionStore.for_app(tmp_path, tmp_path)
    user, _ = await seed(tmp_path, store, 'source')
    directory = store.directory('source')
    tool_only = {'role': 'tool', 'content': 'Tool-owned pixels remain behind their original policy',
                 'metadata': {NAMESPACE: {'refs': [{'provenance': {'kind': 'tool'}}]}}}
    unchanged = [tool_only]
    assert inline_composer_uploads(directory, unchanged) is unchanged
    with pytest.raises(ValueError, match='transfer limit'):
        inline_composer_uploads(directory, [user], limit=1)
    (directory / 'media-upload-issuance.json').unlink()
    with pytest.raises(ValueError, match='confirm the original'):
        inline_composer_uploads(directory, [user])
    assert store.load('source')[0] == [user]


async def test_signed_transfer_inlines_current_and_backup_and_archives_exact_sources(tmp_path, monkeypatch):
    app, target, source, dest, workspace, repo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        session = app._session(sid)
        session['composerImageReferences'] = True
        store = SessionStore.for_app(app.data_dir, workspace)
        original, uploads = await seed(app.data_dir, store, sid)
        directory = store.directory(sid)
        primary = directory / 'transcript.jsonl'
        backup = directory / 'transcript.jsonl.backup'
        backup.write_bytes(primary.read_bytes() + b'{"role":"assistant","content":"earlier revision"}\n')
        originals = {p.name: p.read_bytes() for p in (primary, backup)}
        checkpoint = app.data_dir / 'sessions' / sid / 'context-checkpoint.json'
        checkpoint.write_text('{"old":"derived"}')
        receipt, _ = await export(app, target, sid, workspace)
        payload = read_capsule(Path(receipt['package']))['body']['payload']
        assert 'contextCheckpoint' not in payload and 'composerImageReferences' not in payload['session']
        for name, raw in originals.items():
            entry = payload['native'][name]
            assert entry['sourceSha256'] == portability_data.digest(raw)
            portable = [json.loads(line) for line in portability_data.decode_file(entry).splitlines()]
            assert_inline(portable[0], original, uploads)
            assert (directory / name).read_bytes() == raw
            if name.endswith('.backup'):
                assert portable[-1]['content'] == 'earlier revision'

        host_env(monkeypatch, dest)
        staged = (await target.dispatch('portability.stage', {'path': receipt['package'], 'repository': str(repo)}))['result']
        host_env(monkeypatch, source)
        release_args = {'sessionId': sid, 'id': receipt['id'], 'expectedRevision': receipt['revision'], 'path': staged['receiptPath']}
        # The semantic projection is identical if whitespace changes. The signed
        # raw digest still prevents advancing ownership on a different source.
        backup.write_bytes(originals[backup.name].replace(b'{', b'{ ', 1))
        with pytest.raises(ValueError, match='changed after export'):
            await app.portability.release(release_args)
        assert app.portability.node.get(receipt['id'])['phase'] == 'prepared'
        backup.write_bytes(originals[backup.name])
        released = (await app.dispatch('portability.release', release_args))['result']
        for name, raw in originals.items():
            assert (Path(released['archive']) / name).read_bytes() == raw
        host_env(monkeypatch, dest)
        active = (await target.dispatch('portability.activate', {'sessionId': sid, 'id': staged['id'],
            'expectedRevision': staged['revision'], 'path': released['receiptPath']}))['result']
        assert active['phase'] == 'active'
        imported = target._session(sid)
        assert 'composerImageReferences' not in imported
        dest_directory = portability_data.native_directory(target, imported)
        for name in originals:
            assert (dest_directory / name).read_bytes() == portability_data.decode_file(payload['native'][name])
        assert not (dest_directory / 'media-upload-issuance.json').exists()
        assert not (target.data_dir / 'sessions' / sid / 'context-checkpoint.json').exists()
    finally:
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, dest)
        await target.close()
