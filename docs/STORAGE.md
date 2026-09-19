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
`transcript.jsonl` is the native conversation projection. Foundation's
`SharedSessionStore` remains the authority for execution ownership and its atomic
full-context checkpoint, matching current CLI behavior. Opening a session in a
second host does not create another runtime history. A busy owner rejects new work.

`unified/view.json` contains web presentation: displayed messages, activity cards,
voice presentation and draft/UI associations. It is not the model's runtime
context. Reopen shared history after CLI use to refresh the displayed projection;
the next worker activation always reads Foundation's latest shared checkpoint.

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
state, and the shared root/worker sessions known to this app, including explicitly relocated CI captures. They do not collect
unrelated CLI conversations or external Smart Tool work directories. Backups are
private but unencrypted. Shared session files survive app conversation cleanup.

Do not run pre-0.8 against the migrated database. To roll back, stop Unified,
preserve the post-migration app directory and shared files, restore the retained
pre-migration database and old app session files, then run the older release.
That database represents the migration-time snapshot, not subsequent work. Do not
roll back or delete shared CLI files as part of an app-only rollback. The settings
rollback button changes ecosystem dependencies, not application storage versions.
