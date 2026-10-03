# Catalog and working set — v1 (RATIFIED 2026-10-02)

Clause prefix: **WS**. Parent: [family vision](../docs/architecture/VISION.md).
Governs discovery, residency and durable-history boundaries.

## Who builds against this

Catalog, host, runtime, native-store and client maintainers; operators with large histories.

## What it is

Historical existence, visible catalog membership and runtime residency are separate facts.
Durable history supports on-demand access without loading all stored work into memory.

```text
durable native records -> rebuildable disk index -> filtered catalog page
selected conversation -> requested history window + requested child summaries
accepted execution -> runtime admission -> working -> safely evictable when idle
```

[State and storage](../docs/architecture/state-and-storage.md) provides the design.

## The promises

1. **WS1 — Preserve the durable record.** Native transcripts, metadata and retained
   event logs survive catalog exclusion, index rebuild and cache eviction. Derived
   indexes never become an alternative resume authority or replay past execution.
   Product visibility remains host-owned retained state. If an index cannot apply
   visibility before discovery, listing stays unavailable through complete bounded
   reconstruction; index loss cannot temporarily expose hidden conversations.
   Broken: removing a missing directory deletes its history. Affected: owners of historical work.

2. **WS2 — Filter before loading pages.** Default project discovery includes confirmed
   existing workspace directories; default conversation discovery includes their root
   sessions. Unknown or unreachable paths have an explicit state, not an inferred deletion.
   Broken: paging loads all 25,000 sessions and filters in the browser. Affected: ordinary browsing.

3. **WS3 — Load detail by demand.** History windows, child conversations, large tool
   bodies and artifacts are fetched for the selected resource. Reading history does
   not initialize an agent. AHP retention and window semantics remain authoritative.
   Broken: opening the sidebar starts dormant workers. Affected: people and active executions.

4. **WS4 — Bound resident work.** Runtime, projection, replay and subscriber caches
   have explicit count/byte/idle or admission budgets. Active execution and unresolved
   decisions cannot be evicted as idle; overload has a visible queued/refused outcome.
   Bulk historical payload verification checks complete bounded metadata totals
   before body I/O. Producers and consumers negotiate compatible count, identifier
   and byte bounds; excess selection is refused or explicitly reviewed with omissions.
   A source that is not an admitted regular file cannot block the owner on opening.
   Broken: one slow client grows memory without limit or eviction cancels work. Affected: all users.

5. **WS5 — Charge updates to dependencies.** A resource update touches its record,
   actual dependents and subscribers. No request performs historical global scan,
   projection, hash or checkpoint to process a draft, scalar read or unrelated chat delta.
   Broken: a small update scales with unrelated stored histories. Affected: multi-session hosts.

6. **WS6 — Discover without rereading history.** New native writers publish incremental
   discovery hints after durable changes. Bounded reconciliation recovers missed hints;
   initial migration can scan in the background with visible progress and throttling.
   Broken: every refresh parses every events file or missing a watcher loses a session forever. Affected: legacy users.

## Not in v1

Automatic deletion or archival compression; promote only under a separate retention
decision. Cross-machine path equivalence is not inferred from a matching path string.

## How the kit checks it

- WS1: checksum native records before/after exclusion, index loss and recovery.
- WS1: interrupt visibility reconstruction, restart, and verify listing remains
  fenced until all retained markers and the final checkpoint are verified.
- WS2/WS3: trace filesystem reads and worker starts on the large-history fixture.
- WS4: disconnect viewers, stress slow subscribers and exhaust runtime admission.
- WS4: reject aggregate-overbudget manifests before body reads and special-file
  sources without blocking; verify mismatched peer limits never silently truncate.
- WS5: compare update work as stored sessions grow with the active set fixed.
- WS6: duplicate/drop hints, interrupt rebuild and restart the legacy writer.

Numeric candidate budgets and fixture composition live in the delivery plan, not as passes here.

## Open questions

What residency defaults meet measured latency and memory budgets on supported hosts?
Which directory freshness policy handles offline mounts without noisy catalog churn?

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-10-03 | Clarify WS1 reconstruction visibility and WS4 pre-I/O payload bounds. | Catalog projection review and reproduced payload aggregate/FIFO defects; host composition remains separately qualified. |
| 2026-10-02 | Ratified for implementation; exclude amplifier-agent as the native backend. | User approval of the plan and direction; implementation evidence remains separate. |
| 2026-10-02 | Initial WS1–6. | [Scale requirements and measurements](../docs/architecture/evidence.md). |
