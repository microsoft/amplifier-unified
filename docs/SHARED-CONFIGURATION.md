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

## Smart Tools and Converge

This cutover is a host configuration change. It does not inject Amplifier
settings discovery into portable Smart Tools or add a Converge-specific model
path. Converge's separate tool model execution still needs the optional adapter
work: a host resolves its routing policy and supplies an explicit portable
invocation contract. Tools retain their own domain libraries, standalone usage,
and provider abstraction. That work is separate from this migration.
