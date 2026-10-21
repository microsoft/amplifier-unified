# Remote test origins qualification

## Behavior

A connected Linux testing machine can be the first and only backend on an
origin computer. No local Docker, Incus or DTU installation is required. New
remote checks use the app's deterministic source packager and the original
registered target's existing admission, operation and cleanup controls.

## Evidence

- Focused Linux source, receipt, queue, retained-configuration and recovery checks:
  41 passed, two native-Windows-only cases skipped. Local setup regression lane:
  27 passed. The existing task-environment lane also passed during development.
- Native macOS origin: registered an already configured Linux target using its
  existing SSH authentication and strict host-key verification; copied dirty and
  untracked source; excluded an ignored fixture file; executed a normal check
  successfully; downloaded retained archives; stopped and removed the exact
  owned guest; reconciliation confirmed absence and settled admission.
- No local Docker, Incus or managed snapshot interpreter was configured for that
  Mac origin. The Linux target retained its original configured engine, image,
  account policy and private controller protocol. No host setup was repeated.
- The portable origin CI lane runs actual Git snapshots, retained authority,
  Windows junction/ACL negatives, and target routing on Linux, macOS and Windows.
  This does not establish real Windows SSH-to-target execution by itself.
- The frontend is rebuilt with the locked dependencies and release Node version.
  Rendered Settings acceptance is recorded separately before readiness.

## Boundaries

Tests require a Git working-area root, Git on PATH, an installed SSH client,
non-interactive authentication and a previously trusted host key. They use an
already configured native Linux Incus target. Windows origin support does not
claim native Windows container provisioning, firmware configuration, WSL setup
or automatic Incus-engine updates from Windows. That existing updater limitation
is still reported explicitly. The Linux target can update independently.

Windows shared-write ACLs, directory junctions/reparse points and unsupported
file forms fail before transfer. POSIX parent-substitution tests verify that
selected file reads and private receipt publication cannot be redirected through
a replacement parent link. No test deletes an unrelated resource or changes a
shared daemon or production app.
