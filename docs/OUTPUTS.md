# Output relationships and local review

`outputs.attach/list/read/unlink/relink` share the same host actions in UI and agent tools. Supported kinds are file, dataset, saved canvas, pull request and external document; `outputs.write` saves reusable writing, and `outputs.review` snapshots a local Git diff. Records retain originating conversation and optional original message, content hash, exact immutable resource, parent version and evidence relationships. Only explicit message identity establishes turn origin; it is never guessed. Attachment does not publish or invoke a model.

Files are copied into the existing private content-addressed resource store (up to8MB), with regular-file checks, traversal/symlink boundaries and change detection. Original files remain independent. Saved canvas bodies are copied from their existing exact resources. External HTTP(S) links are references only: contents are not fetched; absent version is unknown and a supplied version is labelled user-supplied evidence. A remote PR URL does not prove its HEAD or current state.

Read actions return at most4000content characters and5review notes by default (maximum10), with independent pagination; binary contents have an authenticated exact-byte download. Downloads use attachment disposition and restrictive CSP; saved HTML never becomes an executable preview. All output records are resource-retention roots, including unlinked records. Unlink changes only relationship state, and restore-link uses revision comparison. Existing backup captures the primary database and resources. Removing a conversation does not delete underlying outputs; registered conversation scope is required to read through the action surface.

Writing blocks in assistant Markdown support the documented `:::writing{variant="document" id="12345"}` syntax, email metadata, safe Markdown, copy and local editing. Saving makes an immutable writing output; editing never changes the original message or sends an email. A subsequent saved version records its parent. Raw HTML and unsafe links retain the normal Markdown protections. Truncated/unknown blocks and fenced examples stay ordinary text. Registered forms support equivalent shared `outputs.write` behavior without special Markdown.

Git review uses bounded read-only argument arrays with external diff/text conversion disabled. Modes are unstaged tracked changes, staged changes and exact base-commit-to-HEAD. The snapshot records resolved commits and its digest; worktree/index output is read twice to reject observed changes. Untracked files and binary contents are excluded. It is not a transactional repository snapshot or a remote PR reviewer. `outputs.comment` stores local notes with optional exact displayed hunk path/side/line. It validates the saved snapshot and never posts comments externally or applies changes. Each snapshot accepts up to200notes.

Forks copy only linked outputs anchored to retained message IDs, reuse immutable resource bytes and record original output identity. Unanchored/future outputs remain with the original conversation. Comments and evidence are local; no tools or downloads are replayed. Ten thousand relationships bound the registry; source and snapshot retention are deliberate, not deletion of underlying work.

`amplifier_outputs` owns portable relationship storage and read-only Git evidence. Unified owns authorization, workspace/source access, private resources and presentation. No provider-specific artifact library is required.

## Retained GIF and MP4 lifetime

`outputs.attachMedia` still requires current canonical assistant identity, exact
workspace bytes/hash, structural media validation and fresh write authority after
worker awaits. Resident and detached saved-alias `displayOnly` identities are
rejected, including marker changes observed after qualification awaits.
A saved output's historical origin is provenance, not a continuing
live-transcript requirement: later append, rewrite, truncation, display eviction
or source-file removal does not revoke the saved version.

Retained byte reads require the owning root conversation, exact registry
ID/version/hash/provenance tuple and current authenticated client selection.
Misses validate bytes, structure and immutable metadata off-thread; bounded warm
cache hits still check the freshly detached resource index and backing file's
device/inode/size/modification/change signature. HEAD checks indexed metadata
without opening or decoding media. Registry/resource replacement or revoked
client/conversation scope fails rather than borrowing another source.

`outputs.list` and retained-media `outputs.read` resolve current scalar ownership
through the existing history directory, including project/alias ambiguity,
suppression, deletion and live owner changes. They do not admit a dormant owner
or load its saved presentation/control body. The latter returns bounded metadata,
real historical origin and paged comments without selecting a browser. Agent reads remain
caller-conversation owned; an unbound read reports `unbound`, not playback evidence
from another browser. Reports and `canvas.openGif` remain authenticated UI-only.
Discovery uses the saved scalar media revision; an older summary without a
recoverable keyed scalar reports revision unavailable rather than inventing zero.
Exact retained metadata and byte access do not require that discovery revision.
The existing Outputs detail uses the existing media viewer/download controls;
only GIF has the existing Open in Canvas action.

Unlink suppresses automatic discovery but retains exact authorized library/byte
access. Relink changes only the relationship revision, not bytes, IDs or playback.
Forks still require linked outputs and surviving origins at the existing cutoff.
Display-only rows cannot establish new fork media ownership; invalid competing
claims remain refusals rather than being silently dropped to admit a fallback.
Existing GIF wrappers are remapped only to exact surviving output clones;
malformed or cutoff wrappers are skipped before copying. Signed/reviewed transfer
routes validate output ownership, bytes/structure and exact GIF references without
requiring the historical origin to remain in the transcript. Generic output and
total transfer limits, signed receipts and existing resource GC remain unchanged.
Generic GIF Canvas output snapshots are omitted from forks rather than carrying
source-session bindings; transfers validate both their encoded snapshot and exact
Canvas source reference.

Validation:30 backend checks across output, resource-retention, server and canvas behavior;210 frontend checks including safe Markdown writing blocks. Actual service/Chromium validates edited writing vs original message, exact file download after source edits, real local Git review/line notes, unlink/restore/reload, mobile bounds, selected chat/draft preservation and zero model calls. This does not establish rendered document fidelity or remote provider/account acceptance.

## Explicit visual inspection

Attach a rendered PNG as a file, then call `outputs.image` directly through `app_control` with that output ID and exact SHA-256. Do not nest this read inside `tool_exec`: its outer result does not retain the required direct app-control receipt. UI and agent use the same action. UI opens an authenticated preview without sending a message or changing the draft. An agent call keeps the exact tool receipt and supplies typed PNG pixels to the next supported provider request, through the existing surface-delivery boundary. Only the latest explicitly requested image is carried, within the current input; compaction, a changed input, or a missing exact tool result requires another explicit read. Cached request-budget pixels are revalidated before transport. A text receipt or successful canvas render is not visual QA.

Image selection does not survive a new user input or resumed runtime. Prior successful inspection receipts remain historical evidence; they do not select images for the new input. If the user asks to compare the saved images again, make a fresh direct `outputs.images` call with both exact IDs and hashes, then judge visibility from the current request's pixels and `comparisonReady` status. Earlier missing-pixel reports do not establish current availability. Unrelated user requests do not renew image inspection.

Images must be complete, noninterlaced8-bit RGB/RGBA PNGs, at most8MB and4096pixels per side. CRCs, bounded decompression and scanlines are validated before delivery. Convert unsupported image formats in the approved computation environment deliberately; this action does not reinterpret safety text as pixels. Original files are not reread: the exact immutable snapshot is used even if the source later changes. No-vision providers and missing/corrupt resources receive a clear no-pixels notice. This provides generated-page inspection without pretending to capture a native screen.


Managed Git review also disables every configured clean, smudge and process filter
for each invocation, without changing repository/global settings. Evidence compares
raw Git and worktree bytes; it does not download or materialize LFS objects. A local
materialized LFS file can therefore appear different from its saved pointer. Quoted
UTF-8 paths (including accented names, tabs and quotes) retain exact review anchors;
lines resembling file headers inside a hunk remain ordinary content.

Image support uses the selected provider model catalog when available, with a bounded cached read; provider-level tags are a fallback when no model catalog exists. An unavailable/failed lookup remains unconfirmed and sends no pixels. No model is substituted.
