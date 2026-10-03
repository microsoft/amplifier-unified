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
- Optional `serviceLifecycle: {"enabled": true}` explicitly enables the owned POSIX
  foreground service lifecycle. Omission preserves the existing nonservice mode.
  The installer allocates the installation and owner IDs; caller IDs are refused.

The installer owns `application.stateDirectory`, supervision, application-update
wiring and quiescence instance/scope. Supplying conflicting values is refused.
It writes private durable application, supervisor and input configuration, plus an
attempt receipt and the one-use pristine authority/claim. It resolves independent
sources, prepares exact signed bytes, and launches with the actual configured app
entrypoint. Missing discovery never means an empty installation. A failed or
uncertain attempt preserves its directory and cannot be repeated as a fresh install.

This is a foreground assembly, **not an OS service registration**. With the
optional service lifecycle enabled, the private supervisor and application
configurations retain the same installation/owner binding. The child verifies
that binding against its signed runtime and the supervisor launch environment.
SIGINT/SIGTERM on the installer request the external owner's qualified stop. The
child accepts shutdown only under a held update or service fence for its actual
instance. Busy work, incomplete participant coverage and unknown outcomes refuse
shutdown. No PID adoption, forced stop or automatic crash restart is performed.
Without the option, installer shutdown is refused as
`service_lifecycle_not_configured`; ordinary unsupervised preview behavior stays
unchanged.

Every configured participant must explicitly implement `serviceStop: {version: 1}`.
The launcher does not invent markers or downgrade the proof when coverage is
missing. The service owner persists its exit receipt, then explicit resume verifies
the retained release offline, starts a distinct instance, and reconciles the
persisted host and owner fences through authenticated service-specific receipts.
Readiness and reopened intake are separate states. Generic update/recovery proof
cannot release service-stop fences. An existing or unowned process is not adopted.
The application's update facade owns only its in-flight forwarding. Long-running
update jobs remain owned by the independent supervisor, which mutually excludes
competing update and service effects.

Installed qualification uses the immutable distribution artifact, then adds only
these installer files and a bin mapping before packing an independent consumer.
The signed test releases include the actual packaged app and exact component
inventory. Source observations use real Git over a local HTTPS read-only fixture
repository; this proves adapter/assembly behavior, not hosted production source
or registry/native/Python currency. It launches the CLI, refuses duplicates and
owned-config conflicts, updates through the app's public actions, reads the
replacement receipt, explicitly reconciles the completed install's host fence,
and rolls back offline. A ready update receipt can precede host fence settlement;
its separate settlement field confirms intake has reopened before a new action.

The optional `DISTRIBUTION_SERVICE_COMPOSITION=1` installed test builds and installs
an independent copy of the current assembled package, then exercises the real
signed CLI with resources and application-update owners. It checks active-turn
refusal, a signed application update, confirmed child exit, reopening the stopped
supervisor, explicit offline resume, exact fence settlement and no adoption.
It requires qualified artifacts for those configured owners. Passing this minimal
graph does not qualify other optional product owners, native agents, OS service
registration or a live deployment.
