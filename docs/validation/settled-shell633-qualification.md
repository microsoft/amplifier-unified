# Settled shell snapshot reuse (#633)

This is a bounded performance change to the current built-in navigation owner,
starting from `c9ba786aebcbaab50d549b1f93404325cca76c3a`. It does not change
public resource contents, selection authority, durable writes or custom modules.
Settled built-in envelopes avoid repeating page-key/context/envelope assembly
when their exact inputs are unchanged. Pending resources still run the existing
completion and retry path.

Executed inside `dtu-unified-codex-dev-20261014`, using private synthetic state
under `/var/lib/unified-dev/shell-snapshot633`, source at
`/opt/unified-dev/checks/shell-snapshot633` and the existing owned read-only Python
dependency environments. No host application, live account, model provider,
browser or spark-1 action was used. Query-owner tests launch real disposable
query processes in the DTU.

| ID | Smoke check | Current result | Regression target |
| --- | --- | --- | --- |
| SHELL-PERF-01 | Repeat settled snapshot during unrelated progress | Unchanged source fails the new no-assembly witness; candidate returns the same immutable envelope without `_snapshot` | No page-key or envelope rebuild for unchanged inputs |
| SHELL-PERF-02 | Change initial preferences, per-client filter and unfinished workspace edit | New exact context; independent client retains its prior envelope and view | No stale filter or cross-client draft changes |
| SHELL-PERF-03 | Remove/admit the selected managed resident while its durable row remains unchanged | Derived managed/all scope changes correctly even with identical directory observations | Effective scope participates in reuse identity |
| SHELL-PERF-04 | Commit workspace rename/path changes and repeat name/path searches | Actual query process updates exact result membership; old envelope stays immutable | No stale name/path filter after canonical epoch changes |
| SHELL-PERF-05 | Update visible notices and shared history context | Unselected notice/error fingerprint and history state refresh | Current activity and context never hidden by a cache hit |
| SHELL-PERF-06 | Held pending read, source-change retry, explicit retry, shared capacity | Existing process-boundary tests pass unchanged | No cached pending state masks completion or Retry |
| SHELL-PERF-07 | Change demand/budget, expire client, change custom module | New demand identity and budget/refusal honored; existing cleanup/custom tests pass | Binding, resource bounds and independent module contracts preserved |

Focused results: 50 tests passed in 27.24 seconds in
`tests/test_shell_resources.py` and `tests/test_shell_query_owner.py`; the final
added name/path-filter test passed separately in 1.07 seconds. Total: **51 passed,
zero skipped**. The unchanged baseline's single expected failure is retained,
not relabeled as a product failure. No broad suite was run for this change.

Evidence and resource ownership are retained outside Git under
`output/afk-approved-20261017/performance-review/shell-snapshot633`.
These checks establish correctness and eliminated redundant assembly. They do
not claim a measured end-to-end latency improvement; controlled scale comparison
belongs to the integrated performance qualification.
