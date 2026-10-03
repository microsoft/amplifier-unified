# Service lifecycle handoff

The TypeScript contract beside this file is a proposal for host/platform owners.
No production service adapter is implemented or qualified by this installer.
The foreground installer refuses SIGINT/SIGTERM with
`service_lifecycle_not_configured`; it keeps the supervisor and child reachable.
A qualified service owner must replace that refusal before managed deployment.

Required acceptance cases currently remain open:

- Active host participant, native work, pending mutation, or incomplete owner
  coverage: refuse stop before signaling anything; preserve active work.
- Confirmed held admission: durably bind command, installation, exact instance,
  scope and fence before signaling the process/job owned by this adapter.
- Missing discovery, stale PID, PID reuse, a different authenticated instance,
  or an existing unowned service: refuse adoption and all signals.
- Lost stop response or supervisor interruption: retain unknown, keep the fence,
  and permit only authoritative read-only reconciliation; do not retry signals.
- Confirmed stopped process: retain a durable stopped receipt across supervisor
  restart; disappearance of an endpoint alone is insufficient.
- Explicit resume: require that stopped receipt, exact retained signed bytes,
  and a new instance; preserve the application state directory and native history.
- Lost resume response: no second launch; independently inspect exact new
  readiness and bind release of the prior host fence to durable service proof.
- Competing stop/resume/adopt commands and conflicting scopes: one authority,
  durable idempotence, no inferred lease expiry and no automatic work replay.
- Unsupported operating system or missing platform ownership proof: refuse.

Host coordination is required for a service-specific held fence and release
proof. Existing distribution-update ready/unchanged proofs must not be relabeled
as evidence of service stop/resume. Native/external Python and registry source
currency still require their own owners.
