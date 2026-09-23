# Scoped bundle sources

Session preparation reads the shared Foundation registry into a fresh in-memory
view. Effective global, project, local and session settings are applied to that
view. Root bundles, app behaviors, routing behaviors and transitive includes use
the configured source overrides, including exact URI keys and namespace paths.
An override of a registered URI also applies when the root is selected by name.

Preparation never persists that view. Auto-discovered bundle names, resolved
source identities, include relationships and a final host `save()` cannot change
the shared registry. Concurrent sessions have separate registrations and load
caches. Source downloads can still share Foundation's content-addressed cache.

Explicit global registrations remain durable in the global settings file.
Foundation's normal persistent registration/update API is unchanged. This change
does not clear or migrate pre-existing registry entries: an earlier persisted
override is indistinguishable from an intentional global registration without
additional evidence.

This requires Foundation's `BundleRegistry(..., persist=False)` capability. Deploy
the Foundation companion before or with this host change. Do not substitute a
persistent registry when the capability is absent.

## Validation

Offline tests use actual Foundation composition with local fixtures and no model
calls or downloads. They cover project/local/session precedence, root names and
direct URI aliases, app behaviors, source-relative namespaces, transitive loads,
sequential and concurrent sessions, failed composition, and byte-identical shared
registry state. Explicit global settings remain effective for later sessions.

The existing bundle selection, host settings, source composition, resume, saved
snapshot, local bundle path and update-inventory tests also pass. These changes do
not alter configuration apply, native history, runtime controls or canvas state.
Live reprepare remains a separately coordinated operation; no running session was
restarted to validate this fix.
