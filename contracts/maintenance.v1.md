# Maintenance and recovery — v1

Clause prefix: **MA**. Parent: [family vision](../docs/architecture/VISION.md).
Implementation guidance under the approved protocol and ownership direction.
This contract binds public package interfaces and trusted local control; it adds
no private replacement for AHP or ACP semantics. The [status ledger](../docs/architecture/implementation-status.md)
separates source, installed fixture, browser, account/device and deployment evidence.

## Who builds against this

Distribution, host, native adapter, capability, client and release maintainers.

## The promises

1. **MA1 — Own the installation's complete local lifetime.** One installation
   supervisor, outside the runtime it replaces, serializes all configured launch,
   restart and update entrypoints. Its custody covers the runtime's entire local
   process tree and writers, including native agents, tool children, callbacks,
   threads and background work. Co-located components use one shared admission
   and drain boundary; repository count, topics and capability count do not create
   separate update participants. Independently managed writers must declare their
   actual storage domain and exclusion mechanism. Missing local custody prevents
   replacement. A viewer closing, an idle UI or a parent process exiting is not
   proof that the installation's writers have stopped.
   Broken: the application exits while its tool child continues writing history.

2. **MA2 — Drain accepted work and exclude actual writers.** Before stopping,
   persist the replacement intent and close fresh application work admission.
   Already accepted work, including its necessary callbacks and child activity,
   retains its admission while it finishes and flushes durable state. Count actual
   local activity, not disconnected viewers or only unsettled promises; cancelling
   an await does not stop its thread or child. Ordinary update deadlines never
   authorize interruption. A separately authorized interruption may stop only the
   exact owned process tree through the same supervisor lifecycle, retaining
   interrupted/unknown outcomes without treating them as drained or completed and
   without replay. Whether drained or explicitly interrupted, prove whole-tree
   exit before a successor can write; this adds no separate rescue lifecycle.
   Acquire real independent storage-domain locks before opening or recovering the
   corresponding shared state. Keep in-process transaction integrity and separate
   authorities' existing locks; do not add per-capability lifetime leases for
   co-located code. Native transfer and whole-home administration retain their
   ordered, exclusive domain gates for those operations, not every ordinary update.
   Broken: replacement starts after its parent exits but before a native child or
   independent writer relinquishes the same storage domain.

3. **MA3 — Separate custody from business outcomes.** Bind the durable
   administrative operation to its command, installation, data scope, prior
   instance/generation and exact target release. Record intent before effects and
   verified custody, activation and admission results before acknowledging them.
   Repeated commands inspect the same operation; lost acknowledgements do not
   authorize another launch or business dispatch. A retained unknown business or
   remote outcome remains unknown and is never replayed or declared successful
   because a replacement is healthy. It does not by itself prevent local restart
   once local writer exclusion and safe state initialization are proven. Uncertain
   local custody still blocks replacement. Existing data-maintenance gates retain
   their exact `fenceId, commandId, purpose, instanceId, dataScope` context and
   authenticated outcome proof; only owner-proven refusal before effects permits
   their no-effect unwind. A generic exception or another owner's refusal is not
   that evidence. Ordinary update has one installation activation barrier, not a
   reverse release chain for every capability.
   Broken: a lost remote publish reply either freezes a proven-stopped installation
   forever or is changed to success to permit a local restart.

4. **MA4 — Verify custody, release and readiness separately.** The supervisor
   selects qualified immutable release bytes under its exclusive installation
   authority, proves the old complete local process tree is stopped, and starts
   one successor. The successor acquires required storage-domain ownership and
   initializes retained state with application work intake closed. Its readiness
   binds the signed installed entrypoint/inventory, actual new instance/generation,
   release and data scope. Only then does the supervisor commit activation and
   open the single work-admission barrier. Process-manager health, an endpoint,
   caller labels and the requested target alone cannot prove these facts.
   Qualify each role's actual runtime/interpreter and entrypoint from its artifact;
   the application need not use the supervisor's interpreter. Where a legacy or
   configured deployment uses runtime inputs outside its qualified artifact,
   qualify those external inputs on each owned start before owners/listeners are
   created. CB5 owns artifact packaging and closure; an immutable artifact launch
   does not require the legacy external-runtime forest audit. Subsequent readiness
   reads use initialized, authenticated identity without repeating any required
   external audit. Deep verification stays separate; qualification is not inherited
   by a replacement. Retained unknown business
   receipts remain readable and unchanged throughout this sequence.
   Broken: a child's exit is relabeled whole-tree custody, or HTTP health alone
   opens intake while another local writer is still active.

5. **MA5 — Dispatch user actions immediately.** Manual checks, preparation and
   installation requests start their eligible work immediately, not at the next
   background poll. Run independent lookups, downloads and verification concurrently
   within resource limits, and prepare before the activation/drain boundary
   where safe. Reuse qualified immutable inputs and appropriately fresh lookup
   results without skipping required release or changed-input verification. Push
   actual phase/progress changes; an advisory is not custody or readiness proof.
   Missing control endpoints fail without queuing a mutation or starting a second
   application. Ordinary update uses the existing installation supervisor, not a
   second policy engine or a census of historical workspace configurations.
   Broken: clicking Install waits for a polling interval before preparation starts.

6. **MA6 — Keep passive recovery usable.** Exact receipts, bounded job/manifest
   pages and explicitly classified metadata remain readable when safe during drain,
   stopped state and closed-intake startup. Report administrative custody/readiness
   separately from unresolved business outcomes. A passive read cannot acquire
   credentials, activate evidence, bootstrap writable authority or replay work.
   Clients isolate optional topic failures (CS9); missing optional capabilities
   cannot prevent receipt inspection or bounded diagnostics.
   Broken: reading voice availability obtains a secret or blocks recovery bootstrap.

7. **MA7 — Describe the archive actually captured.** Select native identities and
   authorized configuration scopes explicitly. Bind review to immutable hashes;
   changed inputs require a new review. Stream archives to private disk and expose
   bounded chunks and manifest pages. Credentials require separate trusted
   authorization and review. Retained source history is never removed by snapshot,
   derived-cache rebuild or reversible configuration reset.
   Broken: a native-only archive is presented as a complete product backup.

8. **MA8 — Account for other writers.** Cooperative locks cover only participating
   authorities and a bounded selected set. A broader configuration tree or a
   whole-native consistency claim requires proven exclusion of every relevant
   writer. File hashes supplement ownership; they cannot establish it. Operator
   assertions about stopped external writers must be explicit, never inferred
   from missing catalog entries or no connected viewers.
   Broken: a legacy CLI writes through an alleged whole-home snapshot boundary.

9. **MA9 — Aggregate declared authority, not discovered directories.** A full
   product archive has a versioned inventory of every configured authority:
   host records and receipts, capability stores and artifacts, native authorities,
   installation/release provenance and any external configured roots. Each entry
   names its owner, schema/revision, consistency proof, bytes/digests and omissions.
   An owner can provide its own snapshot, or the installation owner can capture
   its explicitly allocated storage container after proving all relevant writers
   stopped. A path inferred from a browser request is never authority. Unknown
   files, unregistered external roots or missing owner coverage prevent the
   complete-product claim. Catalog/rebuildable caches need an explicit owner
   classification; they are not presumed disposable because they are large.
   Broken: recursively copying the application directory is labeled complete while
   native checkpoints or externally configured media remain outside it.

10. **MA10 — Restore into a separate inactive destination.** Validate archive
    format, member paths, links, duplicate names, digests and the exact reviewed
    manifest before allocating its final destination. Keep the original archive
    and source authorities unchanged. Source references, credentials, installation
    identity and retained fences require explicit requalification for activation.
    Restore does not replay commands, clear unknown receipts, resume workers or
    treat an old owner token as proof for a new process. A completed file restore
    is distinct from an activated runnable installation.
    Broken: restoring a database automatically retries its unfinished jobs.

11. **MA11 — Cleanup is an exact reviewed operation.** Age filters and counts are
    bounded catalog queries, not a startup history scan. A cleanup preview binds
    source identities, scope and eligibility to a digest; live work, pending
    decisions, children and dependent owner authority must be accounted for.
    Removing discoverability, purging retained application projections, clearing
    rebuildable caches, resetting configuration and deleting canonical history
    are separate choices. Retained event logs are preserved by default. A generic
    reset phrase cannot authorize undeclared paths or broaden the selected scope.
    Broken: an old conversation's age silently authorizes removing its events.

12. **MA12 — Reconcile failed local startup without rewriting business history.**
    Validate required launch files, permissions, credential formats and TLS before
    allocating one-use source authority or starting owners. Failure after allocation
    retains the original attempt, process identity, state, authority and evidence.
    The installation supervisor may explicitly reconcile that same failed attempt
    using proven complete local writer exclusion, exact retained-state/release
    bindings and required domain-lock acquisition. It can then authorize a retained
    restart or rollback through the same single activation path. Outstanding remote
    or business outcomes do not have to become known first; preserve them as unknown
    without replay. Missing local custody or invalid state still prevents startup.
    A lost start/readiness acknowledgement requires observing the original operation
    and actual generation, not blindly launching again. Do not delete guards, replace
    authority or choose a fresh namespace to evade reconciliation. A separately
    reviewed legacy-installation migration preserves failed-attempt state and unknown
    receipts; it is not a permanent alternate launch or rescue protocol. Returning
    to verified retained manual bytes before retirement remains distinct from signed
    replacement and never authorizes business replay.
    Broken: an unresolved remote result blocks a safely stopped local installation,
    or an operator deletes the failed installation's identity to start another one.

## How the kit checks it

Run an ordinary update with real accepted work and an actual child writer.
Close fresh work admission while allowing necessary accepted callbacks to finish;
prove that a parent exit with a surviving child cannot authorize replacement.
Verify complete process-tree exclusion, required domain locks, the successor's
exact artifact/runtime identity and closed-intake readiness before opening work.
Lose the readiness acknowledgement and recover the same operation without a
second launch. Keep an unknown remote outcome unknown across a successful local
restart and assert that no business command is replayed. Qualify the actual
platform adapter; a one-child fixture is not complete process-tree evidence.

Independently test data-maintenance gates with lost acknowledgements, altered
proof/context and retained state. Preserve canonical history through independently
packed signed upgrade/rollback, paged/chunked archive and reviewed reset/undo.

Complete configured-product backup additionally needs every configured authority's
actual snapshot coverage and an aggregate inclusion/omission manifest. Device,
remote-account, full-product restore and service-manager acceptance remain separate.

Reject malformed or incorrectly protected launch inputs before any authority
allocation. Accept a valid public certificate with ordinary read permissions.
Inject failures after source allocation and verify that retained authority and
unknown receipts cannot be reset or silently replayed. Qualify failed-start
reconciliation separately from ordinary activation and legacy migration. The current
[implementation status](../docs/architecture/implementation-status.md) records
which of these boundaries have executable evidence.

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-10-04 | Clarify MA4 startup qualification versus bounded lifecycle readiness; retain signed identity checks and explicit deep audits. | [PR365](https://github.com/microsoft/amplifier-unified/pull/365), actual-entrypoint binder regression and [preview 9 checkpoint](../docs/architecture/evidence/signed-preview9-checkpoint-20261004.json). |
| 2026-10-03 | Add MA12 pre-authority launch validation and explicit failed-start reconciliation. | Real owned-preview startup rejected a public certificate after source allocation; verified stopped-writer fallback preserved histories and uncertain receipts. Reconciliation implementation remains in progress. |
| 2026-10-03 | Clarify MA3 owner-proven refusal before effect admission and retain distinct stale/unknown outcomes. | Reproduced native metadata/reset contention and assembled reset/browser qualification; no relaxation after an effect begins. |
