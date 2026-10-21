# 0.20.174 browser fixture follow-up

Release 0.20.174 remains published at `97e29cbad`. Its post-release run
[39011310311](https://github.com/microsoft/amplifier-unified/actions/runs/39011310311)
reported two browser failures. Extended coverage completed 78 scenarios and
failed workspace starters; readiness stopped in question history. The original
results remain failures. This follow-up changes fixture ordering only.

## Findings and executed checks

| Ordinary path | Current evidence | Regression coverage / target |
| --- | --- | --- |
| Settings → Workspaces → duplicate a starter → local workspace Git | The old editor remains while Duplicate is pending. Opening its disclosure before the copy receipt lets the keyed replacement close it. Forced old ordering reproduces the hidden-checkbox timeout. | Hold the request, prove the old editor is disabled, then require the copy receipt and enabled new editor before the original controls. All 14 starter scenarios pass. |
| Chat → Chat info → Questions & answers → answer history | The first usage response inserts content above the history controls. A controlled response between pointerdown and pointerup moves the same summary by 323px; the click misses and its child remains hidden. Panel and node identity remain unchanged. | Hold the read once, prove pending UI, require the completed usage snapshot, then use ordinary clicks and assert the outer disclosure opens. The complete question scenario passes with its existing strict selectors and answer/retry/provenance assertions. |

Both unchanged browser scenarios also passed locally before the controlled
negative tests. These are ordering-sensitive failures, not evidence that 174's
question-card uniqueness fix regressed. The Questions trace reproduces the exact
hidden-summary symptom; the uninstrumented hosted failure cannot establish its
precise event sequence. No forced click, retry loop, timeout increase, production
change or removed assertion is used by the correction.

## Remaining UX observation

The initial Chat info usage snapshot can move controls below it while loading.
This is an observed layout shift, not fixed by waiting in the question fixture.
A future product change should keep those controls stable or otherwise preserve
an in-flight pointer interaction, with the delayed-response case as its negative
control. That improvement needs separate implementation and qualification; it is
not claimed as delivered here.

## Qualification boundary

Both final scenarios ran against unchanged shipped 174 assets, build
`b1233792339f6a5f`, using synthetic state in the owned spark-2 DTU. The ordinary
questions/readiness and workspace-starters/extended checks own the new assertions;
no duplicate scenario or new broad gate is added. ARM Chromium used process-local
single-process/no-zygote/disable-gpu/no-sandbox arguments; desktop browser process
isolation and live-provider execution were not qualified.

Evidence is retained in `output/postrelease174-fixtures-20261017` in the task
workspace, including the controlled failures, event trace, final logs, source
hashes and cleanup receipt. Fresh full readiness must pass before closing prior
readiness tracking; this focused result does not relabel 172, 173 or 174 runs.
