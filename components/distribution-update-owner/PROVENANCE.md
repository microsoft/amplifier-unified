# Source provenance and ownership boundary

This package is a TypeScript reimplementation of distribution-update behavior
from Amplifier Unified v0.20.48, source commit
`278a3abf69d77c14843884e66e67d4531a2a41b4`. It does not import or execute the old
Python service and does not vendor its native runtime generation implementation.
The repository MIT license is retained in this package.

Authoritative reference tree:
[Amplifier Unified v0.20.48](https://github.com/microsoft/amplifier-unified/tree/278a3abf69d77c14843884e66e67d4531a2a41b4).

| Reference file in that tree | Behavior carried forward | New owner boundary |
| --- | --- | --- |
| `amplifier_web/updates.py` | Immediate scheduler wake, install retained during checks, failed-check gating, parallel downloads, target locks and sibling settlement | `owner.ts`, `parallel.ts`; native update mutation remains external |
| `amplifier_web/update_checks.py` | Availability cache, access isolation, in-flight joins, fresh manual checks | `cache.ts`; bounded in-memory cache, no installation proof implied |
| `amplifier_web/app_updates.py` | Prepare and qualify the candidate that will actually launch | Required injected `ReleasePort.prepare/verify`; production installer is not copied |
| `amplifier_web/application_generations.py` | Retain current and previous qualified distribution identities | Private owner ledger stores opaque resolvable handles; no native generation pointer |
| `amplifier_web/app_replacement.py` | Durable replacement uncertainty before effects | `restart_requested` receipt and no automatic replay |
| `amplifier_web/update_readiness.py` | Exact release/source/dependency, instance and data identity; new process required | `LifecyclePort.inspect` and owner readiness comparison |
| `amplifier_web/update_diagnostics.py`, `amplifier_web/update_report.py` | Bounded receipts and allowlisted shareable facts | Safe projections exclude handles, raw configuration, exception text, paths, credentials and conversations |

Deliberate differences from the reference:

- The reference release exposed ecosystem rollback; it did not expose public app
  rollback. This new distribution owner supplies an explicit compare-and-swap
  rollback operation over its retained previous qualified application candidate.
- Preparation, provenance verification, service admission and readiness are
  injected public ports. The component does not claim to carry the old production
  installer's platform integration merely because its owner contract passes.
- Catalog and preferences are durable, while the generic availability helper's
  cached responses are in memory. Adapters may provide a separate safe durable
  availability cache without treating it as qualification.
- Native generations, worker repair, prewarming, native selection and native
  source currency remain solely the native owner and its maintenance capability.
- Archive membership/restore, reset, AHP registration, release publication and
  production deployment are outside this package. Its offline snapshot primitive
  qualifies stopped evidence and freezes/exports only its own supervisor ledgers.

The installed fixture acceptance is deliberately separate from production
service-manager acceptance; see the generated artifact receipt and README.

## Version 0.2 extension

The signed release adapter, authenticated loopback supervisor transport and CLI
are new TypeScript implementations over this package's public owner/lifecycle
ports. They do not reuse a legacy service endpoint or a private native runtime.
The integration contract and remaining production boundaries are documented in
`SUPERVISOR.md`.

Archive parsing uses the bundled `tar@7.5.22` package from
[node-tar](https://github.com/isaacs/node-tar). Its license and transitive runtime
dependencies are included in the npm artifact. The exact resolved dependency
graph is in `package-lock.json`; the adapter adds signed file inventory checks
before extraction and verifies the complete installed tree afterward.

## Version 0.3 host control

The external host-control adapter uses the public quiescence contract from
`microsoft/amplifier-unified-host` source
`fc28680e55d63f911df51277506ecd3f03bf508c`. It imports no host internals and adds no
host runtime dependency to this package. Its installed test qualifies an exact
independently packaged host plus held fixture participants. Production owner
coverage and service management remain external; see `HOST-CONTROL.md`.

## Version 0.4 runtime identity

The public runtime identity helper composes the existing signed channel and
installed tree validators with the actual Node entrypoint and launcher binding.
The installed supervisor and public-host fixture processes use this helper for
readiness and release proof. Separate child-process tests cover decoy signed
trees, entrypoint mismatch, altered dependencies/receipts, invalid launch values,
platform mismatch and readiness. No additional runtime dependency is introduced.

## Version 0.5 lazy supervisor discovery

The supervisor client adds an optional connection resolver and local discovery
notifications; the fixed connection API is retained. Installed CLI acceptance
constructs the lazy client inside the initial child before supervisor discovery,
verifies unavailable calls are not queued, and receives later pushed receipts.
Tests also replace endpoint/credentials while the old event stream is alive and
verify lost mutation responses are never automatically replayed. Provisioning
authority, host participants and service ownership are unchanged.

### 0.6 production supervisor composition

Added public production ports and runner with explicit, durable, one-use pristine
installation authority. Ordinary host inspection remains strict. Initial and
forward source qualification requires independent observations; signed metadata
is not treated as a live source read. Compatibility with operator adapter modules
is preserved. Qualification includes duplicate cross-process claims, interrupted
and uncertain launches, and an independently installed supervisor driving the
actual signed distribution CLI through upgrade, exact receipt read, and rollback.
No service adoption or native-generation currency is claimed.

### 0.15 instrumented manual systemd source

Adds private authenticated one-shot source authority, full configured held-owner
census verification, durable permanent old-launch retirement and retained Linux
pidfd exit evidence. The controller consumes that evidence through the existing
same-release handoff contract. It never calls systemd stop/start, signals the
source, adopts a numeric PID, or copies application state. The new ingress
participant owns only forwarding lifetimes and its private fence ledger; held
retention/disposal inspection is scoped to that adapter's empty business-reference
inventory and keeps receipt delivery available. Downstream owners remain required.

The packet includes a reviewable generic launcher composition and a minimal
HTTP/WS gateway hook patch. Separately installed Linux qualification uses an
actual public host, a disabled synthetic unit, kernel process exit and one new
owned destination. This does not qualify any live launcher transition or assembled
product owner census. Exact markers, root role and peer callbacks are documented
in `MANUAL-SYSTEMD-HANDOFF.md`. Manual update dispatch and pushed completion have
additional regression coverage with the background scheduler never started.

### Responsive settlement transport

The application-ready receipt and admission settlement remain separate. The
control transport now negotiates JSON whitespace keepalives and bounds silence,
instead of imposing a total ten-second deadline on runtime verification and
multi-owner release. No work replay, relaxed proof, native-generation mutation
or client-owned settlement is introduced. The installed fixture reopens the real
host ledger with an original 20-owner fence and a 21-owner replacement, exercises
slow settlement and explicit reconciliation through independently installed
packages, and preserves a failed pre-release attempt as unknown. Participants
and lifecycle replacement are synthetic; it does not claim a signed launcher,
separate-process replacement, production owner coverage or live deployment.
Signed candidate extraction also restores authenticated new-file modes before
verification, with a restrictive-umask regression. Existing installed bytes and
modes are still immutable qualification evidence; they are never repaired in
place to force verification to pass.

### 0.15.1 ingress recovery and distribution admission

Additive correction after full-census integration review: explicitly accepts
recovery maintenance while forwarding its initiating response and receipt reads;
keeps competing service/update authority held; requires original-instance exact
unchanged proof and preserves uncertainty across ledger reopen. The reachable
HostControl distribution-update path instead requires full forwarding drain and
closes intake. An explicit last-lifetime idle callback wakes the real host event
stream without waiting for a poll. Unsupported purposes remain refused. Tests use
the independently installed public host and exact package; no live state changes
or expanded cross-process recovery authority are claimed.

### 0.7 forward activation source observation

The signed distribution adapter reobserves channel and source currency after
held admission, including after a long wait, without rewriting retained receipts.
The public source port carries an explicit fresh observation context. Tests cover
advanced/protected/superseded sources, lost responses, unchanged activation,
offline rollback, interrupted admission phases, and the installed signed CLI.
This does not extend authority to native-generation or external Python inventory.

### 0.10 application release information

F12.03–.06 legacy behavior is owned by the supervisor: bounded current/history
notes, useful high-impact notice titles, content-bound durable review receipts,
and a published-release fallback link. Legacy sources were
`amplifier_web/release_notes.py`, `frontend/src/release-notes.jsx`, and
`tests/test_release_notes.py`. Explicit editorial title aliases preserve prior
review; changed action/detail does not. Histories retain verified skipped
releases without enumerating caches or joining native component state.

Public API is in RELEASE-NOTES.md; client expansion/filter/draft state is absent.
The signed channel optionally carries notes with no extra network request.
Notes hydration reads only the current signed installed receipt. Weekly
preferences are supported; manual dispatch does not wait for that interval.

### 0.11 explicit staged application activation

Adds terminal preparation and exact receipt-bound activation without changing
ordinary Install. One persisted staged slot pauses automatic installation until
an explicit activation or successful direct install/rollback; it does not turn
a saved candidate into process authority. Activation rechecks saved bytes and
live source eligibility under held admission. Tests cover persisted staging,
stale identities, supersession, admission, lost outcomes and receipt recovery.

### 0.12 offline supervisor snapshot

New offline-only coordination uses the package-owned SQLite ledgers and retained
qualified stop evidence. It records authenticated host participant IDs at stop,
refuses a live supervisor, holds both writer transactions through the caller's
capture and exports standalone SQLite backup images. It never migrates native
stores or treats absence as process ownership. Installer archive membership and
new inactive restore remain separate public composition responsibilities.

### 0.13 bounded owned-child startup diagnostics

Adds a small authenticated failure report on the existing private child IPC
connection. Entry initialization failure after the ownership handshake is
classified into static reason/guidance fields, without capturing output or raw
exceptions. POSIX ownership, one-use launch/stop authority and uncertain effect
semantics are unchanged. Distribution and service receipts preserve safe fields
through durable recovery; initial launchers receive a typed error. This is new
supervisor behavior, with no native-runtime dependency or service deployment.


## Version 0.17 extension

Adds a separate non-authoritative observed runtime/status read for UI and agent
clients. Existing fresh integrity verification remains unchanged for lifecycle
and update decisions. No verification cache, runtime rebaseline, repair authority
or live-installation migration is added. See `HOST-CONTROL.md` for composition,
wire schema, failure labels, qualification and consumer adoption requirements.

## Version 0.19 partial admission abort

Adds distinct abort intent/proof and exact owner settlement receipts for failed
distribution-update admission before native retirement. The public Host contract
is qualified against Host source `9329c570a1f21f7a079a385da122737c505358fa`.
Synchronous Node forwarding gates retain actual acquisition/refusal evidence;
legacy holds are not reclassified and missing peers remain unsupported. The
installed tests use real packaged Host/supervisor/facade/ingress and explicit
identity/fault fixtures, not a signed service replacement or complete production
owner census. See `ADMISSION-ABORT.md` for the contract and adoption limits.
