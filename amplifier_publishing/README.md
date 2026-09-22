# Managed static publishing

`amplifier_publishing` is a standard-library-only component for immutable static
release snapshots and explicit managed listener operations. It knows nothing
about an application, model, workspace, build system, or remote transport. The
caller must first build its site using its existing builder, authorize the source
directory, and scope each call to the authenticated owning session.

Supported runtime hosts are POSIX systems with `flock` and directory-relative
`O_NOFOLLOW` file operations (tested on macOS; Linux-compatible). There is one
active `Publisher` per storage root. Calls are serialized and may be made from
worker threads. No source or deploy commands run inside this library.

## API

```python
from amplifier_publishing import Publisher, PublishingError

publisher = Publisher(private_storage_root)
release = publisher.build(
    built_output_directory, site_id="example", session_id="task-1", request_id="build-1",
)
publisher.preview(release["id"], session_id="task-1", request_id="preview-1")
publisher.review(
    release["id"], session_id="task-1", request_id="review-1", note="Reviewed the preview",
)
receipt = publisher.deploy(
    release["id"], site_id="example", session_id="task-1",
    expected_revision=0, request_id="deploy-1",
)
site = publisher.status("example", "task-1")
publisher.close()
```

- `Publisher(root, *, bind_host="127.0.0.1", max_files=2000,
  max_file_bytes=20*1024*1024, max_total_bytes=100*1024*1024)` opens storage.
- `build(source, *, site_id, session_id, request_id)` returns a release.
- `review(release_id, *, session_id, request_id, note="")` saves a review bound
  to that exact immutable manifest. A review records caller intent; it does not
  claim automated visual inspection or security certification.
- `preview(release_id, *, session_id, request_id)` starts a dedicated loopback
  origin for the snapshot. A preview never changes a deployed site.
- `deploy(release_id, *, site_id, session_id, expected_revision, request_id)`
  deploys or updates a reviewed release; an update keeps the current listener URL.
- `rollback(...)` takes the same arguments as `deploy` and requires a release
  that was previously deployed to this site.
- `stop(site_id, *, session_id, expected_revision, request_id)` closes the site's
  deployment and previews. `remove(...)` also marks the managed site removed.
  Both retain releases, reviews, and receipts for audit. A later explicit deploy
  may reuse the site with its current revision.
- `list(session_id)`, `status(site_id, session_id)`, `releases(session_id)`, and
  `receipts(session_id)` return only that session's records. The list retains
  removed sites so their revision and audit history remain discoverable.
- `export_release(release_id, session_id)` returns only `siteId`, `sessionId`,
  `manifest`, `manifestDigest`, and canonical base64 `files`, suitable for a
  transport import request. It reads the owned immutable snapshot through
  no-follow descriptors and verifies the exact exported bytes, including a
  second hash check after initial release validation. It accepts no source path
  and exports no review, preview, listener, or storage metadata. A transport must
  enforce its own message and transfer limits before sending this payload.
- `snapshot(destination)` copies a consistent private database and all known
  immutable release files to a new directory. It excludes temporary staging and
  owner locks. It does not change live listeners. Restoring a copy never resumes
  historical listeners; callers own backup access, encryption, and retention.
- `capabilities` reports `bindHost`, `accessPolicy`, `authentication`,
  `previewAccessPolicy`, and `publicPublishing`.
- `close()` closes owned listeners and storage and marks running records stopped.
  A context manager is supported.

All mutations except `build` return a receipt. `PublishingError` subclasses
`ValueError` and provides `code` and, when persisted, `receipt`.

## Records and durable retry

A release contains `id`, `siteId`, `sessionId`, `manifestDigest`, `createdAt`,
`files` (`path`, `sha256`, `size`), and `totalBytes`. Its projected `review`,
`previewUrl`, and `previewStatus` describe current related records. Release IDs
are SHA256 of the site, session, and manifest digest; manifest digests are SHA256
of canonical ordered manifest JSON. The captured bytes and original creation
record do not change. Every serve and review verifies the saved content hash.

A site contains `id`, `sessionId`, `status`, `revision`, `url`, `releaseId`,
`previousReleaseId`, `accessPolicy`, `updatedAt`, and `deployedReleaseIds`.
Initial deployment expects revision `0`. Successful lifecycle actions and
listener interruption increment the revision. Reusing an old revision fails.

A receipt contains `id`, `requestId`, `sessionId`, `action`, `siteId`, `releaseId`,
`state`, `createdAt`, `completedAt`, `result`, and `error`. Pending intent is
committed before effects. Domain changes and the completed receipt commit in
one SQLite transaction. Expected validation failures are also durably recorded.

The caller supplies a stable `requestId` for retries. Its namespace is the
session. An exact retry returns the **same historical result**, including a URL
that may no longer be live; use current status to determine liveness. Reusing a
request ID with a different method or arguments fails. A build request binds the
source directory argument, not its later contents: retrying it after editing or
deleting the source returns the original snapshot. A new build requires a new
request ID. Failed requests retain the same historical failure even when the
condition is later repaired; use a new ID for a deliberate new attempt.

An interrupted process leaves pending receipts `unknown`. They never replay.
A pending deployment, rollback, stop, or remove fences the site. A caller may
explicitly stop or remove the site at its current revision to clear the fence;
the original unknown receipt remains unchanged. A process restart marks formerly
running listeners interrupted and clears their live URLs, preserving old URLs
as historical metadata. Only a new explicit operation can start a listener.

The optional private service adds a separate durable RPC ledger. Its mutation
receipts expose `serviceId` and `rpcPayloadDigest`, the SHA256 of the exact
canonical request including `expectedServiceId`. Remote callers must compare
that proof to their saved admission before adopting an outcome: matching only
the request ID, action, or release can confuse an older request with changed
arguments. Historical receipts do not acquire proof merely by being read.
Their original exact-retry checks must verify the supplied arguments first.
An interrupted RPC without a committed outcome stays unknown, even if an inner
receipt has the same ID. See [the private service contract](../docs/PUBLISHING-TARGET.md)
for the transport and legacy recovery boundaries.

## Exposure and storage boundaries

The default deployment and all previews bind only `127.0.0.1` on ephemeral ports,
separate from the consuming application's origin. An administrator may explicitly
configure a deployment bind address in RFC1918 IPv4 ranges. Wildcard, public,
DNS, IPv6, link-local, and other addresses are rejected. Such a site reports
`private-network`; it has **no built-in user authentication or TLS**. Network
access controls and a managed external service remain the embedding host's
responsibility. A configured but unavailable interface fails to bind. This API
does not create cloud accounts, tunnels, DNS records, or public endpoints.

The HTTP server accepts only its exact numeric host and port, preventing an
arbitrary DNS name from rebinding to the listener. It serves only manifest files,
without directory listings, cross-origin access headers, or hidden/traversal
paths. Responses use explicit MIME types, `nosniff`, same-origin resource policy,
no-referrer, and no-store. Request URLs, queries, cookies, and referrers are not
logged. Stopping a listener closes its accepted connections as well as its socket.

Builds require `index.html`, reject symlinks (including source ancestors), special
files, hidden components, ambiguous paths, excessive depth, file counts, and
sizes. The sole hidden-input exception is an empty root `.nojekyll` sentinel,
which existing static builders emit; it is captured in the manifest but never
served. Storage and source trees must be separate. Files are copied through
no-follow descriptors and are not served from the mutable source directory.

Storage is private host-controlled data: `state.sqlite3`, its journal files,
`releases/<id>/manifest.json`, and `releases/<id>/files/**`. Content files are
read-only and hash checked, which detects accidental changes; this is not a
security boundary against a malicious process with the same OS account. A crash
may leave an unused staged/orphan snapshot, retained rather than replayed or
silently assigned a success outcome. The host controls retention and disk quota.
