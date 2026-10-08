# Installed release qualification

The AHP/ACP candidate requires a passing installed-package receipt before it is
offered for release review. Source CI and a successful pack are insufficient.
Run from the distribution checkout with a new private evidence directory:

```sh
npm run test:release -- /owned/qualification.json /owned/evidence/new-run
```

The command fails when inputs are missing, the platform is not Linux, a required
test skips, a check fails, or candidate code changes during the run. It extracts
the supplied archive, uses its bundled owners and served Web assets, and copies
only test fixtures alongside it. It clones the supplied Python environment;
synthetic state, history, signing keys, and account-free provider fixtures are
created separately. Never supply a live application's environment or data roots.

The configuration requires these fields (absolute paths):

| Field | Meaning |
| --- | --- |
| archive, archiveSha256 | Exact assembled Node archive and SHA-256 |
| pythonHome | Complete, disposable candidate Python virtual environment |
| nativeManifest, nativeManifestSha256 | Existing source-closure manifest with `sources[].sourceRoot` and file hashes |
| legacyPython | Separate qualified current-main interpreter |
| legacySource, legacyArchive, legacyArchiveSha256, legacyRevision | Current-main source tree, exact source archive, SHA-256, and full Git revision |
| questionsSource, schedulesSource, observationsSource | Exact legacy fixture module sources |
| providerSource | Deterministic native provider fixture source |
| webTests | Web client's tests directory with its installed test dependencies |
| playwright, browsers | Playwright module entry and installed Linux browser directory |
| node | Node executable |
| allowedCpus | Two available CPUs, for example `0,1` |

The runner must have a functioning user systemd manager and all three Playwright
engines and dependencies installed. The archive rehearsal needs the full qualified
Native/Foundation dependency graph; this command does not silently install
missing runtime dependencies or fetch a moving main branch.

For an isolated runner that cannot create WebKit's process sandbox, the explicit
test-only option `webkitBrowserSandboxDisabled: true` applies only to that
synthetic attachment check and is recorded in the receipt. It does not change
application security settings or qualify browser sandbox compatibility. Preserve
separate default-sandbox browser CI evidence when using this runner constraint.

Mandatory checks:

1. Cold native history through the authenticated gateway: bounded paging, typed
   oversized-frame refusal, surviving clients, and preserved canonical data.
2. Signed 21-owner manual systemd handoff: busy refusal, exact owner fences,
   source-process exit, refused source relaunch, and unchanged history.
3. Streaming with two assigned CPUs, 200% CPU quota and 4 GiB memory: exact text,
   bounded latency, slow-viewer isolation, and FULL SQLite durability.
4. Provider recording settings through the packed browser and actual Native
   owner: private edits, lost acknowledgement, original receipt inspection,
   competing editors, and mobile layout.
5. Packed attachments through Chromium, Firefox, and WebKit: ordinary upload,
   reload, exact bytes, and removal.
6. Signed update, interrupted recovery, explicit reconciliation, browser drafts,
   and continuation of candidate-modified history in the legacy HTTP worker.

Preserve the private `receipt.json`, logs, and subordinate receipts. A pass is
bound to the archive and Native manifest digests. A new archive, changed source
closure, or failed/unknown outcome requires a new evidence directory and a fresh
qualification; never overwrite or automatically replay a failed attempt.

This gate does not publish, replace production, or rename repositories. It does
not qualify physical voice, real accounts, other operating systems, restoration
onto a new machine, or reverse conversion of every product database and every
new candidate chat. Those retain separate release decisions and evidence.
