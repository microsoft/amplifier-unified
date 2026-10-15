# Bounded agent discovery and result delivery

`app_control list_actions` returns `items`, `prefix`, `revision`, `offset`,
`nextOffset` and `total`. Follow `nextOffset` with the same prefix and revision.
Pages are bounded to 12,000 escaped JSON bytes; a changed catalog rejects stale
continuations. The browser's action catalog is unchanged.

An action too large for one page returns `schemaRead` instead of a partial schema.
Pass those arguments to `list_actions`, follow `nextSchemaOffset`, concatenate
`schemaText`, and parse the complete JSON action before dispatch. These calls read
metadata only; they never invoke the action. Exact names, descriptions and schemas
remain available without an output-limit increase or silent truncation.

Permissions, providers, configuration, workspace, module, source and location
dispatches return their exact domain receipts plus compact state metadata. They
do not repeat the unrelated app overview. Use `get_state` with a JSON Pointer for
other UI state. The domain's own success, pending, denied and unknown outcomes are
unchanged; a bridge response alone does not establish domain completion.

The separate `tool-exec` module owns its selected-text limit. Its output-delivery
failure is distinct from an authoritative child execution outcome. A completed
mutation is never replayed merely to print less text. Large arbitrary domain
results still require domain-owned paging or retained receipts; this change does
not claim a generic persistent result store for every tool.

Validation covers exact catalog reconstruction including oversized Unicode
schemas, stale catalog rejection, and the actual session-scoped permissions read
without repeated visible state. Tool-exec separately verifies a completed mutation
whose printed output exceeds 32 KiB and an unknown mutation under the same limit.
These are component checks, not deployed-worker or real-model acceptance.
