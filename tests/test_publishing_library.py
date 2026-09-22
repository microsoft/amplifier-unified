"""Portable lifecycle acceptance uses real sockets and real process restarts."""

import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from urllib.parse import urlsplit

import pytest

from amplifier_publishing import Publisher, PublishingError


@pytest.fixture
def source(tmp_path):
    directory = tmp_path / "build"
    directory.mkdir()
    (directory / "index.html").write_text("<h1>Version one</h1>")
    (directory / "assets").mkdir()
    (directory / "assets" / "app.js").write_text("document.body.dataset.ready='yes'")
    return directory


@pytest.fixture
def publisher(tmp_path):
    with Publisher(tmp_path / "publishing") as owner:
        yield owner


def build(publisher, source, request="build-one", site="site-one", session="session-one"):
    return publisher.build(source, site_id=site, session_id=session, request_id=request)


def review(publisher, release, request="review-one"):
    return publisher.review(release["id"], session_id=release["sessionId"], request_id=request, note="Reviewed exact snapshot")


def deploy(publisher, release, revision=0, request="deploy-one"):
    return publisher.deploy(release["id"], site_id=release["siteId"], session_id=release["sessionId"], expected_revision=revision, request_id=request)


def http(url, path="/", *, host=None, method="GET"):
    target = urlsplit(url)
    connection = HTTPConnection(target.hostname, target.port, timeout=2)
    try:
        connection.request(method, path, headers={} if host is None else {"Host": host})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def assert_closed(url):
    target = urlsplit(url)
    with pytest.raises(OSError):
        socket.create_connection((target.hostname, target.port), timeout=1)


def test_actual_build_preview_review_deploy_update_rollback_stop_remove(publisher, source):
    first = build(publisher, source)
    assert first["manifestDigest"] and len(first["id"]) == 64
    assert first["totalBytes"] == sum(item["size"] for item in first["files"])
    assert first["review"] is None
    preview = publisher.preview(first["id"], session_id="session-one", request_id="preview-one")
    preview_url = preview["result"]["url"]
    assert http(preview_url)[2] == b"<h1>Version one</h1>"
    with pytest.raises(PublishingError, match="Review this exact") as exc:
        deploy(publisher, first, request="unreviewed")
    assert exc.value.code == "review_required" and exc.value.receipt["state"] == "failed"
    reviewed = review(publisher, first)
    assert reviewed["result"]["review"]["manifestDigest"] == first["manifestDigest"]
    started = deploy(publisher, first)
    site = started["result"]
    assert site["revision"] == 1 and site["accessPolicy"] == "loopback-only"
    assert site["url"] != preview_url
    assert http(site["url"])[2] == b"<h1>Version one</h1>"
    (source / "index.html").write_text("<h1>Version two</h1>")
    second = build(publisher, source, "build-two")
    assert second["id"] != first["id"]
    review(publisher, second, "review-two")
    updated = deploy(publisher, second, 1, "update-two")
    assert updated["result"]["url"] == site["url"]
    assert updated["result"]["previousReleaseId"] == first["id"]
    assert http(site["url"])[2] == b"<h1>Version two</h1>"
    assert http(preview_url)[2] == b"<h1>Version one</h1>"
    rolled_back = publisher.rollback(first["id"], site_id="site-one", session_id="session-one", expected_revision=2, request_id="rollback-one")
    assert rolled_back["result"]["revision"] == 3
    assert http(site["url"])[2] == b"<h1>Version one</h1>"
    stopped = publisher.stop("site-one", session_id="session-one", expected_revision=3, request_id="stop-one")
    assert stopped["result"]["revision"] == 4 and stopped["result"]["url"] is None
    assert_closed(site["url"])
    assert_closed(preview_url)
    removed = publisher.remove("site-one", session_id="session-one", expected_revision=4, request_id="remove-one")
    assert removed["result"]["status"] == "removed"
    assert len(publisher.releases("session-one")) == 2
    assert publisher.list("session-one")[0]["revision"] == 5
    assert len(publisher.receipts("session-one")) == 11


def test_exact_retries_are_historical_and_changed_payload_is_rejected(publisher, source):
    release = build(publisher, source)
    (source / "index.html").unlink()
    assert build(publisher, source) == release  # no rebuild, even after source disappears
    with pytest.raises(PublishingError) as exc:
        build(publisher, source.parent / "other")
    assert exc.value.code == "request_conflict"
    first_review = review(publisher, release)
    assert review(publisher, release) == first_review
    with pytest.raises(PublishingError) as exc:
        publisher.review(release["id"], session_id="session-one", request_id="review-one", note="Changed")
    assert exc.value.code == "request_conflict"
    receipt = deploy(publisher, release)
    publisher.stop("site-one", session_id="session-one", expected_revision=1, request_id="stop-one")
    assert deploy(publisher, release) == receipt
    assert publisher.status("site-one", "session-one")["url"] is None
    assert_closed(receipt["result"]["url"])


def test_failed_receipt_is_retried_without_reexecution(publisher, source):
    release = build(publisher, source)
    with pytest.raises(PublishingError) as first:
        deploy(publisher, release)
    review(publisher, release)
    with pytest.raises(PublishingError) as again:
        deploy(publisher, release)
    assert first.value.receipt == again.value.receipt
    assert not publisher.list("session-one")
    assert deploy(publisher, release, request="intentional-new-deploy")["state"] == "succeeded"


def test_stale_revision_and_rollback_requires_prior_deploy(publisher, source):
    release = build(publisher, source)
    review(publisher, release)
    with pytest.raises(PublishingError) as exc:
        publisher.rollback(release["id"], site_id="site-one", session_id="session-one", expected_revision=0, request_id="rollback-never")
    assert exc.value.code == "not_deployed"
    deploy(publisher, release)
    for action in ("deploy", "rollback", "stop", "remove"):
        params = dict(session_id="session-one", expected_revision=0, request_id="stale-" + action)
        with pytest.raises(PublishingError) as exc:
            if action in {"deploy", "rollback"}:
                getattr(publisher, action)(release["id"], site_id="site-one", **params)
            else:
                getattr(publisher, action)("site-one", **params)
        assert exc.value.code == "stale_revision"
    assert publisher.status("site-one", "session-one")["revision"] == 1


def test_sessions_are_separate_and_site_names_reserved_before_deploy(publisher, source):
    release = build(publisher, source)
    for action in ("review", "preview"):
        with pytest.raises(PublishingError) as exc:
            getattr(publisher, action)(release["id"], session_id="other", request_id=action)
        assert exc.value.code == "not_found"
    with pytest.raises(PublishingError):
        build(publisher, source, session="other")
    with pytest.raises(PublishingError):
        publisher.stop("site-one", session_id="other", expected_revision=0, request_id="stop")
    review(publisher, release)
    deploy(publisher, release)
    with pytest.raises(PublishingError):
        publisher.status("site-one", "other")
    with pytest.raises(PublishingError):
        publisher.deploy(release["id"], site_id="site-one", session_id="other", expected_revision=1, request_id="deploy")
    assert publisher.list("other") == publisher.releases("other") == []
    assert all(item["sessionId"] == "other" for item in publisher.receipts("other"))


@pytest.mark.parametrize("path", [".env", ".git/config", "assets/.secret", "assets/back\\slash", "assets/\nodd"])
def test_hidden_and_ambiguous_build_files_rejected(publisher, source, path):
    private = source / path
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text("do not publish")
    with pytest.raises(PublishingError) as exc:
        build(publisher, source)
    assert exc.value.code == "unsafe_path"
    assert publisher.releases("session-one") == []


@pytest.mark.parametrize("kind", ["file_link", "directory_link", "fifo", "source_link", "parent_link"])
def test_symlinks_and_special_files_rejected(publisher, source, tmp_path, kind):
    if kind == "file_link":
        (source / "leak.txt").symlink_to(source / "index.html")
    elif kind == "directory_link":
        (source / "leak").symlink_to(source / "assets", target_is_directory=True)
    elif kind == "fifo":
        os.mkfifo(source / "pipe")
    elif kind == "source_link":
        linked = tmp_path / "linked"
        linked.symlink_to(source, target_is_directory=True)
        source = linked
    else:
        linked = tmp_path / "linked"
        linked.symlink_to(source.parent, target_is_directory=True)
        source = linked / "build"
    with pytest.raises(PublishingError):
        build(publisher, source)
    assert not publisher.releases("session-one")


def test_source_traversal_and_own_storage_rejected(publisher, source):
    with pytest.raises(PublishingError) as exc:
        build(publisher, source / "assets" / "..")
    assert exc.value.code == "unsafe_path"
    with pytest.raises(PublishingError):
        build(publisher, publisher.root, "own-storage")
    with pytest.raises(PublishingError):
        build(publisher, publisher.root.parent, "ancestor-storage")


@pytest.mark.parametrize("limits", [{"max_file_bytes": 5}, {"max_total_bytes": 30}, {"max_files": 1}])
def test_size_bounds(tmp_path, source, limits):
    with Publisher(tmp_path / "limits", **limits) as publisher:
        with pytest.raises(PublishingError) as exc:
            build(publisher, source)
        assert exc.value.code == "size_limit"


def test_empty_directory_count_is_bounded(tmp_path, source):
    for index in range(8):
        (source / str(index)).mkdir()
    with Publisher(tmp_path / "limits", max_files=3) as publisher:
        with pytest.raises(PublishingError) as exc:
            build(publisher, source)
        assert exc.value.code == "size_limit"


def test_http_origin_paths_methods_and_headers(publisher, source):
    release = build(publisher, source)
    url = publisher.preview(release["id"], session_id="session-one", request_id="preview")["result"]["url"]
    for host in ("attacker.test", "localhost", "127.0.0.1", "127.0.0.1:1"):
        assert http(url, host=host)[0] == 421
    for path in ("/.env", "/assets/", "/../index.html", "/%2e%2e/index.html", "/%2ehidden", "/assets%5capp.js", "/%00", "/assets//app.js"):
        status, headers, body = http(url, path)
        assert status == 404 and b"Version" not in body
        assert headers["X-Content-Type-Options"] == "nosniff"
    status, headers, data = http(url, "/assets/app.js?secret=unlogged")
    assert status == 200 and data.startswith(b"document")
    assert "javascript" in headers["Content-Type"]
    assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "Access-Control-Allow-Origin" not in headers
    assert http(url, method="HEAD")[2] == b""
    assert http(url, method="POST")[0] == 501
    target = urlsplit(url)
    connection = HTTPConnection(target.hostname, target.port)
    connection.putrequest("GET", "/")
    connection.putheader("Host", "attacker.test")
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 421
    response.read()
    connection.close()


def test_immutable_digest_detects_tampering_before_and_during_serve(publisher, source):
    release = build(publisher, source)
    review(publisher, release)
    url = deploy(publisher, release)["result"]["url"]
    path = publisher.root / "releases" / release["id"] / "files" / "index.html"
    path.chmod(0o600)
    path.write_text("private credentials must never leak")
    assert http(url)[0] == 404
    with pytest.raises(PublishingError) as exc:
        publisher.preview(release["id"], session_id="session-one", request_id="preview-tampered")
    assert exc.value.code == "integrity_error"
    with pytest.raises(PublishingError):
        build(publisher, source, "same-content-recheck")


def test_graceful_restart_keeps_receipts_but_does_not_resume_urls(tmp_path, source):
    root = tmp_path / "publisher"
    with Publisher(root) as publisher:
        release = build(publisher, source)
        review(publisher, release)
        receipt = deploy(publisher, release)
        url = receipt["result"]["url"]
    assert_closed(url)
    with Publisher(root) as publisher:
        assert deploy(publisher, release) == receipt
        site = publisher.status("site-one", "session-one")
        assert site["status"] == "stopped" and site["url"] is None and site["revision"] == 2
        assert deploy(publisher, release, revision=2, request="explicit-resume")["result"]["revision"] == 3


def test_real_process_crash_fences_unknown_without_replay(tmp_path, source):
    root = tmp_path / "publisher"
    script = '''
import json, os, sys
from pathlib import Path
from amplifier_publishing import Publisher
p=Publisher(sys.argv[1])
s=Path(sys.argv[2])
r=p.build(s, site_id="site-one",session_id="session-one",request_id="build-one")
p.review(r["id"],session_id="session-one",request_id="review-one")
first=p.deploy(r["id"],site_id="site-one",session_id="session-one",expected_revision=0,request_id="deploy-one")
print(json.dumps(first),flush=True)
original=p._save_receipt
def abrupt(receipt,fingerprint):
    if receipt["action"] == "deploy" and receipt["state"] == "succeeded": os._exit(0)
    return original(receipt,fingerprint)
p._save_receipt=abrupt
p.deploy(r["id"],site_id="site-one",session_id="session-one",expected_revision=1,request_id="interrupted-update")
'''
    result = subprocess.run([sys.executable, "-c", script, str(root), str(source)], cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=15, check=True)
    first = json.loads(result.stdout)
    assert_closed(first["result"]["url"])
    with Publisher(root) as publisher:
        site = publisher.status("site-one", "session-one")
        assert site["status"] == "unknown" and site["url"] is None
        unknown = next(item for item in publisher.receipts("session-one") if item["requestId"] == "interrupted-update")
        assert unknown["state"] == "unknown"
        release_id = first["result"]["releaseId"]
        with pytest.raises(PublishingError) as exc:
            publisher.deploy(release_id, site_id="site-one", session_id="session-one", expected_revision=1, request_id="interrupted-update")
        assert exc.value.receipt == unknown
        with pytest.raises(PublishingError, match="unknown operation"):
            publisher.deploy(release_id, site_id="site-one", session_id="session-one", expected_revision=site["revision"], request_id="new-unsafe-retry")
        stopped = publisher.stop("site-one", session_id="session-one", expected_revision=site["revision"], request_id="explicit-reconcile")
        assert stopped["result"]["status"] == "stopped"
        assert next(item for item in publisher.receipts("session-one") if item["requestId"] == "interrupted-update") == unknown


def test_unexpected_failure_after_listener_start_fails_closed(publisher, source, monkeypatch):
    release = build(publisher, source)
    review(publisher, release)
    original = publisher._put
    failed_once = False
    def fail(kind, record):
        nonlocal failed_once
        if kind == "site" and not failed_once:
            failed_once = True
            raise RuntimeError("injected state persistence failure")
        return original(kind, record)
    monkeypatch.setattr(publisher, "_put", fail)
    with pytest.raises(PublishingError) as exc:
        deploy(publisher, release)
    assert exc.value.code == "unknown_outcome"
    assert exc.value.receipt["state"] == "unknown"
    assert not publisher._sites
    assert publisher.status("site-one", "session-one")["status"] == "unknown"


def test_missing_owned_listener_cannot_claim_running(publisher, source):
    release = build(publisher, source)
    review(publisher, release)
    deploy(publisher, release)
    publisher._sites.pop("site-one").stop()
    assert publisher.status("site-one", "session-one")["status"] == "interrupted"
    assert publisher.status("site-one", "session-one")["url"] is None


def test_consistent_backup_restores_audit_and_explicit_resume_only(publisher, source, tmp_path):
    release = build(publisher, source)
    review(publisher, release)
    receipt = deploy(publisher, release)
    original_url = receipt["result"]["url"]
    metadata = publisher.snapshot(tmp_path / "backup")
    assert metadata["releaseCount"] == metadata["siteCount"] == 1
    assert not (tmp_path / "backup" / "owner.lock").exists()
    assert not (tmp_path / "backup" / "staging").exists()
    assert http(original_url)[0] == 200
    with Publisher(tmp_path / "backup") as restored:
        site = restored.status("site-one", "session-one")
        assert site["status"] == "interrupted" and site["url"] is None
        assert deploy(restored, release) == receipt
        assert restored.releases("session-one")[0]["review"]
        resumed = deploy(restored, release, site["revision"], "restored-explicit-resume")
        assert resumed["result"]["url"] != original_url
        assert http(resumed["result"]["url"])[2] == http(original_url)[2]
    with pytest.raises(PublishingError):
        publisher.snapshot(tmp_path / "backup")
    with pytest.raises(PublishingError):
        publisher.snapshot(publisher.root / "backup")


def test_root_has_one_process_owner(publisher):
    with pytest.raises(PublishingError) as exc:
        Publisher(publisher.root)
    assert exc.value.code == "root_busy"


@pytest.mark.parametrize("host", ["0.0.0.0", "8.8.8.8", "localhost", "::1", "169.254.1.1", "100.64.0.1", "127.0.0.2", "224.0.0.1", "172.32.0.1", "192.0.0.1"])
def test_unsafe_bind_addresses_rejected(tmp_path, host):
    with pytest.raises(PublishingError) as exc:
        Publisher(tmp_path / "private", bind_host=host)
    assert exc.value.code == "unsafe_bind"


def test_private_configuration_is_explicit_and_preview_stays_loopback(tmp_path, source):
    with Publisher(tmp_path / "private", bind_host="10.0.0.9") as publisher:
        assert publisher.capabilities == {"bindHost": "10.0.0.9", "accessPolicy": "private-network", "authentication": "none", "previewAccessPolicy": "loopback-only", "publicPublishing": False}
        release = build(publisher, source)
        preview = publisher.preview(release["id"], session_id="session-one", request_id="preview")
        assert preview["result"]["accessPolicy"] == "loopback-only"
        assert preview["result"]["url"].startswith("http://127.0.0.1:")
        assert http(preview["result"]["url"])[0] == 200


def test_dead_owned_listener_is_discarded_before_new_mutation(publisher, source):
    release = build(publisher, source)
    review(publisher, release)
    deploy(publisher, release)
    publisher._sites["site-one"].stop()
    with pytest.raises(PublishingError) as exc:
        deploy(publisher, release, 1, "dead-stale-redeploy")
    assert exc.value.code == "stale_revision"
    assert "site-one" not in publisher._sites
    resumed = deploy(publisher, release, 2, "dead-explicit-redeploy")
    assert http(resumed["result"]["url"])[0] == 200
    publisher.preview(release["id"], session_id="session-one", request_id="preview")
    publisher._previews[release["id"]].stop()
    resumed_preview = publisher.preview(release["id"], session_id="session-one", request_id="preview-resume")
    assert http(resumed_preview["result"]["url"])[0] == 200


def test_empty_smart_tools_nojekyll_sentinel_is_snapshotted_but_never_served(publisher, source):
    (source / ".nojekyll").touch()
    release = build(publisher, source)
    assert next(item for item in release["files"] if item["path"] == ".nojekyll")["size"] == 0
    review(publisher, release)
    site = deploy(publisher, release)["result"]
    assert http(site["url"])[0] == 200
    assert http(site["url"], "/.nojekyll")[0] == 404
    (source / ".nojekyll").write_text("secret")
    with pytest.raises(PublishingError):
        build(publisher, source, "nonempty-sentinel")
    (source / ".nojekyll").unlink()
    (source / "assets" / ".nojekyll").touch()
    with pytest.raises(PublishingError):
        build(publisher, source, "nested-sentinel")


def test_concurrent_exact_requests_execute_once_and_stale_updates_serialize(publisher, source):
    from concurrent.futures import ThreadPoolExecutor
    release = build(publisher, source)
    review(publisher, release)
    with ThreadPoolExecutor(max_workers=4) as workers:
        receipts = list(workers.map(lambda _: deploy(publisher, release), range(4)))
    assert all(receipt == receipts[0] for receipt in receipts)
    assert len(publisher._sites) == 1
    assert publisher.status("site-one", "session-one")["revision"] == 1
    def update(request):
        try:
            return deploy(publisher, release, 1, request)["state"]
        except PublishingError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(update, ("update-a", "update-b")))
    assert sorted(outcomes) == ["stale_revision", "succeeded"]
    assert publisher.status("site-one", "session-one")["revision"] == 2



def test_trusted_storage_ancestors_canonicalize_temporary_directory_aliases(source):
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        with Publisher(Path(temporary) / "publishing") as publisher:
            assert publisher.root == (Path(temporary) / "publishing").resolve()
            release = build(publisher, source)
            review(publisher, release)
            assert http(deploy(publisher, release)["result"]["url"])[0] == 200


def test_manifest_is_globally_path_sorted_for_transport(publisher, source):
    (source / "a").mkdir()
    (source / "a" / "z.txt").write_text("nested")
    (source / "a.html").write_text("sibling")
    (source / "unicodé.txt").write_text("unicode")
    release = build(publisher, source)
    paths = [item["path"] for item in release["files"]]
    assert paths == sorted(paths)
    canonical = json.dumps(release["files"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert release["manifestDigest"] == hashlib.sha256(canonical.encode()).hexdigest()


def test_stop_disconnects_already_accepted_slow_request(publisher, source):
    release = build(publisher, source)
    review(publisher, release)
    site = deploy(publisher, release)["result"]
    target = urlsplit(site["url"])
    connection = socket.create_connection((target.hostname, target.port), timeout=2)
    connection.sendall(b"GET / HTTP/1.1\r\n")
    publisher.stop("site-one", session_id="session-one", expected_revision=1, request_id="stop")
    try:
        connection.sendall(f"Host: 127.0.0.1:{target.port}\r\n\r\n".encode())
        assert connection.recv(1024) == b""
    except OSError:
        pass
    finally:
        connection.close()
