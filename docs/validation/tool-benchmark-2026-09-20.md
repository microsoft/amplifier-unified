# Actual direct and programmatic tool comparison

The corrected Work instructions produced the same correct aggregate in both paths
on configured `terra` / `gpt-5.6-terra` / `high`. Each path read the same three
synthetic JSON files exactly once through ordinary approved Bash calls. Direct
mode used three top-level calls; programmatic mode used one `tool_exec` with three
nested calls and authoritative successful receipts. Both returned
`{"ids":["a","b","c"],"sum":51}`. No provider/model selection was changed.

| Corrected pair B | Direct | Programmatic |
| --- | ---: | ---: |
| Actual model calls | 4 | 2 |
| Input submission to result | 9.3776 s | 6.5753 s |
| Runtime start/catalog before submission | 11.2070 s | 3.1960 s |
| Gross input tokens | 58,653 | 27,500 |
| Gross input + output tokens | 58,762 | 27,759 |
| Cache reads (already included in input) | 42,468 | 20,659 |
| Cache writes (included once in gross input) | 16,173 | 6,835 |
| Provider-reported cost | $0.0502581 | $0.0243393 |
| Canonical model-visible tool result content | 16,980 bytes | 1,873 bytes |

These are observations from one serial pair, not a universal efficiency or speed
claim. The second arm may benefit from shared provider cache. Both arms retained
the identical full Work tool catalog (15 tools, 24,846 JSON inventory bytes),
and all actual provider usage includes prompts, schemas and any discovery. No
shared schema/system overhead was subtracted. Catalog/content bytes are not
provider wire sizes or token estimates. Provider-internal retries may be absent
from public call accounting. The complete normalized, cache and gross counters
remain distinct in [the redacted report](tool-benchmark-2026-09-20.json).

The first pair A is retained as failed evidence. Direct mode returned the correct
result (10.5375 s; 58,402 gross tokens; $0.0499909). Programmatic mode completed
all three reads but returned `{"ids":[],"sum":null}` (11.4333 s; 27,828 gross
tokens; $0.0274099). The then-supplied Work context explicitly named `exit_code`;
Bash actually returns `returncode`. Generated JavaScript followed the incorrect
instruction and printed "Tool failure" even though every underlying receipt
succeeded. This was an instruction defect, not provider or tool execution failure.
The failed pair supports no matched-correctness efficiency conclusion.

Work commit `853696b` (integrated as `d2d3edc`) corrects that exact field and explains
that delegated `success` and managed command completion are separate facts. The
second pair was explicitly authorized to validate this concrete correction;
there were no further live runs. Earlier sum-51 acceptance remains independently
attributed in the existing composed-profile acceptance; it does not replace A.

The harness is `scripts/work_profile/tool_benchmark.py`. It requires `--allow-live`,
an existing configured provider, a reviewed Work bundle, module-root and a fresh
private output directory. It sends one input per arm, caps each loop at five
iterations, saves failures instead of retrying, preserves fixture hashes and
reports source identities. Worker preparation is measured separately from model
work. Pure verdict tests reject missing/failed receipts and wrong aggregates.
Credentials were redacted after shutdown only in each run's generated text:
39 files inspected, three redacted, zero remaining matches in each run; SQLite
was inspected without modification and had zero matches. Original sanitized
report hashes are retained; the public summary does not rewrite source evidence.
