# Saved MCP view diagnostics and recovery

This records the October16 implementation and its original limits. The user
approved the bounded #808 review/update path on October17; see
[the subsequent qualification](mcp-contract-review-20261017.md). The authentic
saved Studious #807 qualification remains open.

Qualification date: October 16, 2026. Baseline:
`e4139b8fff97252debdb7d4fb72a9d1ca396f894`, version 0.20.169. Canonical owners are
the host's MCP binding/agent Canvas scope and the web client's official AppBridge.
The governing promises are preserved saved work, honest connection state,
client-local selection and explicit recovery without replay.

## Scope and observed results

| Report | Confirmed finding | Current result / desired behavior |
| --- | --- | --- |
| #809 passive status with no matching browser | Selection considered retained client records, producing an ambiguous or ready live-view result even when no matching client was connected. | Status now counts connected callers in the same conversation/workspace, reports absence/ambiguity explicitly, and retains independent saved source/binding diagnostics. It never selects, attaches, connects or calls a tool. |
| #807 saved view recovery | A successfully reconnected host binding did not notify the already initialized app. | A non-ready → verified-ready transition sends the official SDK tools/resources list-change notifications once. A cooperative SDK app completes a safe read while its input and original launch receipts remain unchanged. Repeated ready observations add no notification. |
| #807 historical Studious sequence | The original sequence was not captured, and the actual saved Studious build was not exercised. | Deliberate historical selection stays read-only; Latest returns to the verified binding in the synthetic browser fixture. This does not close the original report or prove that existing Studious documents consume recovery notifications. |
| #808 changed contract | The hash covers complete launcher/granted-tool definitions, including descriptions. Added ungranted tools alone do not change it; server instructions are not part of it. | Existing guards remain unchanged. Description changes and removed granted actions are explicitly tested as denied. A compatibility/review policy is a decision, not an implied defect fix. |

The host advertises the official MCP Apps SDK `serverTools.listChanged` and
`serverResources.listChanged` capabilities. These are cache invalidations for
the existing saved allowlist, not grants of newly discovered actions. The fixture
opts into the standard tool-list notification and chooses a read-only refresh.
No initialization input/result or previously issued mutation is replayed.

## Qualification

All execution used the owned spark-2 DTU `dtu-unified-codex-dev-20261014`, a
separate source copy and synthetic state. The persistent development service and
spark-1 were not changed. The built client identifies itself as
`1db3d3e7eb6434c9` / `0.20.169`.

- Focused Python recovery, Smart Canvas and agent scope: **177 passed, 0 skipped**
  in 71.09 seconds (`python.log`).
- AppBridge lifecycle: **9 passed, 0 skipped** (`frontend.log`), including
  repeated-ready suppression and denied/history states.
- `mcp-recovery-browser.mjs`: **passed** (`browser.log`, `recovery.png`). Actual
  SDK app/server, real HTTP host restart, saved source/identity/launch/grant
  checks, input preservation, one safe read per recovered connection, no mutation
  replay, historical version/Latest and changed-contract refusal.
- Negative controls reject the parent implementation: its passive diagnostic
  reports ready for a disconnected retained client (`python-negative.log`), and
  its recovery emits no SDK notification (`frontend-negative.log`).
- The standalone recovery script previously lacked an owner in the extended
  browser plan. It now runs there once, with its screenshot retained alongside
  the runner's per-scenario log. It adds no publication gate or duplicated lane.
  Browser runner/ownership validation: **18 passed** in 3.70 seconds (`runner.log`).

This is deterministic local acceptance, without a live provider, real account or
native macOS/Windows UI. ARM Chromium required the existing process-local launcher
with `--no-zygote --single-process --disable-gpu`; browser process isolation was
reduced for this test. An initial normal Chromium launch crashed before product
acceptance (`browser-initial-startup-failed.log`). A subsequent test attempted the
version selector before expanding Canvas options; the corrected test opens the
actual control (`browser-controls-closed.log`). Those failed attempts remain
evidence, not passes. The frozen ordinary-UX inventory counts are unchanged.

## #808 recommendation requiring a decision

Keep configuration/account identity, sandbox/device permissions, app resource
identity and granted action contracts as separate review dimensions. Do not infer
compatibility from a tool's name or a read-only annotation alone.

A useful next proposal is a bounded contract review with:

1. Added/removed action names and the existing grants clearly distinguished.
   Added actions remain ungranted until explicitly approved.
2. For existing actions, separate input-schema, annotations/visibility/meta and
   descriptive-text differences. Describe configuration/account and permission
   changes separately from tool changes.
3. A versioned, bounded saved definition snapshot for future bindings. Existing
   bindings retain only a fingerprint, so their exact former definitions cannot
   be reconstructed reliably. They must say that the prior definitions are
   unavailable instead of inventing a diff.
4. If approved, an explicit review of the current fingerprint and revalidation at
   commit time, preserving document identity/history/source and local draft state.
   Never reopen or replay the launcher or a previous action to perform migration.

The open decisions are whether description-only edits count as compatible and
whether an explicitly reviewed changed contract may update the current saved
document's binding. Those choices require an updated contract and acceptance
criteria before implementation. No such policy, new grant or migration is
introduced by this fix.
