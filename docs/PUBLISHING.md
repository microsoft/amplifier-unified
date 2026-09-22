# Managed static publishing

Publishing packages a project's built static output into an immutable release,
previews it on the server, records review of that exact version, and explicitly
activates the reviewed release. The Publishing settings page and agents use the
same `publishing.*` actions and task ownership checks.

The initial target is **loopback only**. Its URLs are reachable on the computer
running the service. A browser on another device cannot use those URLs. There is
no public hosting, authentication gateway, arbitrary application process runner,
cloud account configuration, or automatic restart of listeners in this target.
Static browser applications are supported; server-side application processes are
not. Publishing a site is separate from saving a conversation artifact.

## Workflow

1. Run the project's existing build with its documented tools in the task's
   execution folder. Build execution and its dependencies remain the project's
   responsibility. `publishing.build` packages the resulting directory; it does
   not run commands or install dependencies.
2. Call `publishing.build` with a relative `sourcePath`, a `siteId`, and a new
   `requestId`. Only regular, bounded, visible files are accepted. Symlinks and
   hidden/private paths are rejected, apart from an empty root `.nojekyll`
   build sentinel that is retained but never served. The release identifies the exact
   content manifest and copies the bytes into private managed storage. Source
   files remain unchanged.
3. Preview the release with `publishing.preview`. Inspect the content and any
   interactive behavior; a successful HTTP response alone is not visual review.
4. Record findings with `publishing.review`, specifying that exact `releaseId`
   and a review note. Recording a note does not independently verify its claim.
5. Inspect `publishing.list` or `publishing.status`, then explicitly call
   `publishing.deploy` with the reviewed release and site's `expectedRevision`
   (`0` for its first activation). The result records the target, access policy,
   immutable release, revision, and URL.
6. Build and review a new release to update the site. `publishing.rollback`
   restores a release previously deployed to the same site; it does not rebuild.
7. `publishing.stop` closes the site's listeners. `publishing.remove` removes
   the deployment. Both preserve sources, releases, reviews, and receipts.

Each mutation requires a stable caller-generated `requestId`. An exact retry
returns the recorded historical outcome without repeating the effect. Reusing
an ID with different arguments fails. A historical success can contain a URL
whose listener has since stopped: use status for present liveness. Every change
to a deployment requires its current revision, including stop and removal.

The app durably binds each build request to its original execution folder before
invoking the publisher. Exact retries can retrieve that saved result after a
handoff or source removal. Interrupted admission without a library receipt is
reported unknown and never replayed. Existing local releases remain controllable
after the task's execution folder moves to another host.

Lifecycle logs are durable operation receipts, not captured command output or
HTTP request bodies. They contain no build environment or provider credentials.
The app's data directory retains private publishing state. Chats owning releases
can be archived; destructive chat deletion is refused so audit ownership is not
silently lost.

## Recovery and separation

Interrupted operations retain an unknown outcome and are not replayed. On
restart, prior listeners are not treated as live. Inspect the status and receipt
before deliberately stopping or removing an uncertain local deployment. A new
deploy requires a fresh explicit action against the current revision.

The portable `amplifier_publishing` package owns immutable files, lifecycle
transitions, receipts, and the loopback HTTP adapter. It imports no app code.
`amplifier_web.publishing` adds task execution-folder confinement, agent calling
task checks, local-host metadata, and action schemas. The frontend calls those
same actions. No content is served from the app's authenticated origin.

Additional hosts require a separately qualified adapter with truthful access
policy, immutable release identity, deployment inspection, safe retries,
rollback, stop/removal, and restart reconciliation. Unsupported remote targets
must not be presented as working merely because local packaging succeeded.

The staged [private service and SSH transport](PUBLISHING-TARGET.md) supplies a
separate managed process with a private Unix admin socket and explicit network
bind configuration. It is not installed or selected by these app actions.
Remote host installation, client reachability, and the intended access policy
must be qualified before enabling a remote target in the app.
