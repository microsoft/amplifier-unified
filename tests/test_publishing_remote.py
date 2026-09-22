"""Actual local service/socket acceptance; SSH calls are deliberately simulated."""

import base64
import hashlib
import json
from pathlib import Path
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

import pytest

import amplifier_publishing.remote as remote
from amplifier_publishing import PublishingError
from amplifier_publishing.remote import SSHClient, canonical, digest, private_bind
from amplifier_publishing.service import PublishingService


SERVICE_ID = "11111111-1111-4111-8111-111111111111"


def unix_request(socket_path, request, *, timeout=20):
    """Existing lifecycle fixtures explicitly discover then bind each request."""
    if set(request) != {"method"} or request.get("method") != "target":
        if "expectedServiceId" not in request:
            target = remote.unix_request(socket_path, {"method": "target"}, timeout=3)
            request = {**request, "expectedServiceId": target["serviceId"]}
    return remote.unix_request(socket_path, request, timeout=timeout)


@pytest.fixture
def paths():
    # AF_UNIX has a short path bound on macOS; use a private, bounded directory.
    with tempfile.TemporaryDirectory(prefix="pub-", dir=Path("/tmp").resolve()) as directory:
        base = Path(directory)
        yield base / "store", base / "admin" / "admin.sock"


def imported(body=b"<h1>First</h1>", *, request_id="import-1", files=None):
    files = files or {"index.html": body, ".nojekyll": b"", "assets/style.css": b"h1{color:green}", "a/z": b"directory", "a.html": b"file"}
    manifest = [{"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for path, data in sorted(files.items())]
    return {"method": "import", "siteId": "example", "sessionId": "session", "requestId": request_id, "manifest": manifest, "manifestDigest": digest(manifest), "files": {path: base64.b64encode(data).decode() for path, data in files.items()}}


def call(socket_path, method, **args):
    return unix_request(socket_path, {"method": method, "sessionId": "session", **args}, timeout=3)


def read(url):
    with urlopen(url, timeout=3) as response:
        return response.read()


def test_real_socket_full_lifecycle_and_exact_import_retry(paths):
    root, sock = paths
    with PublishingService(root, sock) as service:
        target = unix_request(sock, {"method": "target"})
        assert target["accessPolicy"] == "loopback-only" and target["authentication"] == "none"
        assert stat.S_IMODE(sock.stat().st_mode) == 0o600
        assert stat.S_IMODE(sock.parent.stat().st_mode) == 0o700
        request = imported()
        first = unix_request(sock, request)
        assert first["manifestDigest"] == request["manifestDigest"]
        assert not list(service.staging.iterdir())
        preview = call(sock, "preview", releaseId=first["id"], requestId="preview")
        assert read(preview["result"]["url"]) == b"<h1>First</h1>"
        with pytest.raises(PublishingError, match="Review"):
            call(sock, "deploy", siteId="example", releaseId=first["id"], expectedRevision=0, requestId="unreviewed")
        call(sock, "review", releaseId=first["id"], note="Verified saved page", requestId="review-1")
        deployed = call(sock, "deploy", siteId="example", releaseId=first["id"], expectedRevision=0, requestId="deploy-1")
        assert read(deployed["result"]["url"]) == b"<h1>First</h1>"
        second = unix_request(sock, imported(b"<h1>Second</h1>", request_id="import-2"))
        call(sock, "review", releaseId=second["id"], note="Verified update", requestId="review-2")
        updated = call(sock, "deploy", siteId="example", releaseId=second["id"], expectedRevision=1, requestId="deploy-2")
        assert updated["result"]["url"] == deployed["result"]["url"]
        assert read(updated["result"]["url"]) == b"<h1>Second</h1>"
        rollback = call(sock, "rollback", siteId="example", releaseId=first["id"], expectedRevision=2, requestId="rollback")
        assert read(rollback["result"]["url"]) == b"<h1>First</h1>"
        assert unix_request(sock, request) == first
        changed = imported(b"changed", request_id="import-1")
        with pytest.raises(PublishingError) as conflict:
            unix_request(sock, changed)
        assert conflict.value.code == "request_conflict"
        stopped = call(sock, "stop", siteId="example", expectedRevision=3, requestId="stop")
        assert stopped["result"]["url"] is None
        removed = call(sock, "remove", siteId="example", expectedRevision=4, requestId="remove")
        assert removed["result"]["status"] == "removed"
        assert len(call(sock, "releases")) == 2
        assert call(sock, "receipt", requestId="import-1")["state"] == "succeeded"
        assert call(sock, "receipt", requestId="absent") is None
        assert call(sock, "list")[0]["status"] == "removed"
        assert len(call(sock, "receipts")) == 11
    assert not sock.exists()
    with PublishingService(root, sock):
        assert unix_request(sock, request) == first
        with pytest.raises(PublishingError) as conflict:
            unix_request(sock, changed)
        assert conflict.value.code == "request_conflict"


@pytest.mark.parametrize("change,code", [
    (lambda r: r["files"].update({"index.html": base64.b64encode(b"wrong content!").decode()}), "integrity_error"),
    (lambda r: r.update(manifestDigest="0" * 64), "integrity_error"),
    (lambda r: r["manifest"].append(r["manifest"][0]), "invalid_manifest"),
    (lambda r: r["files"].update({"extra.txt": ""}), "invalid_manifest"),
])
def test_transfer_validation_is_durable_and_changes_conflict(paths, change, code):
    root, sock = paths
    with PublishingService(root, sock):
        request = imported()
        change(request)
        for _ in range(2):
            with pytest.raises(PublishingError) as invalid:
                unix_request(sock, request)
            assert invalid.value.code == code
            assert invalid.value.receipt["state"] == "failed"
        with pytest.raises(PublishingError) as changed:
            unix_request(sock, imported())
        assert changed.value.code == "request_conflict"
        assert call(sock, "releases") == []


@pytest.mark.parametrize("unsafe", ["../escape", "/escape", ".env", "assets/.private", "assets\\escape", "a/../b", "a//b", ".nojekyll"])
def test_transfer_rejects_unsafe_paths(paths, unsafe):
    root, sock = paths
    with PublishingService(root, sock):
        with pytest.raises(PublishingError) as error:
            unix_request(sock, imported(files={"index.html": b"okay", unsafe: b"secret"}))
        assert error.value.code == "unsafe_path"
        assert call(sock, "releases") == []


def test_import_receipt_namespace_scope_and_schema_guards(paths):
    root, sock = paths
    with PublishingService(root, sock):
        release = unix_request(sock, imported())
        for request in (
            {"method": "import", "sessionId": "session", "source": "/private"},
            {"method": "snapshot", "sessionId": "session", "destination": "/private"},
            {"method": "target", "bind": "0.0.0.0"},
        ):
            with pytest.raises(PublishingError) as error:
                unix_request(sock, request)
            assert error.value.code == "invalid_argument"
        with pytest.raises(PublishingError) as error:
            call(sock, "review", releaseId=release["id"], requestId="import-1", note="collision")
        assert error.value.code == "request_conflict"
        call(sock, "review", releaseId=release["id"], requestId="review", note="checked")
        with pytest.raises(PublishingError) as error:
            unix_request(sock, imported(request_id="review"))
        assert error.value.code == "request_conflict"
        assert unix_request(sock, {"method": "receipt", "sessionId": "other", "requestId": "import-1"}) is None
        with pytest.raises(PublishingError) as error:
            unix_request(sock, {"method": "preview", "sessionId": "other", "requestId": "other-preview", "releaseId": release["id"]})
        assert error.value.code == "not_found"


def test_unix_timeout_does_not_replay_and_receipt_reconciles(paths, monkeypatch):
    root, sock = paths
    with PublishingService(root, sock) as service:
        release = unix_request(sock, imported())
        call(sock, "review", releaseId=release["id"], note="checked", requestId="review")
        original = service.publisher.deploy
        calls = []

        def slow(*args, **kwargs):
            calls.append(kwargs["request_id"])
            time.sleep(0.15)
            return original(*args, **kwargs)

        monkeypatch.setattr(service.publisher, "deploy", slow)
        request = {"method": "deploy", "sessionId": "session", "requestId": "lost-response", "siteId": "example", "releaseId": release["id"], "expectedRevision": 0}
        with pytest.raises(PublishingError) as error:
            unix_request(sock, request, timeout=0.02)
        assert error.value.code == "unknown_outcome"
        assert error.value.receipt["requestId"] == "lost-response"
        assert error.value.receipt["id"] == digest(["session", "lost-response"])
        receipt = call(sock, "receipt", requestId="lost-response")
        assert receipt["state"] == "succeeded"
        assert calls == ["lost-response"]
        assert call(sock, "status", siteId="example")["revision"] == 1


def test_interrupted_import_is_unknown_and_never_replayed(paths):
    root, sock = paths
    request = imported()
    service = PublishingService(root, sock)
    receipt = {"id": digest(["session", "import-1"]), "sessionId": "session", "requestId": "import-1", "siteId": "example", "action": "import", "state": "running", "createdAt": "2026-09-22", "completedAt": None, "result": None, "error": None}
    service._save(receipt, digest(request))
    service.close()
    with PublishingService(root, sock) as restarted:
        with pytest.raises(PublishingError) as error:
            unix_request(sock, request)
        assert error.value.code == "unknown_outcome"
        assert call(sock, "receipt", requestId="import-1")["state"] == "unknown"
        assert not list(restarted.staging.iterdir())
        assert call(sock, "releases") == []


def test_private_service_configuration_guards(paths):
    root, sock = paths
    for address in ("0.0.0.0", "8.8.8.8", "localhost", "::1", "127.0.0.2", "169.254.1.2", "100.64.0.1"):
        with pytest.raises(PublishingError):
            PublishingService(root, sock, bind=address)
    assert private_bind("10.2.3.4") == ("10.2.3.4", "private-network")
    # Configuration is checked without binding this nonexistent interface.
    with PublishingService(root, sock, bind="10.2.3.4"):
        target = unix_request(sock, {"method": "target"})
        assert target["accessPolicy"] == "private-network" and target["authentication"] == "none"
    sock.parent.chmod(0o755)
    with pytest.raises(PublishingError) as error:
        PublishingService(root, sock)
    assert error.value.code == "unsafe_root"


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
def test_actual_cli_process_handshake_and_request(paths, stop_signal):
    root, sock = paths
    process = subprocess.Popen([sys.executable, "-m", "amplifier_publishing.service", "serve", "--root", str(root), "--socket", str(sock)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        # A bounded readiness loop, then the actual stdin/stdout client command.
        deadline = time.monotonic() + 5
        while not sock.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sock.exists(), process.stderr.read() if process.poll() is not None else "service readiness timed out"
        result = subprocess.run([sys.executable, "-m", "amplifier_publishing.service", "request", "--socket", str(sock)], input=canonical({"method": "target"}) + "\n", capture_output=True, text=True, timeout=5)
        assert result.returncode == 0 and not result.stderr
        assert json.loads(result.stdout)["result"]["adminTransport"] == "owner-unix-socket"
        first = subprocess.run([sys.executable, "-m", "amplifier_publishing.service", "request", "--socket", str(sock)], input=canonical({**imported(), "expectedServiceId": json.loads(result.stdout)["result"]["serviceId"]}) + "\n", capture_output=True, text=True, timeout=5)
        assert json.loads(first.stdout)["ok"] is True
    finally:
        process.send_signal(stop_signal)
        process.communicate(timeout=5)
    assert process.returncode == 0 and not sock.exists()
    with PublishingService(root, sock):
        assert call(sock, "receipt", requestId="import-1")["state"] == "succeeded"


def client(**options):
    return SSHClient(hostname="private-host", python="/opt/private env/bin/python", socket_path="/home/operator/private/admin.sock", expected_bind="127.0.0.1", **{"expected_service_id": SERVICE_ID, **options})


def test_ssh_command_quotes_remote_paths_and_keeps_request_on_stdin(monkeypatch):
    configured = SSHClient(hostname="private-host", python="/opt/$(touch unsafe)/python", socket_path="/home/operator/a'b.sock", expected_bind="127.0.0.1", expected_service_id=SERVICE_ID)
    argv = configured.command()
    assert shlex.split(argv[-1]) == [configured.python, "-m", "amplifier_publishing.service", "request", "--socket", configured.socket_path, "--timeout", "30.0"]
    assert "StrictHostKeyChecking=yes" in argv and argv[-2] == "private-host"
    calls = []

    def run(command, data, timeout):
        calls.append((command, data, timeout))
        return subprocess.CompletedProcess(command, 0, b'{"ok":true,"result":[]}\n', b"")

    monkeypatch.setattr(remote, "_run_bounded", run)
    assert configured.request({"method": "receipts", "sessionId": "session"}) == []
    assert b'"sessionId":"session"' in calls[0][1]
    assert "session" not in " ".join(calls[0][0])


def test_ssh_timeout_has_receipt_identity_and_no_automatic_replay(monkeypatch):
    calls = []

    def run(command, data, timeout):
        request = json.loads(data)
        calls.append(request)
        if request["method"] == "target":
            return subprocess.CompletedProcess(command, 0, b'{"ok":true,"result":{"serviceId":"11111111-1111-4111-8111-111111111111","protocol":"static-publishing-v1","adminTransport":"owner-unix-socket","bind":"127.0.0.1","accessPolicy":"loopback-only","authentication":"none","publicPublishing":false,"previewAccessPolicy":"loopback-only"}}', b"")
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(remote, "_run_bounded", run)
    request = {"method": "deploy", "sessionId": "session", "requestId": "unknown-deploy"}
    with pytest.raises(PublishingError) as error:
        client().request(request)
    assert error.value.code == "unknown_outcome"
    assert error.value.receipt["requestId"] == "unknown-deploy"
    assert error.value.receipt["remoteReceiptVerified"] is False
    assert [item["method"] for item in calls] == ["target", "deploy"]


def test_ssh_preflight_mismatch_prevents_mutation(monkeypatch):
    configured = client()
    calls = []

    def send(request):
        calls.append(request)
        return {"protocol": "static-publishing-v1", "adminTransport": "owner-unix-socket", "bind": "0.0.0.0", "accessPolicy": "public"}

    monkeypatch.setattr(configured, "_send", send)
    with pytest.raises(PublishingError) as error:
        configured.request(imported())
    assert error.value.code == "target_mismatch" and calls == [{"method": "target", "expectedServiceId": SERVICE_ID}]


def test_url_verification_fetches_exact_bytes_over_actual_http(paths, monkeypatch):
    root, sock = paths
    with PublishingService(root, sock):
        configured = SSHClient(hostname="127.0.0.1", python=sys.executable, socket_path=str(sock), expected_bind="127.0.0.1")
        monkeypatch.setattr(configured, "_send", lambda request: remote.unix_request(sock, request))
        release = configured.request(imported())
        configured.request({"method": "review", "sessionId": "session", "requestId": "review", "releaseId": release["id"], "note": "checked"})
        configured.request({"method": "deploy", "sessionId": "session", "requestId": "deploy", "releaseId": release["id"], "siteId": "example", "expectedRevision": 0})
        result = configured.verify_site(session_id="session", site_id="example")
        assert result["clientReachabilityVerified"] is True
        assert result["releaseId"] == release["id"] and result["manifestDigest"] == release["manifestDigest"]
        (root / "releases" / release["id"] / "files" / "index.html").chmod(0o600)
        (root / "releases" / release["id"] / "files" / "index.html").write_text("tampered")
        with pytest.raises(PublishingError) as error:
            configured.verify_site(session_id="session", site_id="example")
        assert error.value.code in {"url_unreachable", "integrity_error"}


def test_remote_loopback_never_claims_client_reachability(monkeypatch):
    configured = client()
    monkeypatch.setattr(configured, "verify_target", lambda: {})
    with pytest.raises(PublishingError) as error:
        configured.verify_site(session_id="session", site_id="example")
    assert error.value.code == "url_unreachable"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_actual_subprocess_output_overflow_is_bounded_and_unknown(monkeypatch, stream):
    configured = client(timeout=2)
    # No SSH: use an intentionally noisy local child to exercise real pipe IO.
    script = f"import sys; sys.stdin.buffer.read(); sys.{stream}.buffer.write(b'x' * (17 * 1024 * 1024)); sys.{stream}.buffer.flush()"
    calls = []

    def command():
        calls.append(1)
        return [sys.executable, "-c", script]

    monkeypatch.setattr(configured, "command", command)
    started = time.monotonic()
    with pytest.raises(PublishingError) as error:
        configured.request({"method": "receipt", "sessionId": "session", "requestId": "probe"})
    assert error.value.code == "unknown_outcome"
    assert error.value.receipt["requestId"] == "probe"
    assert calls == [1] and time.monotonic() - started < 2


def test_actual_subprocess_timeout_never_replays(monkeypatch):
    configured = client(timeout=0.05)
    calls = []

    def command():
        calls.append(1)
        return [sys.executable, "-c", "import sys,time; sys.stdin.buffer.read(); time.sleep(5)"]

    monkeypatch.setattr(configured, "command", command)
    with pytest.raises(PublishingError) as error:
        configured.request({"method": "receipt", "sessionId": "session", "requestId": "probe"})
    assert error.value.code == "unknown_outcome" and calls == [1]



def test_service_identity_persists_and_guards_every_admitted_request(paths):
    root, sock = paths
    with PublishingService(root, sock) as service:
        identity = remote.unix_request(sock, {"method": "target"})["serviceId"]
        assert remote.service_identity(identity) == identity
        requests = [
            {"method": "list", "sessionId": "session"},
            {"method": "receipts", "sessionId": "session"},
            {"method": "receipt", "sessionId": "session", "requestId": "import-1"},
            imported(),
        ]
        for request in requests:
            with pytest.raises(PublishingError) as missing:
                remote.unix_request(sock, request)
            assert missing.value.code == "service_identity_required"
            with pytest.raises(PublishingError) as swapped:
                remote.unix_request(sock, {**request, "expectedServiceId": SERVICE_ID})
            assert swapped.value.code == "target_mismatch"
        assert service.publisher.releases("session") == service._receipts("session") == []
        imported_release = remote.unix_request(sock, {**imported(), "expectedServiceId": identity})
    with PublishingService(root, sock):
        target = remote.unix_request(sock, {"method": "target", "expectedServiceId": identity})
        assert target["serviceId"] == identity
        assert remote.unix_request(sock, {**imported(), "expectedServiceId": identity}) == imported_release


def test_wrong_identity_cannot_read_existing_receipt_or_change_import(paths):
    root, sock = paths
    with PublishingService(root, sock) as service:
        imported_release = unix_request(sock, imported())
        before = service._receipts("session")
        for request in (imported(), {"method": "receipt", "sessionId": "session", "requestId": "import-1"}, {"method": "target"}):
            with pytest.raises(PublishingError) as error:
                remote.unix_request(sock, {**request, "expectedServiceId": SERVICE_ID})
            assert error.value.code == "target_mismatch"
            assert error.value.receipt is None
        assert service._receipts("session") == before
        assert service.publisher.releases("session")[0]["id"] == imported_release["id"]


def test_ssh_pins_discovery_and_service_swap_after_preflight_is_rejected(paths, monkeypatch):
    root, sock = paths
    with PublishingService(root, sock) as first:
        other_root = root.parent / "store-b"
        other_sock = sock.parent / "other.sock"
        with PublishingService(other_root, other_sock) as replacement:
            configured = client(expected_service_id=None)
            calls = []
            route = {"socket": sock}
            def send(request):
                calls.append(dict(request))
                return remote.unix_request(route["socket"], request)
            monkeypatch.setattr(configured, "_send", send)
            inspected = configured.verify_target()
            assert inspected["serviceId"] == first.service_id
            assert configured.service_id == first.service_id
            assert calls == [{"method": "target"}]
            assert configured.request({"method": "list", "sessionId": "session"}) == []
            assert calls[-1]["expectedServiceId"] == first.service_id
            # Change only the operation destination after the old target passes
            # preflight, simulating a replaced socket or service process.
            def swapped_send(request):
                calls.append(dict(request))
                destination = sock if request["method"] == "target" else other_sock
                return remote.unix_request(destination, request)
            monkeypatch.setattr(configured, "_send", swapped_send)
            with pytest.raises(PublishingError) as error:
                configured.request(imported())
            assert error.value.code == "target_mismatch"
            assert calls[-1]["expectedServiceId"] == first.service_id
            assert replacement.service_id != first.service_id
            assert replacement.publisher.releases("session") == replacement._receipts("session") == []
            assert first.publisher.releases("session") == []
            # A later read is guarded too; changing service is never silent.
            route["socket"] = other_sock
            monkeypatch.setattr(configured, "_send", send)
            with pytest.raises(PublishingError) as error:
                configured.request({"method": "receipts", "sessionId": "session"})
            assert error.value.code == "target_mismatch"


def test_configured_service_identity_cannot_be_overridden_by_request(paths, monkeypatch):
    root, sock = paths
    with PublishingService(root, sock) as service:
        configured = client(expected_service_id=service.service_id)
        calls = []
        monkeypatch.setattr(configured, "_send", lambda request: calls.append(request))
        with pytest.raises(PublishingError) as error:
            configured.request({"method": "list", "sessionId": "session", "expectedServiceId": SERVICE_ID})
        assert error.value.code == "target_mismatch" and calls == []
        assert configured.service_id == service.service_id


def test_exported_release_imports_exact_bytes_without_source_paths(paths):
    from amplifier_publishing import Publisher
    root, sock = paths
    source = root.parent / "source"
    source.mkdir()
    (source / "index.html").write_text("<h1>Portable snapshot</h1>")
    (source / ".nojekyll").touch()
    with Publisher(root.parent / "local") as local:
        release = local.build(source, site_id="example", session_id="session", request_id="local-build")
        exported = local.export_release(release["id"], "session")
        (source / "index.html").write_text("mutated source must not transfer")
        with PublishingService(root, sock):
            imported_release = unix_request(sock, {"method": "import", "requestId": "transfer", **exported})
            assert imported_release["id"] == release["id"]
            assert imported_release["manifestDigest"] == release["manifestDigest"]
            assert "source" not in exported and str(source) not in canonical(exported)


def test_exact_rpc_proof_covers_review_note_and_survives_restart(paths):
    root, sock = paths
    with PublishingService(root, sock) as service:
        identity = service.service_id
        request = {**imported(), 'expectedServiceId': identity}
        release = remote.unix_request(sock, request)
        import_receipt = call(sock, 'receipt', requestId='import-1')
        assert import_receipt['rpcPayloadDigest'] == digest(request)
        assert import_receipt['serviceId'] == identity
        review_request = {'method': 'review', 'sessionId': 'session', 'requestId': 'review-proof', 'releaseId': release['id'], 'note': 'first exact note', 'expectedServiceId': identity}
        review_receipt = remote.unix_request(sock, review_request)
        assert review_receipt['rpcPayloadDigest'] == digest(review_request)
        assert review_receipt['serviceId'] == identity
        with pytest.raises(PublishingError) as different:
            remote.unix_request(sock, {**review_request, 'note': 'a different note'})
        assert different.value.code == 'request_conflict'
        assert call(sock, 'receipt', requestId='review-proof') == review_receipt
    with PublishingService(root, sock):
        assert remote.unix_request(sock, review_request) == review_receipt
        assert call(sock, 'receipt', requestId='review-proof')['rpcPayloadDigest'] == digest(review_request)


def test_rpc_proof_distinguishes_expected_revision_and_preserves_original(paths):
    root, sock = paths
    with PublishingService(root, sock) as service:
        release = unix_request(sock, imported())
        call(sock, 'review', releaseId=release['id'], requestId='review', note='checked')
        deployment = {'method': 'deploy', 'sessionId': 'session', 'requestId': 'deployment-proof', 'siteId': 'example', 'releaseId': release['id'], 'expectedRevision': 0, 'expectedServiceId': service.service_id}
        receipt = remote.unix_request(sock, deployment)
        assert receipt['rpcPayloadDigest'] == digest(deployment)
        with pytest.raises(PublishingError) as different:
            remote.unix_request(sock, {**deployment, 'expectedRevision': 1})
        assert different.value.code == 'request_conflict'
        assert call(sock, 'receipt', requestId='deployment-proof') == receipt
        assert call(sock, 'status', siteId='example')['revision'] == 1


def test_legacy_receipt_requires_original_exact_retry_before_proof(paths):
    root, sock = paths
    with PublishingService(root, sock) as service:
        release = unix_request(sock, imported())
        legacy = service.publisher.review(release['id'], session_id='session', request_id='legacy-review', note='legacy original')
        assert call(sock, 'receipt', requestId='legacy-review') == legacy
        assert 'rpcPayloadDigest' not in legacy
        request = {'method': 'review', 'sessionId': 'session', 'requestId': 'legacy-review', 'releaseId': release['id'], 'note': 'different caller note', 'expectedServiceId': service.service_id}
        with pytest.raises(PublishingError) as conflict:
            remote.unix_request(sock, request)
        assert conflict.value.code == 'request_conflict'
        assert call(sock, 'receipt', requestId='legacy-review') == legacy
        assert service._db.execute('SELECT 1 FROM rpc_receipts WHERE session=? AND request=?', ('session', 'legacy-review')).fetchone() is None
        # Only the original library's fingerprint check can establish that an
        # explicit retry really describes this historical operation.
        exact = {**request, 'note': 'legacy original'}
        proven = remote.unix_request(sock, exact)
        assert proven['result'] == legacy['result']
        assert proven['rpcPayloadDigest'] == digest(exact)
        assert call(sock, 'receipt', requestId='legacy-review') == proven


def test_legacy_import_receipt_proof_requires_exact_original_bytes(paths):
    root, sock = paths
    with PublishingService(root, sock) as service:
        original = imported()
        release = service._import(original)  # Simulate a pre-RPC-proof service.
        legacy = call(sock, 'receipt', requestId='import-1')
        assert 'rpcPayloadDigest' not in legacy
        with pytest.raises(PublishingError) as conflict:
            unix_request(sock, imported(b'different immutable bytes'))
        assert conflict.value.code == 'request_conflict'
        assert call(sock, 'receipt', requestId='import-1') == legacy
        assert unix_request(sock, original) == release
        proven = call(sock, 'receipt', requestId='import-1')
        assert proven['rpcPayloadDigest'] == digest({**original, 'expectedServiceId': service.service_id})


def test_interrupted_rpc_never_borrows_success_from_unproven_inner_receipt(paths):
    root, sock = paths
    with PublishingService(root, sock) as service:
        release = unix_request(sock, imported())
        request = {'method': 'review', 'sessionId': 'session', 'requestId': 'interrupted-rpc', 'releaseId': release['id'], 'note': 'current intended note', 'expectedServiceId': service.service_id}
        # The outer intent is durable, but no matching RPC outcome was saved.
        service._save_rpc({'id': digest(['session', 'interrupted-rpc']), 'sessionId': 'session', 'requestId': 'interrupted-rpc', 'action': 'review', 'serviceId': service.service_id, 'rpcPayloadDigest': digest(request), 'siteId': 'example', 'releaseId': release['id'], 'state': 'running', 'createdAt': '2026-09-22', 'completedAt': None, 'result': None, 'error': None})
        service.publisher.review(release['id'], session_id='session', request_id='interrupted-rpc', note='unproven inner outcome')
    with PublishingService(root, sock) as restarted:
        receipt = call(sock, 'receipt', requestId='interrupted-rpc')
        assert receipt['state'] == 'unknown' and receipt['result'] is None
        assert receipt['rpcPayloadDigest'] == digest(request)
        with pytest.raises(PublishingError) as unknown:
            remote.unix_request(sock, request)
        assert unknown.value.code == 'unknown_outcome'
        assert unknown.value.receipt == receipt
        native = next(item for item in restarted.publisher.receipts('session') if item['requestId'] == 'interrupted-rpc')
        assert native['result']['review']['note'] == 'unproven inner outcome'


def test_known_failure_rpc_receipt_retains_exact_payload_proof(paths):
    root, sock = paths
    with PublishingService(root, sock) as service:
        release = unix_request(sock, imported())
        request = {'method': 'deploy', 'sessionId': 'session', 'requestId': 'unreviewed-proof', 'siteId': 'example', 'releaseId': release['id'], 'expectedRevision': 0, 'expectedServiceId': service.service_id}
        with pytest.raises(PublishingError) as failed:
            remote.unix_request(sock, request)
        assert failed.value.code == 'review_required'
        receipt = call(sock, 'receipt', requestId='unreviewed-proof')
        assert receipt['state'] == 'failed'
        assert receipt['rpcPayloadDigest'] == digest(request)
        assert receipt == failed.value.receipt


@pytest.mark.parametrize('inner_action', ['review', 'different-build'])
def test_legacy_pending_import_cannot_borrow_unrelated_native_outcome(paths, inner_action):
    root, sock = paths
    with PublishingService(root, sock) as service:
        request = imported(request_id='legacy-pending')
        receipt = {'id': digest(['session', 'legacy-pending']), 'sessionId': 'session', 'requestId': 'legacy-pending', 'siteId': 'example', 'action': 'import', 'state': 'running', 'createdAt': '2026-09-22', 'completedAt': None, 'result': None, 'error': None}
        if inner_action == 'different-build':
            receipt['manifestDigest'] = request['manifestDigest']
        service._save(receipt, digest(request))
        if inner_action == 'review':
            release = unix_request(sock, imported(request_id='initial-import'))
            service.publisher.review(release['id'], session_id='session', request_id='legacy-pending', note='unrelated review')
        else:
            source = root.parent / 'different-source'
            source.mkdir()
            (source / 'index.html').write_text('not the intended imported bytes')
            service.publisher.build(source, site_id='example', session_id='session', request_id='legacy-pending')
    with PublishingService(root, sock):
        recovered = call(sock, 'receipt', requestId='legacy-pending')
        assert recovered['state'] == 'unknown' and recovered['result'] is None
        assert 'rpcPayloadDigest' not in recovered
        with pytest.raises(PublishingError) as still_unknown:
            unix_request(sock, request)
        assert still_unknown.value.code == 'unknown_outcome'
        assert still_unknown.value.receipt['state'] == 'unknown'
        assert still_unknown.value.receipt['result'] is None
