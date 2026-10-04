# Signed successors of the full-owner preview

The full-owner launcher supports a signed successor without rewriting the private
installation composition passed by the existing supervisor. Source/bootstrap
launches still require the exact original prepared release. Normal supervised
launches accept either that unchanged original release (including rollback), or
a verified signed release with the binding below. A different release with no
binding is refused before owners, state writes, or network listeners.

This change applies to `src/full-owner-launcher.mjs`. The generic CLI,
installation/service launchers and native runtime-generation selection are
separate contracts; they gain no new overlay or authority from this mechanism.
The distribution updater still restarts the whole application child. This is
not a live replacement of MCP or the frontend inside a running process.

## Publisher inputs

After the reviewed composition commit is known, generate
`package/release-runtime.json`:

~~~json
{
  "schema": "unified-release-runtime-v1",
  "release": {
    "id": "qualified-successor",
    "version": "1.0.0",
    "revision": "FULL_COMPOSITION_COMMIT"
  },
  "baseConfigurationSha256": "SHA256_OF_EXACT_UNCHANGED_PRIVATE_COMPOSITION_BYTES",
  "webDirectory": "web",
  "mcpRuntime": "release-inputs/mcp-runtime.json"
}
~~~

The descriptor is derived assembly metadata. Include it, the built `web/`
assets, the MCP manifest, and the launcher/helper in the final signed file
inventory. Then archive, sign, and qualify those exact bytes. Do not embed the
release digest in its own archive or commit an archive containing its own
unknown future source revision. The verified signed identity must match all
three descriptor release fields.

The existing supervisor keeps its configuration, process ownership, launch
arguments, source policy, and authority. Publisher discovery and source
eligibility checks remain unchanged. A source policy supporting only the
composition repository cannot silently gain additional repositories from a
release. The assembly's component provenance must continue recording actual
upstream source revisions and artifact hashes.

Only two application fields can change: `webDirectory` and `mcp.python`.
Other fields, including native engines, state directories, accounts,
credentials, recovery inputs, owner declarations and private control paths,
are preserved exactly. Unknown descriptor keys and MCP configurations with an
explicit command or external broker are rejected; those would bypass the bound
Python executable.

## Seal the separately qualified MCP runtime

Install the reviewed wheel into a dedicated, non-editable environment. Qualify
its actual interpreter, import origins, dependencies and official transport
before sealing it. Do not reuse a mutable test environment. Keep all credentials,
databases, caches, installation operations and conversations outside runtime
trees.

Use the publisher-side helper (read-only; executes no Python):

~~~js
import {inventoryMcpRuntime} from './src/release-runtime.mjs';

const manifest = await inventoryMcpRuntime({
  trees: [
    {id: 'environment', root: '/absolute/owned/runtime/environment'},
    {id: 'interpreter', root: '/absolute/owned/runtime/cpython-prefix'}
  ],
  python: {tree: 'environment', path: 'bin/python'},
  qualificationReceiptSha256: 'SHA256_OF_THE_REVIEWED_QUALIFICATION_RECEIPT'
});
// Save JSON.stringify(manifest) to package/release-inputs/mcp-runtime.json.
~~~

The executable may be a symlink, but its target must be inside a fully
inventoried tree. Include the exact managed CPython prefix, not a mutable parent
containing many interpreters. Roots must be canonical, non-overlapping, owned
directories. Every file, directory, mode and symlink is bound; extra, missing,
changed, unsupported or group/world-writable entries refuse launch. Remove or
seal an unused build lock in the NEW environment before inventorying it.
Never normalize the old environment in place.

Limits: eight trees, 200,000 entries per tree and a 64 MiB manifest. The helper
streams file hashing. The default MCP launcher passes explicit `-B` alongside
`-I`: isolated Python ignores `PYTHONDONTWRITEBYTECODE`, so the environment
variable alone is insufficient. Do not install packages or generate caches into
sealed trees.
Retain the original environment and web assets for rollback.

Runtime readiness rechecks the external manifest in addition to the signed JS
tree. Concurrent readiness reads share only the in-flight verification. A ready
receipt includes the base-configuration hash, manifest hash and qualification
receipt hash. The qualification digest links evidence; it is not an independent
proof that every module, account or tool works. Actual import/transport and
rendered-browser qualification remains required.

This is local integrity verification under the same ownership assumptions as
the signed JS archive, not remote attestation or protection against a malicious
same-user writer racing file verification and execution. Runtime trees are
operator-sealed inputs for their lifetime, not an updateable package cache.
The manifest does not infer arbitrary dynamic Python imports; qualification
must exclude editable imports and undeclared external code dependencies.

## Prepare, activate and roll back

Use the existing public supervisor connection. Each new mutation gets a recorded,
unique command ID. Manual dispatch starts immediately; observe pushed progress
and inspect the original receipt after a lost response. Never repeat an
uncertain mutation using a new ID.

1. `check` with `{"fresh":true}`.
2. `prepare` with `{"releaseId":"qualified-successor"}`. Verify its
   `succeeded/prepared` receipt and `inspect().staged`.
3. Coordinate a work boundary. In the current preview an open WebSocket retains
   ingress admission, even if the browser looks idle. Disconnect the browser
   explicitly; do not forcibly interrupt active work.
4. `activate` with the exact `preparedCommandId`, `targetDigest` and
   `expectedCurrentId`. The host acquires all declared owners, checks active
   work and gracefully retires idle resident native workers.
5. Verify `succeeded/ready`, settled admission, actual running identity, and
   the new web and Python graph. Sign in again if the preview's process-local
   login session expired.
6. A deliberate rollback uses the public `rollback` operation with the exact
   current release ID. It uses the retained original composition and runtime,
   without requiring a still-current source ref.

Keep the supervisor running: restarting it merely to change launch arguments
loses the in-process child handle. Do not use initial provisioning, manual-source
bootstrap, consumed permits, ledger edits or generic instance commands as an
update shortcut. Unknown outcomes require reconciliation of the original
receipt, never speculative replay.

## Qualification

`node --test test/release-runtime.test.mjs test/full-owner-launcher.test.mjs`
covers the binding and pre-owner rejection rules.

The opt-in `test/signed-successor.integration.test.mjs` requires
`SIGNED_SUCCESSOR_ACCEPTANCE=1`, `SUCCESSOR_BASE_PACKAGE` (the original signed
package directory), and `SUCCESSOR_BASE_PYTHON` (a separately qualified complete
owner/native/catalog environment). It creates independent copies and private
state, local TLS and signed-channel fixtures. Optional `SUCCESSOR_MCP_PYTHON`
selects a separately qualified successor MCP environment; otherwise an independent
copy of the baseline is used. The MCP copies are separate from the mutable
native-owner environment. It uses the actual original
launcher, public prepare/activate API, retained supervisor, all twenty owners,
real native initialization and real MCP initialization. It checks connected
WebSocket refusal, explicit disconnection, successor activation, original-release
rollback, and preservation of canonical history and base composition bytes.
Its Python wrapper and static pages are harmless fixture discriminators, not
production tool or browser acceptance. No paid inference runs.

Optional `SIGNED_SUCCESSOR_RECEIPT` saves the qualification receipt. Fixture
state is retained for inspection. Final production assembly and real user/browser
acceptance remain the deployment owner's responsibility.
