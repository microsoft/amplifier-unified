# Shared session storage

Voice protocol failures and closure keep a separate, local-only journal at
`<app data>/voice-diagnostics/events.jsonl`. It retains bounded categorical error
fields, provider/client event IDs, the provider's terminal reason, local closure
flags, and the last 32 send/ack type-and-ID observations. Human-readable provider
messages are omitted because they can echo user content; context, transcripts,
audio, images, headers and credentials are never selected. The directory is 0700
and files are 0600, rotated before 256 KB with one previous file. These records
are not added to conversation history, app state, or routed diagnostics. Storage
failure cannot interrupt call cleanup. The journal supports diagnosis; an error
code alone does not establish why a provider ended a call.

Unified and app-cli use the same native project session layout:

```
~/.amplifier/projects/<CLI project slug>/sessions/<id>/
  transcript.jsonl
  metadata.json
  context-intelligence/
    events.jsonl
    metadata.json
  unified/view.json
  live-jobs/
```

`AMPLIFIER_HOME` relocates the community root. The CI-supported
`AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH` can separately relocate capture files.
Project slugs follow the CLI exactly, including spaces, underscores and periods.

The community CI hook owns session event capture; Unified reads it rather than
serializing UI progress into another session event log. The host emits the same
prompt-completion event as app-cli at loop-live's final-turn boundary.
`transcript.jsonl` is the sole resume-message authority. Foundation's
`SessionHistoryStore` reads and atomically saves the existing native transcript
and metadata, including CLI-compatible `.backup` recovery. `SharedSessionStore`
retains cross-host acquire/check/release ownership only: new turns do not write
another checkpoint. A native transcript (including an empty one) always wins over
an old common checkpoint. Only a session without native transcript or backup can
explicitly fall back to a legacy checkpoint; old files are retained unchanged.
A busy owner rejects new work. Reads never mount tools/providers, replay work,
emit prompt events, repair source files, or create another conversation copy.

`unified/view.json` contains web presentation: displayed messages, activity cards,
voice presentation and draft/UI associations. It is not the model's runtime
context. CLI projects and top-level chats appear automatically in the workspace
sidebar. Worker histories stay in the same native files and remain accessible
through their parent conversation; independent forks are top-level chats.
Browsing them reads a page of their native transcript without starting a worker;
the displayed history refreshes after CLI changes. The next worker activation
reads the latest native transcript under the shared lock. Warm workers reuse
unchanged mounts; native transcript/metadata/backup changes force a remount.
An external transcript/backup write during an active turn is rejected before Unified saves,
preserving the older CLI compatibility guard.

Saved tool activity uses Foundation's event reader and exact transcript
associations. The native CI path is `context-intelligence/events.jsonl`; the
explicit CI relocation setting is honored and legacy root `events.jsonl` is not
preferred. The UI scans at most 5,000 physical lines or 16 MiB (including malformed and
unrelated records) and retains at most 2,000
compact observations per page load. Truncation, malformed records and backup
recovery are reported; ambiguous or auxiliary activity is not assigned to a chat
turn. Event bodies never become transcript messages. Raw prompts, model reasoning,
API payloads and tool results are not retained in the activity projection.
Missing model-call identity/cost is not estimated from tool activity. These
native activity cards and diagnostics are in-memory only and are excluded from
`unified/view.json`; existing web-owned activity, attachments, canvas references,
voice presentation and drafts keep their existing persistence.

Application-specific settings, command IDs, operation receipts and indexes remain in the app data
directory. Immutable canvas/result bodies live in `artifacts/<sha256>.json`, with
small SQLite references. Live MCP context is separate from static HTML, preventing
every polling update from duplicating an entire application. Reachability collection
keeps saved artifacts and retained results, including transitive references.
Completed operation results expire after 30 days, 200 results or 32 MB; small
execution receipts remain to prevent replay. Running operations are never expired.

### Incremental application state

App-state saves use `state_records` for individual session references, per-session
runtime records, top-level settings/catalog fields, and the committed revision.
The `state` row contains only the ordered session IDs and an empty base. A full
save reconciles all records and deletions; a scoped save visits its declared
records. Both compare serialized values and update only records that changed.
Even a caller without a known mutation scope therefore avoids rewriting the
whole app snapshot for a small progress update. Full saves still serialize the
fields they compare; this is not general mutation tracking.

Use `state_records.load()` for every committed-state read, including backup
inspection and artifact retention. Reading only `state.value` is incomplete.
The reader also accepts existing full snapshots with scoped overlays. The first
full save converts those to individual records in the same SQLite transaction
as the revision and private client state. Failed saves roll back together;
completed messages and operation receipts keep their existing commit points.
This does not change canonical event capture, transcripts, or model context.

### Large-chat projection factoring

The save path uses existing `_coldFields` resources in the **stored
projection only** for large messages and retained execution accounting. Live
objects remain mutable. Every save re-encodes resident content, so nested,
same-length edits cannot reuse a stale reference. Normalization and the existing
execution filtering still precede storage; receipts are not discarded.

This reduces repeated large-file writes when only progress changes, not all
serialization. Unchanged-save CPU can increase. Restart, private drafts, command
replay, nested edits, accounting and missing/corrupt resources require checks.
Derived message summaries must retire when their mutable body is handed out.

Do not infer physical cleanup from transaction rollback: a process may die after
writing a file but before committing its index. Resource creation now takes the
SQLite writer lock first; failed writes undo their index savepoint without
committing or discarding unrelated caller work. Maintenance marks the retained
graph once and inspects at most 128 artifact-directory entries per sweep, retaining
a scan cursor. It removes only unindexed hash files and abandoned atomic-writer
temporary files. Missing retained resources or uncertain presentation recovery
block cleanup. Background root admission now cooperatively checks a 20 ms deadline,
a 50,000-field work limit, and a 256 Ki-character limit per durable JSON record.
If either initial or final root proof exceeds those limits, the entire collection
is deferred: indexed candidates and unindexed files are retained, with a
`root-budget-exhausted` status and diagnostic counter. Live and durable root costs
are recorded separately. This protects responsiveness; it is not incremental GC.
Large mutable graphs can postpone reclamation until a later smaller proof or
explicit maintenance. JSON decoding, individual database operations and custom
mapping access are indivisible, so the deadline is cooperative, not a strict
wall-clock ceiling. Explicit/offline collection keeps its complete proof.
The expensive immutable-resource
walk runs without the application lock or a SQLite writer transaction. The
collector reacquires those boundaries and rechecks the complete root set and
all source-index bindings before pruning. Any change, missing body, uncertainty
or cancellation defers deletion; navigation need not wait for body traversal.
Confirmed deletion keeps its separate writer-excluded reconciliation protocol.

Deletion preview remains read-only. The confirmed path reconciles unindexed
files under writer exclusion before reporting file cleanup complete, yielding
between directory batches without surrendering its app/write locks. Staging
rechecks resource indexes so a re-adopted hash survives. Failure leaves the
confirmed deletion's existing cleanup-pending receipt intact. Backups defer
maintenance. Ordinary orphan cleanup is driven by subsequent successful saves,
not an independent idle timer. Crash tests cover process exit around resource
creation, not power-loss or filesystem corruption guarantees.

The confirmed-deletion gate is deliberately conservative: an active backup or
an uncertain retained resource graph anywhere in the library can defer **all**
physical purge, including the deleted chat's folders and transcripts. The chat
is tombstoned immediately and the receipt reports `cleanupPending`; the files
remain until the blocker is repaired and startup recovery completes. This is a
behavior change, not a promise that only unidentified orphan files are deferred.

Resource adoption refuses existing symlinks/non-regular files and size-mismatched
files rather than silently overwriting them. This is not a same-size integrity
check (cold reads still validate their hash). There is no automatic repair of an
indexed corrupt file in this change. An unindexed malformed file may be removed
by a later safe sweep, but an indexed conflict requires explicit recovery; a
failed save itself does not guarantee that sweep has run.

## Migration and backup

On first 0.8 startup, before accepting requests, Unified preserves the original
`app.sqlite3` in `backups/shared-storage-v1/app.sqlite3`, migrates missing native
transcripts, externalizes artifacts and shrinks the database. Existing shared
transcripts win. Old app-owned session files are left intact. Large installations
can take several minutes for this one-time offline migration.

Normal backups run file copying and compression in a background thread. They
include app configuration (including credentials), artifacts, operation/index
state, and shared root/worker sessions used by this app, including explicitly
relocated CI captures. Automatically discovered CLI entries are a rebuildable
index: listing them does not expand backups, read transcript/event bodies, or run
legacy canvas migration. Opening a chat loads its transcript and lazily scans
bounded CI activity; it does not add that external session to app backup scope. Existing app drafts,
canvas artifacts and indexed diagnostic records remain part of app storage and
backup. Backups do not collect unrelated CLI conversations or external Smart
Tool work directories. Backups are
private but unencrypted. Shared session files survive app conversation cleanup. Removing a chat or resetting
app conversations hides the currently listed chats from automatic discovery; it
does not delete their CLI history. Newly created CLI chats can still appear.
Resetting app settings leaves shared Amplifier configuration and workspace settings untouched.
Backups include the shared global settings, credentials, routing and saved bundles, plus
workspace settings for conversations used in Unified. See [shared configuration](SHARED-CONFIGURATION.md).

Do not run pre-0.8 against the migrated database. To roll back, stop Unified,
preserve the post-migration app directory and shared files, restore the retained
pre-migration database and old app session files, then run the older release.
That database represents the migration-time snapshot, not subsequent work. Do not
roll back or delete shared CLI files as part of an app-only rollback. The settings
rollback button changes ecosystem dependencies, not application storage versions.

### Shared session projection index

Each saved generation builds one transient index of session IDs, direct/native parent candidates, active chats and bounded notification previews. Browser and Terminal projections share those catalog facts while applying their own selection, drafts and notification visibility. Cross-project native aliases still use the canonical parent-matching rules. Every save invalidates this index, even without a revision increment; cached views must never outlive their saved state generation.


## Automatic cleanup of unused code

Settings → Storage → **Automatic cleanup** applies to every workspace on this
app installation. It is enabled by default, with a seven-day period beginning
when an owned code copy is first verified unused, and a 10 GiB size target.
Zero days removes the extra waiting period; a zero size target disables size
pressure. Turning automatic cleanup off leaves explicit, reviewed cleanup
available. Settings changes use a revision check, and a lost reply is recovered
with the original request ID rather than submitting another cleanup.

The policy asks the existing ecosystem-generation and purpose-installed test
runner and qualified dependency-layer owners to remove only copies they can still prove unused. Current and
rollback versions, live generation leases, configurations, observed process
references, local source edits/untracked files/unpublished commits, and uncertain
operations protect a copy. Owners repeat their checks at removal admission;
links are not followed as cleanup targets. Files changed during ecosystem
retirement remain in an owned quarantine for inspection. Interrupted cleanup
receipts remain visible and are not permission to repeat an uncertain effect.

**Review unused copies** is an explicit bounded inspection. The ordinary setting
read performs no scan and starts no worker. Each pass inspects at most four
ecosystem candidates, eight purpose-installed runner candidates and eight
qualified dependency layers, with time
limits and a retained cursor. Incomplete inspection is labeled; uninspected
copies stay put. File-content measurements are not physical disk-allocation
promises: hard links, shared runtimes and copy-on-write blocks can retain storage.
The size target currently applies to the fully measured eligible batch, not a
hard ceiling on all host storage. The age policy reaches other candidates in
later batches. Automatic checks run hourly while the app is open and after
existing updater maintenance triggers; update/activation work takes priority.
Successful runner selection only wakes this policy owner; it cannot bypass the
global off setting or retention grace. Explicit user uninstall and Skip actions
retain their separate existing authorization and exact-owner guards.

The policy never deletes chats, project folders, Git worktrees, saved test source
or result archives, the only copy of an artifact, dependency stores owned by other components, or
old application-installation generations. Current, previously selected,
rollback and unknown Incus engine generations also remain retained until their
shared references can be proved absent. Those categories remain inspectable by
the existing Storage tools; user-file deletion keeps its explicit review.

Receipts record the selected file-content total, completed removals and
unconfirmed attempts. They do not claim unknown effects succeeded. Browser
qualification uses the built client and production service with a small
synthetic installed graph: global settings, stale concurrent saves, exact retry
after a lost save/cleanup acknowledgment, real owner removal, saved evidence
preservation, and desktop/narrow draft protection. It makes no claim that a
production installation or a remote runner has been cleaned.

### Reusable isolated-test dependencies

The same global automatic cleanup policy also owns qualified source-free Python
dependency images. Their private records live under `test-dependency-layers` in
the app data directory. Only unused, exactly identified images are candidates;
active borrows, incomplete native builds and unconfirmed removals remain kept.
Each borrow remains held until its task image is confirmed removed. Fresh use
invalidates an earlier cleanup selection and restarts retention. Native image
references and the saved cleanup policy are rechecked before removal. Small
ownership, inventory and removal receipts remain after image cleanup. Task
source, output and test evidence are never dependency-cache cleanup targets.
Docker measurements exclude the exact immutable base image; shared blocks may
still remain after removal. Other dependency stores retain their existing
ownership and preservation rules.

### Saved test evidence

Settings → Storage → **Saved test evidence** reads recorded sizes only when the
user chooses **Review saved test sizes**. Each page covers at most 32 canonical
test allocations. Earlier pages replace the displayed page; totals apply only
to the displayed, fully accounted records. Newly saved or changed records can
appear after Refresh. This is not a snapshot of all host storage.

Original source, final workspace, and test results are separate roles. Complete
accounting requires a completed removal receipt bound to the allocation's
resource, root, environment and source hash, with a verified complete archive
selection and matching byte totals. A full-environment archive includes the
workspace and results without inventing separate sizes. Missing or inconsistent
records remain visible as unavailable, rather than zero. Remote receipts say
whether downloaded copies are recorded; a total counts one evidence set, not
every possible local and remote copy.

The `storage.cleanup.evidence` read uses bounded scalar projections from the
existing ledger, opened read-only, with a query deadline. It never reads archive
contents, recursively measures directories, loads chat history, starts a worker,
connects to another machine, changes retention, or deletes files. Recorded file
bytes are historical evidence, not current allocated disk space or an estimate
of reclaimable storage. Automatic cleanup continues to protect these files.
