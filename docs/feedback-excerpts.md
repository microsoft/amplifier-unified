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

`feedback.excerpt.stage` requires disclosure acknowledgement (plus warnings where
applicable), stages those exact bytes locally, and uses a stable request ID.
Agents use these same actions with their own conversation and an explicit
connected browser target for staging. The user must have reviewed and approved
the text/visibility; an ordinary feedback request is not that approval.

The final feedback form lists the staged files and requires a separate checkbox
before `feedback.submit` includes reviewed excerpts. The host verifies hashes and
rechecks repository identity and visibility before any upload. A visibility
change refuses all file writes and requires new review. Public upload is allowed
only for explicitly reviewed excerpt files; ordinary attachments still require
a private repository. Files are stored in an isolated root commit and linked
from the issue. Submitted content may remain in repository history and is not
retractable through the app. Unknown delivery outcomes are never reposted.

Validation uses an isolated real service/browser and intercepted GitHub calls.
This does not claim a live issue was posted or that redaction finds every type
of private information. Structured tool-output bundles and per-message omission
controls beyond editing the excerpt are outside this initial slice.
