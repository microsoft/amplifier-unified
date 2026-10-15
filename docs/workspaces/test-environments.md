# Task test environments

Test environments are an explicitly enabled capability for an operator-managed
Linux host. They are not a required consumer installation path. The initial
adapter uses a pinned Digital Twin Universe core in a managed Smart Tool
installation. It does not install Docker, change firmware, configure providers,
or author profiles with a model. Other platforms and a hosted runner are not
qualified by this implementation.

## Shared interface

The user interface and agents call the same host actions:

| Action | Input and result |
| --- | --- |
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

## Initial profile and limitations

The fixed Linux Python profile copies the task's actual Git working area into a
separate container volume. Snapshot provenance names its source and immutable
content. Tracked files and nonignored untracked files are included; symlink text
is preserved without reading its targets; extraction refuses links that leave
the container working folder. Git credentials, repository hooks and
provider credentials are not forwarded. This is not a guarantee that source
files contain no secrets. No original source directory or Docker socket is
mounted into the container.

The profile has an internal network, no published ports, a read-only image,
dropped Linux capabilities and limits for memory, CPU and process count.
Container volumes do **not** have a host disk quota. Dependencies are not installed
automatically, network package installation is unavailable, and remote browser
preview routing is not qualified. This first profile can run Python checks on
the copied source; it is not evidence of a general application deployment or
multi-service acceptance.

Stop retains containers and volumes. Remove requires observed stopped work and
successful bounded exports of both `/workspace` and `/artifacts`; failed export
blocks destruction. Archives are retained without extraction on the host. Local
snapshots and receipts remain after removal. Preparation that failed before
Docker admission can be retired without inventing a running environment.

## Operator setup and upgrade retention

Enablement lives in a private `test-environments.json` file in the app data home.
It identifies the exact managed core installation, qualified commit and local
Docker Unix endpoint. The reviewed image digest is fixed by the app. The app
reports disabled or unavailable when this setup is missing or does not match;
it does not automatically install a fallback or broaden permissions.

Keep the exact managed core installation and configuration until all of its
owned environments have been removed. Managed uninstall and replacement refuse
to delete an installation with unresolved environment references. Changing the backend pin or endpoint does
not migrate existing resources. The initial adapter refuses to manage them under
a different configuration; operators must retain the approved implementation
needed for their recovery before upgrading. Source tests, controlled-backend
browser tests and real Docker qualification remain separate evidence.
