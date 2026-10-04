# Fresh full-owner installation

The full-owner launcher takes one positional composition file. It cannot use the
flat configuration or `--config` child arguments emitted by the generic installer.
Use the dedicated external supervisor entrypoint for an explicitly new instance:

```sh
amplifier-unified-install-full-owner --config /absolute/private/install.json
```

The input has exactly these fields:

```json
{
  "schema": "unified-full-owner-installation-v1",
  "directory": "/absolute/new-instance",
  "compositionFile": "/absolute/private/composition.json"
}
```

Keep the supervisor package/current Node outside the replaceable signed release.
The command remains the supervisor; it does not install a service-manager unit,
probe/adopt another service, automatically retry, or enable restart-on-failure.
Signal handling uses the authenticated service-stop path and refuses busy or
unconfirmed owners. A closed browser alone is not a verified stop receipt.

## Prepare exact private inputs before signing

Use `unified-full-owner-fresh-composition-v1`. Preserve the existing full-owner
application configuration, review requirements and private input permissions.
Choose a lowercase UUIDv4 for `authority.installationId` before signing; it is a
planned identity, never permission to overwrite an installation. Set a stable
`ownerId` and `dataScope`. Instead of `release.prepared`, provide exactly:

```json
"initial": {"id": "release-id", "version": "1.0.0", "revision": "<full Git revision>"}
```

No digest is embedded in this configuration: the signed descriptor hashes its
exact bytes, so including the final release digest would create a hash cycle.
The verified channel supplies the full identity, including digest, which the
pristine installer permanently records. The release configuration also contains
`channelUrl`, `trustedKeysFile`, `accessScope`, `allowedArtifactOrigins`, and
optional explicit development-only `allowLoopbackHttp`. Sources use the existing
`sourcePolicy` with independent HTTPS Git observations. No publisher echo is a
source observation, and source overrides are not silently removed.

For installation directory `I`, use these exact paths:

| Configuration field | Path |
| --- | --- |
| `authority.sourceDirectory` | `I/source` (unused by fresh launch) |
| `authority.claimDirectory` | `I/claim` (unused by fresh launch) |
| `authority.supervisorDirectory` | `I/supervisor` |
| `authority.supervisorDiscoveryFile` | `I/supervisor.json` |
| `authority.supervisorTokenFile` | `I/supervisor-token` |
| `authority.hostDiscoveryFile` | `I/host-control.json` |
| `authority.hostTokenFile` | `I/host-token` |
| `application.stateDirectory` | `I/application` |
| `application.manualIngress.stateDirectory` | `I/ingress` |
| `receiptDirectory` | `I/receipts` |

The immutable composition, trust, TLS key/certificate and access code must already
exist outside `I`; `I` itself must not exist. The installer creates the host token.
Configure dedicated native homes/workspace/catalog and owner state, following
the full-owner qualification contract. This wrapper does not clone user history,
credentials, caches or another instance's private configuration. Ports must be
reserved by the deployment owner; final listener binding remains authoritative.
Use a separate browser profile/context because cookies share a hostname across
ports.

The signed archive must contain `unified-release-runtime-v3` with exact
`baseConfigurationSha256`, active release triple, complete `ownerRuntime`, and
`ownerCensus: {"profile":"native-message-metadata-v1"}`. The private base census
remains the original 20; only that signed profile adds the separately registered
message owner to the effective 21. An explicitly configured Terminal instead
requires the distinct `native-message-terminal-v1` profile: exactly the base20
plus message metadata and Terminal, for22 actual held owners. Its origin and
qualified feed must already be in the immutable private composition; the signed
profile cannot add or override those fields. Source/manual/recovery flags, prepared/initial
aliases and absent/older runtime descriptors refuse fresh launch.


## Transfer staging preflight

When portability is configured, `application.portability.stageDir` must be an
absolute directory path that resolves inside an authorized workspace root.
These are the resolved `application.allowedWorkspaceRoots` plus the configured
worktree owner's derived execution root,
`application.stateDirectory/capabilities/worktrees/git/checkouts`.
A future stage is allowed: the installer resolves its existing directory
ancestors and the missing tail without creating either. Existing symlinks use
their resolved targets before interpreting parent segments; dangling links,
non-directory ancestors and escapes outside the authorized roots refuse.
Containment uses whole path segments, including a workspace root of `/`.

Both public installers check before release work and again immediately before
pristine allocation. The full-owner installer also rechecks before launching.
The worktree path uses the same pure derivation as runtime composition; its
future directories are checked without constructing the owner or creating state.
Omitted or disabled portability adds no requirement. The runtime owner retains
its check after creating/resolving the stage. These read-only checks do not lock
the filesystem namespace or promise atomic protection against a later external
path replacement. They do not classify storage/backup completeness or authorize
changes to an existing failed or unknown installation.

## Allocation, interruptions and future releases

1. Validate private input, layout, reviewed composition and signed initial
   selection without creating the target installation.
2. Prepare and verify the archive, source currency and exact signed runtime/config
   binding in a separate private `.full-owner-prepare-*` sibling directory next to the
   target installation (the same filesystem). Retain its receipt/cache on interruption; it confers no launch
   authority.
3. Exclusively allocate `I` using `createPristineInstallation`, write the full
   signed initial identity and planned ID once, then move the verified cache.
4. Save exact inputs and an unknown launch receipt before the production
   supervisor consumes the genuine one-time claim. It launches the signed child
   through its current Node with precisely `[compositionFile]` arguments.
5. The child verifies the signed active archive and fixed receipt location, reads
   the private original authority and consumed claim, and binds the configured
   initial triple to that original identity. It verifies the signed v3 runtime
   and complete owner census before reporting readiness.

The initial identity is **not** compared against every future active release.
Updates and rollback retain the same original authority and configuration bytes;
each active candidate must independently pass signed descriptor/inventory checks
and the existing supervisor's prepared-target, admission and process-custody
rules. No caller-provided successor flag is accepted.

An allocated installation, partial authority, consumed claim or unknown startup
is never deleted or silently retried. Inspect `installer-attempt.json`,
`initial-provisioning.json`, `initial-provisioning.claim`, supervisor receipts and
child diagnostics. Merely invoking the installer again refuses existing state.
The generic `amplifier-unified-service --directory` reopening format is not this
composition; use the retained external production supervisor configuration and
existing authenticated service APIs, with `startInitial:false`, for explicit
supervisor reopen and receipt-qualified resume. Do not substitute a new initial
claim or infer permission from process absence.

## Qualification limits

Focused tests cover authentic public pristine allocation/claim, independent
source observations, an external current-Node supervisor launching a synthetic
signed child, refusal before allocation, unchanged initial evidence and signed
successor/rollback configuration binding. The synthetic child is not a claim
that all Python/app owners, accounts or browsers have been qualified. A real
full-owner deployment still requires its composed 21-owner and browser checks.


The Terminal profile has separate acceptance layers: fixed signed binding and
fresh one-shot installation tests; the actual 22-owner hold/backup-scope fixture;
and the real Linux ARM installer using the qualified TUI wheel with official
Node/Python archives. Synthetic signed-child tests do not establish a composed
22-owner production platform stop. The release coordinator must still qualify
its final assembled graph and user-facing setup before deployment.
