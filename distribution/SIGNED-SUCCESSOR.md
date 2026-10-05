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

In v1 only two application fields can change: `webDirectory` and `mcp.python`.
V2 additionally supports the narrowly bound native launcher grants described
below. State directories, accounts, credential values, recovery inputs, owner
declarations and private control paths are preserved exactly. Unknown descriptor keys and MCP configurations with an
explicit command or external broker are rejected; those would bypass the bound
Python executable.


## Explicit native launcher grants

A successor may use `unified-release-runtime-v2` with all v1 fields plus:

~~~json
"nativeLauncher": {
  "engineId": "amplifier",
  "baseConfigurationSha256": "SHA256_OF_EXACT_PRIVATE_NATIVE_CONFIG_BYTES",
  "configuration": "release-inputs/native.json",
  "grants": {
    "adminVoiceCredentials": true,
    "adminVoicePreferences": true,
    "adminGenerations": true,
    "runtimeImmutable": true
  },
  "qualificationReceiptSha256": "SHA256_OF_INSTALLED_NATIVE_QUALIFICATION"
}
~~~

This is an explicit operator-reviewed, publisher-signed grant, not permission
inferred from the presence of a settings page. The generated native JSON must
equal the private base JSON with only the declared boolean grants applied.
Each administration grant may be omitted or explicitly revoked with `false`.
The optional `adminVoicePreferences` boolean uses the same exact-value binding.
Existing descriptors with only the prior grants remain valid; omission preserves
the private base value and never enables preferences automatically.
The `runtimeImmutable` policy grant accepts only literal `true`: false, strings,
numbers and other values refuse. Existing true immutability cannot be removed or
weakened; a malformed base value refuses rather than being silently normalized.
Other grant names, non-booleans, missing review evidence, changed homes, roots, module
sources, runtimes, credentials or other native settings refuse startup.

The selected engine must be the unique `nativeAdmin.engine`, with the reviewed
Python argument shape `-I -B -m amplifier_acp --config <absolute-private-file>`.
Only the final config path changes in the in-memory composition. Its executable,
environment and all other engines remain unchanged. Other launcher shapes need
a separate contract; no general argument or configuration overlay is accepted.
The base config must be a canonical private file owned by the launching user.
Its exact bytes and the signed candidate are revalidated at readiness.

Generate this installation-specific configuration during private assembly;
do not commit personal paths or credential values to source or public release
metadata. Include the generated candidate in the signed archive inventory.
The immutable base composition and native config remain available for rollback.
Native runtime code changes and any separately qualified executable adoption
are outside this grant-only contract.

`adminGenerations` enables the native generation owner used by
`updates.check` and runtime preparation/selection. `adminMaintenance` governs
a separate private backup/restore/cache owner and does not imply generation
permission. Neither grants application update authority: that remains owned
by the distribution supervisor. `adminVoiceCredentials` permits the native
credential owner to provide keys through the existing private server boundary;
it does not grant browser credential access or imply that an account is ready.
`adminVoicePreferences` independently permits the native shared preference
update/receipt boundary and its negotiated fields. It does not enable credential
access, create a voice call, or imply microphone/provider availability.

`runtimeImmutable: true` is a top-level native launcher JSON field, independent
of generation markers. The signed candidate carries it through the selected
`amplifier_acp --config` path without changing the private base, executable or
environment. The binding receipt includes the candidate hash, exact declared
grants, and `runtimeImmutable: true` when the resulting JSON requires it, including
an inherited true value. Absence of that receipt field does not claim a mutable
runtime: other native policy sources may also require immutability.

This is configuration binding, not proof of downstream enforcement. The qualified
native owner must monotonically propagate the restriction to Server, Admin,
conversation workers and preparation/probe subprocesses. Ordinary session/bundle
preparation may not install dependencies into the signed runtime. Explicit
preparation of a new separately owned staged environment remains the native
update owner's responsibility. Adoption requires that native implementation and
installed all-path qualification; this change alone does not qualify an older
native binary or change an already-running installation.

Qualification:
- `node --test test/release-runtime.test.mjs test/full-owner-launcher.test.mjs`
  covers allowed deltas, explicit revocation, unauthorized changes, private-file
  and identity checks, readiness drift, and unchanged source/rollback behavior.
- `NATIVE_GRANTS_TEST_PYTHON=/absolute/qualified/python node --test test/native-launcher-grants.integration.test.mjs`
  starts the actual installed native broker with isolated empty homes. It
  reproduces the ungranted refusal, verifies advertised grants and a passive
  generation receipt read, and confirms credential access is enabled with no
  keys present. It exercises an isolated preference update and exact receipt,
  then explicit preference revocation while preserving the saved value. It makes
  no update check after enabling, network/account call,
  session turn or microphone request.
- `NATIVE_IMMUTABLE_TEST_PYTHON=/absolute/qualified/python node --test test/native-launcher-grants.integration.test.mjs`
  exercises the immutable grant against an installed native broker and fresh
  resident worker. The interpreter must already contain the qualified native,
  Foundation, Core, loop-live and context-simple modules; the test installs
  nothing. It verifies the initialization and resident-worker policy markers
  with inherited `AMPLIFIER_RUNTIME_IMMUTABLE=0`, no generation marker, and
  unchanged private config/settings bytes. An offline mount-only provider
  refuses completion, and an installer sentinel must remain untouched. Optional
  `NATIVE_IMMUTABLE_ACCEPTANCE=/absolute/receipt.json` retains the test evidence.
  The interpreter inventory covers regular non-symlink files except bytecode;
  referenced dependency trees require their own qualification. Signature
  verification is a caller precondition, and the mount/policy report is not
  loaded-code attestation or full release acceptance.

An old host receipt classified as unknown must not be rewritten or replayed
solely because a grant is now enabled. A proven pre-effect launcher refusal
should be surfaced as refusal by the native/host error contract. Missing
responses after dispatch still remain unknown. These are separate from signing
and adopting an explicitly reviewed successor configuration.


## Bound native, catalog and media runtime

A successor may use `unified-release-runtime-v3` with all v2 fields and:

~~~json
"ownerRuntime": {
  "profile": "native-catalog-media-v1",
  "engineId": "amplifier",
  "manifest": "release-inputs/owner-python-runtime.json",
  "qualificationReceipt": "release-inputs/owner-qualification.json",
  "mediaMode": "installed"
}
~~~

This fixed profile replaces only the selected native engine's `command`,
`catalogProcess.command` and `media.python` with the inventoried venv
Python path, and sets `media.pythonMode` to `installed`. All three existing
executable slots must agree on an absolute original Python path. The native
engine must be the unique admin engine and retain its exact v2 argument shape.
Catalog must retain its reviewed `-I -B -m amplifier_session_catalog serve`
shape followed, in order, by `--db`, `--home`, `--app-home`,
`--workspace` and both scan intervals set to zero. Existing path arguments,
environments, other engines and private state stay unchanged. Only the v2
grant binding may change the native `--config` argument.

The media owner must support `createMediaCapability({python, pythonMode:'installed'})`
and launch exactly `-I -B -m amplifier_unified_media.worker` without a
PYTHONPATH/PYTHONHOME overlay. An explicit different base mode or a command/broker
bypass is refused. The composition forwards this mode to the owner; merely
replacing its Python executable would otherwise keep loading bundled source.
The application retains the owner's package-source default outside this signed
profile. Deploy both the qualified media Node package and installed Python wheel.

`inventoryPythonRuntime(options)` and `verifyPythonRuntime(manifest)` use
the same tree/file checks and ordering as the unchanged MCP v1 APIs, with
a distinct `unified-python-runtime-v1` schema. Inventory the complete venv,
interpreter prefix and all immutable external module source trees actually used.
V3 permits at most 128 roots so individual retained code checkouts can be sealed;
MCP v1 retains its eight-root limit. The publisher inventory and launch/readiness
verifier share this bound. Earlier verifiers limited Python inventories to 32
roots and reject larger manifests; update the packaging and verifying code
together rather than omitting sources or grouping a mutable registry parent.
Roots remain distinct, canonical, non-overlapping and owned by the current user.
All descendants are verified, including lock-like files and bytecode if present.
The 64 MiB packaged manifest limit, 200,000 entries per tree, sequential tree
walks and at most 16 simultaneous file hashes are unchanged. Operational registry
install-state, source binding metadata and locks remain outside these code roots
as owner state.
Registry configuration paths need not change: inventory the exact retained code
checkout directories they already resolve to. Record allowed registry bookkeeping
writes separately during cold qualification. There are no exclusions inside a
sealed tree, and no claim that the entire mutable registry parent is immutable.
Concurrent source mutation or a changed configured import path blocks adoption.

Symlinks must stay within those trees; retain the venv invocation path instead
of resolving it to the base interpreter. Never install over the original runtime.

Before inventorying, separately qualify the three real launchers in an owned
cold environment with isolated state. Save a signed qualification artifact:

~~~json
{
  "schema": "unified-python-runtime-qualification-v1",
  "profile": "native-catalog-media-v1",
  "python": {"tree": "runtime", "path": "python/bin/python"},
  "launches": [
    {
      "role": "native",
      "module": "amplifier_acp",
      "flags": ["-I", "-B"],
      "moduleFile": {"tree": "runtime", "path": "python/lib/python3.13/site-packages/amplifier_acp/__main__.py"},
      "importPaths": ["/absolute/owned/runtime/python/lib/python3.13/site-packages", "/absolute/owned/runtime/cpython/lib/python3.13"],
      "noRuntimeWrites": true,
      "editableInstalls": false
    }
  ]
}
~~~

The abbreviated example shows one launch; the real receipt requires exactly
three unique roles: native (`amplifier_acp`), catalog
(`amplifier_session_catalog`), and media
(`amplifier_unified_media.worker`). Record each actual executed primary module (the package __main__.py or media worker.py)
file and actual Python import paths, explicit isolation/bytecode flags, absence
of editable installs, and a before/after runtime census proving no writes.
All recorded paths must stay within the inventory; each primary module file
must be an inventoried regular file. Include richer loaded-module hashes and
artifact provenance in the accompanying qualification evidence. Do not invent
a receipt from configuration values or treat the example paths as installation
instructions.

Set the runtime manifest's `qualificationReceiptSha256` to the exact receipt
bytes. Include both files in the signed release inventory. Binding checks the
receipt schema and its recorded claims; it does not execute Python or prove that
unexercised dynamic imports, tools, devices or accounts work. The deployment
owner must review the actual qualification evidence and source currency.

Readiness rechecks all runtime trees and both signed input files. The binding
receipt records the profile, selected engine, manifest and qualification hashes.
V1/v2 and original bootstrap/rollback continue using their original contracts;
v3 does not mutate an old release or grant undeclared native permissions.


### Qualification evidence sources

A v3 descriptor may additionally declare exactly
`"ownerCensus":{"profile":"native-message-metadata-v1"}`. This fixed profile
requires the separate `native-message-metadata` quiescence participant in
addition to every owner in the validated private base census. The binding
returns this effective census separately and records it in the readiness
binding; it never edits the base configuration's `expectedOwners`. Startup
compares the complete actual census exactly, including duplicate detection.
Missing message negotiation, missing base owners, renamed participants and
unreviewed extras all refuse readiness. The new message connection must retain
its own fence; the native administration lease does not cover another pipe.

The profile cannot supply arbitrary owner lists, remove owners, change storage
coverage, or grant account/configuration authority. It is declared only in the
existing signed `release-runtime.json`; no extra private configuration file is
created. Source/bootstrap, v1/v2, and v3 without this profile preserve their
original census contracts. Unknown storage coverage still prevents a complete
installation archive; adding a startup participant does not qualify backup.

Receipt v1 remains unchanged: its per-launch `importPaths` must have been
observed in that actual worker. Do not fill it from a separate interpreter probe,
a configured allowlist, or a superset of permitted roots.

When the existing owner protocol does not expose worker `sys.path`, use
`unified-python-runtime-qualification-v2`. This is a new evidence contract;
it does not change the release v3 descriptor or its configuration authority.
Keep `profile` and `python`, and record separately:

- `importCensus`: `{kind:'isolated-python-import-census', argvPrefix:['-I','-B','-c'], importPaths, entrypoints:[{role,moduleFile}], editableInstalls:false}`.
  Run this separate probe with the same immutable interpreter and installed graph.
  Record its observed paths and resolved native/catalog/media entrypoint files.
- `launches`: exactly three real owner launches, each with `role`,
  `module`, `argvPrefix:['-I','-B','-m',module]`,
  `readiness:'protocol-response'`, `moduleFile`,
  `moduleFileEvidence`, `workerImportPaths`, and
  `noRuntimeWrites:true`. Save the real launch and protocol response evidence;
  importing a module or running it through a tracing `-c`/runpy wrapper does
  not qualify as this actual `-m` launch.
- `moduleFileEvidence` is `entrypoint-resolution` when resolved by the
  independent census, or `worker-inspection` when the owner actually reported
  it. It must match the census entrypoint and remain an inventoried regular file.
- `workerImportPaths` is explicitly `null` when the worker does not expose
  it. If independently observed, record those exact paths. Every recorded census
  or worker path must still remain within the inventoried roots.
- `nativeMountedOriginsReceiptSha256` links the companion evidence from the
  actual fresh native worker's mounted-origin inspection. The runtime qualifier
  must review that receipt against configured source identities and the sealed
  code trees; it is not replaced by the interpreter census.

The receipt hash binds these distinctions. The verifier validates the evidence
shape, hashes and containment; it does not generate or independently reproduce
these observations. Review the companion mounted-origin receipt and the actual
owner launch/no-write evidence before signing. Retain their exact bytes with the
release qualification materials.

This qualifies startup, immutable entrypoint resolution, observed mounted
origins and no writes to inventoried trees. It does not claim an exhaustive
measurement of dynamic worker import paths throughout future sessions.
Configured-but-unobserved imports are not described as loaded. Registry
bookkeeping remains separately reported owner state outside sealed code roots.

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


### Optional Terminal census

A separately reviewed v3 descriptor may select
`"ownerCensus":{"profile":"native-message-terminal-v1"}`. This fixed profile
requires the exact existing private 20-owner base plus both independently held
`native-message-metadata` and `terminal` participants. Missing, duplicate, or
additional participants refuse readiness. The signed descriptor cannot supply
an arbitrary owner list, Terminal configuration, account, origin, feed, or path.
The immutable private configuration must already contain
`application.terminal = {origin, artifacts}`, with the exact HTTPS gateway
origin and a bounded configured feed. This retains Terminal's real request,
device-socket, durable intake, and application-root backup authority.

The prior `native-message-metadata-v1` profile remains exactly 21 owners and
requires Terminal to be absent. Source/manual, descriptor-free, and v1/v2 paths
cannot enable Terminal. Both profiles preserve the private `expectedOwners`
array and all its bytes; the effective census is a separate signed binding.
Fresh installations accept either fixed profile and still require genuine
pristine authority and current signed source/runtime verification.

This census is not artifact or device readiness. The actual Terminal owner
independently verifies the configured release manifest/evidence/wheel and runtime
descriptors. The assembly owner must provide the reviewed exact feed inputs;
changing the signed census cannot override them. Missing or changed feed bytes
leave new setup unavailable while retained receipts/revocation remain available.
Private grants, device hashes, receipts and downloaded scripts are under the
application state root. Public release feeds are separately reproducible inputs,
not proof that an archive includes client installations on other devices.


## Retained installation source policy (release runtime v4)

`unified-release-runtime-v4` retains the exact v3 descriptor and owner-profile
rules. Its `nativeLauncher` additionally requires this closed member:

```json
"sourceResolution": {
  "manifest": {"tree": "qualified-owner-runtime", "path": "source-policy.json"},
  "sha256": "<64 lowercase hexadecimal characters>"
}
```

The tree ID names a tree in the selected `ownerRuntime` Python inventory,
not the MCP inventory or an arbitrary filesystem root. The target is a canonical
regular file inside that tree. Its declared hash, inventoried hash and bounded
actual bytes must agree. The manifest itself is included when sealing the runtime
tree. Do not put its hash in its own source-file declarations.

The effective native configuration must equal exactly:

```js
{
  ...originalNativeBase,
  ...nativeLauncher.grants,
  sourceResolutionManifest: {path: resolvedManifestPath, sha256}
}
```

The effective `runtimeImmutable` value must be literal `true`. No other source,
environment, command, credential, workspace, data-home or launcher changes are
permitted. The native base bytes and hash remain unchanged. The original private
composition, initial installation authority, owner state and paths also remain
unchanged; the descriptor's release triple names the verified current target.
An older v3 rollback continues to bind its original configuration and does not
inherit the newer release's overlay. V1, v2 and v3 reject `sourceResolution`.

The Native v1 document is a closed `{version:1,sources:[...]}` object. Every row
has exactly `requestedUri,basePath,activePath,sourceRoot,resolvedCommit,approval,
files,admissionFiles,packages`, following the
[Native launcher source-closure contract](https://github.com/microsoft/amplifier-app-acp/blob/main/schemas/launcher-source-closure.v1.json).
Limits are 16 MiB, 512 bindings, and 65,536 file declarations **across all rows**.
File names are canonical relative POSIX paths; hashes are exact SHA256.
Admission files must be declared members. Canonical source roots, active targets,
every declared file/hash and package-file membership must be covered by the
selected sealed inventory. Symlink member traversal, missing or ambiguous
bindings, inconsistent hashes, credential-bearing URIs and undeclared fields
refuse before owners start. Resolution `basePath` is an exact canonical existing
directory context; it may be an authorized workspace outside the code inventory
and does not authorize code or directory enumeration there.

`resolveNativeSourceResolution(ownerRuntimeManifest, sourceResolutionDescriptor)`
returns `{path,sha256}`. This read-only export checks bounded policy bytes and
semantic membership against an **already verified sealed inventory**. It does not
hash the whole forest or establish runtime readiness. The release binder calls
the existing complete owner-runtime verifier before using it and repeats that
verification at lifecycle readiness checks. A publisher reusing this helper must
independently verify its inventory. This adds no per-request forest scan.

Static package membership supports flat, `src` and installed site-package layouts.
It is not evidence that Python imports the intended package, that every provider
loads, or that authentication works. Actual native source-policy and provider
origin qualification remains a separate receipt. Binding tests use owned code
fixtures and never start providers, mutate installed state, or authorize a live
update.


## Publisher source assembly verification

`src/source-assembly.mjs` is a source-side staging helper. It does not change
Native/Foundation admission policy or the installed launcher's READY behavior.
Use it on an owned, final candidate before the existing staging READY promotion.

`assembleSourceResolutionManifest({manifest, reviewedArtifacts})` is pure. The
`manifest` is the independently admitted Native v1 document, with final canonical
paths already selected by its owner. It first coalesces every existing
`sourceRoot/relative-file` expectation, refusing contradictory donor declarations
before replacements. It then projects reviewed replacement expectations across
every existing reference to that physical file. Row fields, order and file
membership are preserved. This bounded repair supports digest replacement only:
new/removed members need a separately reviewed source manifest. A conflicting
input is not repaired by choosing the newest row or trusting observed disk bytes.

Each `reviewedArtifacts` entry contains `artifact` and `materialization`:

```js
{
  artifact: {path: "/absolute/reviewed.whl", sha256: "<archive SHA256>",
             revision: "<existing full source revision>"},
  materialization: {
    sourceRoot: "/absolute/owned-final-python",
    files: [{path: "lib/python3.13/site-packages/amplifier_acp/__init__.py",
             sha256: "<reviewed installed file SHA256>", bytes: 1234, mode: 420}]
  }
}
```

Retain existing archive/source identities and provenance fields from the admitted
release manifest or lock; do not resolve new versions. `files` uses the existing
release `ReleaseFile` shape. The materialization inventory must have been
independently reviewed against that archive, including installation transforms.
An archive hash alone does **not** authenticate an arbitrary file list. This
helper does not unpack wheels, infer that relationship, or replace the review
with a destination scan. Keep legal/license members in the reviewed inventory;
unchanged attribution is checked along with unchanged code. The existing full
sealed owner-runtime inventory remains authoritative for the complete forest.

`verifySourceAssembly` checks the actual archive hashes and every declared final
physical file against these expectations. It refuses aliases and observed drift;
it never refreshes expected hashes. The complete staging gate is
`verifySourceAssemblyBeforeReady(plan)`, where `plan` contains exactly:

* `manifest` and `reviewedArtifacts` as above;
* `ownerRuntime`: the existing sealed `unified-python-runtime-v1` inventory;
* `nativeConfiguration: {path, sha256}`: the independently reviewed exact final
  configuration bytes, preserving approved homes, source policy and RCs;
* `native: {command, args, cwd, env, initialize}`: the exact final installed
  Native engine, complete explicit launch environment and initialize parameters.

The Native command must be the inventoried Python slot, with the existing
`-I -B -m amplifier_acp --config /absolute/final.json` arguments. The helper
inherits no environment. It verifies the complete sealed forest, the exact
configuration and mechanically emitted source manifest, then sends only ACP
`initialize`. It closes stdin after the one valid protocol-v1 response and
requires exit 0, no signal and empty stderr. Error, timeout, protocol mismatch
or changed post-preflight bytes refuse before a successful result is returned.
There is no session creation, prompt, admin/action request or provider call.
The caller must supply an owned offline staging state; a live service's private
homes/configuration are not acceptable test inputs. No runtime readiness claim
is made merely by emitting the manifest.

The thin maintained entry point binds a plan to its independently reviewed exact
SHA256, supplied outside that file:

```sh
node distribution/scripts/verify-source-assembly.mjs emit /absolute/plan.json <reviewed-plan-sha256>
node distribution/scripts/verify-source-assembly.mjs verify /absolute/plan.json <reviewed-plan-sha256>
```

`emit` outputs only the existing Native document. Stage those exact bytes, bind
the final reviewed config to their hash, and seal the existing owner runtime
inventory. Bind all final inputs into the reviewed plan, then run `verify`.
Only its exit 0 and `assembly-verified` result may advance the existing staging
plan to READY. Neither command uploads artifacts, changes a release channel,
writes a READY marker, allocates installation authority or launches an app.
The original historical staging plan and its receipts remain evidence; this
helper replaces the incremental row-patching step for a future owned candidate.

The fixture uses the unchanged Native v1 schema with four URIs sharing a root.
It checks digest conflicts, admitted replacement projection, unchanged context
RCs/attribution, observed/archive drift and a synthetic broker's initialize-only
success/refusal. Synthetic success does not qualify installed Native, a composed
graph, models/accounts, browsers or deployment. Native/Foundation independently
review the materialization receipt and perform the exact final installed Native
preflight before a normal merge or application.

### Component download publication is a separate boundary

The reviewed Host29 archive can exist locally while TUI's unchanged release
workflow has no compatible download input. A private source merge and CI
`npm pack` without artifact retention do not produce a durable URL. Older public
vendor archives cannot substitute for the admitted archive hash. The packaging
owner must select an authorized maintained channel for the exact bytes, or a
separately authorized authenticated download path in the existing workflow.
Retain legal attribution, source revision, artifact hash and access policy; verify
the downloaded bytes using the intended CI access mode before dispatching all
four platform jobs. This helper supplies neither hosting nor publication
authority. Archive publication, package CI, assembly verification, installed
adoption and user-facing acceptance keep separate receipts.

## V5: one managed Smart Tool installer

`unified-release-runtime-v5` inherits the exact v4 source-closure/runtime fields
and additionally requires `mcpInstaller`:

~~~json
{
  "profile": "managed-uv-v1",
  "executable": {"tree": "smart-tool-installer", "path": "bin/uv"},
  "qualification": {
    "tree": "smart-tool-installer", "path": "qualification.json",
    "sha256": "SHA256_OF_QUALIFICATION_METADATA"
  }
}
~~~

The existing `unified-mcp-runtime-v1` inventory includes one dedicated owned sealed
`smart-tool-installer` tree. Its selected `bin/uv` must be a regular executable,
owned by the launching user, with no setuid/setgid bits or symlink parents. The
selected `qualification.json` is a regular file bounded to 16 KiB. Its closed
metadata shape is `{schema, version, origin, executable, platform, arch}`:
`schema` is `unified-managed-uv-qualification-v1`, `version` is the reviewed uv
version string, and `origin` is a bounded credential-free HTTPS artifact URL.
`executable` contains the same `tree` and `path` plus exact inventoried `sha256`,
`bytes` and `mode`; `platform` and `arch` must match the launching Node process.
Version, origin and architecture metadata do not independently prove that those
bytes are uv: Root reviews actual binary version/interface/dry-run evidence
before signing. Validation narration is not part of the runtime metadata schema.

After verifying the full MCP inventory, the canonical exported
`resolveManagedInstaller(manifest, descriptor)` checks the selected metadata and
current executable bytes. It executes no code and is the shared resolver for a
future retained helper successor. Publishers must verify the whole forest before
calling it; selected resolution alone is not a forest verifier. The binder
projects only `application.mcp.installer.executable` to the resolved absolute path
and repeats its checks through the existing readiness verifier. An original
composition already declaring `mcp.installer`, or bypassing the signed broker via
`mcp.command`/`mcp.broker`, refuses this profile rather than silently replacing
another authority. Original composition bytes, environment, credentials, native
inputs and all other settings remain unchanged.

Versions v1–v4 retain their exact key sets and behavior; they reject the new field.
A rollback to a descriptor without this binding restores the original installer
configuration, including absence. No ambient PATH is added. The accepted MCP
owner consumes the absolute installer through its trusted launch configuration
and checks executable availability before checkout. Availability, package
installation, descriptor smoke, renderer prerequisites, accounts and creative
readiness remain separate evidence. No old failed or unknown request is replayed.

This source change does not update a frozen retained helper, provision a binary,
create another environment, sign, activate, or establish actual Unfold acceptance.
Root owns those later steps. Put qualification metadata in the selected inventory
rather than introducing another private overlay; it binds the binary identity,
not the final manifest hash, avoiding a hash cycle. Existing sealed-tree lifetime
and same-user tamper limits continue to apply.
