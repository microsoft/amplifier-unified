# 0.20.174 affected-change qualification

The user clarified #786: an explicit request to create and start an independent
chat should execute directly, without an additional confirmation. The additive
`session.handoff.start` action shares canonical creation/delivery, immutable
identities and no-replay recovery with reviewed suggestions. The behavioral
contract and agent guidance now distinguish these cases. Raw agent creation
remains refused; new direct requests require the actual current root transport,
with source Stop and assignment checks after lock waits.

This release also fixes #826: adjacent Questions and NewChatHandoffs components
had identical React keys. Shipped 173 reproduced three question cards for one ID;
namespaced keys preserve per-session reset without corrupting reconciliation.

## Executed checks

All application and browser execution used synthetic private state inside the
owned spark-2 development DTU. No live provider or spark-1 adoption occurred.

- Frozen focused host/receipt, release-note, browser ownership and frontend-proof
  tests: **167 passed**, zero skips, 55.84s.
- Full frontend tests: **1,014 passed**, zero skips, 20.54s.
- Unchanged real QuickJS catalog/permissions output-limit test: **1 passed**,
  1.29s, using a task-private interpreter with existing dependency paths.
- Final built-client two-browser handoff smoke: **8.057s**, build
  **0.20.174 / b1233792339f6a5f**, 83 matching host/DTU source and asset hashes.
  Direct start accepted in 464ms, with no confirmation and exactly one target
  and input despite lost acknowledgement, retry with a fresh outer command ID,
  reload and status reads. Existing reviewed flow, drafts, client isolation and
  narrow layouts remain covered.
- Questions/co-render regression: **6.937s** on the same built client; strict
  uniqueness assertions cover draft saves, review co-render, reload and cancel,
  followed by existing answer/retry/cross-device/voice/mobile cases.
- Activity inbox tail: **7.709s**, verifying the #824 fixture correction with
  empty-inbox setup, paging/review/retry/second-client/pending question scenarios.
- Dependency lock check passed; no dependency change or shared installation.

Independent review identified the delegated-worker principal gap in the initial
direct candidate. The final implementation reuses existing root/generation and
assignment admission checks. Tests cover child, missing/stale/terminal transport,
Stop while queued, terminal receipt inspection, mode binding, unknown delivery,
late acknowledgement and restricted companion denial. The final browser fixture
also supplies the transport envelope. Earlier candidate results remain distinct.

The initial local test command referenced a nonexistent test filename and ran no
tests; the corrected final command is the 167-test result above. Private browser
scratch-path setup failures and the 173 negative control are retained as evidence,
not counted as passes or hidden by weaker assertions.

## Limits and ownership

Agent grounding in the explicit user's request is a behavioral obligation, not
host inference of semantic consent. Target configuration and normal file access
are checked; no complete immutable transitive bundle graph is claimed. Browser
recipients are synthetic: exact acceptance is proven, not live model completion.
Timings are single-run regression witnesses, not production latency percentiles.

Existing questions/readiness and cross-workspace-handoff/extended scenarios own
the added coverage. Frozen inventories and scenario counts are unchanged.
Broad 172/173 failed readiness runs remain historical failures; a new full 174
readiness pass is required to close their post-release tracking. Publication uses
the normal package/frontend/runtime gates and broad verification runs afterward.

Evidence: `output/direct-handoff786-20261017` in the task workspace. Shared DTU,
dependencies and active previews are retained; task scratch/source cleanup is
recorded in WORKSPACE-MANIFEST.json. Publishing does not adopt spark-1.
