# Unified shell behavior and skill

Compose `behaviors/unified-shell.yaml` into an existing host. It includes the
pinned Microsoft `skills-tool` behavior, enables the normal skills catalog, and
adds `@unified:skills`. The minimal skills behavior avoids adding another
curated collection; collections already provided by the chosen root or other
behaviors remain available through Foundation's list composition.

`bundle.yaml` is a resource namespace anchor. It does not choose a provider,
orchestrator, session root, or system instruction. Do not select it as a
standalone runtime bundle. The skill's instructions and supporting files live
in `skills/amplifier-shell/`; the small `context/shell-skills.md` points agents
there. The skill body loads on demand.

## App defaults and existing configurations

When no `bundle.app` list is configured, Unified shows **Unified shell** as an
enabled app behavior in Settings → Capabilities → Bundles and composes it into
ordinary sessions. The default is computed without writing shared settings.
The usual add, toggle, reorder, and remove actions are shared by the UI and
agents. Explicit lists, including an empty list, remain authoritative. Disabled
or removed entries stay disabled or removed.

For an existing configuration with an explicit behavior list, add the behavior
through the Bundles UI or the `bundles.add` action:

```json
{
  "uri": "git+https://github.com/bkrabach/amplifier-unified@main#subdirectory=behaviors/unified-shell.yaml",
  "role": "behavior",
  "name": "Unified shell"
}
```

The app resolves this exact default URI to its own packaged behavior and
resources. Explicit alternative Git revisions retain normal Foundation
resolution. The repository and installed wheel have the same relative layout
for skills, shell documentation, SDK, and examples. The skills implementation
is pinned separately because namespace skill sources require its deferred
resolution support.

Behavior changes take effect on subsequent runtime preparation, not by
rewriting an already running session. Saved complete bundle snapshots retain
their existing no-recomposition semantics. An exported snapshot embeds static
context and carries a portable Git skills source; exports currently warn that
this skills source tracks Unified's main branch. Pin it when reproducibility
across exports matters.

## Other Amplifier hosts and coding agents

Other hosts can compose the same Git behavior directly. For Amplifier CLI:

```sh
amplifier bundle add 'git+https://github.com/bkrabach/amplifier-unified@main#subdirectory=behaviors/unified-shell.yaml' --app
```

Repository access is required when loading from Git. Unified's built-in copy
does not fetch a second Unified checkout, although the Microsoft skills
behavior/module use Foundation's normal dependency resolution and caching.

An external coding agent can read `skills/amplifier-shell/SKILL.md` in a
checkout, or expose that directory through its native skill discovery. There
is one authoritative skill; no private Codex-only copy is required.
The behavior supplies knowledge and skills discovery, not an app connection.
Live control still requires Unified's `app_control` tool or an authenticated
connection. Delegated workers that inherit the skills tool can discover the
skill; restricted agents do not gain tools their spawn policy excludes.

## Validation

Check the composed behavior with Foundation, not only a YAML parser. Verify
that it preserves the root's instruction, provider and orchestrator; that
`tool-skills` appears once with existing and Unified sources; and that the
namespace resolves before the first provider request's skills catalog renders.
Also test fresh defaults, explicit lists, disabling/removal, UI/agent action
parity, saved snapshots, and the built wheel from outside the checkout.

The behavior does not install the Node/Rollup/Playwright shell validator
toolchain. Read [the shell guide](README.md) for extension validation setup.

After building a wheel, `scripts/validate_shell_skill.py WHEEL` checks its
relative references, loads the actual upstream skills behavior through
Foundation, and mounts the skills tool with early and deferred namespace
resolution. Install the pinned `amplifier-module-tool-skills` module in the
validation environment, or pass its source module directory as a second
argument. The script uses provider-free coordinator fixtures and does not
claim to test a live conversation or browser interaction.
