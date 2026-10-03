# Implementation and acceptance status

Architecture implementation is active. The new distribution is an independently
installable integration candidate, not a qualified replacement for the entire
published product. No architecture candidate has been activated on Spark-1.
Source commits, fixture tests, installed packages, real accounts/devices and
installed production workers are different evidence boundaries.

## Landed boundaries

Fifteen new repositories contain independently owned source and tests:

- `amplifier-unified-client-web`, `amplifier-unified-client-tui`
- `amplifier-unified-host`, `amplifier-unified-protocol-extensions`, `amplifier-unified-interop`
- `amplifier-unified-capability-native`, `amplifier-unified-capability-resources`,
  `amplifier-unified-capability-media`, `amplifier-unified-capability-operations`,
  `amplifier-unified-capabilty-mcp` (rename pending)
- `amplifier-app-acp`, `amplifier-session-catalog`, `amplifier-ahp-client-kit`
- `amplifier-publishing`, `amplifier-portability`

The MCP source is landed in the newly created
`microsoft/amplifier-unified-capabilty-mcp` repository. The requested canonical
name is `amplifier-unified-capability-mcp`; the current identity has push access
but no GitHub admin permission to correct the typo. Its component receipt names
the actual source and records the requested spelling separately. No repository
visibility or public package release changed. The old `amplifier-app-tui`
repository is intact.

Foundation PR418 (`eed03cf62dc932b49fdea7d21275f8d08b0cf03e`) and PR419
(`c60c5fe59ae52b32b5cd327247fe2dbbe45cfea4`) are merged. Scheduling, operations,
worktrees and Recall are optional independently installable packages under
`libraries/`; the base Foundation package acquires no eager application store.
The original four-library installed-consumer checks passed on Python 3.11 and
3.13. Hosted CI and subsequent full-product qualification remain separate.
Foundation PR424 (`ecea0132c2e46045957a62fa0259ef239424af83`) adds indexed
session-scoped original schedule command results for lost-response recovery.
Its nine installed checks passed on Python 3.11 and 3.13, and all 15 hosted checks
passed before the user-authorized admin merge. The base Foundation API is unchanged.

The distribution composes public artifacts. Product adapters for worktrees,
publishing, Recall, portability, feedback, coordination, workspace management,
distribution supervision and native maintenance live with the distribution; their
reusable mechanisms retain their separate owners. The component artifact manifest
records exact qualified candidates for reproduction and rollback, not permanent
pins. Managed deployments must resolve and qualify current component sources.

## Implemented paths and evidence limits

| Area | Implemented and exercised | Remaining acceptance or gap |
| --- | --- | --- |
| Native agent | ACP around full Core `AmplifierSession`, Foundation, modules and bundles; native controls and canonical history remain behind ACP | No `amplifier-agent` lite dependency. Each actual provider/account still needs its own run |
| Durable host | Official AHP/ACP SDK boundary, scoped subscriptions, receipts, recovery fences, bounded pending messages, lazy selected native history | 32-client/eight-agent scale and actual slow-socket isolation passed; selected installed native history is bounded; mixed-version activation remains |
| Web | Local persisted drafts and editors, official AHP state, bounded selected resources; two-viewer browser checks | Browser acceptance is recorded per feature; it does not imply TUI/device parity |
| TUI | New connected repository, private persistence and real terminal fixture qualification | Two real TUIs plus two Chromium clients passed shared-session/private-draft isolation; deployment-platform terminal checks remain |
| Catalog | Separate disk index, filtered root-session/workspace pages, bounded source discovery and opt-in durable CLI location hints | CLI PR367 is green but awaits normal review; import/rebuild/ownership cases remain |
| Resources | Canvas, immutable artifacts and shared app state; resumable disk attachments with URI-only history; installed browser upload/recovery and selected ACP content; explicit offline document/version import with source preservation | Interactive legacy Apps import, transfer attachment-body omissions, large remote attachment resolution and the complete renderer/device matrix remain |
| Workspaces | Durable independent registration, reviewed name-based creation, display names, history-preserving hide/reattach and shared indexed pages; two installed clients with zero agent starts | POSIX currently; browser placement and host restart/recovery qualification are separate |
| MCP | Independent installed broker, SDK transports, saved Apps, grants, account/OAuth binding and bounded source observation | Actual external accounts, installed callbacks, legacy registration/artifact import; repository spelling correction |
| Operations | Independent scheduling, operation output journal, required questions, quiet watches and typed admission | Full native process/browser end-to-end acceptance, legacy records import and active-turn question delivery breadth |
| Coordination | Indexed root pages, selected worker pages, durable scoped follow-up/interruption, bounded report waits, actual Core/Foundation persistent child and real question/task attention | Actual native/static browser passed scoped controls during an outstanding wait; old unindexed worker reports remain explicitly unavailable |
| Recall | Passive indexed native history, opt-in memory delivery and bounded independently stored notes | Current complete-note comparison is limited to 100 scoped notes; large-note policies and legacy import remain |
| Native maintenance | Independent generation inspect/check/prepare/select/rollback, resident evidence, separate repair reconstruction and exact receipt recovery | Distribution self-update, automatic preferences, backup/reset/retention and support export remain separate |
| Distribution updates | Separate durable supervisor owns release checks, preparation, preferences, rollback and proven process replacement; optional host capability facade survives client/app loss | Authenticated production supervisor transport, exhaustive host quiescence coverage and actual service-manager activation remain in progress |
| Session lifecycle | Native-owned preserved-history tombstones, actual canonical context fork and immutable export | Actual native/browser fork, edit, preserved source, canonical export, remove and restore passed. Managed hard-delete is not implemented by soft delete |
| Publishing | Public library plus product adapter, selected workspace guards, immutable releases and reviewed local publication | Actual remote SSH/provider accounts and legacy publishing records |
| Feedback | Independent indexed owner, private client drafts, bounded immutable uploads, exact excerpt consent, non-destructive corrections and unknown-delivery reconciliation; actual native browser and unpacked static-client fixture checks | Explicit paged offline legacy receipt import passed; actual GitHub identity and user-data migration remain separate |
| Portability | Independent library; byte-preserving cross-language evidence; native Foundation transfer fences; historical-only operation/resource imports with explicit omissions | Two actual installed native hosts passed signed transfer, reviewed omissions, source fencing, canonical-byte preservation and inactive evidence import. Browser/agent surface and remote-host acceptance remain |
| Media | Public lifecycle owner, explicit client leases and selected native transcript recording | Physical microphone/camera/screen, voice providers and shared real-account acceptance |

The installed distribution graph passed 17 tests without skips with host
`9d2e5de`, native adapter `198efe6`, native capability `00ef02d`, operations
`ec9979c`, Foundation schedule receipts, independent workspace/feedback/coordination
owners and exact signed-capsule review. It verifies cross-client workspace state,
preserved hidden history, original schedule results, and supervisor continuation
after child-facade closure. The separate fresh-consumer receipt records the exact
packaged distribution and client artifacts tested outside the source tree.
This includes actual Core/Foundation execution with an offline provider, a real
MCP SDK subprocess/watch, attachment resolution and two-host signed transfer.
The transfer preserved source/destination canonical transcript bytes, retained the
source fence and imported questions/artifacts as historical evidence only. Source
provider audit bytes were unchanged by the transfer; destination model calls were
limited to the two explicit readiness probes.

The graph additionally proves that agent-origin coordination remains an agent
message in AHP, cannot be read as a user-consent message, and cannot cross into
another conversation. Question attention uses a covering index (64 IDs with
explicit overflow) instead of hydrating saved question bodies. The operations
package passed 41 Python and three subprocess tests; coordination passed six
installed Python and four installed Node transport tests, with its separate
actual persistent-child run recorded against current Foundation/module sources.

Web `548aded` passed 518 frontend tests, the native configuration journey, mixed
agent configuration and absence of native administration, both from source and
from its unpacked static release. Cancellation during a queued configuration
change took 135 ms from source and 136 ms from static assets. Independent AHP
capability requests no longer wait in a global frontend command queue. These are
individual fixture timings, not production percentiles. Private settings recovery
also passed a dropped model-change reply, reload, and exact original receipt;
no change was replayed and no credential/configuration values entered the journal.
Earlier native feedback
acceptance remains separately recorded. Reload-safe private files
and editors, send-only uploads, lost-reply recovery, exact excerpt disclosure and
capability absence are covered. GitHub writes used an owned local fixture; this
is not actual-account feedback qualification.

The installed feedback importer also passed 11 checks covering bounded pages,
unchanged source bytes, current-owner conflicts, exclusive ownership and exact
legacy receipts. In-flight legacy sends become unknown and are never resent.
No user database has been imported automatically.

Resources `13728ef` passed 22 owner checks and four independently installed
legacy-document migration checks. One selected source mapping is read-only;
version bodies/hashes and IDs survive, reruns preserve destination edits, and
ambiguous/unsupported Apps remain retained. This is migration-tool qualification,
not evidence that any user data has already moved.

Two actual Chromium clients and two Ratatui terminal processes passed the joint
isolation test against host `7719146` and web `a5f14c4`: private drafts survived
reload/restart, typing produced no browser protocol frames or server sequence
updates, and commands reached only their intended conversations.

A separate fresh native generation (`12f2a2644ad844eb951cd47229060072`)
qualified current configured source heads, registry Core 2.0.1 and 98 installed
packages. One bounded actual OpenAI provider turn returned
`AMPLIFIER_NATIVE_CURRENT_OK` with no tools. This ACP/account result is separate
from browser, device and deployed-worker acceptance.

The published Claude ACP adapter completed one actual authenticated turn through
the installed host, returning `OK` with no tools. The published Codex adapter
negotiated and reached execution, but its advertised model was rejected with HTTP
400; it did not complete successful inference and was not retried. Copilot and an
independent production AHP client remain unqualified.

The catalog's fresh-process synthetic benchmark at 25k and 100k sessions (4k
project rows, 100 present directories) measured 50-item page p95 at approximately
2.96 ms and 2.39 ms and RSS approximately 24.4 MiB. This is a warm-filesystem
component measurement, not browser responsiveness or production performance.
The combined installed host/catalog fixture used 32 AHP clients and eight active
ACP fixture agents with the same 4,000/100/1,000 project/directory/root-session
shape. Standard title-action notifications reached all four subscribed viewers
at p95 27.56 ms (25k sessions) and 11.39 ms (100k); 50-item catalog requests were
43.46 ms and 38.50 ms. Each round separately checked read-visible catalog
convergence (p95 152.61 ms and 58.80 ms). Action echoes are not catalog disk
receipts. The test verified exact prompt counts, no dormant starts, no event-log
opens and upstream action schemas. Workstation scheduling and warm filesystem
caches apply; Node memory includes the benchmark clients. These scale runs do not
establish browser paint; separate selected-history and slow-socket evidence follows. The reproducible
receipt is [combined-scale.json](evidence/combined-scale.json), from interop
`37b7a962ffb363ebeb315dd4bbe919dfecd9eff0`.

A separate [working-set receipt](evidence/working-set.json) records host
`7070393` and interop `218e8d7`. With 32 real WebSocket connections and eight
fixture agents, a paused socket was closed with code 1013 while the healthy
viewer received the complete 7.5 MiB response; 30 uninterested clients received
no chat events. Pending intents stopped at 50, reconnect cursors at 4,096, and
replay at 64 events. Execution completed exactly once with every viewer detached.
The actual installed native passive reader loaded 50 selected turns from a
10,000-turn transcript by reading 128 KiB of its 6.94 MB file. Its 700 indexed
children were read in pages of 25, with no event-log reads, execution-agent starts
or changed history hashes. This exact native reader was `07e7724`, independently
verified against all 115 installed Python source files. A generic AHP client
omitting a bounded view deliberately retains complete standard history semantics;
1,500 fixture turns were qualified, not arbitrary full-history memory bounds.

Browser coordination uncovered root-channel request serialization: a 30-second
wait held unrelated controls until its read finished. Host `ad94a4a` separates
independent capability reads/actions after handshake while preserving standard
resource action ordering. Against the actual installed native worker, the
unpacked static web release answered Follow up in 59 ms, receipt inspection in
42 ms and Interrupt in 50 ms with the wait outstanding. Exactly one native
follow-up and interruption arrived. These are individual acceptance timings,
not percentile performance estimates or production measurements.

Native repair `1cbd41d` passed 312 tests (one optional skip) and rebuilt a separate
current-source generation with 98 distributions plus actual Core/Foundation
profile qualification, without a prompt or activation. Original pointer, source
receipts and canonical sessions remained unchanged. The public maintenance
adapter separately exposes review, reconstruction, pointer-CAS selection and
resident-only evidence. Five adapter tests include actual ACP administration;
complete product repair UI and installed distribution replacement remain distinct.

Loop-live PR12 merged normally after all six hosted checks; an isolated actual
Core/Foundation/ACP/host steering test passed against that public merge. Its
frozen Foundation graph is recorded separately from current-source qualification.

Detailed owner receipts are authoritative for their exact commits. Counts from
separate suites must not be added into a claim that the complete system passed.
The distribution's tests visibly skip unavailable installed executables; a run
with skips is not the complete installed graph.

## Cutover requirements

The ratified [Q1–Q12 gates](delivery-plan.md) still apply. In particular:

1. Extend the passed two-browser/two-TUI qualification to fixed active sets with
   25k and 100k histories, slow viewers, concurrent commands, cold selected history
   and cache loss. Component benchmarks do not replace whole-system measurements.
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
