# Agent protocol — v1 (RATIFIED 2026-10-02)

Clause prefix: **AP**. Parent: [family vision](../docs/architecture/VISION.md).
Governs ACP agent adapters and the host that calls them.

## Who builds against this

Amplifier adapter/runtime maintainers, generic host implementers and independent ACP clients.

## What it is

An agent adapter wraps the native application runtime. The generic host uses ACP
for execution, rather than importing an engine's objects or storage implementation.

```text
host or independent ACP client -> ACP adapter -> native runtime and storage
conversation identity -> adapter kind + native session ID + execution authority
optional capability -> negotiated extension or explicit unavailable result
```

[Protocol fit](../docs/architecture/protocol-fit.md) contains the mapping inventory.

## The promises

1. **AP1 — Use one execution boundary.** New host execution reaches every agent,
   including Amplifier, through negotiated ACP. Standard capabilities precede
   extensions. Direct native access remains inside the adapter, not a production shortcut.
   Broken: an Amplifier-only host path bypasses the interop tests. Affected: replacement agents.

2. **AP2 — Preserve native composition.** The Amplifier adapter retains supported
   bundles, providers, tools, hooks, contexts and loops. Adapter extraction is not
   permission to substitute a differently constrained engine or discard capabilities.
   Broken: ACP adoption silently removes a provider or live loop. Affected: Amplifier users.

3. **AP3 — Keep identity and authority.** A session retains its agent/native identity;
   one execution owner writes its native history. ACP loading/resuming and lock
   acquisition preserve that authority; UI attachment neither changes engines nor takes ownership.
   Broken: a new viewer starts a competing native writer. Affected: every session participant.

4. **AP4 — Translate lifecycle faithfully.** Prompt completion, cancellation,
   permissions, errors, tool progress and resource callbacks retain upstream meanings.
   Capabilities without a faithful mapping are explicit limitations or negotiated extensions.
   Broken: an admission response is treated as a finished turn. Affected: clients and automation.

5. **AP5 — Keep advanced behavior optional.** Standard config options expose supported
   session controls. Composition administration and advanced runtime controls have
   separate scoped interfaces; their absence never blocks an ordinary supported turn.
   Broken: a generic ACP client must implement Amplifier's settings UI. Affected: community clients.

6. **AP6 — Report legacy compatibility honestly.** Classify existing native sessions
   as qualified for resume, temporarily owned elsewhere, read-only, or unsupported
   with a reason. Preserve their records. Never recreate state by replaying past tool work.
   Broken: failed resume becomes a silent new conversation. Affected: users with historical work.

## Not in v1

Cross-engine session conversion or identical advanced features in every peer; promote
only with explicit semantics and independent acceptance. No new kernel protocol dependency.

## How the kit checks it

- AP1/AP4: run our host with another ACP agent and our agent in an independent client.
- AP2/AP5: compare every affected module-class implementation and advertised control.
- AP3: race independent clients/hosts against the same native session and inspect writes.
- AP6: exercise legacy roots, children, missing modules and interrupted ownership.

Inspect the full control inventory before declaring native feature parity.

## Reviewed native context clearing

The optional native adapter owns [context-clear v1](https://github.com/microsoft/amplifier-app-acp/blob/main/docs/context-clear.md)
and its schemas; the [native bridge contract](https://github.com/microsoft/amplifier-unified-capability-native/blob/main/contracts/native-bridge.v1.md)
owns the corresponding authenticated host actions. This is an AP3–6 extension,
not an additional core ACP requirement. Hosts advertise review, apply and passive
receipt inspection only after actual capability negotiation. Older peers have no
generic-clear fallback.

Apply requires the reviewed history/control revisions and the original public
command identity. The adapter preserves transcript and metadata before-images and
event logs, clears the explicit goal, and refuses an unfinished task until the
user explicitly pauses it. Archived segments are display history; canonical
edit, fork and native transcript export remain limited to active history.

Receipt lookup retains the original native session and canonical workspace even
after relocation or a change to the host's default workspace. It starts no worker,
repeats no mutation and does not infer an outcome from missing or unknown evidence.
An older success receipt cannot replace a newer goal. Clients discard stale
reviews and confirmations after relevant state or selection changes and explicitly
refresh current goal state when needed. The assembled qualification is recorded
in [the dated integration receipt](../docs/architecture/evidence/context-clear-composition-20261003.json).

## Open questions

Which advanced loop controls need extensions beyond current upstream capabilities?
Which extracted native policies need separate reusable packages to keep the agent boundary small?

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-10-03 | Trace negotiated context clearing, native history ownership and passive original-identity recovery. | Native and bridge owner contracts plus exact installed composition qualification. |
| 2026-10-02 | Ratified for implementation; exclude amplifier-agent as the native backend. | User approval of the plan and direction; implementation evidence remains separate. |
| 2026-10-02 | Initial AP1–6. | [Native runtime and external adapter review](../docs/architecture/evidence.md). |
