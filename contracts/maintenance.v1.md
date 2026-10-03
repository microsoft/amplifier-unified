# Maintenance and recovery — v1

Clause prefix: **MA**. Parent: [family vision](../docs/architecture/VISION.md).
Implementation guidance under the approved protocol and ownership direction.
This contract binds public package interfaces and trusted local control; it adds
no private replacement for AHP or ACP semantics. The [status ledger](../docs/architecture/implementation-status.md)
separates source, installed fixture, browser, account/device and deployment evidence.

## Who builds against this

Distribution, host, native adapter, capability, client and release maintainers.

## The promises

1. **MA1 — Cover real lifetimes.** Derive required participants from configured
   topics, resource writers and native callbacks. Each participant owns its intake,
   queued calls, reverse callbacks, threads, subprocesses, listeners and active
   external work. Missing coverage is a refusal. Viewer count, an idle UI and a
   historical command receipt do not establish whether work is still executing.
   Broken: a cancelled await releases accounting while its thread still writes.

2. **MA2 — Hold before reporting idle.** A successful acquisition retains intake
   closure and its process/writer leases through the protected operation.
   Process-local counters require the corresponding actual process ownership;
   durable ledgers alone cannot exclude another live owner. Acquire process leases
   before opening/recovering shared owner state. Native transfer intake precedes
   the single shared native-home exclusive administration gate.
   Broken: a second owner rewrites the first owner's active receipts.

3. **MA3 — Bind exact proof and outcome.** Context is the immutable tuple
   `fenceId, commandId, purpose, instanceId, dataScope`. Persist the release outcome
   and canonical authenticated proof before acknowledging release. A repeated
   release accepts only the same context, outcome and proof, including after
   process replacement. Unknown work stays held for inspection. A known refusal
   before effects may unwind the acquisition; uncertainty cannot use that shortcut.
   Broken: changing a proof on retry releases an unrelated retained gate.

4. **MA4 — Verify the running installation.** An independent supervisor owns
   application replacement. Admission covers the actual old process; readiness
   derives from the signed installed entrypoint, inventory, new process identity,
   data scope and required owner initialization. A held gate does not make a
   correctly initialized replacement unreadable. A requested target, environment
   label or caller-supplied assertion is insufficient release evidence.
   Broken: a ready claim copied from the requested release reopens intake.

5. **MA5 — Keep control responsive and explicit.** Manual checks and preparation
   dispatch immediately, independent work can run concurrently, and progress/idle
   notices are pushed advisories. Only a fresh held proof permits replacement.
   Late supervisor discovery does not prevent initial child readiness. Missing
   control endpoints fail; they never queue or replay a mutation. External service
   or unknown process adoption needs its own ownership contract.
   Broken: observing a missing endpoint silently starts another application.

6. **MA6 — Keep passive recovery usable.** Exact receipts, bounded job/manifest
   pages and explicitly classified metadata remain readable when safe under a
   held gate. Read classification cannot hide an effect such as evidence activation
   or credential acquisition. Clients isolate optional topic failures (CS9).
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

## How the kit checks it

Hold a real callback, thread, listener and child process; lose acknowledgements;
kill owners before/after durable release; reject altered proof/context; restart
with retained gates. Run an independently packed signed application through
upgrade and rollback, recover its exact receipt, and preserve canonical history
through a paged/chunked archive and reviewed reset/undo.

Complete configured-product backup additionally needs every configured authority's
actual snapshot coverage and an aggregate inclusion/omission manifest. Device,
remote-account, full-product restore and service-manager acceptance remain separate.
