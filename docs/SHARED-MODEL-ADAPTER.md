# Optional shared model configuration adapter

This host extension supplies shared Amplifier model configuration to an explicit
native runtime through an optional adapter interface. It ships in Unified
v0.11.8 and is not enabled by installing or updating Unified. The consuming
runtime owns adapter selection and adoption; Unified does not identify or
configure downstream applications.

## Scope and adoption

Install the reviewed Unified and compatible native runtime packages in the
manager's Python environment. Their Foundation requirements must agree with
Unified's tested immutable revision in `pyproject.toml` so the packages can be
installed together through ordinary dependency resolution.
Do not bypass dependency checks to combine incompatible versions. This adapter
currently ships inside Unified; extracting a smaller shared package is separate
work, and portable domain libraries acquire no Unified dependency.

For a **new** native manager, add this field to its operator-owned private
runtime JSON, retaining its existing domain bundle and execution settings:

```json
{
  "config_adapter": "amplifier_web.host.shared_runtime_config"
}
```

This fragment is not a complete domain runtime configuration. It replaces the
private root provider list with enabled shared provider instances and adds the
normal routing hook. It does not import the default chat bundle, shared tools,
application behaviors or arbitrary session limits over the domain runtime.
Relevant provider/routing configuration and source overrides use the normal
host settings rules. Root defaults use shared provider priorities; agent roles
use the existing routing resolver. There is no required provider or model.

All configuration discovery stays in the host adapter. Runtime model schemas
materialize environment references after normal module preparation. Expanded
credentials are not written to settings or adapter state. Existing explicit
launch environment remains authoritative over `keys.env`.

Saved native sessions retain the strict runtime configuration digest guard.
The adapter includes custom routing-file fingerprints and non-credential
environment inputs in that identity. A changed matrix behind the same path is
a configuration change. Credential rotation alone is not a model-policy change.
Direct worker roles use the normal resolver, and preparation preserves already
resolved child preferences. Their resolved model plan also enters adapter state.

Enabling this adapter for an existing native manager changes its configuration
identity and is deliberately refused on resume. Use a new native session, or
separately implement/review an exact old-to-new migration with the runtime owner.
There is no bypass flag. No active manager is restarted by this change. Custom
routing files must remain stable while a manager prepares a root or child; a
mid-run edit is refused at the next child preparation rather than silently
altering its routing. Pin module sources for reproducibility; a mutable source
reference is not a content lock.

## Verification

- Unified v0.11.8 release candidate on the published v0.11.7 base: **1,028 passed, 11 skipped**. Frontend: **154 passed**. Empty-host and pending-review browser checks, distribution verification and a fresh installed-wheel readiness check passed.
- Shared scope, instance merge/disable, source precedence, schema-aware
  credentials, unchanged resume, changed routing/environment refusal, child
  preference preservation and visible startup-failure regressions passed.

These host checks do not qualify a downstream application's execution, visual
workflow, provider accounts or existing-session migration. Each integration
owns those checks and their evidence. Validate co-installation with ordinary
dependencies, without `--no-deps` or source-path injection, and distinguish
offline adapter compatibility from live-provider execution.

The [TUI handoff](TUI-SHARED-CONFIGURATION.md) gives the interoperable settings
contract and adoption checks. When releasing an integration that changes
existing users' configuration authority, add an Updates notice describing the
new authority, the saved-session boundary and the exact adoption steps. Merely
shipping this opt-in implementation changes no existing manager's authority.
