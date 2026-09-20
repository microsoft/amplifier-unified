# Optional shared model configuration adapter

This host extension supplies shared Amplifier model configuration to an explicit
native runtime. It implements the companion Converge runtime proposal's optional
adapter interface. It is not enabled by installing or updating Unified.

## Scope and adoption

Install the reviewed Unified and compatible native runtime packages in the
manager's Python environment. The companion runtime proposal aligns its
Foundation requirement with Unified's tested immutable revision
`695f875c0908f45f8dc78b1fcde80ecddebffd7c`, allowing normal dependency resolution.
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

- Unified suite after rebasing onto v0.11.5: **934 passed, 10 skipped**.
- Companion Converge runtime suite on the published v0.2.2 base:
  **122 passed**, including real Foundation history compatibility.
- A clean environment resolved and installed both built packages with ordinary
  dependencies, without `--no-deps` or source-path injection.
- Shared scope, instance merge/disable, source precedence, schema-aware
  credentials, unchanged resume, changed routing/environment refusal, child
  preference preservation and visible startup-failure regressions passed.

A separate live qualification used a temporary shared root/workspace and
temporary native histories. A workspace override selected the `fast` role's
OpenAI instance; a native-session override selected the `general` role's
Anthropic instance. The normal `hooks-routing` capability resolved both, and
the native runtime launched the workers directly:

| Role | Resolved provider/model | Observed provider request | Result |
| --- | --- | --- | --- |
| `general` | Anthropic / `claude-haiku-4-5` | `haiku` instance | `READY` |
| `fast` | OpenAI / `gpt-5.6-luna` | `luna` instance | `READY` |

These are qualification choices, not adapter defaults. The checks prove shared
scope resolution and real execution through two provider families. They do not
prove every provider, TUI implementation, visual workflow, or migration of the
existing Converge manager. Its active configuration and processes were untouched.

The [TUI handoff](TUI-SHARED-CONFIGURATION.md) gives the interoperable settings
contract and adoption checks. When releasing an integration that changes
existing users' configuration authority, add an Updates notice describing the
new authority, the saved-session boundary and the exact adoption steps. Merely
shipping this opt-in implementation changes no existing manager's authority.
