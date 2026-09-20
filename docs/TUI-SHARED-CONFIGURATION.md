# Shared Amplifier configuration: handoff for the TUI team

The CLI, Unified and upcoming TUI should read the same user and workspace
configuration. Unified adopted these files in v0.11.0. The TUI should consume
them directly rather than import them into an app-owned settings copy.

This document describes the implemented Unified behavior, the existing CLI
conventions it follows, and the remaining cross-host adoption work. It does not
claim the TUI has implemented these features yet.

## 1. Resolve configuration for a real workspace and native session

`AMPLIFIER_HOME` selects the shared root; the default is `~/.amplifier`.
An application's data directory must not silently relocate that shared root.

Read these files in order, with later values taking precedence:

| Scope | File |
| --- | --- |
| User | `$AMPLIFIER_HOME/settings.yaml` |
| Workspace/team | `<workspace>/.amplifier/settings.yaml` |
| Workspace/machine | `<workspace>/.amplifier/settings.local.yaml` |
| Native session | `$AMPLIFIER_HOME/projects/<slug>/sessions/<native-id>/settings.yaml` |

Use the resolved absolute workspace directory, not an app's workspace label.
The CLI-compatible slug replaces `/` and `\` with `-`, removes `:`, and ensures
a leading `-`. Preserve spaces, periods and underscores. Use the original native
session ID; do not substitute a TUI tab ID or Unified conversation ID.
Do not create a session merely to open an empty composer. Until a native session
exists, resolve only the first three scopes.

Missing files and empty YAML documents contribute an empty mapping. Malformed
YAML and non-mapping top-level documents are errors with a file-specific remedy;
they must not silently discard a user's configuration.

## 2. Merge exactly, then compose the runtime

Settings dictionaries merge recursively. Scalars and ordinary lists replace.
`config.providers` is the exception: merge rows by `id`, falling back to
`module`; recursively merge the matched row and retain other instances. An
empty provider list therefore does not remove inherited provider instances.
Use supported disable settings instead of assuming `[]` means deletion.

Sort effective providers by ascending `config.priority` (default `100`), keeping
the existing order for ties. Preserve instance identities even when multiple
instances use the same provider module. Translate `id` to the runtime's
`instance_id` where required; do not collapse rows to one per provider family.

Bundle composition has its own module-list merge rules. Keep it separate from
settings-file merging; otherwise ordinary settings lists accidentally become
additive and cannot express replacement.

Shared fields already consumed by Unified include:

| Fields | Responsibility |
| --- | --- |
| `bundle.active`, `bundle.app`, `bundle.added` | Default bundle, application behaviors, named bundle registrations |
| `config.providers` | Provider instances, models, priorities and credential references |
| `modules`, `config`, `overrides`, `sources` | Supported module configuration and source overrides |
| `routing.matrix`, `routing.overrides` | Matrix selection and role candidate overrides |
| `configurator` | Supported saved enablement/configurator choices |
| `voice.preferred_model`, `voice.fallback_model` | Common voice model preferences |

The consuming module still defines which configuration fields it supports.
Preserve unknown fields when writing. Shared storage does not imply identical
host capabilities. In particular, Unified's hook-enablement extension is not
consumed by every CLI version; `web_bundles` is optional editor metadata, not a
replacement for standard bundle registrations or behavior lists.

## 3. Routing and credentials

Use the existing routing module and its `model_role_resolver` capability.
Do not build a second provider/model matcher into the TUI, Converge or a Smart
Tool launcher. Normal session defaults use provider priorities and default
models; agents with `model_role` use routing roles. Selecting a routing matrix
does not by itself assign a role to the root conversation.

The CLI-compatible custom matrix directory is `$AMPLIFIER_HOME/routing/`.
Unified additionally searches, in this order:

1. `<workspace>/.amplifier/routing.local/`
2. `<workspace>/.amplifier/routing/`
3. `$AMPLIFIER_HOME/routing/`
4. The routing bundle's shipped matrices

The workspace directories are an extension for the TUI to adopt explicitly.
Use the same order in the picker, effective-source display and mounted hook.
Show which file won when names collide.

Example shared routing override (the provider ID must exist in your settings):

```yaml
routing:
  matrix: balanced
  overrides:
    reasoning:
      candidates:
        - provider: my-review-provider
          model: my-supported-model
        - base
```

`base` expands the selected matrix's candidates for that role. Follow the routing
module's schema and validation; model names and supported parameters come from
the installed provider, not from an app-specific list.

Credentials belong in `$AMPLIFIER_HOME/keys.env`, or the explicit launch
environment. Keep references such as `${PROVIDER_API_KEY}` in YAML. Explicit
environment values win; file-managed values should refresh on the next safe
mount and must not become permanent overrides simply because a child inherited
them. Resolve optional provider fields using the provider's declared schema;
do not replace every missing environment reference with an empty string.
Never log or save the expanded provider plan as an ordinary settings artifact.

Retain existing explicit OAuth token paths and a single refresh owner. Do not
copy refresh tokens into a TUI-owned credential store. Newly created shared
token files belong under the shared root with private permissions.

## 4. Common voice preferences and app-owned state

Voice preferences are ordinary shared settings, without an app namespace:

```yaml
voice:
  preferred_model: your-supported-voice-model
  fallback_model: your-supported-fallback-model
```

Workspace and session overrides follow the same precedence. A host must report
unsupported transports/models honestly; this schema does not provide a new
voice provider. Unified currently implements its existing Live and Realtime
transports. A TUI without voice should preserve these fields untouched.

Keep presentation and installation state app-owned: layout, focus, drafts,
deployment/TLS/authentication, update policy, notification transport, diagnostic
destinations, caches, operation UI state and Smart Tool server registrations.
A Smart Tool registration may contain host-specific launch/install information;
it is not a second place to store common provider choices. Resetting TUI
preferences must not erase shared Amplifier settings or native history.

## 5. Writes, refresh and existing conversations

For a settings update, acquire `<settings-file>.lock`, reread the latest YAML,
change only the requested fields, write a temporary file in the same directory,
flush it, then atomically replace the target while still holding the lock.
Preserve existing permissions; new private settings/credential files should be
owner-readable and owner-writable only. This coordinates participating writers;
an older host or external editor that ignores the lock can still race.

Do not rewrite YAML on a read. An empty shared file is an intentional file,
not permission to replay an old app snapshot. Monitor settings, credential and
routing-file changes; invalidate idle mounts and refresh before the next turn.
Do not interrupt active execution to remount it.

Distinguish inherited defaults from explicit per-conversation choices. Unified
retains deliberate `configuration.json` / `control-state.json` conversation
overrides. Those are existing Unified state, not a new shared TUI schema.
Prefer native-session `settings.yaml` for new interoperable settings choices.

Native session history and execution ownership are separate from settings
locking. Reuse Foundation's native history and shared execution lock. If a host
that ignores that lock modifies history, detect the conflict and preserve both
histories instead of blindly overwriting the transcript.

## 6. Portable Smart Tools and the Converge bridge

The [Smart Tools specification](https://github.com/microsoft/amplifier-smart-tools)
places domain behavior in the library, with a thin required CLI and optional
MCP/UI adapters. Its invocation contract does not prescribe a universal model
injection protocol. Keep deterministic capabilities usable without provider
credentials, and keep model-backed domain expertise inside the tool's library.

Converge's Create and Direction capabilities are currently deterministic. Its
separate native execution runtime already accepts explicit provider-independent
configuration. The optional bridge added alongside this handoff resolves shared
model settings at that host boundary; it does not make those domain libraries
discover `~/.amplifier` or add an Amplifier requirement to unrelated Smart Tools.

The proposed runtime adapter interface has two functions:

```python
resolve_config(config, *, workspace, session_id) -> dict
async prepare_bundle(config, bundle, *, is_child=False) -> PreparedBundle
```

An operator explicitly selects an installed adapter in the private runtime
configuration using `config_adapter`. Tool request arguments cannot select it.
Unified's implementation is `amplifier_web.host.shared_runtime_config`; it must
be installed in the manager's Python environment. Its code can be reused by an
Amplifier host, but it is not yet a separately packaged cross-app SDK and is not
part of the Smart Tools specification.

The bridge replaces private root provider defaults with enabled shared provider
instances, attaches the ordinary routing hook and applies relevant shared source
overrides. It retains the tool's own bundle, instructions, actions, limits and
agent declarations. Child sessions retain the normal runtime's already-resolved
provider preferences. No OpenAI provider or GPT model is required by the bridge.

For a saved Converge native session, changed resolved configuration or changed
custom routing-file contents must still fail the runtime's configuration digest
check. This patch introduces no blanket permission to change saved sessions.
Adoption for an existing manager requires a separately reviewed exact migration
or a new native session. New settings are resolved for new managers; active
managers are not restarted. The Converge integration remains an isolated draft
pending coordination with its runtime owner and idle adoption.

If a different portable tool uses its own model client, adapt its existing public
library interface. Do not impose this Amplifier runtime interface on that tool.
Context passed to a tool should be mechanically assembled data; a reference to
private host state is not a portable context payload.

## 7. TUI adoption checks

- Resolve user, workspace, local and native-session settings in the documented
  order, including malformed YAML and intentionally empty files.
- Preserve multiple instances of one provider; test identity merging, disable
  choices, priority and ordinary list replacement independently.
- Edit one setting from each host and observe it in the others without an
  import or restart. Race cooperating writers and retain unknown fields.
- Resolve the same routing role with two provider families, then change it at
  workspace and session scope. Include a custom-matrix filename collision.
- Rotate file-managed credentials safely, retain explicit environment priority,
  and verify no secret appears in YAML, logs or exported diagnostics.
- Preserve native session IDs, transcripts, explicit choices and execution
  ownership across hosts. Test an older non-cooperating history writer.
- Preserve unsupported voice and future-app fields on unrelated edits.
- Keep deterministic Smart Tool operations working without model credentials.
  Test the model adapter separately from domain operations and MCP/UI transport.
- Record actual live-provider checks separately from offline compatibility
  tests; passing one does not establish the other.

## Implementation references

In the Unified repository:

- `amplifier_web/shared_settings.py`: settings paths, merge rules and locked writes
- `amplifier_web/session_files.py`: shared home, exact workspace slug and native IDs
- `amplifier_web/host/config.py`: environment and source configuration
- `amplifier_web/host/session.py`: runtime composition and shared routing injection
- `amplifier_web/provider_environment.py`: provider-schema-aware materialization
- `amplifier_web/host/shared_runtime_config.py`: optional native runtime model bridge
- `tests/test_shared_settings.py`, `tests/test_shared_runtime_config.py`: compatibility fixtures
- `docs/SHARED-CONFIGURATION.md`: migration and app-owned state

In the Converge Agents proposal, `packages/project-runtime` owns the optional
adapter seam. Its default explicit-configuration path remains available without
installing Unified. For a standalone TUI, adopt the file contract and shared
Foundation/routing primitives. If several hosts want a common reader/writer
package, extract the small settings layer with these fixtures rather than
copying an entire application's runtime.

See `docs/SHARED-MODEL-ADAPTER.md` for installation boundaries and verification
evidence, including real calls through two provider families. Compatible package
pins matter when co-installing the optional host adapter and native runtime;
their dependency resolver must agree on the Foundation revision.
