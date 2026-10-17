# Bounded projection encoding qualification

Base: `8f5fa1ea15f2fa30ecfbb610a96e3c9dc2793ca8`. Owner: the isolated
spark-2 development DTU, `dtu-unified-codex-dev-20261014`. No provider,
browser, live account, or spark-1 workload was used for this qualification.

The measured hot path wrote/hashed/reserved every small JSON fragment separately.
The candidate groups fragments into bounded 64 KiB writes without changing JSON
options, byte order, input/spool caps, authority, or process ownership. Aggregate
reservation still precedes each write. Charged and not-yet-reserved pending bytes
are accounted separately during failure cleanup. A large string fragment is
written in bounded slices, but its original JSON/UTF-8 allocation remains.

## Focused correctness

`tests/test_history_projection_encoding.py` and
`tests/test_history_projection_process.py`: **41 passed, zero skips, 7.92 s**.
Coverage includes exact UTF-8/escape/float/sorted-key output and SHA256, private
file mode, exact input and aggregate limits, one-byte-over refusal, invalid JSON
before/after partial writes, partial filesystem failures, stop before first flush
and between large-fragment slices, concurrent reservation ownership, cancellation,
coalescing, real child-process failure/respawn and cleanup. The unchanged baseline
fails the structural batching witness: 52,725 writes for the small-fragment input.
No corruption checks, timeout policies, or product assertions were relaxed.

## Frozen microbenchmark

The same detached synthetic 5,272-workspace object was passed to unchanged and
candidate encoders, in alternating order for 15 measured pairs after two warmup
pairs. Every sample matched exact output bytes, digest, and charged spool bytes;
cleanup returned the charge to zero. Input: 727,755 bytes, SHA256
`89c22facc4bd72c7be3a946ce19fadaa2d270317dd73631a8c791da705d6803a`.

| Encoding only | Before | After |
| --- | ---: | ---: |
| Median wall time | 41.317 ms | 27.527 ms |
| Median process CPU | 41.317 ms | 27.528 ms |

Median wall time was 33.4% lower in this fixture on Linux aarch64 / Python 3.13.16.
This is not an end-to-end navigation or event-loop latency claim. JSON traversal,
caller input construction, file buffering, and other query costs remain.

The unchanged source is exported from the base revision; the reusable manual
probe runs only in an owned DTU:

```sh
python tests/fixtures/projection_encoding_benchmark.py \
  --before-source /owned/path/history_projection_process-before.py \
  --output /owned/path/encoding-evidence
```

Raw frozen input, all sample timings, source hashes, failed baseline and passing
focused logs are retained under the workspace evidence directory
`output/afk-approved-20261017/performance-review/encode633/`.

## Coverage inventory addition

| Scenario | Current result | Desired / regression threshold |
| --- | --- | --- |
| Encode a 5,272-workspace detached navigation input | Exact bytes/digest/budget, 27.527 ms median | Preserve byte/budget identity and bounded writes; compare future timings on the same frozen fixture |
| Stop, malformed JSON, or filesystem failure during encoding | Partial file removed; only owned charge released | No leaked artifact, negative/shared charge, or command replay |
| Concurrent encoded inputs at aggregate cap | Refused input preserves active reservation | Never write unreserved bytes or exceed aggregate cap |

The focused tests are collected by pytest. The microbenchmark is an explicit
manual performance probe, not a timing-based CI assertion. Integrated scale
navigation and streaming measurements remain a separate parent-owned check.
