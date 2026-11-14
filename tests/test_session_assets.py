"""Real byte/ref/checkpoint seams, without inference or a running host."""
import base64
from contextlib import closing
import copy
import io
import json
from types import SimpleNamespace

import pytest

from amplifier_web import attachments
from amplifier_web.host.storage import SessionStore
from amplifier_web import session_assets as assets


def png():
    from PIL import Image
    stream = io.BytesIO()
    Image.new('RGB', (2, 3), color='navy').save(stream, format='PNG')
    return stream.getvalue()


async def uploaded(tmp_path, monkeypatch):
    from amplifier_foundation.session.media import SessionMediaCapability
    home = tmp_path / 'home'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    row = attachments.save(home, 'image.png', base64.b64encode(png()).decode())
    store = SessionStore(tmp_path / 'native')
    media = store.media('source')
    cap = SessionMediaCapability(media, enabled=True)
    coordinator = SimpleNamespace(get_capability=lambda _: cap)
    encoded = await attachments.encode(SimpleNamespace(id='admitted', text='Review', attachments=[row]), coordinator)
    message = {'role': 'user', **encoded}
    canonical = store.save('source', [message], {})
    store.acknowledge_media('source', canonical, media)
    return home, store, media, message, row


async def test_upload_reference_checkpoint_and_selected_fork(tmp_path, monkeypatch):
    home, store, media, message, row = await uploaded(tmp_path, monkeypatch)
    try:
        ref = assets.message_refs([message], 'source')[0]
        assert ref['provenance']['id'] == row['id']
        assert ref['provenance']['expires_at'] is None
        assert not any(item['type'] == 'image' for item in message['content'])
        assert base64.b64encode(png()).decode() not in json.dumps(message)
        for path in store.directory('source').rglob('*.json*'):
            assert base64.b64encode(png()) not in path.read_bytes()
        with media.resolve_ref(ref) as stream:
            assert stream.read() == png()
        with closing(store.media('target')) as target:
            fork = assets.adopt_messages([message], media, target)
            target_ref = assets.message_refs(fork, 'target')[0]
            assert target_ref['provenance'] == ref['provenance']
            assert target_ref['sha256'] == ref['sha256']
            with pytest.raises(ValueError):
                target.resolve_ref(target_ref)
            canonical = store.save('target', fork, {})
            store.acknowledge_media('target', canonical, target)
            with target.resolve_ref(target_ref) as stream:
                assert stream.read() == png()
            assert assets.adopt_messages([], media, target) == []
    finally:
        media.close()


async def test_manifest_closure_independent_source_home_and_exact_authority(tmp_path, monkeypatch):
    home, store, media, message, row = await uploaded(tmp_path, monkeypatch)
    try:
        refs = assets.message_refs([message], 'source')
        manifest = assets.capture_media(media, refs)
        uploads = assets.upload_rows([message])
        legacy = assets.capture_uploads(home, uploads)
        assets.validate_media(manifest, refs)
        assets.validate_uploads(legacy, uploads)
        destination_home = tmp_path / 'destination-home'
        assets.install_uploads(destination_home, legacy, uploads)
        assert attachments.file_path(destination_home, row['id'])[0].read_bytes() == png()
        with closing(store.media('destination')) as target:
            with pytest.raises(ValueError):
                assets.install_media(target, manifest, refs, receipt=object(), verifier=lambda *a, **k: False)
            token = object()
            assets.install_media(target, manifest, refs, receipt=token,
                verifier=lambda candidate, *, receipt, target_session_id:
                    receipt is token and candidate in refs and target_session_id == 'destination')
            mapped = {**refs[0], 'session_id': 'destination'}
            with target.resolve_ref(mapped, committed=False) as stream:
                assert stream.read() == png()
        for mutation in ('missing', 'extra', 'bytes'):
            altered = copy.deepcopy(manifest)
            if mutation == 'missing':
                altered.clear()
            elif mutation == 'extra':
                altered['forged'] = next(iter(manifest.values()))
            else:
                next(iter(altered.values()))['content']['sha256'] = '0' * 64
            with pytest.raises(ValueError):
                assets.validate_media(altered, refs)
    finally:
        media.close()


def test_legacy_upload_conflicts_and_path_claims_fail_before_overwrite(tmp_path):
    home = tmp_path / 'source'
    row = attachments.save(home, 'image.png', base64.b64encode(png()).decode())
    uploads = assets.upload_rows(visible=[{'attachments': [row]}])
    manifest = assets.capture_uploads(home, uploads)
    original = attachments.file_path(home, row['id'])[0].read_bytes()
    altered = copy.deepcopy(manifest)
    altered[row['id']]['content'] = assets.packed(b'wrong')
    with pytest.raises(ValueError):
        assets.install_uploads(home, altered, uploads)
    assert attachments.file_path(home, row['id'])[0].read_bytes() == original
    with pytest.raises(ValueError):
        assets.upload_rows(visible=[{'attachments': [{**row, 'id': '../../escape'}]}])
    assert assets.upload_rows([{'role': 'user', 'content': json.dumps({'attachments': [row]})}]) == {}


@pytest.mark.parametrize('name,data', [('document.pdf', b'%PDF-synthetic'),
                                      ('large.txt', b'text ' * 25000)])
async def test_nonimage_locator_works_at_destination(tmp_path, monkeypatch, name, data):
    origin = tmp_path / 'origin'
    destination = tmp_path / 'destination'
    row = attachments.save(origin, name, base64.b64encode(data).decode())
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(origin))
    encoded_before = await attachments.encode(SimpleNamespace(text='Read', attachments=[row]))
    selected = assets.upload_rows(visible=[{'attachments': [row]}])
    assets.install_uploads(destination, assets.capture_uploads(origin, selected), selected)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(destination))
    from amplifier_core.message_models import ChatRequest, Message
    request = ChatRequest(messages=[Message(role='user', **encoded_before)])
    original = request.model_dump()
    projected = attachments.project_attachment_paths(request, destination)
    locator = projected.messages[0].content[-1].text.split('\nLocal file: ', 1)[1]
    from pathlib import Path
    assert Path(locator).read_bytes() == data
    assert str(origin) not in projected.messages[0].content[-1].text
    assert request.model_dump() == original


@pytest.mark.parametrize('expires_during_transfer', [False, True, 'during-decode'])
async def test_real_portability_media_and_legacy_closure_after_origin_absent(tmp_path, monkeypatch, expires_during_transfer):
    from amplifier_foundation.session.media import SessionMediaCapability
    from test_portability import setup_hosts, host_env, export
    app, target, source, destination, root, destrepo, sid, *_ = await setup_hosts(tmp_path, monkeypatch)
    try:
        host_env(monkeypatch, source)
        row = attachments.save(app.data_dir, 'uploaded.png', base64.b64encode(png()).decode())
        legacy = attachments.save(app.data_dir, 'legacy.pdf', base64.b64encode(b'%PDF-example').decode())
        session = app._session(sid)
        session['messages'][0]['attachments'] = [row, legacy]
        store = SessionStore.for_app(app.data_dir, root)
        with closing(store.media(sid)) as media:
            cap = SessionMediaCapability(media, enabled=True)
            encoded = await attachments.encode(SimpleNamespace(id='input', text='Keep this task', attachments=[row]),
                SimpleNamespace(get_capability=lambda _: cap))
            rows = [{'role': 'user', **encoded}, {'role': 'assistant', 'content': 'Completed original work'}]
            if expires_during_transfer:
                from datetime import datetime, timedelta, timezone
                origin = {'kind': 'host', 'session_id': sid, 'id': 'screen-observation',
                          'created_at': (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                          'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
                staged = await media.ingest(png(), source_format='image-bytes', origin=origin, policy=None)
                await media.publish(staged['staged_refs'], origin=origin, disposition='retain')
                rows[0]['metadata'][assets.MEDIA]['refs'] += staged['staged_refs']
            store.acknowledge_media(sid, store.save(sid, rows, {}), media)
        exported, _ = await export(app, target, sid, root)
        host_env(monkeypatch, destination)
        staged = (await target.dispatch('portability.stage', {'path': exported['package'], 'repository': str(destrepo)}))['result']
        host_env(monkeypatch, source)
        released = (await app.dispatch('portability.release', {'sessionId': sid,
            'id': exported['id'], 'expectedRevision': exported['revision'], 'path': staged['receiptPath']}))['result']
        # Rename, do not delete: the destination cannot use the origin's assets.
        (app.data_dir / 'attachments').rename(app.data_dir / 'attachments-preserved')
        (store.directory(sid) / 'media').rename(store.directory(sid) / 'media-preserved')
        if expires_during_transfer:
            # Advance observation eligibility without sleeping or renewing it.
            import amplifier_foundation.session.media as foundation
            from datetime import timedelta
            real_date = foundation._date
            original_expired = assets.expired
            if expires_during_transfer == 'during-decode':
                # Reproduce the final-owner race, not just expiry before the
                # host check: the exact source is already irreversibly released.
                # Arm only at the install loop's last unexpired source check,
                # then cross expiry while decoding that signed original payload.
                armed, crossings = False, []
                original_unpacked = assets.unpacked
                def expired_before_decode(ref):
                    nonlocal armed
                    result = original_expired(ref)
                    if ref['provenance']['id'] == 'screen-observation':
                        assert not result
                        armed = True
                    return result
                def decode_crossing_expiry(value):
                    nonlocal armed
                    raw = original_unpacked(value)
                    if armed:
                        armed = False
                        crossings.append(True)
                        monkeypatch.setattr(foundation, '_date',
                            lambda value: real_date(value) - timedelta(days=3))
                    return raw
                monkeypatch.setattr(assets, 'expired', expired_before_decode)
                monkeypatch.setattr(assets, 'unpacked', decode_crossing_expiry)
            else:
                monkeypatch.setattr(foundation, '_date', lambda value: real_date(value) - timedelta(days=3))
                monkeypatch.setattr(assets, 'expired', lambda ref: ref['provenance']['expires_at'] is not None or original_expired(ref))
        host_env(monkeypatch, destination)
        result = (await target.dispatch('portability.activate', {'sessionId': sid,
            'id': staged['id'], 'expectedRevision': staged['revision'], 'path': released['receiptPath']}))['result']
        assert result['phase'] == 'active'
        if expires_during_transfer == 'during-decode':
            assert crossings == [True]  # No second decode/import/retry after release.
        current = target._session(sid)
        saved_store = SessionStore.for_app(target.data_dir, current['workspace'])
        saved = saved_store.load(sid)[0]
        ref = assets.message_refs(saved, sid)[0]
        with closing(saved_store.media(sid)) as media:
            saved_store.acknowledge_media(sid, saved, media)
            with media.resolve_ref(ref) as stream:
                assert stream.read() == png()
            if expires_during_transfer:
                screen = assets.message_refs(saved, sid)[1]
                assert screen['provenance'] == origin
                with pytest.raises(ValueError, match='expired'):
                    media.resolve_ref(screen)
        assert attachments.file_path(target.data_dir, legacy['id'])[0].read_bytes() == b'%PDF-example'
        assert attachments.file_path(target.data_dir, row['id'])[0].read_bytes() == png()
    finally:
        host_env(monkeypatch, source)
        await app.close()
        host_env(monkeypatch, destination)
        await target.close()


@pytest.mark.parametrize('point', ['content', 'metadata'])
def test_upload_install_stages_before_uuid_publication_and_retries(tmp_path, monkeypatch, point):
    source, target = tmp_path / 'source', tmp_path / 'target'
    row = attachments.save(source, 'image.png', base64.b64encode(png()).decode())
    selected = assets.upload_rows(visible=[{'attachments': [row]}])
    manifest = assets.capture_uploads(source, selected)
    original = SessionStore._atomic
    def fail(path, value):
        if path.name == ('content' if point == 'content' else 'metadata.json'):
            raise OSError('injected asset staging failure')
        return original(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(SessionStore, '_atomic', fail)
        with pytest.raises(OSError, match='staging failure'):
            assets.install_uploads(target, manifest, selected)
    assert not attachments.location(target, row['id']).exists()
    assets.install_uploads(target, manifest, selected)
    assert attachments.file_path(target, row['id'])[0].read_bytes() == png()


async def test_actual_fork_selects_prefix_while_source_writer_retained(tmp_path, monkeypatch):
    from amplifier_foundation.session.media import SessionMediaCapability
    from amplifier_web.session_store import fork_session
    home = tmp_path / 'home'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(home))
    store = SessionStore.for_app(home, tmp_path)
    source = {'id': 'source', 'status': 'idle', 'workspace': str(tmp_path), 'messages': [
        {'id': 'first', 'role': 'user', 'text': 'First'},
        {'id': 'second', 'role': 'user', 'text': 'Second'}]}
    with closing(store.media('source')) as media:
        cap = SessionMediaCapability(media, enabled=True)
        messages = []
        for index, text in enumerate(('First', 'Second')):
            row = attachments.save(home, text + '.png', base64.b64encode(png()).decode())
            encoded = await attachments.encode(SimpleNamespace(id=text, text=text, attachments=[row]),
                SimpleNamespace(get_capability=lambda _: cap))
            messages.append({'role': 'user', **encoded})
            source['messages'][index]['attachments'] = [row]
        store.acknowledge_media('source', store.save('source', messages, {}), media)
        # Discarded malformed media cannot block an earlier selected prefix.
        messages[-1]['metadata'][assets.MEDIA]['refs'] = [{'session_id': 'foreign'}]
        store.save('source', messages, {})
        result = fork_session(home, source, 'fork', turn=1)
        fork = store.load('fork')[0]
        refs = assets.message_refs(fork, 'fork')
        assert len(refs) == 1 and len(result['messages']) == 1
        assert refs[0]['provenance'] == assets.message_refs(messages[:1], 'source')[0]['provenance']
        with closing(store.media('fork')) as target:
            store.acknowledge_media('fork', fork, target)
            with target.resolve_ref(refs[0]) as stream:
                assert stream.read() == png()
            assert {assets.ref_key(record['ref']) for record in target.journal()} == {assets.ref_key(refs[0])}


def test_nonregular_attachment_refused_without_blocking(tmp_path):
    import os
    row = attachments.save(tmp_path, 'image.png', base64.b64encode(png()).decode())
    content = attachments.file_path(tmp_path, row['id'])[0]
    content.rename(content.with_name('preserved'))
    os.mkfifo(content)
    with pytest.raises(ValueError, match='unavailable'):
        with attachments.open_file(tmp_path, row['id']):
            pytest.fail('FIFO cannot become attachment bytes')


def test_expiry_comparison_stable_and_wrong_policy_visible():
    ref = {'provenance': {'expires_at': '2000-01-02T00:00:00+00:00'}}
    before = {'assets': {'v': 1, 'media': {'ref': {'ref': ref, 'state': 'available', 'content': 'not read'}}, 'attachments': {}}}
    after = {'assets': {'v': 1, 'media': {'ref': {'ref': ref, 'state': 'expired'}}, 'attachments': {}}}
    assert assets.release_comparison(before) == assets.release_comparison(after)
    assert 'content' in before['assets']['media']['ref']


async def test_observer_source_race_stops_before_target_publication(tmp_path, monkeypatch):
    from amplifier_foundation.session.transcript_guard import NativeTranscriptGuard
    _, store, media, message, _ = await uploaded(tmp_path, monkeypatch)
    try:
        refs = assets.message_refs([message], 'source')
        guard = NativeTranscriptGuard(store.directory('source') / 'transcript.jsonl')
        verifier = assets.source_reader_guard(guard, refs)
        with closing(store.media('source', read_only=True, source_read_verifier=verifier)) as observer:
            original = observer._open_verified
            def race(*args):
                stream = original(*args)
                (store.directory('source') / 'transcript.jsonl').write_text('changed source')
                return stream
            monkeypatch.setattr(observer, '_open_verified', race)
            with pytest.raises(Exception, match='changed outside'):
                assets.capture_media(observer, refs)
    finally:
        media.close()


async def test_expired_observation_adopts_metadata_without_pixels_or_renewal(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    store = SessionStore(tmp_path / 'native')
    origin = {'kind': 'tool', 'session_id': 'source', 'id': 'actual-call',
              'created_at': (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
              'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    with closing(store.media('source')) as source:
        staged = await source.ingest(png(), source_format='image-bytes', origin=origin, policy=None)
        await source.publish(staged['staged_refs'], origin=origin, disposition='retain')
        ref = staged['value']
        rows = [{'role': 'user', 'content': 'Original screen observation',
                 'metadata': {assets.MEDIA: {'refs': [ref]}}}]
        store.acknowledge_media('source', store.save('source', rows, {}), source)
        monkeypatch.setattr(assets, 'expired', lambda _: True)
        import amplifier_foundation.session.media as foundation
        real_date = foundation._date
        monkeypatch.setattr(foundation, '_date', lambda value: real_date(value) - timedelta(days=3))
        manifest = assets.capture_media(source, [ref])
        assert manifest[assets.ref_key(ref)] == {'ref': ref, 'state': 'expired'}
        with closing(store.media('fork')) as target:
            fork = assets.adopt_messages(rows, source, target)
            adopted = assets.message_refs(fork, 'fork')[0]
            assert adopted['provenance'] == ref['provenance']
            store.acknowledge_media('fork', store.save('fork', fork, {}), target)
            with pytest.raises(ValueError, match='expired'):
                target.resolve_ref(adopted)
            assert not any((target.root / 'assets').iterdir())
        with closing(store.media('imported')) as target:
            assets.install_media(target, manifest, [ref], receipt='authorized-package',
                                 verifier=lambda *a, **k: True)
            adopted = {**ref, 'session_id': 'imported'}
            imported = [{**rows[0], 'metadata': {assets.MEDIA: {'refs': [adopted]}}}]
            store.acknowledge_media('imported', store.save('imported', imported, {}), target)
            with pytest.raises(ValueError, match='expired'):
                target.resolve_ref(adopted)


async def test_upload_origin_requires_real_admitted_owner_and_original_receipt(tmp_path, monkeypatch):
    _, store, media, message, row = await uploaded(tmp_path, monkeypatch)
    try:
        message['metadata']['amplifier_input'] = {'version': 1, 'kind': 'user', 'id': 'admitted'}
        origin = assets.resolve_upload_origin(message, session_id='source', attachment_id=row['id'],
                                             input_id='admitted', media_store=media)
        assert origin == assets.message_refs([message], 'source')[0]['provenance']
        assert assets.resolve_upload_origin(message, session_id='source', attachment_id=row['id'],
                                            input_id='other-input', media_store=media) is None
        legacy = copy.deepcopy(message)
        legacy['metadata'].pop(assets.MEDIA)
        assert assets.resolve_upload_origin(legacy, session_id='source', attachment_id=row['id'],
                                            input_id='admitted', media_store=media) is None
    finally:
        media.close()


@pytest.mark.parametrize('preserve_system', [False, True])
async def test_core_message_nullable_media_metadata_matches_actual_save_ack(tmp_path, monkeypatch, preserve_system):
    from amplifier_core.message_models import Message
    _, store, media, row, _ = await uploaded(tmp_path, monkeypatch)
    try:
        message = Message(**row)
        rows = [Message(role='system', content='Host guidance'), message]
        canonical = store.save('source', rows, {}, preserve_system=preserve_system)
        assert canonical == store.load('source')[0]
        refs = assets.message_refs(canonical, 'source')
        assert refs[0]['provenance']['expires_at'] is None
        store.acknowledge_media('source', canonical, media)
        with media.resolve_ref(refs[0]) as stream:
            assert stream.read() == png()
    finally:
        media.close()