# Feedback follow-ups

The browser and agent use the same feedback actions. A feedback ID is the original submission request ID, never a guessed repository issue number. The host verifies the current GitHub owner, canonical issue URL and original feedback marker before a follow-up. Saved receipts and audit versions survive restarts; repository text is untrusted data.

- `feedback.get` reads the original report and bounded comment pages. Use a new request ID to refresh; exact retries return the same snapshot.
- `feedback.reconcile` reads GitHub for an unknown original submission. Only a unique fresh owner/marker match binds the original receipt. No match or incomplete/ambiguous search leaves delivery unknown and never resends it.
- `feedback.comment` appends the explicitly requested text. `feedback.update` adds a correction version as a comment, preserving the original report and maintainer changes. It does not replace the issue title or body. GitHub does not provide atomic conditional issue edits.
- `feedback.close` and `feedback.reopen` change only issue state after checking the revision from a fresh read. Title, body and comments remain intact. This check detects changes before the write; it is not an atomic compare-and-swap with GitHub.

Every mutation requires its own stable request ID. Exact retries are deduplicated; uncertain writes are never automatically repeated. Refresh the report to inspect the current remote result before deliberately starting another change. Corrections and state changes retain author, source revision, prior values and canonical receipt links in local audit storage; full audit bodies are not broadcast in UI snapshots.

The Feedback panel exposes delivery checking, correction drafts and close/reopen. Correction drafts persist per browser. Publishing a correction or changing report state is explicit; editing a draft does not post anything. The product still does not edit third-party comments or silently add post-submission attachments. Reviewed excerpt attachments are a separate follow-up.

## Explicit ordinary-file additions (source candidate)

This increment requires host action routing integration before it is runnable.
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
Browser staging saves one intended request ID per selected file, with its exact
name, browser MIME, size, SHA256 and original feedback scope before effects.
In-memory File references remain until acknowledgement or observed host staging
evidence. They do **not** survive reload. The client's `stagingReceipts` projection
reads saved local staging IDs/hash/size/selection; absent evidence requires an
explicit exact-file reselection using the original IDs, never a new selection ID
as a retry. Other reports and browser clients retain their independent drafts.
Manager-owned isolated tests and exact-code/outcome reviews remain required
after service routing integration; this source candidate is not live delivery
evidence.

### Qualification and routing prerequisite

The authored Python tests exercise AppService storage with an intercepted GitHub
wire, including both response loss and loss of local evidence/acknowledgement
saves at every phase, multiple blobs, restart without mutation replay,
commit-account failure, opposite-evidence concurrent reconciliation, unchanged
initial upload/correction wire, and forged scope/manifest/hash/visibility.
`frontend/tests/feedback-additions-browser.mjs` is explicitly a **component unit
fixture with a synthetic host**, including staging loss/reselection; it is not
an installed HTTP/SSE test. These tests have not been executed in this source lane.

The parent owns the remaining `service.py` wiring. The action definitions are
already composed by `feedback.definitions()` from `feedback_additions.definitions`;
do not duplicate schemas. Add `feedback.attachments.review`,
`feedback.attachments.add`, and `feedback.attachments.reconcile` to all three
existing follow-up routing sets: work-paused admission, the dispatch branch that
calls `self.feedback.followups.accept()` and queues `followups.run`, and response
receipt `requestId` propagation. Existing generic `feedback.*` save scope and
per-client projection remain in force. No service edit is made by this candidate.

After wiring, the manager must build/install the exact integrated package in the
one retained serial DTU and check authenticated `/api/actions` plus real
`/api/events` against **installed** backend/static assets. Intercept GitHub only,
not host actions or EventSource: observe local staging IDs, explicit review,
confirmation, partial/unknown phase display, client/report draft isolation and
reload reads without replay. Verify one lost staging HTTP acknowledgement both
before and after host acceptance; preserve saved IDs and require exact reselection
when no host receipt/bytes remain. Capture imported package path/version, asset
identity, actual action schemas, HTTP receipts, SSE results and zero runtime/model
calls. This installed fixture/qualification is blocked on the parent-owned routing
and has not been authored or run here. Fake-host success cannot discharge it.
