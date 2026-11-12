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

## Referenced resource namespaces

An effective instruction can reference a resource in a registered namespace
without composing that bundle's tools or includes. Ordinary preparation resolves
only such missing prose namespaces through the same selected source overrides
and admission observer. It reads the selected manifest's resource-root metadata,
not its capability graph. Existing composed namespace roots win; unknown
namespaces and missing or escaping files remain unresolved and block portable
export. A disabled capability is not restored merely to supply its resource.

This work runs at ordinary preparation after runtime edits and instruction-file
inclusion, not at initial composition, per provider request or export. Its
transitive discovery scan stops at 500 files, 1 MB per file and 8 MB total, plus
a 500-namespace bound. Reaching a discovery bound records a warning; it does not
make an otherwise valid ordinary startup fail. Portable export retains its own
strict size limits and unresolved-reference refusal. The scan does not visit unrelated cached
repositories. Snapshots do not resolve current registrations or fetch new inputs.
Export reads these preparation-owned bindings locally and keeps its strict
missing-resource refusal; it never downloads a namespace behind a selected plan.

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
