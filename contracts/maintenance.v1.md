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

## How the kit checks it

Hold a real callback, thread, listener and child process; lose acknowledgements;
kill owners before/after durable release; reject altered proof/context; restart
with retained gates. Run an independently packed signed application through
upgrade and rollback, recover its exact receipt, and preserve canonical history
through a paged/chunked archive and reviewed reset/undo.

Complete configured-product backup additionally needs every configured authority's
actual snapshot coverage and an aggregate inclusion/omission manifest. Device,
remote-account, full-product restore and service-manager acceptance remain separate.
