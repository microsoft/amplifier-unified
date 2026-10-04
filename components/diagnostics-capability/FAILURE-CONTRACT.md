# Diagnostic failure containment

Diagnostics are derived live observations. Storage failure must not rewrite a database, infer an empty index, replay an effect, or make the full owner appear ready.

- Only a genuinely absent diagnostics database is initialized. Pre-existing storage is validated through a read-only connection (including crash-left WAL, without checkpointing) before any schema/settings/command write; missing tables or settings fail closed across restart with original evidence unchanged.
- Invalid launcher/security/path configuration and corrupt startup databases fail closed. The capability's passive read may report unavailable storage; `ready()` still fails.
- A runtime database/schema error latches storage unavailable. Reads report `available: false`, `storageError: true`, and `records: null`. Counts and previous policy are references, not fresh proof. Capture/forwarding stop.
- Malformed runtime policy reports `configurationError: true`, disables capture/forwarding, and preserves the stored bytes. A reviewed explicit save can replace only the policy in an otherwise readable database at its actual revision.
- Storage preflight refusal before an effect begins reports `accepted: false, executed: false`; it does not manufacture a durable receipt. Failure after dispatch remains unknown. Existing unknown commands never become rejected because of a later storage fault.
- Receipt inspection reads the exact original command if possible; unreadable evidence remains unavailable/unknown. No fallback retries or automatic database repair/recreation occur.
- Faulted storage refuses quiescence acquisition/release; it cannot supply durable settlement proof or full-owner readiness.
- The client preserves per-tab private policy and original pending commands. Missing or degraded owner snapshots keep that editor visible with unavailable counts and disable owner effects. Reload never resubmits.

Qualification separates source tests, exact installed owner/node/static bytes, actual owned database/filesystem faults, and live/real-account acceptance. No live acceptance is implied.
