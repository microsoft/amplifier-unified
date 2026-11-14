"""The root media store is not the attachment chooser's observation quota."""
import io
from contextlib import closing

from PIL import Image

from amplifier_web.host.storage import SessionStore
from amplifier_web.attachments import MAX_FILES


async def test_default_store_accepts_group_beyond_attachment_chooser_and_checkpoint(tmp_path):
    store = SessionStore(tmp_path / 'sessions')
    origin = {'kind': 'tool', 'session_id': 'root', 'id': 'actual-group'}
    originals = []
    with closing(store.media('root')) as media:
        assert media.policy.max_images is None
        assert media.policy.max_bytes is None
        assert media.policy.max_pixels is None
        assert media.policy.ttl_seconds is None
        for index in range(max(12, MAX_FILES + 2)):
            output = io.BytesIO()
            Image.new('RGB', (2, 2), (index, 40, 80)).save(output, format='PNG')
            raw = output.getvalue()
            originals.append(raw)
        refs = media.ingest_group_sync(originals, origin=origin, policy=None)['value']
        assert len(refs) == len(originals)
        assert len(refs) > MAX_FILES
        assert all(ref['provenance']['expires_at'] is None for ref in refs)
        await media.publish(refs, origin=origin, disposition='retain')
        rows = [{'role': 'tool', 'content': 'Actual image group',
                 'metadata': {'experimental.media.v1': {'refs': refs}}}]
        canonical = store.save('root', rows, {})
        store.acknowledge_media('root', canonical, media)
        for ref, raw in zip(refs, originals):
            with media.resolve_ref(ref) as stream:
                assert stream.read() == raw