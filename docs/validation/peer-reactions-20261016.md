# Peer exchanges and assistant reactions

User-approved scope: #676 means incoming messages labeled peer, presented like
delegates. #745 allows Amplifier to react to a human message. The shared client
vision's economical feedback and host-derived authority govern these changes.

| Smoke case | Previous behavior | Desired / candidate behavior | Owning check |
| --- | --- | --- | --- |
| Receive peer input | Full message interrupts the conversation | One compact expandable exchange, no body render/read while collapsed | peer-attribution-browser |
| Open/copy peer input | Full message only | Keyboard expansion and exact Markdown copy; human lookalike remains ordinary input | peer-attribution-browser |
| Reveal peer from saved reference | Full message | Correct exchange expands; other client draft/selection unchanged | peer-attribution-browser |
| Reload / 320–390px / 200% zoom / light and dark | Caption-only qualification | Caption visible, no clipping, collapsed presentation survives reload | peer-attribution-browser |
| Partial older page | No proven sender when global uniqueness is unknown | Preserve neutral attribution and exact content; do not infer from local page | peer-attribution-browser; test_peer_attribution |
| Agent acknowledges human input | Reaction indistinguishable from user's | Visible Amplifier badge, no generated turn | test_message_interactions; peer-attribution-browser |
| Same emoji from user and agent; either removes its own | One ambiguous shared selection | Independent ownership and durable state across reload/restart | test_message_interactions; peer-attribution-browser |
| Agent targets peer/scheduled/assistant/another chat or supplies actor | Same-chat gate only | Refuse invalid target/actor, no annotation or runtime mutation | test_message_interactions |
| Older/focused/viewport reaction refresh | User reactions only | Both channels, bounded reads and explicit removals | test_message_interactions; test_transcript_windows; peer-attribution-browser |

The browser scenario is now an explicit additive extended-check owner. Frozen
historical inventory and original scenario hashes remain unchanged. These are
synthetic host-backed tests in the owned spark-2 DTU, not paid-model or macOS
native acceptance. A live model's decision to acknowledge with a reaction remains
provider-dependent; host action semantics and client presentation are covered.

Candidate evidence: 127 focused Python tests passed in 44.70s; 12 focused client
tests and 18 registry checks passed. The production client build and full browser
scenario passed, including an exact UTF-8 clipboard digest, deferred full-text
read, reveal, durable reload, and 320/390px light/dark layouts at 200% zoom.
The zoom check caught and corrected timestamp crowding of the peer source label.
The fixture now permits its real isolated shell-query helper while continuing to
reject every model/provider subprocess; the passing run used one query helper
and started no model. Evidence is retained in the workspace's
`output/decision-queue-20261016/`. Combined-release assets are qualified separately.
