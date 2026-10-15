# Combined copied-history and synthetic-runtime acceptance

Status: proposal only. No service, mount, database, or workload in this proposal
has been created. Implementation and startup require the parent review. The
existing read-only-execution replica and persistent development instance stay
available and unchanged.

## Purpose and boundaries

Measure ordinary navigation, selected recent history, and visible streaming with
the captured catalog/history scale and 0, 6, and 12 real RuntimeManager workers
in the same app. Workers use the already qualified deterministic local provider;
this does not qualify real provider latency, credentials, external integrations,
or historical tool execution. Keep the capture revision and event-tier completeness
in every result. Missing event shards cannot be reported as complete Work history.

## Proposed owned resources and mounts

Create a sibling `unified-scale-runtime-20261015.service` with its own private
RootDirectory, network namespace, loopback listener, Unix socket, browser context,
auth token, caches, and evidence. Use a separate account from the existing replica
where possible. No host port or existing preview service is replaced.

| Source | Namespace destination | Access |
| --- | --- | --- |
| Exact qualified app source and owned dependencies | Dedicated source/dependency paths | Read-only |
| Existing extracted native project directories | Original canonical native project paths | OS read-only binds |
| Existing extracted app content-addressed blobs | Blob lower layer | Read-only |
| New small owned blob upper/work directories | App blob merged view | Copy-on-write; never changes the lower |
| SQLite online backup of the sanitized app/catalog databases | Original app database paths | New writable branch |
| Fresh synthetic native project and workspace | Separate new project/workspace paths | Writable |
| Fresh config, runtime/session locks, cache, temporary files | Dedicated private paths | Writable |

Do not duplicate the expanded event corpus. Reuse the one extracted corpus through
read-only binds; retain its compressed immutable capture. Copy only the database
branch and small configuration required by the experiment. Inventory every bind
and verify its effective mount flags from inside the service namespace.

The writable app database can record navigation, indexes, caches, and synthetic
owners. This is not a claim that every imported metadata row is immutable. Copied
native files are OS read-only; copied app blobs have an immutable lower and a
disposable upper. Application-level owner guards additionally refuse imported
owner mutation. An overlay alone is not an execution or mutation fence.

## Execution admission contract

Start with an empty immutable allowlist. Before accepting browser requests, a
controlled fixture setup creates only new synthetic sessions in the fresh
synthetic workspace and records exact session IDs, native identities, workspace,
bundle/provider identity, and source-generation binding. Verify disjointness from
every captured identity. Freeze the allowlist for the entire measured process.
Failure to create or validate all bindings aborts startup.

Use the real RuntimeManager and ordinary worker bootstrap, with a narrow wrapper
that refuses start, send, warm, control, takeover, and child creation for every
owner outside that exact allowlist. An imported selected chat, a renamed owner,
or a caller-supplied synthetic-looking ID never grants authority. Revalidate the
full binding at each entry, not just ID membership. Disallow delegation entirely
unless a later reviewed protocol explicitly admits a new synthetic child.

Guard ordinary dispatch and agent app_bridge dispatch independently. Expand nested
shell commands before checking their actual effect. Permit bounded navigation and
read APIs for imported owners. Deny imported owner edit, delete, fork, resume,
stop, rename, rebind, configuration changes, publishing, schedules, and any other
mutation; do not depend on a small blacklist of known dangerous action names.
Unknown effect routes fail closed. Runtime teardown may stop only fixture-owned
processes and does not modify imported saved authority or handoff receipts.

Before app startup disable copied schedules, collaboration, observations that can
launch work, notifications, updates, publishing, connectors, and Smart Tools.
Use fresh private settings containing only the deterministic provider. Never load
copied account credentials, browser state, notification destinations, or forwarding
configuration. No historical task is replayed.

## Network and process proof before workloads

Require a private loopback-only namespace, empty proxy environment, no inherited
host sockets, and explicit fresh authentication. Prove external connectivity is
denied and only the owned app/local provider can be reached. A shared DTU loopback
is insufficient because other services may be listening there.

Before measuring, negative controls attempt imported-owner admission through user
dispatch, nested shell dispatch, agent bridge, direct runtime start/send/control,
rebound synthetic identity, and a new unlisted child. Assert rejection, no worker,
no provider call, and unchanged copied-file hashes. Positive controls require one
allowlisted worker to stream and stop normally. Record actual child cleanup.

## Measurement and reporting

Reuse the qualified real-worker benchmark recipe rather than introduce a second
runtime simulator: `tests/fixtures/runtime_concurrency_server.py`, its local
`amplifier_module_provider_concurrency` provider, and
`frontend/tests/runtime-concurrency-browser.mjs` in the integrated readiness
source. The fixture uses a real RuntimeManager with the qualified worker Python
and `runtime_worker.py`; the provider emits actual coordinator streaming hooks.
Its existing use of an already qualified interpreter bypasses fresh runtime
qualification and must remain an explicit limitation. Empty `bundle.app` and the
excluded image-generation module prevent implicit external modules.

Freeze one app/source/build/database baseline and capture tier.
Run 0/6/12 workers with exact admission/stream receipts and sampled copied-history
navigation in the same process. Record main-loop responsiveness, request latency,
stream cadence, CPU/RSS, query work, and cleanup. Pause capture I/O and other test
loads during comparable timing windows. Warm and cold observations remain separate.

Retain compact sanitized receipts outside the private data tree. No transcript,
credential, workspace content, or private title is committed. Do not describe an
inert scale-only run or a small synthetic-only run as combined acceptance.

## Cleanup

Record all resources under `readinessReplica20261015` before startup. After review,
stop only the sibling service, confirm no processes or open file references, unmount
its binds/overlay, and remove its writable database branch, blob upper, socket,
browser profile, and temporary source. Preserve immutable captures, the original
reading replica, persistent development chats, and compact evidence.
