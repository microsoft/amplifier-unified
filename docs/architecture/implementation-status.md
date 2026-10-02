# Implementation and acceptance status

Architecture implementation is active. The new distribution is an independently
installable integration candidate, not a qualified replacement for the entire
published product. No architecture candidate has been activated on Spark-1.
Source commits, fixture tests, installed packages, real accounts/devices and
installed production workers are different evidence boundaries.

## Landed boundaries

Fourteen new repositories contain independently owned source and tests:

- `amplifier-unified-client-web`, `amplifier-unified-client-tui`
- `amplifier-unified-host`, `amplifier-unified-protocol-extensions`, `amplifier-unified-interop`
- `amplifier-unified-capability-native`, `amplifier-unified-capability-resources`,
  `amplifier-unified-capability-media`, `amplifier-unified-capability-operations`
- `amplifier-app-acp`, `amplifier-session-catalog`, `amplifier-ahp-client-kit`
- `amplifier-publishing`, `amplifier-portability`

`amplifier-unified-capability-mcp` has independently tested local source, but its
requested remote is not accessible to the current GitHub identity. Its archive
can participate in isolated integration; remote landing remains incomplete.
Repository visibility has not been changed and no public package release is
implied. The old `amplifier-app-tui` repository is intact.

Foundation PR418 (`eed03cf62dc932b49fdea7d21275f8d08b0cf03e`) and PR419
(`c60c5fe59ae52b32b5cd327247fe2dbbe45cfea4`) are merged. Scheduling, operations,
worktrees and Recall are optional independently installable packages under
`libraries/`; the base Foundation package acquires no eager application store.
The original four-library installed-consumer checks passed on Python 3.11 and
3.13. Hosted CI and subsequent full-product qualification remain separate.

The distribution composes public artifacts. Product adapters for worktrees,
publishing, Recall and native maintenance live with the distribution; their
reusable mechanisms retain their separate owners. The component artifact manifest
records exact qualified candidates for reproduction and rollback, not permanent
pins. Managed deployments must resolve and qualify current component sources.

## Implemented paths and evidence limits

| Area | Implemented and exercised | Remaining acceptance or gap |
| --- | --- | --- |
| Native agent | ACP around full Core `AmplifierSession`, Foundation, modules and bundles; native controls and canonical history remain behind ACP | No `amplifier-agent` lite dependency. Each actual provider/account still needs its own run |
| Durable host | Official AHP/ACP SDK boundary, scoped subscriptions, receipts, recovery fences, bounded pending messages, lazy selected native history | Whole-system concurrency/latency and complete mixed-version activation gates remain |
| Web | Local persisted drafts and editors, official AHP state, bounded selected resources; two-viewer browser checks | Browser acceptance is recorded per feature; it does not imply TUI/device parity |
| TUI | New connected repository, private persistence and real terminal fixture qualification | Full multi-client journey and deployment-platform terminal checks remain |
| Catalog | Separate disk index, filtered root-session/workspace pages and bounded source discovery | Native CLI writer hints, import/rebuild/ownership cases must be accounted for before cutover |
| Resources | Canvas, immutable artifact bodies, shared app state and durable effects via a public owner | Legacy artifact migration and the complete renderer/device matrix remain |
| MCP | Independent installed broker, SDK transports, saved Apps, grants, account/OAuth binding and bounded source observation | Actual external accounts, installed callbacks, legacy registration/artifact import; remote repository access |
| Operations | Independent scheduling, operation output journal, required questions, quiet watches and typed admission | Full native process/browser end-to-end acceptance, legacy records import and active-turn question delivery breadth |
| Recall | Passive indexed native history, opt-in memory delivery and bounded independently stored notes | Current complete-note comparison is limited to 100 scoped notes; large-note policies and legacy import remain |
| Native maintenance | Independent generation inspect/check/prepare/select/rollback and exact receipt recovery | Distribution self-update, automatic preferences, backup/reset/repair/retention and support export are not replaced by this owner |
| Session lifecycle | Native-owned preserved-history tombstones, actual canonical context fork and immutable export | Host/client mapping and final edited-history proof must be qualified. Managed hard-delete is not implemented by soft delete |
| Publishing | Public library plus product adapter, selected workspace guards, immutable releases and reviewed local publication | Actual remote SSH/provider accounts and legacy publishing records |
| Portability | Independent library and contract tests | Product adapter, browser/agent surface, paired-host transfer and unknown-outcome reconciliation still require composed acceptance |
| Media | Public lifecycle owner, explicit client leases and selected native transcript recording | Physical microphone/camera/screen, voice providers and shared real-account acceptance |

Detailed owner receipts are authoritative for their exact commits. Counts from
separate suites must not be added into a claim that the complete system passed.
The distribution's tests visibly skip unavailable installed executables; a run
with skips is not the complete installed graph.

## Cutover requirements

The ratified [Q1–Q12 gates](delivery-plan.md) still apply. In particular:

1. Qualify two browsers and two real TUIs, fixed active sets with 25k and 100k
   histories, slow viewers, concurrent commands, cold selected history and cache loss.
2. Finish the feature ledger. Existing supported behavior needs qualified
   preservation or a separate explicit retirement decision. Design approval does
   not silently retire coordination, feedback, attachments, portability, maintenance
   breadth or any device feature.
3. Preserve and classify native history and old owner records. Unknown tool or
   publication effects are inspected, never recreated through prompt/tool replay.
   Ambiguous private client state remains recoverable rather than being reassigned.
4. Exercise actual vendor agents/clients and supported accounts. Installation or
   capability negotiation alone does not establish a successful authenticated turn.
5. Qualify an owned Spark-2 instance using its live development guide, then release,
   update, rollback and fresh-worker adoption. Coordinate production ownership;
   Spark-1 is not a development validation target.
6. Retire old full-state paths only after the supported migration and adoption
   boundary is explicit. Their continued presence during migration does not grant
   them the new architecture's performance claims.
