# Feedback follow-ups

The browser and agent use the same feedback actions. A feedback ID is the original submission request ID, never a guessed repository issue number. The host verifies the current GitHub owner, canonical issue URL and original feedback marker before a follow-up. Saved receipts and audit versions survive restarts; repository text is untrusted data.

- `feedback.get` reads the original report and bounded comment pages. Use a new request ID to refresh; exact retries return the same snapshot.
- `feedback.reconcile` reads GitHub for an unknown original submission. Only a unique fresh owner/marker match binds the original receipt. No match or incomplete/ambiguous search leaves delivery unknown and never resends it.
- `feedback.comment` appends the explicitly requested text. `feedback.update` adds a correction version as a comment, preserving the original report and maintainer changes. It does not replace the issue title or body. GitHub does not provide atomic conditional issue edits.
- `feedback.close` and `feedback.reopen` change only issue state after checking the revision from a fresh read. Title, body and comments remain intact. This check detects changes before the write; it is not an atomic compare-and-swap with GitHub.

Every mutation requires its own stable request ID. Exact retries are deduplicated; uncertain writes are never automatically repeated. Refresh the report to inspect the current remote result before deliberately starting another change. Corrections and state changes retain author, source revision, prior values and canonical receipt links in local audit storage; full audit bodies are not broadcast in UI snapshots.

The Feedback panel exposes delivery checking, correction drafts and close/reopen. Correction drafts persist per browser. Publishing a correction or changing report state is explicit; editing a draft does not post anything. The product still does not edit third-party comments or silently add post-submission attachments. Reviewed excerpt attachments are a separate follow-up.

## Explicit ordinary-file additions (source candidate)

Host action routing is present in this source candidate. Fresh installed-package
qualification is still required; this is not a claim of delivered recovery.
It does not implement protected original-title/body edits: GitHub's unsupported
conditional issue PATCH remains a separate blocker. A correction comment is
not relabelled as an original edit, and this increment does not complete that
promise.

Optional `feedbackId` on `feedback.attachment.add/remove` chooses a separate
per-client, per-original-feedback ordinary-file draft. Initial drafts, selection
and excerpt consent retain their existing flow. SHA256 is visible for every
follow-up file; no chat attachment or source path is implicitly included.
`feedback.attachments.review` reads the original submitted (or positively
reconciled) local receipt and verifies the allowlisted repository, exact issue,
original marker and current account. It freezes the complete manifest, private
destination, account/issue/repository IDs and retention disclosure. Review
alone is not consent and uploads nothing.

Explicit `feedback.attachments.add` requires the review request ID and the
complete exact confirmed `{id,sha256}` set. The host rechecks scope, bytes,
account, issue and private repository before any upload. It creates only
isolated Git Data blobs/tree/root commit/ref and an append-only marked comment
on the same issue. There is no issue title/body/state PATCH, new issue,
main-branch write, comment replacement or asset deletion. The initial upload
transport is not changed. Limits remain 8 files, 8 MiB each, 24 MiB total.
Excerpts are refused in this increment.

Every remote POST persists a sending fence, exact endpoint, input fingerprint
and known prerequisites in the existing private follow-up SQLite audit before
the request. Phase IDs are local audit IDs, **not GitHub idempotency keys**.
Returned validated immutable objects are saved as evidence while the phase
remains sending, before further awaits. A commit is acknowledged only after
its isolated tree, empty parents, exact message and canonical Git object are
validated **and** the exact SHA's account-linked committer is read and verified.
Failed or mismatching account lookup leaves commit unknown and ref/comment
not started. A known returned SHA can later be checked read-only; it is never
permission to create another commit.
Acknowledged phases are retained; sending phases become unknown on interruption
or restart; not-started phases remain stopped. Exact request retries return the
saved receipt with no remote writes. Neither content addressing nor a new
request ID authorizes replay. A frozen uncertain addition blocks new uploads
for that client/report.

`feedback.attachments.reconcile` is explicitly read-only. It verifies exact
known objects/bytes and full manifest trees, expected refs/root commits and
bounded uniquely matching comment marker/body/actor/target. Missing,
ambiguous or inaccessible evidence leaves delivery unknown. Stored files with
unknown or not-started comment delivery are reported separately as partial,
not success or failure. Reconciliation never resumes not-started work; any
continuation requires a separately guarded design.
Concurrent checks merge only positively verified phases into the latest durable
receipt under the service lock, comparing immutable destination, manifest and
phase input/prerequisite bindings. An older absent/inaccessible observation
cannot replace a newer succeeded phase or submitted receipt. Network reads do
not hold that lock, and the positive evidence survives reload and AppService
restart.

Privacy is a preflight observation, not an atomic permanent visibility
guarantee. Uploaded files remain in repository history; local removal does not
delete remote history. Shared snapshots exclude audit bodies, raw bytes,
source paths and GitHub stderr. Browser review/confirmation, persistent pending
intent and read-only checking use the same action identities as agent callers.
Browser staging awaits the host acknowledgement of one intended request ID per
selected file, with its exact submitted name, size, SHA256 and original feedback
scope before effects, including every same-ID check/reselection entry point.
Browser MIME is descriptive only. A failed or uncertain complete-intent save
prevents dispatch. Addition dispatch likewise waits for acknowledgement of its
complete pending arguments.
In-memory File references remain until acknowledgement or observed host staging
evidence. They do **not** survive reload. The client's `stagingReceipts` projection
reads saved local staging IDs/hash/size/selection. Same-client explicit staging
checks retain their original IDs. A newly attached browser after natural reload
is a different client; its inherited selection is read-only, not an editable
staging draft or permission to reselect/upload. Other reports and browser clients
retain their independent drafts.
Manager-owned isolated tests and exact-code/outcome reviews remain required
for this exact candidate; this source candidate is not live delivery
evidence.

### Exact read-only attachment recovery

A new client copied from a previous attach captures exact references in a
host-only client-record field, outside writable view state and public projection.
It captures the source's present canonical staging/operation rows, plus explicitly
saved staging and pending-addition hints. It carries prior references through
later reloads and restart, not a list of ancestor clients or permission to query
their future operations. The original client and duplicated tab may remain live;
neither transfers ownership or shares writable selections. Existing identities,
missing previous records and absent feedback initialization do not capture.

Each reference binds original owner, report, exact ID, action and immutable
fingerprint/intent. Missing pending additions freeze their complete arguments.
They become readable only if a later canonical acceptance matches those
arguments and original owner, including the original completed review binding.
Missing staging hints grant no file link until a canonical row matches the
host-recorded submitted-name/size/SHA binding. Staging records both this submitted
binding and the sanitized stored-name/sniffed-MIME manifest derived from the
actual bytes. Browser MIME and writable owner/lineage fields grant no authority.
A naked review ID grants neither file reads nor reusable consent.

`attachmentDrafts` and `stagingReceipts` remain current-owner projections.
`attachmentRecovery` exposes only validated read-only saved files, uncertainty
hints and a report-local blocker; inherited operation receipts are labelled
`readOnly`. The raw references, private audit and bytes are never projected.
Capture includes active operations and the latest 20 terminal operations, selected
staging and the latest 20 unselected staging rows; explicit pending references
and carried references are retained without silent eviction. Canonical file and
addition-history links remain readable where validated, even after a new draft.

An inherited queued/sending/unknown/partial addition or an unmatched captured
intent blocks new additions for that report, even if writable copied state is
cleared. Independent reports are not blocked. An unobserved intent can remain
blocked indefinitely: no hidden retry, cancellation, handoff or re-upload is
provided. Inherited editable-file reuse remains **unqualified and unsupported**.
When no relevant uncertainty remains, an explicit new draft uses this client's
own new file/request IDs, fresh review and fresh confirmation.

Exact same-payload inherited lookups return existing evidence without scheduling
a run; changed payloads conflict, and missing inherited IDs cannot become new
commands. The only new inherited operation is an explicit read-only delivery
check for an exact captured addition. Its receipt belongs to the current caller;
the addition's owner, immutable arguments and phase fences remain original.
Fresh account/private-destination checks precede existing GitHub reads. Positive
evidence merges into the latest durable receipt under the lock, revalidating the
inherited binding and never downgrading known success or acknowledging unattempted
phases. A non-owner check does not clean up the original staging selection.
No POST/PATCH, new upload phase or automatic continuation is licensed.

### Required manager qualification

The authored Python tests exercise AppService storage with an intercepted GitHub
wire, including both response loss and loss of local evidence/acknowledgement
saves at every phase, multiple blobs, restart without mutation replay,
commit-account failure, opposite-evidence concurrent reconciliation, unchanged
initial upload/correction wire, and forged scope/manifest/hash/visibility.
Corrective falsifiers add exact attach/duplicate snapshots while the original
remains active, late acceptance before/after reload and restart, forged/mutated
bindings, submitted versus sanitized names and sniffed MIME, report isolation,
retained bounded history and inherited concurrent reconciliation without owner
selection cleanup. Old strict phase/response-loss assertions remain unchanged.
`frontend/tests/feedback-additions-browser.mjs` is explicitly a **component unit
fixture with a synthetic host**, including complete-intent acknowledgement fences
and copied read-only UI; it is not an installed HTTP/SSE test. These tests have
only been syntax-checked, not executed in this source lane.

The manager must build/install the exact integrated package in the
one retained serial DTU and check authenticated `/api/actions` plus real
`/api/events` against **installed** backend/static assets. Intercept GitHub only,
not host actions or EventSource: observe local staging IDs, explicit review,
confirmation, partial/unknown phase display, client/report draft isolation and
natural reload/duplicate-client reads without replay while the original remains
live. Verify lost staging/addition HTTP acknowledgements both before and after
host acceptance; prove complete hints were already present at attach and preserve
exact IDs across another reload and AppService restart. Missing inherited
acceptance must remain blocked without re-selection or upload. Capture imported package path/version, asset
identity, actual action schemas, HTTP receipts, SSE results and zero runtime/model
calls. Serial full Node and whole feedback Python suites, the current receiving
browser check and both exact-code/results reviews remain parent-owned. None ran
here; fake-host success cannot discharge installed receiving evidence. Original
issue title/body editing remains blocked; this does not complete the full
feedback-edit promise.
