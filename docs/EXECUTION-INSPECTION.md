# Public execution inspection

Assistant replies in the conversation load their complete saved text automatically
when they approach the viewport. The browser still starts with bounded history
and 4 KB message previews; off-screen messages do not all fetch at once. Full text
uses the existing authenticated, digest-checked, 16 KB detail pages. Navigating away
cancels the fetch. A failed fetch offers a visible retry and keeps the preview.
User messages and large worker summaries retain their explicit expansion controls.

Execution rows show a compact public operation summary, status and elapsed time.
Expanding a tool exposes independently expandable input, result and error fields,
copy controls, durable call identifiers and explicit web-result links. Each field
has a 512-character snapshot preview and loads independently through the same
detail API. These fields are available through the shared public state/detail
interface; inspection never executes the original tool again.

Tool-boundary inputs/results are retained only after known credential fields,
credential environment values, bearer/credential-bearing URL patterns, and private
reasoning blocks are filtered. Values are capped at 64,000 characters per field,
with explicit truncation markers, plus depth/item limits. This is best-effort
redaction, not proof that arbitrary tool text contains no sensitive information.
Provider requests, responses and hidden reasoning are never captured by this
inspection path. Existing diagnostics metadata export still excludes tool content.
Older records do not gain payloads retroactively and say when details were not
retained.

Turn and step timers use durable host start/end timestamps. While running, the
browser advances from the host HTTP response clock (second precision); final
elapsed time comes from the recorded timestamps. Queued/retrying phases remain
labelled as such; elapsed is total time since the recorded start, not an invented
queue/active-time breakdown. Unknown terminal end times are not animated forever.
Root completion/stop/error settles dangling foreground calls without ending
independently running workers or background session naming. Naming carries an
explicit background lifecycle from its scheduler through provider instrumentation
and remains pending on its original turn until its own completion/error/cancellation.
If its worker process exits without a final provider event, the host settles only
the background call IDs observed in that process; ordinary turn status cannot
invent a background completion.

Usage is counted once per model-call identity. Missing metrics on running calls
are pending; missing metrics after completion are unavailable, with an explanation
that the provider did not report them. Known reported, estimated and partial costs
remain distinct. A missing cost is never displayed as a measured zero. Empty newly
started turns appear before their first tool/provider event. Conversation-wide
rollups, configurable dashboards and provider pricing estimates are separate work.
