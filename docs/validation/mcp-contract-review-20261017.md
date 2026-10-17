# Saved MCP contract review and updated live documents

The user approved option1 on October17, following the original #808 recommendation.
The [governing contract](../clients/mcp-contract-review.md) keeps configuration,
account, resource identity, permission requests and existing action definitions
separate. Description changes are reviewed explicitly. New actions are never
implicitly granted. This is an additive acceptance checklist; it does not change
the frozen ordinary-UX baseline or erase the October16 evidence.

| Smoke item | Before / current regression signal | Desired result and executed acceptance |
| --- | --- | --- |
| MCP-REVIEW-01: changed existing action | Changed schema blocks ordinary reconnect. | Review identifies input/output/description/visibility changes, shows current definitions and removed grants; exact human approval restores only existing names. Python and real SDK browser passed. |
| MCP-REVIEW-02: additions / legacy snapshot | New catalog names cannot be inferred to be existing grants; older bindings store hashes only. | Added names stay ungranted; older/malformed definition snapshots report comparison unavailable. Python and browser passed. |
| MCP-REVIEW-03: changed resource / account / configuration / permission metadata | A hash alone is insufficient review evidence. | Configuration/account/resource changes remain blocked. Permission requests are shown, never granted. Definition, resource, metadata, catalog or selection drift rejects the review. Python passed. |
| MCP-REVIEW-04: update same artifact | Old saved HTML cannot acquire new startup code by reconnecting. | Explicit acknowledgement saves new HTML as another version of the same artifact, preserving launch arguments/result/domain identity; no launcher or prior mutation runs again. Real SDK browser passed. |
| MCP-REVIEW-05: two mounted clients | First candidate pinned history but remounted the other iframe, losing its input; retained as a failed test. | Separate render identity preserves actual DOM and input while authority revision/generation changes. Old targets and current historical appCall are denied. Two independent browser processes and Python passed. |
| MCP-REVIEW-06: historical action | Disabled reconnect gave no useful next action. | Always-visible Open live view explicitly selects Latest; history remains read-only until that choice. Reload saved HTML asks before resetting input. Browser/lifecycle checks passed. |
| MCP-REVIEW-07: caller authority | Review approval must not be available to a restricted device or agent. | Restricted companion HTTP requests and agent approval rejected. Review fingerprints bind the actual reviewing client and selection generation, including away/back navigation. Python passed. |
| MCP-REVIEW-08: original Studious saved bytes | No authenticated original saved Studious document has been available for reproduction. | **Not executed / remains open**: exact old artifact and its original host sequence are still needed. Generic cooperative SDK acceptance is not Studious acceptance. |
| MCP-REVIEW-09: optional review and Canvas layout | The complete browser census caught a 43.78125-pixel strip above a healthy tool; its unchanged geometry assertion failed. | Healthy review belongs in Canvas options; required review stays visible when those options are closed. Focused rebuilt-client qualification is pending for this correction; the original failure is retained. |

## Executed checks and limits

All application execution used `dtu-unified-codex-dev-20261014`, source
`/opt/unified-dev/checks/mcp-contracts-20261017`, ephemeral loopback listeners and
private synthetic state. There were no provider/account calls, live adoption,
main service changes or writes to spark-1. The source baseline is release175
`c8f5fd8978d67822ed38d1a55f8da92507663877`; generated assets stay out of this
scoped source commit and are rebuilt by the integration owner.

- MCP contract/recovery/CanvasViews: **81 Python tests passed, no skips**,67.00s.
- MCP lifecycle/read gate: **13 client tests passed, no skips**.
- Production build and extended real SDK MCP recovery browser passed. It includes
  unchanged-contract reconnect, real host restart, saved-version/Latest selection,
  existing launch receipts, changed-contract refusal, explicit approval, new-tool
  refusal, new HTML adoption and two-client input/authority assertions.
- Existing broad-run ownership remains the single `mcp-recovery` scenario; no
  duplicate test lane was added.

Raw logs, screenshots, file hashes and resource ownership are retained under
`output/afk-approved-20261017/mcp` in the task workspace. Initial failures are
retained: ARM Chromium crashed when creating a second context under its required
single-process flags; two independent browser processes corrected that setup.
The first human-origin check omitted the web `ui` origin; the actual browser
caught it and the established `{ui,user}` human origins are now used. The first
historical pin remounted a second iframe; the regression now proves both DOM
preservation and strict authority invalidation. An initial lifecycle assertion
expected the obsolete disabled history reconnect control; it now requires no
history reconnect and preserves all denied-state/no-notification assertions.

The owned ARM launch uses `--no-zygote --single-process --disable-gpu` and
`--no-sandbox`; process isolation is reduced for this test environment. Native
macOS/Windows device behavior and a real Studious account were not tested.

## Original #807 evidence boundary

Scoped read-only discovery inspected only MCP artifact/catalog metadata in the
existing immutable replica and authorized spark-1 installation. Each contained
12 MCP artifacts, none Studious; no credentials or transcripts were copied.
The report belongs to a different reporter. An accessible same-named repository
was an older unrelated design-system application, not the October2026 MCP board
app. It is not substituted as acceptance evidence. No external reporter message
was sent. #807 must remain open for the actual saved artifact and original sequence;
the independently proven host actions and safe replacement flow can still ship.

## Delayed review response regression

Integration review found that a held contract read could finish after the view's
binding changed, displaying obsolete definitions or an old error in the new view.
The host already refused stale approval. The client now discards responses from
superseded or closed reviews, including a late approval receipt's refresh callback.
Both read-outcome cases failed on the unchanged candidate. A second held-response regression exposed an approval status refresh overwriting
a newer contract refusal. Approval now uses the existing generation-checked
connection refresh. That regression also failed on the unchanged implementation.
All five new lifecycle checks and the ten existing MCP viewer checks pass in the
owned DTU (15 total).
The full frontend suite owns these checks automatically through its existing glob;
no extra browser lane was introduced. Evidence: `late-review-before.json` and
`late-review-final.json`, plus `late-status-before.json` and
`late-status-after.json`, under the task's MCP evidence directory.
