# Internal job history

Native history is preserved independently of ordinary chat navigation. Producers may record these top-level fields in `metadata.json`:

- `session_visibility`: `internal` for an implementation job, or `chat` for an ordinary conversation.
- `session_purpose`: optional machine-readable label matching `[a-z][a-z0-9_.-]{0,79}`, for example `memory.suggestion`. It is diagnostic metadata, never prompt text or a credential.

Only an explicit `session_visibility: internal` declaration hides an otherwise independent root. A purpose label alone, an agent/recipe origin, noninteractive CLI mode, lack of a TTY, a generated title, or similar prompt wording is insufficient. A launcher may create a user-requested standalone conversation; that conversation stays a root. Recorded child ancestry remains worker history. Explicit independent fork lineage remains a new root conversation.

Unified derives `sessionKind: internal`, omits that row from ordinary chat lists, recent/pinned/header navigation, workspace chat counts and default history/recall searches, and makes its native history read-only. The row and canonical files remain intact. Global and workspace discovery receipts expose a separate `internalSessionCount`.

For diagnostic access, `app_control` history list/search/read accepts `include_internal: true`. This is separate from `include_children: true`. `recall.search` accepts `includeInternal: true`; `recall.read` still verifies the exact indexed source revision. Full app state and direct saved-history reading remain available. These visibility fields are presentation provenance, not an authorization boundary.

Producers must persist creation provenance and retain it across resume. They must not relabel existing unmarked user sessions from a new process's environment. A user-requested independent fork needs its own creation identity.

Unknown historical sessions remain visible. Unified does not inspect prompts or titles to retroactively classify them, delete or archive them, or rewrite native history. A safe historical classification requires trustworthy producer evidence or a separately reviewed explicit decision.

The consumer does not invent missing producer declarations. Existing unmarked internal jobs remain visible until their origin can be established independently. This consumer reads shared-format metadata directly and never imports, installs or invokes amplifier-app-cli.

Auxiliary memory inference must call the configured provider directly without creating a persisted conversation. A CLI-based producer workaround is not part of Unified's implementation or dependencies.

## Validation

Synthetic native-history fixtures cover explicit internal provenance, ordinary roots, worker ancestry, independent forks, legacy unmarked sessions and unchanged canonical files. Frontend navigation tests cover filtering; this is not a claim that existing unmarked production jobs have been reclassified. Task/worker coordination and full state remain diagnostic surfaces and can retain internal rows; ordinary chat counts and notification-message previews exclude internal jobs.
