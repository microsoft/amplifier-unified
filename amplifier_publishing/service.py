"""Long-lived private static publisher with an owner-only Unix admin socket.

No package installation, build command, host provisioning or SSH is performed.
Run ``python -m amplifier_publishing.service --help`` for the explicit CLI.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import shutil
import signal
import socketserver
import sqlite3
import stat
import sys
import threading
import uuid

from .publisher import Publisher, PublishingError
from .remote import MAX_MESSAGE_BYTES, MUTATIONS, canonical, decode_message, digest, encode_message, private_bind, service_identity, timeout_value, unix_request

MAX_FILES = 2000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024


def _now():
    return datetime.now(timezone.utc).isoformat()


def _identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", value):
        raise PublishingError("invalid_argument", f"{name} must be a bounded plain identifier")
    return value


def _private_directory(path):
    path = Path(path).absolute()
    if ".." in path.parts:
        raise PublishingError("unsafe_root", "Service directories cannot contain traversal")
    if path.is_symlink():
        raise PublishingError("unsafe_root", "Service directories cannot be symlinks")
    # These are explicit host configuration paths, not untrusted artifact paths.
    # Canonicalize host-controlled ancestors (e.g. macOS /var -> /private/var).
    path = path.parent.resolve() / path.name
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    # All existing ancestors must be real directories, and the leaf is owned by
    # this account. Its contents are never exposed through the HTTP listener.
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise PublishingError("unsafe_root", "Service directories must be owner-only (0700)")
    except OSError as exc:
        raise PublishingError("unsafe_root", "Service directories cannot contain symlinks") from exc
    finally:
        os.close(fd)
    return path


def _error(exc):
    result = {"code": exc.code, "message": str(exc)}
    if exc.receipt is not None:
        result["receipt"] = exc.receipt
    return {"ok": False, "error": result}


def _manifest(request):
    manifest, files = request["manifest"], request["files"]
    if not isinstance(manifest, list) or not 1 <= len(manifest) <= MAX_FILES or not isinstance(files, dict) or len(files) != len(manifest):
        raise PublishingError("invalid_manifest", "Import requires a bounded manifest and exactly its files")
    decoded = {}
    total = 0
    previous_path = None
    for entry in manifest:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            raise PublishingError("invalid_manifest", "Manifest entries require path, size and sha256")
        path, size, sha = entry["path"], entry["size"], entry["sha256"]
        if not isinstance(path, str) or len(path.encode()) > 1024:
            raise PublishingError("unsafe_path", "Import paths must be bounded relative paths")
        parts = path.split("/")
        sentinel = path == ".nojekyll" and size == 0
        if not sentinel and (len(parts) > 20 or any(not part or part.startswith(".") or "\\" in part or any(ord(c) < 32 for c in part) for part in parts)):
            raise PublishingError("unsafe_path", "Hidden, traversal and ambiguous paths are not publishable")
        if previous_path is not None and path <= previous_path:
            raise PublishingError("invalid_manifest", "Manifest paths must be unique and sorted")
        previous_path = path
        if type(size) is not int or size < 0 or size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
            raise PublishingError("size_limit", "Import exceeds decoded file or total byte limits")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise PublishingError("invalid_manifest", "Manifest SHA256 must be lowercase hexadecimal")
        encoded = files.get(path)
        if not isinstance(encoded, str) or len(encoded) != 4 * ((size + 2) // 3):
            raise PublishingError("integrity_error", "File bytes do not match the manifest size")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise PublishingError("integrity_error", "File bytes must be canonical base64") from exc
        if len(data) != size or base64.b64encode(data).decode() != encoded or hashlib.sha256(data).hexdigest() != sha:
            raise PublishingError("integrity_error", "File bytes do not match their manifest")
        if path in decoded or any(parent in decoded for parent in ("/".join(parts[:i]) for i in range(1, len(parts)))):
            raise PublishingError("unsafe_path", "Import paths overlap")
        decoded[path] = data
        total += size
    if set(files) != set(decoded) or "index.html" not in decoded:
        raise PublishingError("invalid_manifest", "Import requires index.html and exactly the manifest file set")
    if request["manifestDigest"] != digest(manifest):
        raise PublishingError("integrity_error", "Manifest digest does not match the exact manifest")
    return decoded


class PublishingService:
    """One owner, durable import receipts, immutable releases and local control."""

    def __init__(self, root, socket_path, *, bind="127.0.0.1"):
        self.bind, self.access_policy = private_bind(bind)
        root = _private_directory(root)
        self.state_root = _private_directory(root.parent / f".{root.name}-service")
        self.socket_path = Path(socket_path).absolute()
        socket_parent = _private_directory(self.socket_path.parent)
        self.socket_path = socket_parent / self.socket_path.name
        if self.socket_path.exists() or self.socket_path.is_symlink():
            raise PublishingError("socket_busy", "Admin socket path already exists; verify its owner before removing a stale socket")
        if self.socket_path == root or self.socket_path.is_relative_to(root):
            raise PublishingError("unsafe_root", "Admin socket must be outside the publisher store")
        self.staging = _private_directory(self.state_root / "transfers")
        self.publisher = Publisher(root, bind_host=self.bind)
        self._mutex = threading.RLock()
        self._thread = None
        self._server = None
        self._closed = False
        try:
            self._db = sqlite3.connect(self.state_root / "imports.sqlite3", check_same_thread=False, isolation_level=None)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("CREATE TABLE IF NOT EXISTS imports (session TEXT, request TEXT, fingerprint TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(session,request))")
            self._db.execute("CREATE TABLE IF NOT EXISTS rpc_receipts (session TEXT, request TEXT, payload_digest TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(session,request))")
            self._db.execute("CREATE TABLE IF NOT EXISTS identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), service_id TEXT NOT NULL)")
            self._db.execute("INSERT OR IGNORE INTO identity VALUES (1, ?)", (str(uuid.uuid4()),))
            self.service_id = service_identity(self._db.execute("SELECT service_id FROM identity WHERE singleton=1").fetchone()[0])
            self._recover()
            self._server = _AdminServer(str(self.socket_path), self)
            self.socket_path.chmod(0o600)
            self._socket_inode = self.socket_path.stat().st_ino
        except BaseException:
            if self._server:
                self._server.server_close()
            if hasattr(self, "_db"):
                self._db.close()
            self.publisher.close()
            raise

    def _save(self, receipt, fingerprint):
        self._db.execute("INSERT OR REPLACE INTO imports VALUES (?,?,?,?)", (receipt["sessionId"], receipt["requestId"], fingerprint, canonical(receipt)))

    def _recover(self):
        for session_id, request_id, fingerprint, body in self._db.execute("SELECT session,request,fingerprint,body FROM imports").fetchall():
            record = decode_message(body)
            if record["state"] == "running":
                previous = next((r for r in self.publisher.receipts(session_id) if r["requestId"] == request_id), None)
                result = previous.get("result") if previous else None
                proven_build = (previous and previous.get("action") == "build" and previous.get("state") == "succeeded"
                                and previous.get("siteId") == record["siteId"] and previous.get("sessionId") == session_id
                                and record.get("manifestDigest") and isinstance(result, dict)
                                and result.get("siteId") == record["siteId"] and result.get("sessionId") == session_id
                                and result.get("manifestDigest") == record["manifestDigest"])
                if proven_build:
                    record.update(state="succeeded", result=result, error=None, completedAt=previous["completedAt"])
                else:
                    record.update(state="unknown", completedAt=_now(), error={"code": "unknown_outcome", "message": "Import ended before its outcome was recorded; it was not replayed"})
                self._save(record, fingerprint)
        for (body,) in self._db.execute("SELECT body FROM rpc_receipts").fetchall():
            receipt = decode_message(body)
            if receipt["state"] == "running":
                # A matching inner receipt ID is insufficient proof of arguments.
                # Keep uncertainty if the exact RPC outcome was not committed.
                receipt.update(state="unknown", completedAt=_now(), error={"code": "unknown_outcome", "message": "Process ended before the exact RPC outcome was recorded; it was not replayed"})
                self._save_rpc(receipt)

    def _save_rpc(self, receipt):
        self._db.execute("INSERT OR REPLACE INTO rpc_receipts VALUES (?,?,?,?)", (receipt["sessionId"], receipt["requestId"], receipt["rpcPayloadDigest"], canonical(receipt)))

    @staticmethod
    def _rpc_result(receipt):
        if receipt["state"] != "succeeded":
            error = receipt.get("error") or {"code": "unknown_outcome", "message": "RPC outcome is not confirmed; it was not replayed"}
            raise PublishingError(error["code"], error["message"], receipt=receipt)
        return receipt["result"] if receipt["action"] == "import" else receipt

    def _rpc_mutation(self, request):
        """Bind an exact RPC payload to its outcome without relabeling history."""
        sid, rid = request["sessionId"], request["requestId"]
        payload_digest = digest(request)  # Includes expectedServiceId.
        previous = self._db.execute("SELECT payload_digest,body FROM rpc_receipts WHERE session=? AND request=?", (sid, rid)).fetchone()
        if previous:
            if previous[0] != payload_digest:
                raise PublishingError("request_conflict", "requestId was already admitted with another exact RPC payload")
            return self._rpc_result(decode_message(previous[1]))

        legacy = next((row for row in self._base_receipts(sid) if row["requestId"] == rid), None)
        receipt = {"id": digest([sid, rid]), "sessionId": sid, "requestId": rid, "serviceId": self.service_id,
                   "rpcPayloadDigest": payload_digest, "action": request["method"], "siteId": request.get("siteId"),
                   "releaseId": request.get("releaseId"), "state": "running", "createdAt": _now(),
                   "completedAt": None, "result": None, "error": None}
        if legacy is None:
            self._save_rpc(receipt)  # Durable reservation before any effect.
        # A legacy receipt already reserves this ID. Do not reserve or stamp it
        # for a new payload until the original exact-retry path proves a match.
        try:
            result = self._mutate(request)
            proven = (next((row for row in self._base_receipts(sid) if row["requestId"] == rid), None)
                      if request["method"] == "import" else result)
            if not self._matches_rpc(proven, request):
                raise RuntimeError("RPC returned an unrelated operation receipt")
            receipt = {**proven, "serviceId": self.service_id, "rpcPayloadDigest": payload_digest}
        except PublishingError as exc:
            if legacy is not None and not self._matches_rpc(exc.receipt, request):
                # Includes request_conflict: keep the original unproven receipt
                # intact rather than claim its outcome for different arguments.
                raise
            if self._matches_rpc(exc.receipt, request):
                receipt = {**exc.receipt, "serviceId": self.service_id, "rpcPayloadDigest": payload_digest}
            else:
                receipt.update(state="unknown" if exc.code == "unknown_outcome" else "failed", completedAt=_now(), error={"code": exc.code, "message": str(exc)})
        except Exception as exc:
            if legacy is not None:
                raise PublishingError("unknown_outcome", "Historical RPC outcome could not be proven; it was not relabeled or replayed") from exc
            receipt.update(state="unknown", completedAt=_now(), error={"code": "unknown_outcome", "message": "RPC outcome could not be proven; it was not replayed"})
        self._save_rpc(receipt)
        return self._rpc_result(receipt)

    @staticmethod
    def _matches_rpc(receipt, request):
        return (isinstance(receipt, dict) and receipt.get("sessionId") == request["sessionId"]
                and receipt.get("requestId") == request["requestId"] and receipt.get("action") == request["method"])

    @staticmethod
    def _import_result(receipt):
        if receipt["state"] != "succeeded":
            error = receipt["error"] or {"code": "unknown_outcome", "message": "Import outcome is not confirmed; it was not replayed"}
            raise PublishingError(error["code"], error["message"], receipt=receipt)
        return receipt["result"]

    def _import(self, request):
        sid, rid = request["sessionId"], request["requestId"]
        # Keep historical import fingerprints compatible: identity is an
        # admission guard, while the persisted payload remains its exact bytes.
        fingerprint = digest({key: value for key, value in request.items() if key != "expectedServiceId"})
        previous = self._db.execute("SELECT fingerprint,body FROM imports WHERE session=? AND request=?", (sid, rid)).fetchone()
        if previous:
            if previous[0] != fingerprint:
                raise PublishingError("request_conflict", "requestId was already used with different import bytes or metadata")
            return self._import_result(decode_message(previous[1]))
        if any(r["requestId"] == rid for r in self.publisher.receipts(sid)):
            raise PublishingError("request_conflict", "requestId was already used by another publishing action")
        receipt = {"id": digest([sid, rid]), "sessionId": sid, "requestId": rid, "siteId": request["siteId"], "manifestDigest": request["manifestDigest"], "action": "import", "state": "running", "createdAt": _now(), "completedAt": None, "result": None, "error": None}
        self._save(receipt, fingerprint)  # Durable reservation before any staged bytes.
        staging = self.staging / receipt["id"]
        try:
            files = _manifest(request)
            staging.mkdir(mode=0o700)  # Existing leftovers are never trusted/reused.
            for path, data in files.items():
                target = staging / path
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with open(target, "xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                target.chmod(0o400)
            release = self.publisher.build(staging, site_id=request["siteId"], session_id=sid, request_id=rid)
            if release["manifestDigest"] != request["manifestDigest"]:
                raise PublishingError("integrity_error", "Built release differs from the exact imported manifest")
            receipt.update(state="succeeded", result=release, completedAt=_now())
        except PublishingError as exc:
            receipt.update(state="unknown" if exc.code == "unknown_outcome" else "failed", completedAt=_now(), error={"code": exc.code, "message": str(exc)})
        except Exception:
            receipt.update(state="unknown", completedAt=_now(), error={"code": "unknown_outcome", "message": "Import outcome could not be recorded; it will not be replayed"})
        self._save(receipt, fingerprint)
        if staging.exists():
            shutil.rmtree(staging)
        return self._import_result(receipt)

    def _base_receipts(self, session_id):
        merged = {r["requestId"]: r for r in self.publisher.receipts(session_id)}
        for (body,) in self._db.execute("SELECT body FROM imports WHERE session=? ORDER BY rowid", (session_id,)):
            receipt = decode_message(body)
            merged[receipt["requestId"]] = receipt
        return sorted(merged.values(), key=lambda r: (r["createdAt"], r["requestId"]))

    def _receipts(self, session_id):
        merged = {receipt["requestId"]: receipt for receipt in self._base_receipts(session_id)}
        for (body,) in self._db.execute("SELECT body FROM rpc_receipts WHERE session=? ORDER BY rowid", (session_id,)):
            receipt = decode_message(body)
            merged[receipt["requestId"]] = receipt
        return sorted(merged.values(), key=lambda receipt: (receipt["createdAt"], receipt["requestId"]))

    def handle(self, request):
        """Return a bounded JSON envelope. Arbitrary methods/paths are not RPCs."""
        try:
            encode_message(request)
            with self._mutex:
                if self._closed:
                    raise PublishingError("closed", "Publishing service is closed")
                return {"ok": True, "result": self._dispatch(request)}
        except PublishingError as exc:
            return _error(exc)
        except (TypeError, KeyError):
            return _error(PublishingError("invalid_argument", "Request fields are invalid"))
        except Exception:
            return _error(PublishingError("unknown_outcome", "Service could not confirm the outcome; inspect the durable receipt"))

    def _dispatch(self, request):
        fields = {
            "target": set(), "list": {"sessionId"}, "releases": {"sessionId"}, "receipts": {"sessionId"},
            "receipt": {"sessionId", "requestId"}, "status": {"sessionId", "siteId"},
            "import": {"sessionId", "requestId", "siteId", "manifest", "manifestDigest", "files"},
            "preview": {"sessionId", "requestId", "releaseId"},
            "review": {"sessionId", "requestId", "releaseId", "note"},
            "deploy": {"sessionId", "requestId", "releaseId", "siteId", "expectedRevision"},
            "rollback": {"sessionId", "requestId", "releaseId", "siteId", "expectedRevision"},
            "stop": {"sessionId", "requestId", "siteId", "expectedRevision"},
            "remove": {"sessionId", "requestId", "siteId", "expectedRevision"},
        }
        method = request.get("method")
        if not isinstance(method, str) or method not in fields:
            raise PublishingError("invalid_argument", "Unknown method or missing/extra request fields")
        unbound_discovery = method == "target" and set(request) == {"method"}
        if not unbound_discovery:
            if "expectedServiceId" not in request:
                raise PublishingError("service_identity_required", "Inspect the target and include its expectedServiceId before requesting service data or operations")
            if service_identity(request["expectedServiceId"]) != self.service_id:
                raise PublishingError("target_mismatch", "Service identity differs from the inspected target; no operation was admitted")
        expected_fields = {"method"} | fields[method] | (set() if unbound_discovery else {"expectedServiceId"})
        if set(request) != expected_fields:
            raise PublishingError("invalid_argument", "Unknown method or missing/extra request fields")
        for name in ("sessionId", "requestId", "siteId", "releaseId"):
            if name in request:
                _identifier(request[name], name)
        if method == "target":
            return {"serviceId": self.service_id, "protocol": "static-publishing-v1", "adminTransport": "owner-unix-socket", "bind": self.bind, "accessPolicy": self.access_policy, "authentication": "none", "publicPublishing": False, "previewAccessPolicy": "loopback-only", "limits": {"messageBytes": MAX_MESSAGE_BYTES, "files": MAX_FILES, "fileBytes": MAX_FILE_BYTES, "totalBytes": MAX_TOTAL_BYTES}}
        sid = request["sessionId"]
        if method in MUTATIONS:
            return self._rpc_mutation(request)
        if method == "list":
            return self.publisher.list(sid)
        if method == "releases":
            return self.publisher.releases(sid)
        if method == "receipts":
            return self._receipts(sid)
        if method == "receipt":
            return next((r for r in self._receipts(sid) if r["requestId"] == request["requestId"]), None)
        if method == "status":
            return self.publisher.status(request["siteId"], sid)
        raise PublishingError("invalid_argument", "Unknown service request")

    def _mutate(self, request):
        method, sid = request["method"], request["sessionId"]
        if method == "import":
            return self._import(request)
        if self._db.execute("SELECT 1 FROM imports WHERE session=? AND request=?", (sid, request["requestId"])).fetchone():
            raise PublishingError("request_conflict", "requestId was already used by an import")
        args = {"session_id": sid, "request_id": request["requestId"]}
        if method in {"deploy", "rollback", "stop", "remove"}:
            args["expected_revision"] = request["expectedRevision"]
        if method in {"deploy", "rollback"}:
            args["site_id"] = request["siteId"]
        if method == "review":
            args["note"] = request["note"]
        identity = request["siteId"] if method in {"stop", "remove"} else request["releaseId"]
        return getattr(self.publisher, method)(identity, **args)

    def start(self):
        if self._thread is not None or self._closed:
            raise PublishingError("invalid_state", "Service cannot be started twice")
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def close(self):
        if self._closed:
            return
        if self._thread:
            self._server.shutdown()
            self._thread.join(timeout=3)
        self._server.server_close()
        with self._mutex:
            self.publisher.close()
            self._db.close()
            self._closed = True
        if self.socket_path.exists() and self.socket_path.lstat().st_ino == self._socket_inode:
            self.socket_path.unlink()

    def __enter__(self):
        return self.start()

    def __exit__(self, *_args):
        self.close()


class _AdminServer(socketserver.UnixStreamServer):
    def __init__(self, path, service):
        self.service = service
        super().__init__(path, _AdminHandler)


class _AdminHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(10)
        try:
            raw = self.rfile.readline(MAX_MESSAGE_BYTES + 1)
            if not raw.endswith(b"\n"):
                raise PublishingError("invalid_message", "Request must end with a newline within its byte limit")
            response = self.server.service.handle(decode_message(raw))
            self.wfile.write(encode_message(response))
        except PublishingError as exc:
            self.wfile.write(encode_message(_error(exc)))
        except OSError:
            # The caller may have gone away after a committed operation.
            # Durable receipts remain queryable; never repeat the operation.
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run an explicitly configured private service")
    serve.add_argument("--root", required=True)
    serve.add_argument("--socket", required=True)
    serve.add_argument("--bind", default="127.0.0.1")
    request = sub.add_parser("request", help="Send one JSON request from stdin to a local admin socket")
    request.add_argument("--socket", required=True)
    request.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args(argv)
    if args.command == "serve":
        stopped = threading.Event()
        previous_signal = signal.signal(signal.SIGTERM, lambda *_args: stopped.set())
        try:
            with PublishingService(args.root, args.socket, bind=args.bind) as service:
                print(canonical({"ready": True, "target": service._dispatch({"method": "target"})}), flush=True)
                while service._thread.is_alive() and not stopped.is_set():
                    service._thread.join(timeout=0.5)
            return 0
        except KeyboardInterrupt:
            return 0
        except PublishingError as exc:
            print(canonical(_error(exc)), file=sys.stderr)
            return 1
        finally:
            signal.signal(signal.SIGTERM, previous_signal)
    else:
        try:
            timeout_value(args.timeout)
            raw = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1)
            value = decode_message(raw)
            result = {"ok": True, "result": unix_request(args.socket, value, timeout=args.timeout)}
        except PublishingError as exc:
            result = _error(exc)
        sys.stdout.buffer.write(encode_message(result))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
