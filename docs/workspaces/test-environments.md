# Task test environments

Test environments are an explicitly enabled capability for a managed
Linux host. The workspace controls provide setup against an installed local
Docker or Incus service. They are not a required consumer installation path. The
adapter uses a pinned Digital Twin Universe core in a managed Smart Tool
installation. It does not install Docker, change firmware, configure providers,
or author profiles with a model. Other platforms and a hosted runner are not
qualified by this implementation.

## Shared interface

The user interface and agents call the same host actions:

| Action | Input and result |
| --- | --- |
| `workspace.environments.setup` | Existing backend choice and stable request identity; approved runner installation, offline probe and private configuration. |
| `workspace.environments.setup.reconcile` | Passive inspection of the original setup and its retained effects. |
| `workspace.environments.setup.cleanup` | Remove only verified disposable resources from interrupted setup. |
| `workspace.environments.read` | Workspace and root conversation identity; bounded resources, availability, source location, current observation and retained results. |
| `workspace.environments.start` | Approved profile plus stable `requestId`; immutable source snapshot and one owned environment. |
| `workspace.environments.execute` | Resource identity, exact revision, stable request identity and command; bounded stdout, stderr, exit code and truncation flags. |
| `workspace.environments.stop` | Resource identity, exact revision and stable request identity; observed stopping outcome with data retained. |
| `workspace.environments.remove` | The same ownership and revision checks; source and artifact archives retained before observed teardown. |

The resource ledger owns allocation identity and lifetime. The existing operation
request journal owns command admission and results. A duplicate request returns
its receipt. An interrupted or timed-out command remains uncertain and is never
automatically replayed. A read can observe containers without proving that an
earlier command finished. Stop of a conversation revokes agent approval; it does
not itself claim to stop an environment or cancel an in-container command.
Manual operations serialize for each environment. Queued checks use durable
guest supervision: cancellation requests termination, host restarts observe the
same job, and cleanup waits for a confirmed terminal result. The manual execute
action retains its bounded synchronous semantics. See the
[managed runner contract](managed-test-runner.md) and [queued checks](../QUEUED-TESTS.md).

Agents need an explicit, session-scoped `test_environments` capability grant,
their current root execution binding, and an unblocked task assignment. A peer
cannot borrow that authority. A direct user action authorizes that action through
the same host adapter. Approval can be revoked while an operation waits; the
adapter rechecks before the external effect begins.

The action interface and durable ownership are independent of the DTU library.
The initial backend boundary is a host-owned `prepare/start/read/execute/stop/remove`
invocation with owner-derived paths, stable identities and bounded JSON receipts.
Only its private helper imports DTU. A future provider must implement that
boundary and qualify ownership, recovery, source fidelity and retention; exposing
a generic MCP endpoint is not an equivalent adapter.

## Profiles and limitations

The fixed Linux Python profile copies the task's actual Git working area into a
separate container volume. Snapshot provenance names its source and immutable
content. A private, durable readiness receipt binds successful source extraction
to that exact universe and snapshot digest. A running container without that
receipt remains unready for checks; inspection never repeats source extraction.
Stop and preservation before removal remain available for partial source copies.
Tracked files and nonignored untracked files are included; symlink text
is preserved without reading its targets; extraction refuses links that leave
the container working folder. Git credentials, repository hooks and
provider credentials are not forwarded. This is not a guarantee that source
files contain no secrets. No original source directory or Docker socket is
mounted into the container.

Docker tests use an internal network, no published ports, a read-only image,
four narrowly selected supervisor capabilities and limits for memory, CPU and
process count. Commands run as an unprivileged user; supervisor records are
private to root. Incus uses its separate unprivileged offline guest boundary.
Container volumes do **not** have a host disk quota. The small Python profile
runs for up to five minutes without preparation. Python project and browser
profiles prepare locked dependencies in a separate owned networked builder,
then run offline for twenty minutes by default (thirty maximum). Preparation
does not receive host credentials or source mounts. Remote browser preview
routing and general multi-service deployment remain unqualified.

Stop retains containers and volumes. Remove requires observed stopped work and
successful bounded exports of both `/workspace` and `/artifacts`; failed export
blocks destruction. Archives are retained without extraction on the host. Local
snapshots and receipts remain after removal. Owned dependency images and builders
must also be confirmed removed before their resource owner is retired.

## Operator setup and upgrade retention

The supported setup action writes a private `test-environments.json` file in the app data home.
It identifies the exact managed core installation, qualified commit and local
Docker Unix endpoint. The reviewed image digest is fixed by the app. The app
reports disabled or unavailable when this setup is missing or does not match.
Setup downloads approved dependencies only after an explicit user action or a
scoped `test_host_setup` agent grant; ordinary inspection does not prompt.

Keep the exact managed core installation and configuration until all of its
owned environments have been removed. Managed uninstall and replacement refuse
to delete an installation with unresolved environment references. Changing the backend pin or endpoint does
not migrate existing resources. The initial adapter refuses to manage them under
a different configuration; operators must retain the approved implementation
needed for their recovery before upgrading. Source tests, controlled-backend
browser tests and real Docker qualification remain separate evidence.
