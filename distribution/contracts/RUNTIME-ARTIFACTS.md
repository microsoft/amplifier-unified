# Runtime artifact roles — implementation slice

[CB5](../../contracts/component-boundaries.v1.md) owns the desired packaging
contract. This slice adds an explicit profile to the existing signed release
descriptor; it does not migrate the current full-owner installation.

```json
{
  "schema": "runtime-artifact-v1",
  "kind": "executable",
  "interface": {"name": "acp", "version": "1"},
  "args": ["-I", "-B", "-m", "amplifier_acp"],
  "writableRoots": ["state"]
}
```

This is the descriptor's `profile`, not another manifest or policy engine.
Its existing `entrypoint` names a regular executable inside the artifact (for
example `bin/python3.13`). `static` profiles instead name an installed static
entrypoint and have no arguments or writable roots. The existing descriptor
retains platform/architecture, exact files, source components and artifact digest.
The existing signed channel authenticates it; `releaseDigest` includes the profile.
An interface label is a compatibility requirement, not protocol conformance proof.

`SignedReleaseAdapter.prepare` keeps its source resolver and freshness checks.
`verifyReleaseTree` remains the common complete-file verifier. Profiles can bind
standard npm or Python distribution metadata through a component's optional
`metadata: {kind, path}` relative to the artifact root. The component root still names its code; Python
metadata may live in the separate standard `.dist-info` directory. Every installed npm
package or Python `METADATA` file must have exactly one declared metadata binding.
Components without metadata describe source provenance only. They do not assert
that a package was installed. Existing unprofiled JS/npm descriptors retain their
original digest, package census and launch behavior; legacy runtime forests are
not promoted to artifact qualification.

`resolveRole(target, interface, writableRoots)` consumes the existing opaque
`PreparedRelease` and returns its signed identity and verified launch/static plan.
`resolveArtifactRoles` composes these independent results without comparing
interpreters, opening journals or allocating lifecycle authority. Installation
configuration supplies canonical writable directory paths separately. They cannot
contain or be contained by code. Signed names map to
`AMPLIFIER_RUNTIME_ROOT_<NAME>`; private configuration/secret references may remain
in installation-local launch configuration. Loader/import overrides are refused
on this profile path. Resolving a role does not start it or change current state.

The new path replaces external forest selection and cross-role interpreter
equality with each artifact's relative executable, exact bytes and declared
interface. It needs no private configuration hash or five-file derivation. The
legacy binder/helper remains available for legacy qualification and rollback;
no referenced generation, fence, unknown receipt or history is deleted.

Qualification currently includes signed Node legacy fixtures, static Web and role
composition fixtures, and one actual standard installed Python/publishing role on
Linux arm64. Python's executable, prefix, import paths and installed module stayed
inside that artifact; tampering and an outside-interpreter symlink were refused.
Fixture keys and source observations are synthetic. The Python case does not
qualify Native ACP, the full Host, upstream channel currency, platform portability,
application readiness, complete process custody or live activation. The current
full-owner launcher is still legacy; adopting profiles there requires a separately
qualified composition and readiness contract. No production release is produced.
