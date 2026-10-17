# Chat info layout stability smoke

This extends the existing ordinary Questions browser scenario; it adds no new
scenario, background poll or broad release gate. The path is Chat → Chat info →
Questions & answers, including the first usage snapshot and an explicit refresh.

| Case | Baseline 0.20.174 | Acceptance target |
| --- | --- | --- |
| Initial usage completion during a desktop pointer click | The same history summary moves from y=698.0625 to y=1021.328125 (323.265625px); the new stability assertion fails. | Its rectangle changes by at most 1px, node stays connected, and the original pointerup opens the disclosure. |
| Refresh with expanded, growing usage detail | Previously unqualified separately. | The same target remains stable while canonical synthetic receipts grow from zero to six provider/model groups. |
| Initial read and expanded refresh at 390×844 | Previously unqualified separately. | Preserve the target through both reads and grow from six to twelve provider/model groups without a missed click or horizontal page overflow. |

The test intercepts actual `capacity.read` requests and waits for a bounded
request-arrival witness. It observes pending UI, scrolls the target into view
**before** pointerdown, releases the read, and waits for the completed rendered
snapshot. It does not scroll or reposition the pointer during completion. The
same pointerup must activate the disclosure. Model groups come from synthetic
canonical execution receipts in the private fixture, not an invented API reply.

Existing strict card uniqueness, handoff co-render, draft, answer/retry,
cross-client duplicate rejection, cancellation, voice provenance and narrow
layout checks remain. The final narrow screenshot scrolls the answered card into
the viewport explicitly. Full live-provider, device-native and unrelated panel
behavior are outside this browser fixture's claims.

The candidate `0.20.175 / f4235893cb248843` passed the complete Questions
scenario. All four cases measured exactly **0px** change for the history
rectangle and refresh-control x/y/height; button width may change with its
pending label. Pointerup opened the disclosure in every case. The expanded
snapshot contained the expected six/twelve model groups, and no browser errors
were observed. Desktop and narrow screenshots were inspected.

Qualification receipts and observed rectangles are retained under
`output/chat-info175-20261017/browser` in the task workspace. The failed shipped-174 baseline stays
separate from the successful candidate. Application/browser execution uses synthetic state in the owned
spark-2 development DTU with process-local ARM Chromium arguments; it does not
adopt the release on spark-1.
