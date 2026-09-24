# Feedback follow-ups

The browser and agent use the same feedback actions. A feedback ID is the original submission request ID, never a guessed repository issue number. The host verifies the current GitHub owner, canonical issue URL and original feedback marker before a follow-up. Saved receipts and audit versions survive restarts; repository text is untrusted data.

- `feedback.get` reads the original report and bounded comment pages. Use a new request ID to refresh; exact retries return the same snapshot.
- `feedback.reconcile` reads GitHub for an unknown original submission. Only a unique fresh owner/marker match binds the original receipt. No match or incomplete/ambiguous search leaves delivery unknown and never resends it.
- `feedback.comment` appends the explicitly requested text. `feedback.update` adds a correction version as a comment, preserving the original report and maintainer changes. It does not replace the issue title or body. GitHub does not provide atomic conditional issue edits.
- `feedback.close` and `feedback.reopen` change only issue state after checking the revision from a fresh read. Title, body and comments remain intact. This check detects changes before the write; it is not an atomic compare-and-swap with GitHub.

Every mutation requires its own stable request ID. Exact retries are deduplicated; uncertain writes are never automatically repeated. Refresh the report to inspect the current remote result before deliberately starting another change. Corrections and state changes retain author, source revision, prior values and canonical receipt links in local audit storage; full audit bodies are not broadcast in UI snapshots.

The Feedback panel exposes delivery checking, correction drafts and close/reopen. Correction drafts persist per browser. Publishing a correction or changing report state is explicit; editing a draft does not post anything. The product still does not edit third-party comments or silently add post-submission attachments. Reviewed excerpt attachments are a separate follow-up.
