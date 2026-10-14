# Release 0.20.137 qualification

This maintenance candidate extends selected release 0.20.136 (`ecf6de5472001c21966e473a6b4c98e166bea854`). Separate integration features on main remain outside this selected candidate. Tests use the owned `dtu-unified-public-readiness-20261008`, source `/opt/unified-work-activity`.

## Live evidence from 0.20.136

The authorized spark-1 update and Mac reload verified that exact revision and `app-TQ9FhFvX.js`. Individual first visits displayed messages and work rows in 2,050 ms for CPU usage, 2,256 ms for AHP/ACP, 1,393 ms for multi-worker workspaces and 1,047 ms for sidebar pinning from the workspace list. Repeat CPU/AHP visits took 276/211 ms; workspace interim replies took 165 ms. CPU after six chats took 211 ms. UX feedback first loaded in 3,201 ms with work rows and an existing failure alert. These are individual observations, not controlled percentiles. No history-read errors occurred.

Seven background collection root checks averaged about 11.3 ms, maximum 19 ms; incomplete proofs safely deferred collection. This safeguards responsiveness but can postpone reclamation on large graphs. The startup catalog still decoded 27,177 persisted navigation metadata rows once (5.17 seconds); it did not read 27,177 transcripts. Fully lazy catalog query/materialization remains separate architectural work.

New stage timings identified expensive oversized-index admission bookkeeping and repeated same-source history reads. A frozen overlapping selection/catalog test reproduced two identical transcript reads on the prior implementation. The fix rechecks revision after acquiring the existing load lock; appends and replacements remain observable.

Oversized event-index sizing now stops at a proven rejection and retains at most 256 metadata-only rejection receipts. Receipts never admit an index or replace canonical validation. Changed source/association signatures and increased budgets trigger measurement. Frozen 18,758-node checkpoint-restored graph: median first oversize sizing 503.74 to 341.85 ms; unchanged restored rejection 513.55 to 0.021 ms; changed source 507.12 to 348.52 ms. Three samples each; checkpoint decode/save excluded. Exact raw fields, accounting and source freshness remain covered.

## Qualification

- 103 history/loading/retry tests passed; baseline duplicate-read regression failed, while actual append/replacement cases continued to pass.
- 148 event/index-retention/checkpoint/history-demand tests passed, including bounded source-change diagnostics and rejection receipt invalidation.
- Independent backend reviews passed.
- 0.20.136 hosted browser passed composer and surface-context checks, then exposed the pre-existing missing conversation readiness panel. The ownership contract places this in Chat Advanced > Computer access. The existing read-only component is restored there; global-only settings retain their scope. Browser assertions remain intact.

Final selected-source qualification:

- 315 affected backend tests passed in 53.54 seconds; 116 release policy/automation tests passed in 6.79 seconds. All 857 backend/test/release files matched the DTU snapshot before and after these checks.
- The subsequent narrow browser-demand fix restores runtime result receipts to the four conversation settings pages, scoped to the requesting client. Ordinary chat still omits these receipts. Independent review and 66 browser-demand/detail/client-isolation tests passed in 19.34 seconds on the final source; only the demand module and its test changed from the earlier backend snapshot.
- 690 frontend tests passed. The complete desktop-readiness and native-feature browser journey passed with the original assertions retained, including explicit checks, scope/consent behavior, independent context, narrow layout, and computer tool shortcuts reaching their actual destination. This synthetic browser check does not establish physical-device OS permission behavior.
- Two production builds produced identical SHA-256 hashes for all 311 packaged static files. Selected build: `0.20.137 / 432c12d93c206582`, application asset `app-CNqMVV2y.js`.
- Evidence is retained under the workspace's `output/release137-source/backend`, `output/work-activity-20261013/post-release-136-audit`, `history-load-followup` and `retention-sizing` directories. Owned temporary fixture/browser processes and transferred build archives were removed after evidence/hash verification; the active checkout and owned DTU are retained for integration and live follow-up.

Full Python and extended browser verification runs after publication against the exact selected revision. Live 0.20.137 acceptance remains pending.
