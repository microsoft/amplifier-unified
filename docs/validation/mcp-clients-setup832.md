# Independent MCP clients and dismissed connection setup

The extended browser lane now owns `mcp-clients-browser.mjs`. It uses the exact
saved Smart Tool operation receipt rather than a collection intentionally absent
from compact browser state. Its credential-free clients explicitly request the
same read-only provider discovery used by AI Connections, then close the visible
ordinary Settings control once, waiting for the accepted close receipt.

The completed journey exposed an existing product defect: focusing the browser
after that accepted close reset the per-workspace prompt marker. The unchanged
empty provider catalog then reopened Settings over the live tool. The correction
retains focus-triggered provider discovery without clearing that marker. A new
workspace still receives its first prompt; observing a configured connection
resets the marker, allowing a later loss of that connection to prompt again.

## Qualification

The unchanged renderer fails the dismissed-empty-catalog regression while both
preservation controls pass. The corrected source passes all 1,036 frontend tests
and 18 browser-registry checks. Packaged build `b931a4d86a1c52fe` passes the complete
MCP client journey in 10.205 seconds under the strict process owner, with no
residual or forcibly terminated children. Desktop and phone clients retain their
iframe and unfinished input through disconnect/reconnect, foreground refresh,
late stale reads and a completed call overtaken by disconnect. Exactly four
intentional mutations are recorded; reconnect does not replay calls. Later
reconnects require Settings to remain absent; no dismissal loop hides reopening.

Evidence is retained under
`output/afk-approved-20261017/mcp-clients-receipt832/`. Earlier fixture-readiness,
wrong desktop close-control, and product-reopening failures remain separate.
The original 65-scenario snapshot remains unchanged; the independent MCP case is
an additive owner, bringing the extended list to 84 and the source-workflow union
to 127 before any subsequent additions. This uses a real local MCP server and
cooperative SDK document, a synthetic host runtime and no provider credentials;
it is not live-provider or physical-device acceptance.
