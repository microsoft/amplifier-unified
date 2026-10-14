# Release 0.20.135 qualification

This focused performance correction builds on merged main `2631f8b7e` and uses
synthetic state in the owned `dtu-unified-public-readiness-20261008`, source
`/opt/unified-work-activity`. Published-host updates and Mac navigation acceptance
are separately authorized by the user.

## Observed costs and changes

On live 0.20.134, repeat visits to CPU usage, AHP/ACP and multi-worker chats showed
messages and work rows in 226, 275 and 261 ms. A later CPU switch during profiling
took 993 ms. Cold AHP, workspace-list interim and multi-worker visits took 4,112,
949 and 1,815 ms. These are individual observations, not controlled percentiles.
The previous warmup correction reduced its measured configuration snapshot to
0.02–0.03 ms, but did not remove every background cost.

A bounded 20-second, 100-Hz GIL profile recorded 1,099 samples without read errors.
441 included the catalog loop and 132 included the two execution snapshots in
event-view refresh; sample categories overlap. An earlier profile independently
identified repeated resource-root and immutable-body traversal during cleanup.

This release addresses those paths:

- Empty metadata deltas skip redundant catalog merges/publications after checking
  local overlays and public facts. Selected transcript freshness stays independent.
- Cold discovery remains cooperative (64 steps/25 ms between filesystem calls)
  and runs at a paced one-second cadence. Targeted backlog is prioritized. Healthy
  notifications allow 15 minutes between completed cold passes; unavailable watches
  retain a 60-second recovery fallback. An individual filesystem call can exceed
  the cooperative budget. Cacheless startup is also incremental.
- Cleanup retains a bounded cache of validated outgoing resource references,
  never file bodies. Exact source identity and file signatures gate reuse; missing,
  changed, corrupt or substituted resources prevent uncertain deletion. Fresh live
  and committed root checks remain. Container-only traversal avoids scalar walks.
- Event projection snapshots retain the historical join/lifecycle/accounting
  inputs needed by the reader, including completed rows and full legacy fallback
  bodies. Reconstructed canonical detail payloads and derived summaries are omitted.
  Detached snapshots and the concurrent-update rejection check remain intact.

## Local qualification

- Catalog/native-history: 170 tests passed in 58.05 seconds.
- Resource retention, orphan handling, shared storage and diagnostics: 54 tests
  passed in 19.55 seconds.
- Event view, checkpoint/retention and accounting: 115 tests passed in 2.05 seconds.
- Independent source reviews found no blockers in the three changes.
- Frontend: 686 tests passed. Two production builds produced identical file hashes.
- Release identity, notes, qualification and post-release contracts: 116 tests passed.
- The feedback lifecycle browser scenario passed with all original usage/export
  assertions after its fixture published receipts through the runtime-event owner.
  Directly mutating the execution tree had bypassed persistence/invalidation.

Synthetic cleanup comparison: 27,000 cold metadata rows and six active chats with
2,000 messages each produced the same 27,000 references in 207.65 ms before versus
139.55 ms after. A 128-file, 32 MiB unchanged resource graph took 37.04 ms to validate
initially versus 1.18 ms for warm metadata checks, with zero warm body bytes read.
These are synthetic timings, not predictions for every host.

## Limits

Live 0.20.135 results will be measured after publication. Compact workspace inputs
and fresh resource roots still require enumeration; this is not a claim that all
maintenance scales exclusively with active chats. Cold first reads still have
real work to do. Exact provider request/response details remain available on demand.

No final full Python suite was run locally for these focused corrections. Automatic
post-release checks remain separate from publication. The 0.20.134 installed-package
check passed; its browser run passed the repaired new-chat recovery case and then
failed in the feedback usage-total scenario, corrected locally above. Later skipped
browser checks are not claimed successful.

## Selected maintenance publication

PR #647 merged into main during this qualification with unresolved passive-history
hydration and optional-hashing readiness concerns. The qualified maintenance
candidate intentionally precedes that integration. Preserve this candidate's
ancestry when integrating the performance corrections into main, then use the
publisher's supported `release_revision` input to publish this exact candidate.
Cancel only this integration's automatically triggered publication before it can
publish the broader main snapshot. The selected candidate excludes #647; this is
not qualification or release approval for that integration or its successor #650.
