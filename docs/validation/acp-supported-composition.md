# Supported ACP pilot composition

This is the public summary of the bounded synthetic ACP pilot completed on
November 16, 2026. It records the combinations exercised and their limits; it is
not a deployment pin, full ACP conformance claim or live-adoption decision.
Use the [installed backend guide](../architecture/unified-acp-backend.md) for
current setup, [AP](../../contracts/agent-protocol.v1.md) for the promises, and
[protocol fit](../architecture/protocol-fit.md) for capability boundaries.

Conversations owns the shared transcript, membership and room grants. Instance
owns the continuing participant and its attachments. Unified owns native
execution, input receipts, writer ownership and Stop. The pilot used those
existing owners, with distinct native identities for two participants. It did
not introduce another conversation store or recovery protocol. The
[Conversations room contract](https://github.com/microsoft/amplifier-constellation-conversations/blob/8b1e01417d3d1d15729ec663d67cd894a988af09/docs/shared-rooms.md)
describes the source-side ownership and removal policy.

## Recorded fresh-grant context read

| Part | Exact recorded selection |
| --- | --- |
| Unified native ACP backend | [0.100.4, `e70babd692b4`](https://github.com/microsoft/amplifier-unified/tree/e70babd692b40972eed93459b58576c3d0c83ad3); installed published wheel, not the standalone ACP application |
| Constellation Instance | [0.22.9, `add22caad3be`](https://github.com/microsoft/amplifier-constellation-instance/tree/add22caad3beb0da544d5e58cffddc1eff4f101c); published Linux ARM64 binary reports build `19600cea3c6a`, whose full source tree equals the accepted tag's tree |
| Constellation Conversations | [0.14.1, `8b1e01417d3d`](https://github.com/microsoft/amplifier-constellation-conversations/tree/8b1e01417d3d1d15729ec663d67cd894a988af09); exact published Linux ARM64 asset and tag verified; the binary contains no compiled-commit witness |
| Native composition | Explicit Work profile with [canonical MCP 0.3.1, `77bba0469396`](https://github.com/microsoft/amplifier-module-tool-mcp/tree/77bba04693963b0fb24ce6fe1313c39c35e2d3be), a deterministic local provider, and normal native preparation; actual workers bound to the qualified 137-distribution graph including Core 2.0.2 |

This composition used ACP v1 new/prompt and optional exact-ID resume, plus a
transient HTTP MCP attachment. The explicit profile supplied MCP; the result
does not establish that every default or custom profile includes it. No paid
model, live account, browser or background job was used in this case.

Three public inputs established A and B, then requested B's context read after
removing A while the room was idle. The retained runtime evidence establishes:

- A's removal preserved B's room binding and native ID. Old room-grant hashes
  were revoked, and B resumed that same native ID with a fresh room-only grant.
- The actual mounted MCP `context_read` returned the correct provider and room,
  all four frozen seed/reply rows with their source identities and text, and the
  new read input. It was one invocation: one tool start, one successful result,
  no tool error, and matching Instance/source read audits.
- A's registered credential was refused for an explicit read of the removed
  room. This was a removed-participant credential check, **not** a request using
  an intercepted old bearer grant.
- The native input counts were A: one and B: two. Canonical prompt-start and
  prompt-finish events matched those inputs; all finished normally. B had one
  new session and one resume, without replacement, Load, cancellation or replay.

**The original runner exited with a failed assertion after the successful read.**
It incorrectly counted `session/prompt` in Instance's generic `acp.activity`
events; that owner deliberately records prompts in `acp.prompt.started` and
`acp.prompt.finished` instead. Separate read-only checks of the retained native
events and durable Instance/source ledgers verified the facts above after normal
shutdown. No application was restarted and no input was repeated. Unreached API
checks were not reported as passed; final fixed-file equality and worker graph
lineage were checked separately from saved evidence. This is a qualified narrow
result from a failed harness run, not a replacement passing end-to-end run.

## Earlier lifecycle evidence remains separate

These completed cases supply complementary evidence. They were not rerun or
relabeled as the 0.100.4 composition above. All used isolated synthetic state
and deterministic providers; preserved fixture failures remain part of their
records.

| Case | Recorded composition | Supported observation and limit |
| --- | --- | --- |
| Viewer loss and reconnect | Standalone ACP 0.1.5; Conversations 0.12.8 (`254e9d616137`) | Accepted work produced one effect while the browser was closed; zero established viewer connections were observed throughout the sampled interval. Passive reconnect preserved room/native IDs and produced no new provider/tool work. This is not host-loss survival; the browser used the fixture's self-signed TLS exception. |
| Unfinished work and graceful shutdown | Unified 0.99.4 (`e6edf4c391de`); Instance 0.22.7 (`92a0c9753988`); Conversations 0.14.0 | Observation release and stopping the source service left the native job owned. Graceful shutdown of the native owner cancelled and saved it; reopening recovered that disposition without redispatch, then resumed the same ID for new input. It does not promise work survives owner shutdown. |
| Independent participant owner loss | Unified 0.99.6 (`42486190958e`); Instance 0.22.7; Conversations 0.14.0 | Retained evidence supports A's exact-ID recovery and B's independent completed job/effect. The runner later failed on a provider-count assertion; later process-residency checks were unreached. This is not universal exactly-once or machine-crash acceptance. |
| Removal with independent pending jobs | Unified 0.100.2 (`24d4eba18b97`); Instance 0.22.7; Conversations accepted removal change `731c36f42760` | At room idle, removing A retained B's binding and revoked the old room grants. B's attachment was truthfully refused while its job still owned the old tools, without prompt/replay or stopping it. B completed once, settled, and resumed the same ID with fresh authority; explicit native Stop cancelled A. The remaining single-agent plain-text turn succeeded. Three earlier fixture failures remain preserved; the final scenario passed. |

The Conversations 0.14.0 binaries in the shutdown/loss cases reported build
`42a42e59f3b9`; their accepted release source was `52f0ee770ad2`. The removal
case used a CI binary built from `56670e3384fd`, with its tree verified equal to
reviewed `b6174396d8ae` and accepted `731c36f42760`; it was not the later 0.14.1
published asset. Native IDs, job/input records and observed effects are the
continuity claims; worker PID residency is not.

## Later source review and remaining boundary

Source review through [Unified 0.105.0, `c5cd4b57655f`](https://github.com/microsoft/amplifier-unified/tree/c5cd4b57655f40992fe699ef8cc8c036726429b7)
and documentation base `f3fc2837f338` found the native `/api/acp` attachment and
exact-ID resume path retained. Later approval forwarding and optional
Instance-owned matching policy are described by the current backend guide;
the recorded cases above do not qualify those changes. This closeout performs
no new runtime test or policy change.

The [retired Unified `/api/companion` observation adapter](https://github.com/microsoft/amplifier-unified/commit/08b102fac773038ba78ab3f8551b8d05caf2dfbd)
is distinct from **Constellation Companion**, the separate voice experience.
Its replacement by generalized client observations does not retire `/api/acp`.
This source finding neither resumes nor qualifies Companion voice work.

The bounded pilot's ordinary participation, removal, fresh-grant retrieval and
named lifecycle outcomes are recorded above. Concurrent participant/source
removal versus input admission remains a named follow-up: qualify the serving
owners' accepted/refused outcome, current authority and preserved identities
without implicit retry. Do not infer that race from sequential removal tests.

Full legacy `session/load`, complete media/resource replay, client terminals,
arbitrary host-loss survival, live-provider behavior, broad feature parity and
production adoption remain outside this record. Existing Stop and room
serialization policy are unchanged. There is no new release gate or requirement
to repeat the completed cases merely because a later version exists; assess
changed owners and qualify the affected boundary.

Raw synthetic transcripts, local paths, runtime identifiers, custody hashes,
original failures and independent reviews remain in the retained qualification
archive. This public summary intentionally excludes credentials and private
machine records; it does not present that archive as a public reproducible test
suite. Temporary runtime resources were retired after review.
