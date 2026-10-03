# Implementation and acceptance status

Architecture implementation is active. The new distribution is an independently
installable integration candidate, not a qualified replacement for the entire
published product. No architecture candidate has been activated on Spark-1.
Source commits, fixture tests, installed packages, real accounts/devices and
installed production workers are different evidence boundaries.

## Current qualification boundary (2026-10-03)

The owned Spark-2 preview now serves distribution `e16f9dbb`, host `25a72556`,
native ACP `4d8982ca` and web `ff06d4b`. A fresh managed chat completed one
real OpenAI `gpt-6-sol` turn through public AHP and ACP into full
Core/Foundation, in about three seconds. The previous process and all its owned
children exited before activation; all 24 pre-existing canonical history files
remain byte-identical after startup and the acceptance turn. Rollback inputs are
retained. TLS and the deployed web scripts were verified. This checkpoint's
post-deployment browser interaction remains unverified; the matching immutable
web build passed the local browser gates. Spark-1 is unchanged.

This preview retains its existing manual capability subset and adds managed chat
creation. The full supervisor/background/cleanup/recovery owner census is not
activated there. An explicit existing-state service handoff is being qualified
separately; a manually launched process must not be treated as an owned signed
service. Installed candidates and the live preview are distinct acceptance scopes.
See [the checkpoint receipt](evidence/spark2-checkpoint-20261003.json). A single remote
chat rename produced one 347-byte update in 30 ms and completed its acknowledgement
in 44 ms, with no model call. This is a Mac-to-Spark-2 API sample, not a browser
paint or production-scale latency measurement.

The managed creation checkpoint has 689 passing web tests, zero skips, and source
plus independently installed static browser qualification. It covers browser-local
new drafts, two-client draft isolation, selected bundle/model discovery, explicit
first-send allocation, original receipt inspection after a lost creation response,
and independent history copies with fresh owned directories. Selected native
configuration reset requires exact private `RESET`, a reviewed immutable job/hash,
and renewed review after reload. Its installed browser archive fixture contains
2,130 manifest entries and 3,256,320 bytes in 13 verified chunks. These checks do
not qualify app-local product configuration reset or managed file removal.

The next source graph includes separately packaged managed-file reference guards
across 15 owners and durable native-cache jobs. They remain integration candidates.
All18-owner history hiding already passes the actual Core/Foundation distribution
fixture and a fresh installed Linux consumer without skips or agent warmup;
rendered cleanup controls passed source and independently installed static browser checks on web `8e63e81` (700 tests, zero skips). Native history, event logs and
product records remain preserved. The signed service fixture previously passed
with all17 configured owners on installed Node22, including real runtime
initialization and retirement without inference. None of those fixture results
implies full-product live deployment or real-account acceptance for every owner.

Foundation PR426 and PR427 are merged (`c356c969`, `467f870a`). PR428 is now
merged as `8462030d` after all six Linux/Windows Python3.11/3.12/3.13 jobs and
CLA passed. It adds a retained canonical session address that reopens the same
history/lease after a workspace is removed, without recreating it. It conveys no
operation authority. Spark-2 remains on the Foundation revision qualified for its
checkpoint; the next candidate will qualify the new merged dependency.

The 328-row inventory records 91 qualified offline browser rows, 151 implemented
but unqualified rows, 74 partial rows, six gaps and six pending decisions. No
pending decision authorizes retiring behavior. Full-product recovery, managed
file removal, app-local product resets, external-account/device checks and remaining client
parity still require their stated gates. The CSV and per-control receipts retain
those narrower limits.

The [reset scope audit](evidence/reset-scope-audit-20261003.json) corrects the
legacy parity boundary: app-local configuration and conversation presentation
were reset with retained originals; shared Amplifier settings, credentials and
workspace configuration were preserved. New execution receipts remain durable
no-replay authority. Optional retired duplicate-file removal already has a native
and host API; its trusted root binding and rendered controls remain unqualified.

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
Foundation PR425 (`211f2fcc6287a1365564350c276080ee0cda7a2d`) adds the optional
operations library durable intake fence. Sixteen installed tests passed on Python
3.11 and 3.13 and all 15 hosted checks passed before the authorized admin merge.
It owns only the caller-selected ledger; process ownership and external work
coverage remain each embedding owner's responsibility.

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
| Distribution updates | Independent durable supervisor, authenticated private control, actual signed CLI startup, complete configured owner fencing, upgrade/rollback and exact receipt recovery | Release publisher/source qualification, external Python inventory and actual service-manager activation remain separate |
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
2. Complete the control-level acceptance in the [328-entry feature ledger](feature-migration.csv).
   Every requirement from the existing settings inventory has an assigned owner,
   disposition and evidence boundary; this inventory coverage is not a migration
   acceptance pass. Existing supported behavior needs qualified
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


## Earlier twelve-owner recovery qualification

The earlier assembled recovery check used actual Core/Foundation with 12 configured
capability owners: native administration, resources, workspaces, media, MCP,
operations, coordination, worktrees, publishing, Recall, feedback and recovery.
It retired the native worker, held all owner intake gates, exported reviewed native
bytes, checked the archive SHA and reopened intake using durable native lease
release evidence. It made no model call and does not qualify full-product backup.
The independently lived application-update facade has separate held-forwarding,
process ownership and exact release tests. The later signed-launch qualification
below supersedes the then-outstanding CLI replacement gate.

Web `85ef2cb` passed 537 unit tests and actual-native source/unpacked-static browser
checks for bounded workspace and root-chat pages, missing-directory/child
exclusion, exact lost-create recovery, persisted dirty compare-and-swap editors,
explicit reattachment and history-preserving removal. Navigation projections no
longer copy selected chat bodies: an 8,000-message fixture reduced serialization
from 15.9 MB for one old projection to about 35 KB for all narrow projections.
The observed cost changed from 16.1 ms to 0.66 ms in that local fixture; these are
not production latency percentiles. Long-history browser checks retained only 80
mounted messages across 4,000 loaded turns and produced no typing frames.

Web `9cf68ba` passed 541 unit tests plus installed-owner and clean static browser
checks for imported historical operation evidence. Pages contain 25 bounded
summaries; only explicitly selected immutable bodies are loaded. Source hashes
remain unchanged, saved running/pending states remain inert and no historical
entry starts a worker, prompt or control. Explicit fixture chat creation is a
separate operation and is not counted as history-driven work.

### Workspace browsing and durable release-proof qualification

The assembled distribution now includes host `13bd19a`, catalog `b194a37`,
workspace owner `48cb829`, resources `60a7cd5`, media `510f228`, MCP
`9c16dc7` and worktrees `5f60dd3`. Independent installed consumers passed
210 checks without skips; the assembled distribution passed 21 integration
checks without skips, including native Core/Foundation execution, signed
two-host transfer and selected recovery across all configured owners.
Workspace directory browsing is authorized and paged, creation defaults use
durable compare-and-swap, and archive selection no longer changes missing-directory
or hidden-project visibility. Directory scans reject more than 10,000 entries and
omit hidden paths and symlinks explicitly.

A release retry now requires the same recorded proof and outcome through process
replacement. Legacy released records without a proof signature remain unconfirmed;
they do not authorize another effect. The test suite retains actual child ownership
on Node 22 and uses isolated installed Python imports, including subprocesses,
so the old monorepo cannot supply a different implementation. Browser recovery and
workspace controls have separate acceptance; these results do not establish deployed
worker, remote account, or device acceptance.

### Signed application launch and native transfer intake

Supervisor 0.5.0 (`a7a8d8fa`), native `dfd06ad`, native bridge `b9300f3`,
portability `13653afb` and media `fd5d97c` are integrated. The assembled graph
passed 24 tests without skips, including the actual independently packed application
CLI launched from a signed installation, startup before supervisor discovery,
an immediate check/install request, actual process replacement, the exact original
receipt in the replacement and rollback. The test uses a local fixture publisher;
it does not establish production source qualification or service-manager adoption.
The separate supervisor package passed 53 tests on Node 22 and Node 25.

Selected native recovery now acquires thirteen configured owner participants,
including the complete portability callback/process lifetime, before snapshotting.
The transfer peer closes local intake before the shared native admin writer gate.
Only the exact administration engine may participate in this single-authority
composition. Active probe subprocesses independently hold writer leases, including
after their parent exits; unmanaged probes cannot claim equivalent coverage.

Passive voice configuration no longer resolves credentials or starts a worker.
The native owner reports disabled, missing and configured availability explicitly;
the exact sanitized read remains available through its held maintenance gate.
Credential/configuration mutations retain their existing authorization and gate.
Media passed 24 source tests and nine independent installed configuration/lifecycle
tests; native voice metadata passed eleven installed tests and the bridge seventeen.

Web `8008f3a` passed 551 unit checks, the core two-client browser journey, and
source/static-package recovery runs against the prior twelve-owner graph.
The 768,000-byte selected archive used three bounded resource chunks and a final
SHA check. Stale previews were refused, a lost snapshot admission was recovered
without recapture, selected configuration reset/undo preserved canonical history,
and no model/runtime was resumed. Optional owner reads and client-tool attachment
refusals no longer prevent readable core/recovery connections while intake is held.
Web `b0f8506` subsequently passed 558 unit checks, the core two-client browser
journey, expanded workspace controls and disabled-voice recovery, with independent
static-package repeats. Directory pages, default CAS, lost mkdir/default replies,
archive domains and held recovery remain usable with no voice credential grant.

### Global session queries and optional notifications

Workspace owner `ae77023f` adds an explicitly advertised, bounded global session
query. Omitted workspace selection queries authorized existing visible directories;
an invalid explicit selection is refused. Archive/search/root filters precede
keyset pagination, and cursors remain bound to client and filter. Four actual
host/catalog tests pass from source and installed Node artifacts; fourteen installed
Python checks pass. Five installed source files were byte-verified. No query starts
an agent, reads native events or changes standard AHP `listSessions` semantics.
The broader browser pagination/search journey has a separate acceptance receipt.

Notifications owner `85eb183` is composed as an optional independent participant.
The default-disabled path skips session/result/credential reads. Completed turns
use owner policy, while schedule attention requires Operations' explicit notify
decision. Shared settings/receipts are redacted, preview is opt-in, and uncertain
delivery is never resent. The independent owner passed sixteen Python and four
Node checks, including local TLS, process interruption and two actual AHP clients.
The assembled completion/policy test uses its real installed owner with a fixture
transport. No external notification account or device display is claimed.

With these components, all 25 assembled tests passed without skips. Recovery
acquired all fourteen configured participants, including notifications and
portability. Actual signed CLI upgrade/rollback and two-host native transfer also
passed. The application-update panel separately passed source/static browser
qualification against a private authenticated supervisor and installed distribution;
its lifecycle ports are fixtures, not production service-manager acceptance.


### Native archive plans, current source checks and control inventory

Native adapter `6e614bac`, native bridge `66340e1` and recovery owner `246cc342`
add disk-backed immutable archive plans, bounded selection and manifest pages.
The assembled graph with supervisor 0.7 passed 30 checks without skips, including
both session recovery and explicitly selected workspace/shared configuration
recovery across fourteen owner participants. This remains selected native backup;
other owners' independent stores are not implied to be included. Web `5feb602`
separately qualified source and static-package review of 2,130 entries in pages
of 25 and a 3,246,080-byte archive in thirteen checked chunks. That browser fixture
used twelve owners; it does not replace the separate fourteen-owner test.

Supervisor 0.7 (`11e1e5f9`) has explicit pristine initial provisioning and a final
forward-activation qualification under held admission. A waiting candidate must
still match a freshly authenticated channel and independently observed configured
sources before restart. Refusal releases the unchanged installation; rollback
retains its offline byte-verification authority. Independent package suites passed
69 checks each on Node 22 and 25. This is not service-manager crash adoption.

The distribution's public Git source resolver queries an explicit repository/ref
allowlist independently of publisher claims, with bounded process concurrency,
output and deadlines. Four contract tests passed; a separate real read-only
Foundation remote observation resolved `211f2fcc`. A configured URL rewrite was
refused. The successful isolated probe used explicit task-owned Git configuration
and did not modify the user's configuration. Registry-owned and external Python
source currency need their respective owners and remain separate.

The [control migration ledger](feature-migration.csv) contains all 328 requirements
from the preserved settings inventory. Its [per-control audit](evidence/control-audit.json)
against web `1ec0024` records 32 narrowly qualified offline-browser controls,
56 partial controls, 159 implemented but unqualified controls, 75 gaps and six
pending decisions. These counts are an audit snapshot, not a whole-product pass.
Each row retains its exact acceptance scope and evidence. No feature retirement
is inferred from an absent handler, unavailable backend or unanswered question.

Catalog `31a3a3f` adds bounded, cold retention candidate pages and all-kind native
family guards. The sealed wheel passed 44 tests on each of Python 3.11 and 3.13
from independent installations. Candidate age never authorizes removal; native
writer exclusion and exact reviewed source evidence remain host responsibilities.
Native `120a5b7` includes scoped permissions and full-operation writer exclusion
through long archive work; its 100 installed checks passed with no skips.
All seven scoped permissions controls passed source and independently installed
static two-client browser checks against distribution `6cfdcd76` and native
`120a5b7`. Staged update browser acceptance remains in progress.
