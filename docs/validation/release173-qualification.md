# 0.20.173 integration qualification

This candidate completes #786 with a compact human review before creating an
independent chat in another existing workspace and submitting its opening brief.
It also includes #823: elapsed Work labels no longer trigger redundant browser
observation requests, and the Activity smoke handles an already-cleared inbox.

All application/test processes used the owned spark-2 development DTU with
synthetic state. No spark-1 update, restart or live-model request was performed.

## Affected-change evidence

- Full frontend suite: **1,014 passed**, zero skips; 26.2s.
- Combined handoff, creation, managed-chat, independent-client and real
  programmatic-tool catalog checks: **104 passed**, zero skips; 100.84s.
- Final handoff suite after the narrow publication-failure correction:
  **34 passed**, zero skips; 42.43s. This overlaps the combined count above.
- Release identity/notes/frontend-proof and adjacent action checks: 132 passed
  in the initial run; its one programmatic subprocess failure was diagnosed as
  missing QuickJS under isolated Python, then passed unchanged in a task-owned
  runtime with explicit existing dependency paths. The combined run above uses
  that private runtime; no shared dependency installation was modified.
- Browser-plan ownership: **18 passed**; the new scenario is additive. Original
  frozen workflow/scenario hashes are unchanged. Extended coverage has 79
  scenarios, with 122 unique source browser scripts across owners.
- Locked dependency check passed. Production client is **0.20.173 /
  ce042f1a141277a9**. Two builds produced identical hashes for all 311 files.
- Host and DTU matched all **2,188** executable source, test, script, configuration
  and built-client files before final qualification.

The built-client browser exercises exact review, cancellation, client isolation,
reload, a lost real confirmation response, read-only reconciliation, one durable
chat/input, preserved drafts, explicit navigation and stale-folder refusal.
Desktop and 320/390px screenshots were visually inspected. See the final measured
result in [the additive smoke record](cross-workspace-handoff-smoke.md).

Independent review covered original/restored client authority, future-proposal
privacy, restricted companion denial, exact local configuration drift, Stop,
publication failures, restart and unknown/no-replay outcomes. Confirmation uses
the canonical creation and input handlers; it does not relax raw agent creation.

## Limits and recurring ownership

The browser recipient is synthetic: acceptance of the exact opening message is
proven, not completion of its requested reading or a real provider response.
Remote bundle references and transitive dependencies resolve normally; the
review does not freeze their entire graph. Native device/provider behavior is
not claimed. Local single-run timings are regression witnesses, not latency
percentiles or a production-scale guarantee.

Four guidance probes intentionally require UNIFIED_RUNTIME_PYTHON and were not
counted as local passes. The publication release-runtime lane supplies the
resolved Core runtime and rejects missing/skipped owned cases. Publication owns
fresh installed-wheel/runtime checks; broad Python, readiness and extended
browser verification follows publication in the background.

Raw evidence, initial failures, environment diagnosis, screenshots and source
hashes are retained in the task workspace under output/handoff786-20261017.
Publishing does not authorize adopting the release on spark-1.
