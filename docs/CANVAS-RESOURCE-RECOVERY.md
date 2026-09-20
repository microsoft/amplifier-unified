# Recovering exact saved canvas resources

Unified 0.11.9 corrects cleanup reachability across shared state, all retained
clients, saved operation receipts and nested resource references. Use that
release or later before recovering files deleted by an earlier release.

Create a private full backup first with `maintenance.backup`. Inventory missing
references without running cleanup or replaying the original tool action. A
missing display is not evidence that the operation itself did not complete.

An authenticated host controller can submit this action to `POST /api/actions`:

```json
{
  "id": "<unique-command-id>",
  "action": "maintenance.restoreResource",
  "args": {
    "id": "<original-64-character-resource-hash>",
    "value": {"content": "<exact-original-content>"}
  }
}
```

`value` is the original JSON object, not necessarily a `content` object. For
example, an MCP view binding must be recovered as its exact original object.
The host serializes it as UTF-8 JSON with sorted keys, no insignificant spaces,
and unescaped Unicode. Its SHA-256 must equal the retained resource ID. The ID
must already be directly referenced by shared state, a retained client record
or a saved operation record. Recovery of only indirectly referenced resources
is outside this action's scope.

The action restores the missing resource index/file under its original hash.
It does not replace conflicting existing content, change artifact or session
identities, modify model configuration, or call any tool. An exact repeated
request is harmless. Check command completion and
`maintenance.resourceRecovery`; `restored: false` means matching content was
already available. Read the resource through `/api/state/detail` and verify it
remains available after a normal save at least 60 seconds later.

Use authoritative backups, exact retained results/receipts, or byte-identical
installed static resources as recovery evidence. Keep candidates private.
If the hash does not match, stop: do not manufacture dynamic state or substitute
a fresh tool result for the old view. Record any unrecoverable resource IDs and
reopen a read-only view separately only when appropriate. Never replay a
mutating tool merely to restore a display.
