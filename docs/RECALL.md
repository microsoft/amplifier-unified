# Indexed recall and controlled memory

The host exposes one set of actions to settings and agents. `recall.refresh` builds a derived SQLite FTS5 index of registered user/assistant conversation text, including voice text available through canonical history, saved task objectives/corrections and linked output metadata. Output bodies are not indexed by the metadata adapter. It reads original files without rewriting them or starting a model. Refresh is explicit and incremental by source signature; `recall.status` and `recall.wait` expose coverage and failures. First indexing is a background operation. Child conversations are excluded unless requested.

`recall.search` performs lexical AND matching, ranked across the entire indexed scope rather than only a recent page. Task, workspace and all-registered scopes are available, with bounded pagination. Search hits carry conversation/message identity, source revision and a text digest. Results describe the index snapshot; they do not claim the source is still current. `recall.read` verifies source signatures and exact indexed revision before returning at most4000characters. Changed sources require refresh. Partial, interrupted and never-checked coverage are explicit. A partial empty result is not evidence of absence. This is lexical retrieval, not semantic search or generated summary. Task and output hits identify their exact record and revision. Task corrections and output unlinking invalidate old reads; refresh replaces the derived entries while preserving the original records.

`memory.list/read/create/update/delete` manage explicit notes in task, workspace or global scope. The default inspection includes notes available to the current work; an explicit all-scopes view also permits reviewing and deleting notes whose original chat is no longer registered. Corrections use revision comparison. Up to50prior versions remain inspectable; deletion removes the note and all those versions. Command receipts contain identity/revision/digests, not deleted note text. Original conversations, existing private backups and previously emitted tool results are preserved. Deletion is not a forensic erasure guarantee.

Agent writes require a source message with host-attributed user provenance (UI, user or voice). Agent-generated user bubbles, retrieved documents and unattributed historical messages cannot supply that provenance. The agent remains responsible for interpreting whether the user's request authorizes the particular note; provenance is not a semantic consent classifier. Normal new-input attribution depends on the durable-question integration. Automatic workspace consolidation and context use are separately opt-in, as described below. Notes and historical content are reference data, never new authority.

The portable `amplifier_recall` package owns derived indexing/storage only. The Unified adapter owns original source access, action authorization and scope. SQLite uses private file permissions, write-ahead logging and secure-delete settings; recovery makes a consistent SQLite backup. The UI supports index progress, source reads, explicit note creation/correction/deletion and retained revision inspection without changing selected conversation or draft.

Validation includes5000indexed conversations, stale external source edits, passive reads, scope isolation, revision conflicts, retry receipts, orphaned-note management, forged agent provenance, restart persistence and read-only originals. The actual service/browser scenario covers search/source verification, correction, deletion/reload, desktop/mobile layout and draft preservation. Model quality and semantic recall are not claimed by these fixtures.

## Opt-in workspace personalization

`memory.status/configure` expose two independent, default-off workspace choices:
contribute user-confirmed references and use relevant saved references automatically.
Enabling contribution does not enable use. A conversation exclusion prevents both
its contribution and its participation in automatic context. Excluding a source
also withdraws its derived notes from automatic use in other conversations; saved
notes remain inspectable and deletable. Explicit notes retain task/workspace/global
scope; automatic extraction writes only workspace notes in the same Recall store.

Contribution runs from the existing idle lifecycle, or from the explicit bounded
`memory.consolidate` action. `sourceSessionId` selects one registered source in the
calling workspace; omission examines that workspace. Root user conversations must
be idle/completed, free of active workers, and carry host-attributed accepted user
messages. Child/agent/scheduled/question inputs, assistant/tool content and imported
history without attribution do not qualify. No extra OS timer, CLI session, task
controller, transcript copy, or background task continuation is created.

The portable [memory library](https://github.com/microsoft/amplifier-bundle-memory)
owns bounded extraction, quote verification and lexical relevance through its
explicit host-scoped API. Its personal suggestion policy and CLI are unchanged.
The workspace policy captures lasting preferences, settled project decisions and
human-confirmed successful approaches, with original source identities, hashes,
exact human quotations and explicitly model-derived wording. This is classification,
not proof that an outcome occurred. Quotation checks establish attributable evidence,
not semantic consent or factual correctness. Agents must interpret the actual user
request before enabling contribution or changing settings; any attributable message
is not automatically authorization for those changes.

Each eligible changed source gets at most one attempt. The default is three model
attempts per UTC day per workspace, configurable from one to ten. A request includes
at most 16,000 source characters, 12,000 characters of complete known references,
and 4,096 output tokens; the worker call times out at 60 seconds. It uses the source
conversation's configured provider/model and existing model-call admission and
telemetry. Provider charges can apply. Provider-internal retries are not a distinct
host attempt or a hard USD ceiling. Attempts and skipped/cap reasons are visible;
failed, interrupted and unconfirmed attempts are not automatically replayed. Sources
or settings changed during a call invalidate its result before saving.
Foreground input cancels an auxiliary consolidation without waiting for its provider;
the interrupted attempt remains visible and is not replayed. Cancellation cannot
retract a provider request that was already accepted. Known references supplied to
later consolidation calls obey the same source withdrawal checks as automatic use.

A supported newer human correction can identify earlier notes it supersedes. The
host checks chronology and current revisions, saves the replacement and marks the
older notes superseded in one transaction. Superseded notes remain inspectable and
are excluded from automatic context. Manual correction time takes precedence over
an older extraction's source time. Classification can still miss implicit conflicts;
inspect or correct saved notes when the model's interpretation is wrong.

Correction/deletion suppresses further extraction from that unchanged source
message, independent of the model's quotation boundaries or paraphrase. This
conservative rule can also suppress another potential note from the same original
message; a new user statement or explicit memory creation can supply it. The store
retains only a suppression digest after deletion, not deleted note/version text.

When use is enabled, the existing provider delivery chain selects at most five
notes sharing lexical terms with the latest attributable user input, with a 6,000
character context budget. An irrelevant query receives no notes. Scope, source
availability, exclusions and exact memory revisions are revalidated before provider
transport; revocation or correction after request fitting removes stale context.
The injected block is ephemeral and does not rewrite canonical history. Current
saved wording is supplied with provenance and source IDs/hashes; the original raw
quotation stays behind `memory.source`, so an old quotation cannot compete with a
corrected preference. Standing preferences can guide the answer, subordinate to the
current user request. Memory never grants tool permission or starts new work.

`memory.context` previews the selection without a model call. `memory.status` reports
the latest request selection and shared query terms; transcript notices announce
new saved references and changed context selections. Context receipt means supplied
to a provider request, not proof that the model followed it. Read/correct/delete use
the same existing actions in Settings and agents. `memory.source` verifies original
human evidence; changed, removed or withdrawn sources fail visibly. Turning either
control off preserves inspectable notes and existing historical output.
