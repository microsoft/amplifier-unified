# Incremental JSONL history I/O

Normal checkpoints now use Foundation's intent-aware append writer when the
worker's Foundation supports it. It validates the saved prefix, writes only new
rows, and advances the backup by the previous append. Unchanged history writes
metadata only. Edits, shortening, and recovery use atomic replacement. Older
pinned worker generations retain their existing portable writer until updated.

A pending append marker means readers use the complete previous backup. This
covers interruption in a JSON line and between complete lines of one batch.
Recovery is read-only; the next explicit save replaces the incomplete primary.
The writer requires the existing session ownership lock. Arbitrary concurrent
external writers remain unsupported. The Foundation release supplying
`indexed_messages` must precede this app change; workers can upgrade later.

Chat paging, delivery checks, and activity association reuse a bounded process
index of offsets, hashes, and small lookup fields. Message bodies stay on disk.
External CLI edits, replacement, and truncation invalidate it. Known appends
extend it. Event associations retain prompt fingerprints rather than full
prompt strings. A process restart rebuilds these disposable indexes; they are
not another saved session state format.

CI remains the event archive. This change does **not** remove `transcript.jsonl`
or switch resume authority: the current local CI transcript extractor supplies
user prompts and final answers, not a verified lossless reconstruction of tool,
hook-injected, and provider continuation messages. Before making the transcript
fully reconstructible from CI, record explicit canonical message mutations and
verify reconstruction coverage. Do not infer a complete resume context from
visible chat text or repeated provider request snapshots.

## Validation

DTU synthetic benchmark: 7,104 messages, 64,329,500-byte transcript; three samples
per warm path, medians. Both cases use the real host save and chat-page code.
No provider requests or production history are used.

| Operation | Previous | Candidate |
| --- | ---: | ---: |
| Append 50 KB (wall time) | 701 ms | 156 ms |
| Append CPU time | 685 ms | 133 ms |
| Append bytes read | 192.7 MB | 51 KB |
| Append bytes written | 128.5 MB | 100 KB |
| Warm 100-message page | 231 ms | 4.9 ms |
| Warm page bytes read | 64.3 MB | 1.0 MB |
| Metadata-only save | 352 ms | 133 ms |
| First page/index preparation | 233 ms | 271 ms |

These are path measurements, not whole-server CPU improvements. Current context
and checkpoint APIs still supply the full message list; prefix validation still
serializes/hashes it. This is why save CPU remains proportional to history size.
A future revision-aware context change API can remove that remaining scan
without assuming that old messages are immutable.

Tests cover CLI append/replacement/in-place edits, valid-prefix crashes,
partial-line crashes, corrupt-primary backup recovery, invalid late rows,
no-op timestamps, paging offsets, exact activity associations, cache eviction,
and independently updated worker dependencies.
