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

Privacy is a preflight observation, not an atomic permanent visibility
guarantee. Uploaded files remain in repository history; local removal does not
delete remote history. Shared snapshots exclude audit bodies, raw bytes,
source paths and GitHub stderr. Browser review/confirmation, persistent pending
intent and read-only checking use the same action identities as agent callers.
Manager-owned isolated tests and exact-code/outcome reviews remain required
after service routing integration; this source candidate is not live delivery
evidence.
