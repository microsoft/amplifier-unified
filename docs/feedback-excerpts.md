# Reviewed conversation excerpts

Feedback includes no transcript automatically. **Attach transcript excerpt**
starts with one visible message and lets the user choose a loaded message range.
The shared `session.export` action freezes minimal Markdown with exact boundaries;
missing or ambiguous boundaries never fall back to the entire conversation.
Hidden rows/instructions, private reasoning, tool payloads and file contents are
excluded. Excerpt preparation omits the conversation title and attachment/artifact
identifiers, then applies bounded, best-effort redaction of recognized paths,
URLs, credentials, known environment secrets and email addresses. Counts and
remaining detection warnings accompany the preview; detection is not complete.

`feedback.excerpt.review` accepts that snapshot and optional edited text. It
checks the configured GitHub repository's actual public/private flag without
publishing, then returns exact text, filename, byte size, content hash, scope,
warnings and destination. Edits require a new reviewed snapshot and invalidate
previous acknowledgements. Inputs over 64 KB are refused without truncation.
Whole-conversation, large or sensitive exports require an additional review
acknowledgement; the browser offers a minimal range by default.

Outside the Full initial-publication path below, `feedback.excerpt.stage` requires disclosure acknowledgement (plus warnings where
applicable), stages those exact bytes locally, and uses a stable request ID.
Agents use these same actions with their own conversation and an explicit
connected browser target for staging. The user must have reviewed and approved
the text/visibility; an ordinary feedback request is not that approval.

The final feedback form lists the staged files and requires a separate checkbox
before `feedback.submit` includes reviewed excerpts. Consent includes the exact
`confirmedExcerpts: [{id, sha256}]` set, taken from staged attachment metadata;
adding, removing or changing an excerpt requires fresh confirmation. The host
rejects stale consent even when an agent changed the draft. Staging also requires
the reviewed conversation to remain selected and cannot overwrite newer feedback
text. The host verifies hashes and
rechecks repository identity and visibility before any upload. A visibility
change refuses all file writes and requires new review. Public upload is allowed
only for explicitly reviewed excerpt files or ordinary files with exact public
destination/file confirmation. Files are stored in an isolated root commit and linked
from the issue. Submitted content may remain in repository history and is not
retractable through the app. Unknown delivery outcomes are never reposted.

Validation uses an isolated real service/browser and intercepted GitHub calls.
This does not claim a live issue was posted or that redaction finds every type
of private information. Structured tool-output bundles and per-message omission
controls beyond editing the excerpt are outside this initial slice.

## Initial publication under Full access

A current root agent or a trusted host system with an explicit calling
conversation can prepare, stage and submit exact initial feedback under that
conversation's saved Full access. It does not need a browser, a selected chat,
a new human message or disclosure/warning acknowledgements. The existing
minimal export and excerpt preparation checks still apply. Selection of an
immutable same-conversation review retains agent/system attribution; the
preparation action does not imply human or independent review.

Initial Full file staging uses conversation ownership in the existing attachment
rows and returns the exact file ID and SHA256. Browser drafts remain private.
`attachmentIds` selects only that owner's files; unselected excerpts do not force
publication of the whole staging set. The host rechecks stored bytes and the
prepared excerpt's repository identity/visibility. Optional `confirmedExcerpts`
and `confirmedFiles` are exact content preconditions when supplied, not claims
that a human reviewed them. A mismatched set or hash still refuses the send.

For files or an explicit `uploadDestination`, the sender verifies and records the
actual fixed feedback repository before its first write. Plain-text feedback
without that precondition goes directly to issue creation, retaining the same
authority check before the write. Full accepts publication/retention risk without a separate
`feedback.destination` call; an explicitly supplied `uploadDestination` remains
an exact precondition. Accepted payloads and request fingerprints stay unchanged.
New Full receipts retain the actual origin, source conversation, access revision,
native/execution identity and Stop boundary, plus the admitted root generation
for an agent. Every upload/issue write checks that retained authority. Revocation
or Stop prevents later writes; once a write may have happened, its outcome stays
unknown and is never replayed. An accepted background send can outlive its turn.

This slice supports initial publication only. It does not qualify native child
publication, Work-bound effects, later-file additions, or general GitHub issue,
PR or release management. Existing Ask-mode/browser confirmation and ordinary
direct follow-ups keep their behavior. Root admission consumes the canonical
current-generation/Stop check; it is not a second generation authority service.
