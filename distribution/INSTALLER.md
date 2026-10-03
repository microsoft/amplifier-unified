# Isolated production installer assembly

The proposed `amplifier-unified-install --config /absolute/private-installation.json`
entrypoint imports the public distribution Git source resolver and production
supervisor factory. Integration must register `src/install-cli.js` as that bin;
`installation.js` exports `installProductionDistribution` and
`readInstallationConfiguration` for trusted composition.

The private JSON schema is `unified-installation-v1` with:

- `directory`: a new absolute canonical namespace; an existing directory is refused.
- `dataScope`: the explicit installation scope.
- `release`: `channelUrl`, independently trusted `trustedKeys`, opaque `accessScope`,
  and `allowedArtifactOrigins`. `allowLoopbackHttp` is an explicit local-test option.
- `sourceTracking`: trusted configured `sources` repository/ref allowlist. Optional
  `git`, `env`, `timeoutMs`, `deadlineMs` and `concurrency` are the public Git source
  resolver's options. They are local installer authority, never publisher values.
- `application`: configured account, web directory, authorized workspace roots,
  default workspace, gateway and engines, plus supported optional app features.
- Optional `releaseId`; otherwise the freshly verified channel recommendation is used.

The installer owns `application.stateDirectory`, supervision, application-update
wiring and quiescence instance/scope. Supplying conflicting values is refused.
It writes private durable application, supervisor and input configuration, plus an
attempt receipt and the one-use pristine authority/claim. It resolves independent
sources, prepares exact signed bytes, and launches with the actual configured app
entrypoint. Missing discovery never means an empty installation. A failed or
uncertain attempt preserves its directory and cannot be repeated as a fresh install.

This is a foreground assembly, **not a qualified managed service**. SIGINT/SIGTERM
requests are currently explicitly refused as `service_lifecycle_not_configured`,
leaving supervisor and child reachable. Host/platform owners must implement the
stop/resume/adoption contract in `contracts/` before managed deployment. Killing
only the supervisor, adopting a PID, or inferring exit from missing discovery is
not safe. No OS service is created by this entrypoint.

Installed qualification uses the immutable distribution artifact, then adds only
these installer files and a bin mapping before packing an independent consumer.
The signed test releases include the actual packaged app and exact component
inventory. Source observations use real Git over a local HTTPS read-only fixture
repository; this proves adapter/assembly behavior, not hosted production source
or registry/native/Python currency. It launches the CLI, refuses duplicates and
owned-config conflicts, updates through the app's public actions, reads the
replacement receipt, explicitly reconciles the completed install's host fence,
and rolls back offline. A ready update receipt can precede host fence settlement;
passive reconciliation completes that boundary before the separate rollback action.
