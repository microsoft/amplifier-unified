# Diagnostic capture qualification

The reported call had saved provider captures, while independently paged Work
omitted their attachment to the live call. The capture notice also used changing
healthy index statistics in its acknowledgement fingerprint. This change fixes
those promised behaviors within the existing bounded history and explicit
disclosure boundaries; it does not change the architecture direction.

New captures carry the host's task-local call identity before canonical logging.
Provider-supplied identity is removed even from summary-only events. Exact root
public/native mapping preserves the app HTTP owner and child namespaces. A
single accepted request attempt may supply references; retries, duplicate fields
and legacy records remain separate observations. Legacy recordings can still be
opened by provider, model and recorded time through the saved-recordings shortcut.
Payload bytes are never copied into browser metadata or usage accounting.

Accepted-page identity participates in the live detail validator, so a completed
open call refreshes when recordings arrive and renews references before older
pages expire. Full downloads resolve through their actual event or resident
owner and recheck detached authority after opening the file. Partial newer
recordings replace the prior attempt's entire metadata set.

Qualification used synthetic state in an owned spark-2 DTU, based on main
`ca44105a06bb41d89f82045fc517b1d4d2f720df`. The rebuilt bundled client is
`ce18a0a69358b5ad`; the package version was not changed. Exact input hashes and
test evidence are in [the receipt](diagnostic-capture-20261018.json).

- Three failing-before witnesses reproduced the notice acknowledgement,
  host-call capture identity, and absent live-window attachment defects.
- 111 focused Python tests passed in 16.95 seconds. After the accepted-page
  lifetime correction, all 15 affected ownership cases passed in 4.57 seconds,
  including more than eight appended page generations with readable references.
- The built-client provider-recording smoke passed with 107 paged live steps,
  delayed canonical flush without a new execution event, explicit raw disclosure,
  full downloads from linked and separate saved observations, and legacy
  navigation through two collapsed saved pages at desktop and 390px widths.
- The existing built-client `canonical-work` smoke passed its complete-projection
  path, raw disclosure, paging/retry/copy, preview bounds, reload and mobile checks.
- Diagnostic acknowledgement uses the real `attention.read` service path. The
  existing focused/visible acknowledgement timer was not newly browser-tested.
- The community context-intelligence writer at `a80ce69d0e96e9a343501f238dcd867350dd9aa2`
  was inspected: its priority-100 logger preserves arbitrary scalar keys through
  sanitization and writes the resulting data dictionary to canonical JSONL.
  The Core hook-ordering test uses the real HookRegistry and wrapped providers.

Intermediate failed runs remain separate evidence. Five download tests initially
lost manually owned windows to the normal unobserved-interest cleanup loop;
their fixture now explicitly owns that lifecycle. An absent-capture assertion
was corrected alongside omission of empty capture metadata. Browser diagnostics
proved that similarly named responses were two valid canonical events: the
app's summary completion and the raw provider attempt. The smoke now opens and
distinguishes both by payload availability, then retains the selected row ID.

Evidence is retained in the owning workspace at
`output/diagnostic-capture-20261018/`, including negative and intermediate logs,
final checks, screenshots and input hashes. All published evidence describes
synthetic data. No live provider invocation, historical counter provenance,
full-suite, physical-touch or installed-release adoption is claimed. Chromium
used isolated ARM test flags (`--no-zygote --single-process --disable-gpu`).
No live installation was changed.
