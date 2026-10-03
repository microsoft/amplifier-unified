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
- Backup, reset, AHP registration, release publication and production deployment
  are outside this package. Offline snapshot coordination can consume the public
  quiescence/readiness ports without moving those responsibilities here.

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
