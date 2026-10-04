# Current installed cold working-set acceptance

This opt-in fixture checks [WS1–5](../../contracts/working-set.v1.md) and
[HP](../../contracts/host-protocol.v1.md) through a public installed distribution.
It creates only synthetic, owned state. It never submits a prompt, opens an
account, invokes a device, or accesses a deployed service.

The configured composition contains 21 actual participants, including native
message metadata and manual ingress. A lazy supervisor connector is configured
but never started or contacted: this is not a service replacement test.

## Run

Supply an independently identified installed distribution and a compatible Python
interpreter containing its native, catalog and owner packages. Node must satisfy
the distribution's published engine requirement. These are fixture inputs, not
dependencies resolved by the test:

```sh
UNIFIED_DISTRIBUTION_ENTRY=/owned/consumer/node_modules/@amplifier/unified/src/index.js \
COLD_WORKING_SET_PYTHON=/owned/python/bin/python \
COLD_WORKING_SET_DIRECTORY=/owned/receipts \
COLD_WORKING_SET_RECEIPT=/owned/receipts/cold.json \
node --test distribution/test/cold-working-set.integration.test.mjs
```

Set `COLD_MIXED_CLIENTS=1` to add two browser contexts and two installed Ratatui
terminal processes. Also supply:

- `COLD_PLAYWRIGHT_ENTRY`: the identified Playwright test module entry.
- `COLD_PTY_PYTHON`: a test interpreter containing `pyte`.
- `COLD_PTY_OBSERVER`: the TUI repository's identified `scripts/terminal_probe.py`.
- `COLD_TUI_EXECUTABLE`: an installed terminal client executable.

The PTY helper is an observer only. The client executes from the installed
package. Observer records, draft stores, captures and all generated owner data
stay within the new fixture directory. Python runs with isolated imports and
bytecode disabled. The original fixture and failure captures remain after every
run, so the caller can inspect and retain them.

## What is asserted

- Two canonical roots contain 10,000 and 2,000 turns; 700 child records are
  indexed before serving. Root browsing returns only the two roots.
- Thirty-two simultaneous protocol clients can list and subscribe to metadata
  without native history reads or execution starts.
- Selected windows contain 50 and 25 turns. One explicit earlier-page request
  returns 50 more turns with the correct cursor order.
- Thirty uninterested clients receive no chat events. Repeated list reads emit
  no shared state actions and perform no additional native history I/O.
- Independent audit hooks reject catalog transcript reads, all event-log reads,
  and native worker launches. An impossible worker command supplies a second
  execution guard. Transcript reads through the actual native reader are counted.
- Detaching viewers clears selected native turn retention. Original canonical
  transcript, metadata and event bytes remain unchanged.
- With mixed clients enabled, both browsers and terminals read separate saved
  histories. Private typing causes no browser requests or shared actions. Drafts
  survive browser reload, terminal restart and per-session browser navigation.
  Browser projections and selected host windows remain bounded; terminal modes
  are restored after exit.

## Evidence boundary

This is cold selected-history and client-isolation acceptance. It does not measure
active agent throughput, slow-stream backpressure, 100,000-row catalog keyset
cost, full-history requests without a view, physical terminal rendering, actual
model accounts, or live deployment capacity. The 700 children establish that root
browsing excludes delegated records; this fixture does not navigate their pages.

The first recorded run used the exact preview-4 Node payload and exact native,
catalog and owner wheel files in a small owned overlay. Its Python dependencies
were reused through read-only donor sites to avoid a large duplicate install.
That is explicitly not a fresh resolver transaction or a byte-identical whole
live Python environment. The external qualification packet records the archive,
wheel, imported-path and client identities; no machine paths or user history are
embedded in this source test.

Existing `app-reset.integration.test.mjs` and `full-owner-service-fixture.mjs`
retain their older 20-owner fixture contracts. They are not evidence for the new
21-owner composition. Historical scale receipts likewise remain scoped to their
recorded package revisions; this fixture does not relabel them as current passes.
