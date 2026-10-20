# Managed test runner

This extends the existing task environment and queued-check contracts. The host
owns setup, admission, preparation, execution and evidence-preserving cleanup.
The user and agent use the same actions. A browser tab or model turn is not the
lifetime of an accepted check.

## Setup and readiness

`workspace.environments.setup` selects an existing Docker or Incus service and
installs the exact qualified deterministic tool dependencies into app-owned
storage. It accepts a backend choice and stable request identity, never an
arbitrary executable, image, socket, command or credentials. Setup does not
install a container daemon, alter firmware, restart shared services or configure
a model provider. Missing platform or backend requirements are reported before
an environment is launched.

Setup is a separate host effect: a direct user action authorizes it, while an
agent needs `test_host_setup` in global scope. This does not grant authority to
change unrelated configuration. Routine task-owned testing needs no separate `test_environments` approval.
Root ownership, Stop and current backend identity still govern checks. Passive
discovery and result reads do not request configuration authority.

Setup retains its identity and phase before effects. Repeating a request returns
its retained result; interrupted effects are observed, never blindly repeated.
Ready requires an actual backend probe. Readiness includes its observation time
and does not claim every project's dependencies are prepared. Upgrading a backend
does not replace the retained backend of an existing environment.

## Runner update policy

App-managed runner packages follow their declared moving upstream source under
the global update preference. A resolved commit is an installation identity,
not a standing update pin. Explicit user pins remain explicit. Existing
app-generated pins can be migrated only with an owned setup receipt establishing
their origin; matching a package name or a familiar commit is insufficient.

A runner update installs separately and qualifies the exact package,
dependencies, adapter protocol and configured execution target before selection.
The qualification includes a task-owned source snapshot and actual offline
execution, result retention and owned cleanup. Matching a version number or a
self-reported schema does not qualify a runner. Updating package code must not
rerun host setup, alter a daemon/project/network, or reset operator limits.

Selection is an atomic change for future accepted checks. Existing jobs,
environments, unknown outcomes and rollback references retain their exact
installation, controller, configuration and execution target. A lost reply or
restart reconciles the saved operation; it does not repeat a check or replace an
unknown environment. Old packages remain available until all references are
settled. Each registered machine qualifies its own configured backend; a probe
on another machine is not acceptance evidence.

The updater reports a failed qualification plainly and continues using the
previous runner. Unsupported configured installations remain visible as
owner-managed or explicitly pinned; they must not be reported as current merely
because no automatic update path is implemented.

## Prepared profiles

`linux-python` remains available for short checks without project preparation.
`python-project` and `browser-test` prepare locked project dependencies from the
exact copied source. The browser profile includes Chromium and an isolated X11
display capability, with a longer advertised execution budget. Capabilities name
the supported profile, timeout and resource limits; unsupported lockfiles or
dependencies fail before the check starts.

The normal path is one queued check. When admitted, the host freezes the Git
working area, prepares its dependencies and starts the test environment. It does
not require a user or agent to hand-author private backend configuration or
manually build an image. Preparation runs separately with network access needed
for dependency retrieval, fixed host-owned recipes, no original writable source
mounts and no inherited provider credentials. Test execution is offline. A
private dependency that needs unavailable authentication is a preparation failure,
not permission to reuse an unrelated host environment.

Preparation records the source and lockfile hashes, recipe identity, dependency
inventory, exact image identity, phase and owned builder. These are evidence,
not permission to substitute an image by name. Temporary builders and prepared
images have explicit owners and cleanup outcomes; failed or unknown cleanup is
not hidden as a successful check. Repeated preparation must not accumulate
unowned images or silently replay an interrupted build.

## Durable execution

New queued checks have a guest job identity bound to the existing root, launch,
source and command. The guest supervisor retains process identity, bounded output,
deadline and terminal result independently of the host connection. Commands run
without write access to the supervisor's control records. Lost acknowledgements
and host restarts observe that exact job; they do not run its command again.

Cancellation is a retained request, followed by observed termination of the owned
job or preparation process. A cancelled UI wait is not evidence that the process
stopped. Unverifiable process or backend identity leaves the result unknown and
preserves ownership. Cleanup begins only at a confirmed terminal boundary.
Old operation receipts retain their prior semantics and are never converted into
new executions during recovery.

Two root chats can make independent progress subject to physical resource
admission. Waiting for capacity belongs to the host queue, not repeated model
polling or peer-message exchanges. Completion notifications use the existing
saved subscription and current conversation guard; notification delivery failure
does not rerun a completed test.

## Qualification

Source tests, real container checks, browser controls, and installed adoption are
separate evidence. Qualification includes setup through shared actions, Python
and frontend workloads, the complete long coexistence workload, two roots,
cancellation, a host restart while checking, retained output, notification and
owned resource cleanup. Linux qualification does not imply native macOS,
Windows, WSL or a hosted-runner path is supported. Interactive access to an app
inside the test environment requires a separately qualified preview route;
headless browser tests inside the environment do not establish that capability.
