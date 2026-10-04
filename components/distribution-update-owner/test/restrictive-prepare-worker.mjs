import assert from "node:assert/strict";
import { readFile, writeFile, lstat, chmod } from "node:fs/promises";
import { join, dirname } from "node:path";
import { SignedReleaseAdapter } from "../dist/index.js";

const { options, identity } = JSON.parse(await readFile(process.argv[2], "utf8"));
process.umask(0o077);
options.resolveSources = async () =>
  await (await fetch(new URL("/sources", options.channelUrl))).json();
const adapter = new SignedReleaseAdapter(options);
const context = {
  commandId: "restrictive-prepare",
  signal: new AbortController().signal,
};
const target = await adapter.prepare(identity, context);
assert.equal(await adapter.verify(target, context), true);
const installed = await adapter.installed(target);
for (const file of installed.release.files) {
  assert.equal(
    (await lstat(join(installed.root, file.path))).mode & 0o777,
    file.mode,
    file.path,
  );
}
for (const path of [
  options.directory,
  join(options.directory, "artifacts"),
  join(options.directory, "staging"),
  join(options.directory, "releases"),
  dirname(installed.root),
  installed.root,
]) {
  assert.equal((await lstat(path)).mode & 0o777, 0o700, path);
}
const probe = join(options.directory, "private-probe");
await writeFile(probe, "private", { mode: 0o666 });
assert.equal((await lstat(probe)).mode & 0o777, 0o600);
assert.equal(process.umask(), 0o077);
// Restoration applies only to a newly extracted private candidate. Do not
// silently normalize a locally changed retained/installed release on reuse.
const entrypoint = join(installed.root, "server.mjs");
await chmod(entrypoint, 0o600);
assert.equal(await adapter.verify(target, context), false);
await assert.rejects(adapter.prepare(identity, context), /local_source_changes/);
assert.equal((await lstat(entrypoint)).mode & 0o777, 0o600);
console.log(JSON.stringify({
  prepared: true,
  verified: true,
  umask: process.umask(),
  privateParents: true,
  exactModes: true,
  unrelatedFileMode: 0o600,
  changedInstalledModePreserved: true,
}));
