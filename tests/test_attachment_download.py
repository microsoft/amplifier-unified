"""Original UUID routes exercised with a deliberately seeded future private binding."""
import json
import os
from io import BytesIO

import pytest

from amplifier_web import attachments
from amplifier_web.host.storage import SessionStore
from amplifier_web.server import create_app
from test_attachments import PNG, upload
from test_service import Runtime


def seed(home, *, committed=True):
    from amplifier_foundation.session.media import MEDIA_NAMESPACE

    sid = "saved-conversation"
    store = SessionStore.for_app(home, home / "workspace")
    writer = store.media(sid)
    rows, uploads = [], []
    try:
        for name in ("first name.png", "another.png"):
            row = upload(home, name)
            origin = {"kind": "upload", "session_id": sid, "id": row["id"]}
            ref, = writer.ingest_sync({"mime": row["mime"], "stream": BytesIO(PNG)},
                source_format="image-bytes", origin=origin, policy=None)["staged_refs"]
            writer.classify_origin(origin, classification="composer-upload")
            writer.publish_sync([ref], origin=origin, disposition="retain")
            rows.append({"role": "user", "content": [{"type": "text", "text": f"[Session image {ref['sha256']}]"}],
                         "metadata": {MEDIA_NAMESPACE: {"refs": [ref]}}})
            binding = {"project": store.base_dir.parent.name, "ref": ref}
            uploads.append((row, binding))
        if committed:
            store.acknowledge_media(sid, store.save(sid, rows, {}), writer)
    finally:
        writer.close()
    return uploads, store.directory(sid) / "media"


def bind(home, row, binding):
    meta = attachments.location(home, row["id"]) / "metadata.json"
    meta.write_text(json.dumps({**row, "sessionMedia": binding}))
    return meta


@pytest.fixture
async def browser(tmp_path, aiohttp_client):
    app = await create_app(tmp_path, workspace=tmp_path, runtime=Runtime(), voice=False,
                           preload_providers=False, background_updates=False, background_work=False)
    return await aiohttp_client(app), {"Authorization": "Bearer " + app["control_token"]}


@pytest.fixture
def checkpointed_reader():
    if os.name != "posix":
        pytest.skip("Foundation native media source downloads require POSIX")
    from amplifier_foundation.session.media import SessionMediaStore
    if not hasattr(SessionMediaStore, "resolve_checkpointed_ref_for_source_read"):
        pytest.skip("Selected Foundation lacks the read-only checkpointed source reader")
    return SessionMediaStore


async def test_original_wins_on_all_foundation_versions(tmp_path, browser, monkeypatch):
    from amplifier_web import attachment_download

    row = upload(tmp_path)
    bind(tmp_path, row, {"not": "a valid binding"})
    monkeypatch.setattr(attachment_download, "_grant", lambda *args: pytest.fail("read alias for an original"))
    client, auth = browser
    result = await client.get(row["url"], headers=auth)
    assert result.status == 200 and await result.read() == PNG


@pytest.mark.skipif(os.name != "posix", reason="Foundation native media is POSIX only")
@pytest.mark.parametrize("missing", ["method", "module"])
async def test_missing_reader_preserves_original_and_refuses_alias(tmp_path, browser, monkeypatch, missing):
    from amplifier_foundation.session.media import SessionMediaStore
    (row, binding), _ = seed(tmp_path)[0]
    if missing == "module":
        import sys
        monkeypatch.setitem(sys.modules, "amplifier_foundation.session.media", None)
    else:
        monkeypatch.delattr(SessionMediaStore, "resolve_checkpointed_ref_for_source_read", raising=False)
    bind(tmp_path, row, binding)
    (attachments.location(tmp_path, row["id"]) / "content").unlink()
    client, auth = browser
    assert (await client.get(row["url"], headers=auth)).status == 404


async def test_sleeping_download_uses_uuid_names_and_existing_http_semantics(
        tmp_path, browser, checkpointed_reader, monkeypatch):
    uploads, root = seed(tmp_path)
    client, auth = browser
    for row, binding in uploads:
        bind(tmp_path, row, binding)
        (attachments.location(tmp_path, row["id"]) / "content").unlink()
    # No hydrated SessionStore, model checkpoint or writer acquisition on read.
    monkeypatch.setattr(SessionStore, "__init__", lambda *a, **k: pytest.fail("started session owner"))
    monkeypatch.setattr(checkpointed_reader, "verify_checkpoint", lambda *a: pytest.fail("model read"))
    import fcntl
    lock = (root / "writer.lock").stat()
    real_flock = fcntl.flock
    def guard(fd, op):
        assert not os.path.samestat(os.fstat(fd), lock), "download touched the native writer lock"
        return real_flock(fd, op)
    monkeypatch.setattr(fcntl, "flock", guard)
    from urllib.parse import quote
    for row, _ in uploads:
        assert attachments.metadata(tmp_path, row["id"]) == row
        assert (await client.get(row["url"], allow_redirects=False)).status == 401
        assert (await client.head(row["url"], allow_redirects=False)).status == 401
        response = await client.get(row["url"], headers=auth)
        assert response.status == 200 and await response.read() == PNG
        assert response.headers["Content-Disposition"] == "inline; filename*=UTF-8''" + quote(row["name"])
        head = await client.head(row["url"], headers=auth)
        assert head.status == 200 and await head.read() == b""
        for key in ("ETag", "Content-Type", "Content-Length", "Last-Modified", "Content-Disposition"):
            assert head.headers[key] == response.headers[key]
        assert head.headers["Content-Type"] == row["mime"]
        assert (await client.get(row["url"], headers={**auth, "If-None-Match": head.headers["ETag"]})).status == 304
        part = await client.get(row["url"], headers={**auth, "Range": "bytes=2-4", "If-Range": head.headers["Last-Modified"]})
        assert part.status == 206 and part.headers["Content-Range"] == f"bytes 2-4/{len(PNG)}"
        assert await part.read() == PNG[2:5]
        stale = await client.get(row["url"], headers={**auth, "Range": "bytes=2-4", "If-Range": "Thu, 01 Jan 1970 00:00:00 GMT"})
        assert stale.status == 200 and await stale.read() == PNG
        assert (await client.get(row["url"], headers={**auth, "Range": "bytes=10000-"})).status == 416
    assert len(list((root / "assets").iterdir())) == 1


@pytest.mark.parametrize("case", ["published-only", "other-uuid", "different-size", "different-mime",
                                 "unclassified", "expiring", "revoked", "linked-original", "linked-project",
                                 "linked-grant", "public-grant", "corrupt-original"])
async def test_unavailable_alias_keeps_its_private_url_closed(tmp_path, browser, checkpointed_reader, case):
    uploads, root = seed(tmp_path, committed=case != "published-only")
    (row, binding), (_, other) = uploads
    slot = attachments.location(tmp_path, row["id"])
    (slot / "content").unlink()
    ref = binding["ref"]
    if case == "other-uuid":
        binding = other
    elif case == "different-size":
        row["size"] += 1
    elif case == "different-mime":
        row["mime"] = "image/jpeg"
    elif case == "expiring":
        ref["provenance"]["expires_at"] = ref["provenance"]["created_at"]
    elif case == "revoked":
        binding = None
    elif case == "unclassified":
        for classification in (root / "origins").glob("*.classification.json"):
            classification.unlink()
    elif case == "linked-original":
        (slot / "content").symlink_to(slot / "missing")
    elif case == "linked-project":
        project_dir = root.parents[2]
        sibling = project_dir.with_name(project_dir.name + "-link")
        sibling.symlink_to(project_dir, target_is_directory=True)
        binding["project"] = sibling.name
    elif case == "corrupt-original":
        (root / "assets" / ref["sha256"]).write_bytes(b"x" * len(PNG))
    meta = bind(tmp_path, row, binding)
    if case == "public-grant":
        meta.chmod(0o644)
    elif case == "linked-grant":
        moved = meta.rename(slot / "moved")
        meta.symlink_to(moved)
    client, auth = browser
    response = await client.get(row["url"], headers=auth)
    assert response.status == 404 and PNG not in await response.read()


async def test_revoking_private_binding_during_snapshot_returns_nothing(
        tmp_path, browser, checkpointed_reader, monkeypatch):
    (row, binding), _ = seed(tmp_path)[0]
    bind(tmp_path, row, binding)
    (attachments.location(tmp_path, row["id"]) / "content").unlink()
    read = checkpointed_reader._open_verified
    snapshots = []
    def revoke(observer, *args):
        snapshot = read(observer, *args)
        snapshots.append(snapshot)
        bind(tmp_path, row, None)
        return snapshot
    monkeypatch.setattr(checkpointed_reader, "_open_verified", revoke)
    client, auth = browser
    response = await client.get(row["url"], headers=auth)
    assert response.status == 404 and PNG not in await response.read()
    assert len(snapshots) == 1 and snapshots[0].closed


async def test_response_streams_the_verified_snapshot_when_the_asset_changes(
        tmp_path, browser, checkpointed_reader, monkeypatch):
    uploads, root = seed(tmp_path)
    row, binding = uploads[0]
    bind(tmp_path, row, binding)
    (attachments.location(tmp_path, row["id"]) / "content").unlink()
    read = checkpointed_reader.resolve_checkpointed_ref_for_source_read
    def change_after_snapshot(observer, ref):
        stream = read(observer, ref)
        (root / "assets" / ref["sha256"]).write_bytes(b"x" * len(PNG))
        return stream
    monkeypatch.setattr(checkpointed_reader, "resolve_checkpointed_ref_for_source_read", change_after_snapshot)
    client, auth = browser
    response = await client.get(row["url"], headers=auth)
    assert response.status == 200 and await response.read() == PNG
    assert (await client.get(row["url"], headers=auth)).status == 404


async def test_cancel_while_snapshot_is_open_closes_executor_owned_file(monkeypatch):
    import asyncio
    import tempfile
    import threading
    from aiohttp.test_utils import make_mocked_request
    from amplifier_web.attachment_download import _SnapshotResponse, _OpenSnapshot

    started, resume, finished = threading.Event(), threading.Event(), threading.Event()
    snapshots = []
    def open_source():
        stream = tempfile.TemporaryFile()
        snapshots.append(stream)
        started.set()
        assert resume.wait(5)
        stream.write(PNG)
        stream.seek(0)
        return stream

    response = _SnapshotResponse(open_source, 1000000000)
    assert not snapshots  # Constructing a response does not open its data.
    # Force a not-modified result, which must never transfer a duplicate descriptor.
    request = make_mocked_request("GET", "/", headers={"If-None-Match": "*"})
    monkeypatch.setattr(_OpenSnapshot, "open", lambda *args: pytest.fail("opened range after 304"))
    original_make = response._make_response
    def finish(*args):
        try:
            return original_make(*args)
        finally:
            finished.set()
    monkeypatch.setattr(response, "_make_response", finish)
    pending = asyncio.create_task(response.prepare(request))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    finally:
        resume.set()
    assert await asyncio.to_thread(finished.wait, 5)
    assert len(snapshots) == 1 and snapshots[0].closed
