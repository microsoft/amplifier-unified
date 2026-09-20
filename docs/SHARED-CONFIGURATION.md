# Shared Amplifier configuration

Unified uses the same settings files as amplifier-app-cli. A future TUI can
consume this contract without importing either application. `AMPLIFIER_HOME`
defaults to `~/.amplifier`; `--data-dir` / `AMPLIFIER_WEB_HOME` only relocate
Unified's own state.

| Priority (last wins) | File |
| --- | --- |
| User defaults | `$AMPLIFIER_HOME/settings.yaml` |
| Workspace / team | `<workspace>/.amplifier/settings.yaml` |
| Workspace / machine | `<workspace>/.amplifier/settings.local.yaml` |
| Conversation | `$AMPLIFIER_HOME/projects/<slug>/sessions/<id>/settings.yaml` |

Workspaces are real directories. The slug and native session ID are the same
ones used for shared transcripts. Dictionaries overlay recursively; ordinary
lists replace. `config.providers` is the CLI exception: merge entries by `id`,
falling back to `module`, retaining other provider instances and merging each
instance's configuration. Lower numeric `config.priority` values come first.
Unified reports malformed YAML instead of silently ignoring it.

Shared fields include bundle registrations/defaults/behaviors (`bundle`),
provider instances (`config.providers`), module configuration and source
overrides (`modules`, `config`, `overrides`, `sources`), routing selection and
role overrides (`routing`), and supported saved configurator settings
(`configurator`). The existing module/bundle runtime still determines which
configuration fields it supports. A shared file does not make all host features
identical. In particular, older CLI versions do not consume Unified's hook
enablement extension; `web_bundles` contains optional editor metadata, never
authority over the standard `bundle.app` / `bundle.added` fields.

Credentials use `$AMPLIFIER_HOME/keys.env`. Explicit launch-environment values
win; values loaded from the file refresh on subsequent mounts. Provider editing
stores credential references in YAML and secrets in this private file. Existing
explicit OAuth token paths are retained. Newly created ChatGPT token files live
under the shared root. Tokens are not copied to a second refresh owner.

User-authored routing matrices live in `$AMPLIFIER_HOME/routing/`, as in the CLI.
Unified additionally supports `.amplifier/routing/` and
`.amplifier/routing.local/` in a workspace, ahead of global custom matrices and
the routing bundle's shipped matrices. These workspace directories are a
Unified extension until other hosts adopt them. The same lookup order is used
by the editor and the mounted routing hook.

Voice preferences are common settings, with no app namespace:

```yaml
voice:
  preferred_model: gpt-live-1
  fallback_model: gpt-realtime-2.1
```

These fields select models supported by the consuming app's voice transport.
Unified currently supports its existing Live and Realtime transports; this
change does not add another voice provider. A workspace or conversation can
override the global voice preferences. Other hosts can adopt these fields when
they add voice.

Settings writes lock `<file>.lock` across read/modify/atomic replace, matching
the CLI's locking convention. Unknown fields are preserved. This coordinates
with CLI writers that use the lock; an older or external editor that ignores
the lock can still race. Settings reads never import a cache or rewrite YAML.
File changes invalidate parked session mounts; active work is not interrupted.
New sessions resolve the default bundle from their own workspace. Deliberate
existing Unified conversation overrides (`configuration.json` and
`control-state.json`) remain in force; they are not flattened into user defaults.

## Upgrade from private settings

Startup performs an additive migration once per shared root and used workspace:

- If a shared settings file is absent, seed it from its corresponding old
  Unified file, translating provider ordering to numeric priorities.
- If it already exists, keep its values. Add only missing named bundle
  registrations; do not import old model choices, behavior lists or routing
  selections over an existing shared configuration.
- Copy missing credential names and custom routing files. Existing shared names
  and files win, including intentionally empty settings files.
- Seed missing global voice preferences from the saved app preferences once.
- Retain old files and private pre-migration backups under the app's
  `backups/shared-settings-<id>/`. Imported workspace snapshots are not replayed.

After migration, changes to `~/.amplifier-unified/config/settings.yaml`, imported
workspace snapshots and `.amplifier-unified/settings*.yaml` have no effect.
Inspect the retained files for intentionally conflicting choices you want to
adopt, and put those choices in the appropriate shared scope.

The app keeps deployment/TLS/authentication, update policy, browser layout,
Smart Tool server registrations, notification transport configuration,
diagnostic destinations, runtime caches, and conversation UI/operation state.
Resetting app settings does not reset the shared settings. Private backups now
include shared configuration and relevant workspace settings.

## Smart Tools and optional native runtimes

This cutover is a host configuration change. It does not inject Amplifier
settings discovery into portable Smart Tools. The opt-in
`amplifier_web.host.shared_runtime_config` adapter supplies shared provider
instances and the ordinary routing hook to a compatible native runtime, while
retaining its domain bundle and agent declarations. It has no required provider
or model. A runtime owner can explicitly adopt this optional interface;
adoption remains separate from this migration and must not restart an active
manager or bypass its saved-session configuration guard.

The adapter is an Amplifier host extension, not a universal Smart Tools
invocation contract. Deterministic domain operations remain independent of
provider credentials. See [the TUI handoff](TUI-SHARED-CONFIGURATION.md) for the
shared file contract, optional adapter interface and cross-host adoption checks.

## Root bundle defaults in Unified

The conversation bundle picker uses built-in conversation profiles and explicit
standalone registrations (`bundle.added`), independently of provider/model selection.
Cached namespace roots and source overrides alone do not make a bundle selectable:
add-ons remain in capability management. The shared `bundles.list` catalog applies
this policy to both UI and agent clients, without fetching or mounting bundles.
New conversations use an explicit bundle choice first,
then the workspace's shared local/project default, then Unified's optional app
preference, then the shared user default (falling back to `anchors`). Existing
conversations retain their saved root.

The default picker exposes three scopes. **This Unified app** stores `appBundle`
in app state and leaves shared settings untouched. **This workspace on this
computer** edits `bundle.active` in `.amplifier/settings.local.yaml`; clearing it
reveals a project/team default if present. **Shared Amplifier settings** edits
`~/.amplifier/settings.yaml` and can affect the CLI and other consuming hosts.
`bundle.default` provides the same scoped action to agents; `bundle:null` clears
only the chosen override. The older `settings.update {patch:{bundle:...}}` action
retains its shared-global meaning for compatibility.

For an existing conversation, choose a new root from the bundle control beside
the model, then **Switch bundle** directly or **Preview changes** first. Forking
also supports either route. Switching requires idle work. When a preview token is
supplied, the current configuration must still match that preview. Without a
token, the target is validated during Apply. The validated composition is reused
within that switch instead of resolving it again during mounting; it is not
cached between requests. First-time module installation still takes time. The
root is resolved with current host composition; previous conversation mount-plan edits,
module toggles, mode, and budget overrides are discarded. A compatible model pin
and its reasoning effort survive. An unavailable provider pin requires an
explicit reset choice. Original user/tool history is retained; old authoritative
system/developer rows are replaced by the new root's instructions. The worker
journals the affected session files, mounts the replacement under the same
Foundation execution ownership, and restores the original files/configuration if
mounting fails. Interrupted transactions are recovered on the next mount without
replaying work. Per-conversation settings from shared files still apply.

**Fork with this bundle** leaves the source unchanged and validates a new root
before publishing its independent history. It retains visible voice references,
tool evidence, and artifact snapshots, without copying job ownership, approvals,
or active goals. It does not clone underlying external MCP tool work. Both paths
use `bundle.preview`, `bundle.switch`, and `bundle.fork` through the shared UI/agent
action registry. A receipt means accepted; final `bundleChange` state reports the
outcome. Previews are intentionally invalid after host restart.
