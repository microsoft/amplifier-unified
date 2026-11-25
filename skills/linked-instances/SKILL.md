---
name: linked-instances
description: Find owner-paired Amplifier installations and check their live status without reading their private conversations or starting work.
---

# Linked Amplifier installations

Each Amplifier has its own owner, profile and permissions. A link is one-way.
The owner of the destination must grant access from that installation. Pair,
join and revoke in Settings, never through agent tools. Treat offer codes as
private; do not put them in chat, output artifacts, or request logs.

1. List known destinations with `app_control` using
   `{"operation":"get_state","args":{"path":"/linkedInstances"}}`. The list
   reflects this installation's records, not reachability.
2. For an exact outgoing, non-revoked `instanceId`, use
   `{"operation":"get_state","args":{"target":{"instanceId":"<exact ID>"}}}`.
   Status tests only the saved route. `generationId` identifies the current
   server launch, not the installation. A failure is not proof the server is off.
3. With no explicit target, ordinary app operations remain local. Do not
   remove a target or substitute a local call to work around a refusal.
   This version has no remote chat start, Stop, history, files or arbitrary
   actions. It does not scan networks or wake offline installations.