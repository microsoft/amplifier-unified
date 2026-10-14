# Release 0.20.136 qualification

This selected maintenance candidate extends published `c38fc7ea13a93bf1c9d528dd5faa10ffe521fc22` (0.20.135), excluding the separate integration feature changes on main. Development qualification uses the owned `dtu-unified-public-readiness-20261008`, source `/opt/unified-work-activity`. Repeated spark-1 published updates and Mac navigation acceptance are explicitly user-authorized.

## Evidence and changes

Live 0.20.135 repeat visits displayed messages and work rows in 337/239/223 ms for CPU usage, 203/196 ms for AHP/ACP, and 260 ms for multi-worker workspaces. The final CPU observation followed visits across six chats. First CPU/AHP visits took 7,077/5,353 ms; first workspace interim messages appeared at 549 ms and work at 956 ms. These are individual DOM observations, not controlled percentiles. UX feedback had an existing recorded turn failure and no final work groups in its latest page; it is not claimed as a passing work-row case.

Diagnostics showed two roughly 0.56-second cleanup root checks at each minute, aligned with event-loop delays. Warm immutable-body checks reused their validated edge cache, but live and durable root enumeration still held the application lock. Background collection now cooperatively bounds complete root proof admission by time, fields and JSON record size. An incomplete initial or final proof retains all indexed and unindexed files and reports its deferral reason. Explicit/offline collection retains its complete proof. This is a responsiveness safeguard, not incremental collection; large mutable graphs can postpone reclamation, and individual database/mapping/JSON operations remain indivisible.

Cold transcript loading and passive event projection could run concurrently against the same Foundation transcript index, then rebuild when message/turn inputs arrived. Idle initial loads now defer passive projection only while the actual per-session load lock is held. Active roots, live workers, explicit reads, completed loads and failed/cancelled loads remain eligible. Exact historical usage and turn/producer ownership are preserved.

New bounded diagnostic stages distinguish transcript indexing/display facts, native activity scan/association and event scan/row-copy/join/accounting/retention costs. Counters identify cold/input/source rebuilds and graph sizes. No bodies, paths, credentials or estimated byte counts are added.

## Targeted qualification

- Stage instrumentation: 183 focused DTU tests passed in 41.99 seconds.
- Initial-load deferral: 183 adjacent tests passed, followed by 31 final focused tests after cancellation and cold-storage boundary regressions were added.
- Background root budget: 75 resource/retention/orphan/shared-storage/state-record tests passed in 24.76 seconds. Both initial and final exhaustion preserve files and release locks/transactions; a later small complete proof collects correctly.
- Synthetic 27,000 dormant metadata rows plus six chats with 2,000 loaded messages each: full reference traversal median 45.19 ms versus 5.38 ms before safe work-budget deferral. This is not a prediction of live latency.
- Independent source reviews found no blockers in these changes.
- The complete composer browser scenario passed after its fixture checked accessible names instead of a native title deliberately removed by custom tooltips, and navigated through the current Chat / Advanced / Computer access controls. Exact 0.20.135 source compatibility was checked. The DTU used its existing ARM single-process Chromium preload; hosted browser verification remains separate.

0.20.135 hosted Python passed 7,204 tests and 6 subtests, with 163 skips. Its feedback lifecycle scenario passed; its browser run subsequently failed the stale composer assertion corrected here. Later skipped browser cases are not claimed successful. No final full Python suite was run locally for this focused candidate; post-release checks follow its immutable selected revision.

## Final candidate checks

The exact selected candidate passed the combined 12-file backend suite: 289 tests in 65.27 seconds. All 686 frontend tests passed. Release-note validation initially found a malformed notices entry; the limitation was moved into the ordinary changes text before publication. After correcting that notes schema, all 116 release checks passed in 4.93 seconds. Two production builds passed in 8.54/8.60 seconds and produced identical SHA256 values for all 311 generated assets. Source and returned assets were hash-verified against the owned DTU.

## Publication

Preserve this qualified candidate's ancestry when merging into main. Cancel only this integration's automatic broader-main publication, then use the supported `release_revision` input to publish the exact qualified candidate. Local frontend/build/release checks above passed; live 0.20.136 measurements remain pending publication.
