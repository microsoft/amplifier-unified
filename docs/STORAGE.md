# Shared session storage

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

App settings, command IDs, operation receipts and indexes remain in the app data
directory. Immutable canvas/result bodies live in `artifacts/<sha256>.json`, with
small SQLite references. Live MCP context is separate from static HTML, preventing
every polling update from duplicating an entire application. Reachability collection
keeps saved artifacts and retained results, including transitive references.
Completed operation results expire after 30 days, 200 results or 32 MB; small
execution receipts remain to prevent replay. Running operations are never expired.

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
Resetting app settings creates workspace overrides only for workspaces used by
the app, never for unresolved or untouched indexed CLI projects.

Do not run pre-0.8 against the migrated database. To roll back, stop Unified,
preserve the post-migration app directory and shared files, restore the retained
pre-migration database and old app session files, then run the older release.
That database represents the migration-time snapshot, not subsequent work. Do not
roll back or delete shared CLI files as part of an app-only rollback. The settings
rollback button changes ecosystem dependencies, not application storage versions.
