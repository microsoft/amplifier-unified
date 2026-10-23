# History read-receipt query regression

`HistoryDirectory.effective` joins current notice fingerprints to the current
read acknowledgments. Materializing the acknowledgment input once per query lets
SQLite index its keys instead of scanning the JSON virtual table for every
notice. It adds no persistent cache or acknowledgment authority.

The maintained checks in `tests/test_directory_read_receipts.py` cover:

- Fourfold growth in both notices and acknowledgments must take less than sixfold
  SQLite VM work, without admitting dormant chats. The unchanged query failed
  this check at 293,000 versus 4,172,000 instructions.
- Exact completion, manual-unread and error fingerprints, missing/stale receipts,
  live notice replacement, bounded identity scopes, in-place receipt changes,
  and frozen readers retain their existing meanings.

Focused directory navigation, native ownership, exact-ID/child scopes, overlay
encoding and revision tests cover suppression and current projection behavior.
All 100 focused checks passed in an isolated Linux environment.

An additional private synthetic publication probe used eight concurrent roots,
1,200 historical messages and accounting rows per root, 2,000 dormant chats,
two subscribed clients and 48 terminal receipts. Bridge admission fell from
3.96–4.02 seconds to 2.54–2.59 seconds; 19 browser projections fell from 2.05
seconds to 0.33 seconds. Every terminal receipt was durable before the final
read, historical messages and private drafts were unchanged, and each bridge
request received one reply. The probe uses buffered synthetic worker streams
and real reducers, persistence and projections; it is not live-provider or
installed-runtime acceptance. Original attempts and instrumentation are retained
privately, including a discarded timing run that overlapped other tests.

This removes one measured publication cost. It does not establish that the
reported live 60-second delay is resolved: serialization remains substantial
(91 encodings of unchanged history, about 236 MB, in this workload). Runtime
event ordering, durable saves, Stop, generation boundaries and bridge timeouts
are unchanged.
