# Copied-scale performance qualification — October 17, 2026

Measured source before the later non-browser shortcut guard and fixture-only CI corrections: `a3eb8360e96010ee3cf8f89ff015dbf9401099df`; comparison: `bce890d3412713b25818c3c17bd66cab1304fa45`.

The browser exercised existing copied chats and a 274-chat workspace with 40-row pages while 1, 6 and 12 real local worker processes ran deterministic 250 ms provider responses. The frozen copy contains 27,508 sessions and 5,272 projects. Four effective CPU cores and 8 GiB were available. Copied owners could not execute; only fourteen synthetic owners were admitted. The app and worker used published Core 2.0.2.

All functional journeys passed and stopped their owned workers. The measurements do **not** establish that every performance target is met. Warm acknowledgement remains above 300 ms; 12-worker streaming exceeded the 250 ms target in both the final sample and its independent confirmation.

| Active workers | Before warm acknowledgement median | Candidate median | Before first workspace | Candidate first workspace | Before stream lag p95 | Candidate stream lag p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 674 ms | 593 ms | 494 ms | 374 ms | 146 ms | 152 ms |
| 6 | 770 ms | 786 ms | 543 ms | 473 ms | 191 ms | 216 ms |
| 12 | 934 ms | 1025 ms | 866 ms | 986 ms | 239 ms | 364 ms |

One independent 12-worker confirmation measured 1,043 ms acknowledgement median, 1,051 ms first workspace and 284 ms stream-lag p95. Previous-page latency changed from 445 ms before to 679 ms in the candidate sample, then 494 ms in confirmation. The six-worker exact filter changed from 275 to 376 ms; it remains below the 500 ms desired target. These warnings and original samples are retained, not averaged away or retried until green.

The narrow fixes eliminate unchanged navigation-envelope assembly and batch JSON fragments into bounded 64 KiB writes. A separate fixed-input, alternating 15-pair encoder benchmark improved from 41.317 to 27.527 ms median with exact bytes, digest and budget accounting. That 33.4% encoding improvement is not an end-to-end speedup claim.

Limits: three send samples per stage make reported send p95 simply the observed maximum. Streaming uses 23–24 markers per stage. Sequential synthetic history grows and service caches are warm, so this is not a randomized causal comparison. Runtime qualification is bypassed for the offline minimal fixture; normal cold Work activation, live providers, globally atomic capture and full event fidelity are not qualified. Namespace/mount preparation is excluded from app timing. Chromium153 runs with no-sandbox/no-zygote/disable-gpu, without single-process.

Earlier Chromium launch/setup failures and one accidentally overlapping harness run remain recorded separately and excluded. The final workload and its confirmation ran strictly serially with explicit process/worker cleanup. Functional passes do not close #633; remaining latency targets require further work.

Exact raw receipt SHA256 values and sanitized aggregates are retained in [the aggregate receipt](copied-scale-176.json). Raw receipts remain private because they contain copied owner identities.
