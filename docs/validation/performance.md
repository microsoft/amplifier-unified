# Performance validation

This guide applies [WS1/3/5](../../contracts/working-set.v1.md) and
[CS3/4/7](../../contracts/client-state.v1.md) to changes in the current application
and its dependencies. It governs evidence and regression checks, without changing
public read/action contracts or claiming the target architecture is implemented.

## Declare the operation and its scaling inputs

Before changing a frequent read, publication, save or preparation path, name its
canonical owner, trigger and consumers. State what grows: active owners versus
stored sessions, messages and their bytes, retained jobs/receipts, subscribers,
requested pages or pending work. Include content shape where it matters: many
small rows, a few large tool bodies and nested mutable values exercise different
costs. Multiple growing inputs can multiply work, such as scanning every message
for every retained job.

State the intended work bound and any necessary exception. Recovery may need to
validate complete history; that is different from doing the same scan on each
unrelated update. A required linear pass over the admitted owner's transcript is
legitimate. Guard against repeated full scans per job where one transcript pass
plus per-job checks preserves the same recovery result. Trace reads, copies,
serialization, hashing and comparisons before filtering the final result. A small
response, unchanged file or quiet UI does not prove that its construction was cheap.

## Separate current presentation from historical evidence

Routine state carries the current fields its consumers need. Historical bodies
use existing authorized detail/history reads when they are not needed in that
view. Apply projection before expensive copying or encoding. Review every removed
field against its consumers and retain exact identity, revision and read coverage.
Do not hide a required current field or disable a control merely to meet a byte
target; omitted coverage is distinct from an empty value. If current fields remain
unbounded, report that limitation rather than claiming a total payload bound.

Derived caches and indexes retain one canonical history authority. Document their
dependency/invalidation witness, ownership, growth limit and retirement behavior.
An object identity, list length or file size alone does not prove unchanged nested
content. Count cache-validation work too. Moving a full scan to another process
changes responsiveness, but does not by itself reduce total work or memory.

## Keep a meaningful regression check

Choose a small case and a representative larger case for the affected operation.
Hold the active/viewed set fixed while growing the historical input when testing
independence from history. Exercise cold misses and warm reuse when either changes.
Use synthetic fixtures that preserve the relevant size, shape and event sequence;
an empty history or mocked-away persistence cannot qualify that cost.
Vary independent inputs separately: a fixed notice set in a growing native
catalog exposes different query work than a growing notice set without native
history. Inspect the executed query plan and work counts; selecting a few fields
or joining a small result does not prevent an earlier full materialization.

Also vary retained body bytes with the row count fixed. A constant SQL instruction
count can still hide repeated parsing of large bound JSON parameters. Scalar
queries should not bind unused bodies; measure parameter/statement bytes as well
as rows and preserve the authorized full-detail read.

Prefer stable work assertions at the affected boundary: rows read or visited,
records copied/encoded, queries, publications, frames/bytes, retained cache bytes
or runtime starts. Pick the counts that expose the regression; avoid tests that
only mirror helper calls. A check should detect the old behavior and still permit
a different correct implementation. Pair the work assertion with semantic oracles:
exact history/receipt preservation, current control availability, revision and
subscriber causality, and no unintended wake, replay or provider call as applicable.
Exercise failure, cancellation, concurrent mutation or lost acknowledgement when
the optimization changes those boundaries. Select affected cases rather than
turning this list into an unrelated universal test matrix.

Declare each budget with its workload, units, scope and reason. Derive byte/count
limits from the intended resource contract and measured fixture; derive timing
tolerances from a comparable baseline and observed noise. Do not invent a universal
latency cap or loosen assertions/retries to obtain a pass. Where timing is noisy,
retain the distribution and use deterministic work counts for the regression guard.

## Record evidence that another owner can compare

Compare baseline and candidate with the same fixture, assertions, platform class
and runtime mode. Record exact source, built client, installed package and loaded
dependency identities relevant to the path; files present on disk alone do not
prove which code a process loaded. Record cache state, instrumentation, repetitions,
concurrency and provider/network use. Label source, synthetic installed and live
observations separately, and retain failed setup attempts and material limitations.

Measure the user-visible boundary as well as the changed component where relevant:
admission, acknowledgement, publication and ready/rendered completion can differ.
Do not add nested inclusive timings. Process CPU can include threads; wall time
includes waiting. Distinguish application payload bytes from compressed wire bytes,
RSS from PSS, and cold startup from warm reuse. Account for observer overhead when
it can change the conclusion. Keep diagnostic collection bounded and sensitive
receipts private. Public evidence uses synthetic data, counts and hashes rather
than transcript bodies, credentials or private environment details.

## Keep qualification proportional to risk

Follow the [fast publication policy](release-qualification.md#local-first-fast-publication-policy-updated-october-14-2026).
Run the affected regression and changed user flow; use installed built assets when
the claim depends on packaging or browser/server integration. Reuse exact applicable
receipts rather than repeating unrelated suites. Broad post-release checks remain
background follow-up, and this guide adds no blanket merge or publication gate.

Keep budgets and measurements with their executable fixture or exact evidence
record. Update them when workload or contract changes, with a reason and retained
comparison. Link that evidence from the PR; passing one fixture is not a claim
about all histories, platforms, provider behavior or deployed user latency.

## Maintained client smoke

`frontend/tests/client-traffic-browser.mjs` uses the real server and built client
with synthetic retained task history. It grows history from one to 128 large
records while holding current fields fixed; opens Work, details and runtime
panels; observes causal-only updates and reconnect; and reads a complete historical
record through paged details. It checks frame/projection growth, application-root
rendering, view-report feedback, current controls and unchanged canonical history.
Its byte budgets are scoped to that fixture; timings are diagnostic.

`tests/test_attention_demand.py` holds the viewed/resident set fixed while
growing acknowledged notice owners and their resolved-question history. Routine
views read existing notice facts without decoding unrelated session summaries;
selected details and bounded navigation receipts keep their exact fingerprints.
Global counts still aggregate scalar notice/read facts, and explicit Activity
retains all notices for review and mark-all. Neither path claims constant-time
global counts or a total byte bound. The check records SQLite work and diagnostic
CPU alongside decode/visit counts, and preserves native naming, workspace,
suppression, current read receipts and transaction boundaries.
Its native-catalog case keeps 220 notice owners and two residents fixed while
growing unrelated native rows from 300 to 27,000. Selected notices, visible read
receipts and explicit Activity must preserve their exact results without growing
SQL work with that unrelated history. This covers canonical native metadata for
local notice owners without scanning native-only rows that have no notices.

`tests/test_directory_scalar_payloads.py` holds the catalog and current notice set
fixed while growing retained body bytes. Scalar notice/revision queries must keep
their SQL inputs bounded by the requested metadata; full-detail reads retain the
exact bodies. Summary-only edits still invalidate observations, and frozen readers
retain their own state. The overlay encoding checks exercise mixed scalar/full
query order and reuse each variant only until the installed overlay changes.
These checks bound SQL inputs, not all live-summary preparation or global counts.

The additive Extended browser registration runs after publication. The separate
`Client performance smoke` workflow selects affected PR paths and supports manual
runs; it is not a required status or a publication dependency. Run it locally in
the owned test environment with the production client built. Its synthetic runtime
does not qualify native recovery, model calls or installed-package provenance.
