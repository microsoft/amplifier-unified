import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtemp,
  rm,
  readFile,
  writeFile,
  symlink,
  mkdir,
  rename,
} from "node:fs/promises";
import { createHash } from "node:crypto";
import * as tar from "tar";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  SignedReleaseAdapter,
  readSignedChannel,
  releasePath,
  releaseDigest,
  verifyReleaseTree,
} from "../dist/index.js";
import { publisher, artifact } from "./release-fixtures.mjs";
const context = { commandId: "fixture", signal: new AbortController().signal };
async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "signed-release-")),
    pub = await publisher();
  t.after(async () => {
    await pub.close();
    await rm(root, { recursive: true, force: true });
  });
  const a = await artifact(root, 1, pub.origin);
  pub.publish([a], 1);
  const options = {
    directory: join(root, "candidates"),
    channelUrl: pub.origin + "/channel.json",
    trustedKeys: pub.keys,
    accessScope: "fixture",
    allowedArtifactOrigins: [pub.origin],
    allowLoopbackHttp: true,
    resolveSources: async () =>
      await (await fetch(pub.origin + "/sources")).json(),
  };
  return { root, pub, a, options, adapter: new SignedReleaseAdapter(options) };
}
test("signed adapter verifies exact bundled package graph and preserves changed installed files", async (t) => {
  const { adapter, a } = await fixture(t);
  const check = await adapter.check({ ...context, fresh: true });
  assert.equal(check.recommendedId, a.release.identity.id);
  const target = await adapter.prepare(a.release.identity, context);
  assert.equal(await adapter.verify(target, context), true);
  const installed = await adapter.installed(target);
  assert.equal(installed.release.components.length, 2);
  assert.equal((await adapter.resolveLaunch(target)).command, process.execPath);
  await writeFile(join(installed.root, "server.mjs"), "local edit");
  await assert.rejects(
    adapter.prepare(a.release.identity, context),
    /local_source_changes/,
  );
  assert.equal(
    await readFile(join(installed.root, "server.mjs"), "utf8"),
    "local edit",
  );
});
test("untrusted, expired and tampered signed channel data is rejected", async (t) => {
  const { pub, a } = await fixture(t);
  const signed = pub.envelope([a.release], a.release.identity.id);
  assert.throws(
    () =>
      readSignedChannel(
        { ...signed, signature: Buffer.alloc(64).toString("base64") },
        pub.keys,
      ),
    /channel_untrusted/,
  );
  assert.throws(
    () =>
      readSignedChannel(
        pub.envelope([a.release], a.release.identity.id, 0),
        pub.keys,
      ),
    /expired/,
  );
  assert.throws(() => readSignedChannel(signed, {}), /channel_untrusted/);
});
test("forward preparation resolves current source refs and preserves configured overrides", async (t) => {
  const { adapter, a, options } = await fixture(t);
  options.resolveSources = async () =>
    a.release.components.map((c) => ({ ...c, protected: true }));
  await assert.rejects(
    adapter.prepare(a.release.identity, context),
    /source_preserved/,
  );
  options.resolveSources = async () =>
    a.release.components.map((c) => ({
      ...c,
      revision: "e".repeat(40),
      protected: false,
    }));
  await assert.rejects(
    adapter.prepare(a.release.identity, context),
    /source_advanced/,
  );
});
test("download corruption cannot become an installed candidate", async (t) => {
  const { adapter, a, pub } = await fixture(t);
  pub.resources.set("/fixture-v1.tgz", Buffer.from("corrupt"));
  await assert.rejects(
    adapter.prepare(a.release.identity, context),
    /artifact_digest_mismatch/,
  );
});
test("expiry of the channel does not turn retained rollback evidence into a tracking pin", async (t) => {
  const { adapter, a, pub } = await fixture(t);
  const target = await adapter.prepare(a.release.identity, context);
  const installed = await adapter.installed(target);
  const path = join(installed.root, "..", "receipt.json");
  const receipt = JSON.parse(await readFile(path, "utf8"));
  receipt.signed = pub.envelope([a.release], a.release.identity.id, 0);
  await writeFile(path, JSON.stringify(receipt));
  assert.equal(await adapter.verify(target, context), true);
  pub.resources.set(
    "/channel.json",
    Buffer.from(JSON.stringify(receipt.signed)),
  );
  await assert.rejects(adapter.prepare(a.release.identity, context), /expired/);
});
test("missing component inventory fails qualification even with a valid publisher signature", async (t) => {
  const { adapter, a, pub } = await fixture(t);
  const release = structuredClone(a.release);
  release.components = release.components.slice(0, 1);
  release.identity.digest = releaseDigest(release);
  pub.publish([{ ...a, release }], 1);
  await assert.rejects(
    adapter.prepare(release.identity, context),
    /candidate_inventory_mismatch/,
  );
});
test("portable archive paths reject traversal, absolute names, alternate separators and device names", () => {
  for (const path of [
    "../escape",
    "/escape",
    "a/../../escape",
    "a\\b",
    "C:/escape",
    "CON",
    "a/../b",
    "a./b",
  ])
    assert.throws(() => releasePath(path), /invalid_release_path/);
});

test("release digests ignore caller property insertion order and inventory order", async (t) => {
  const { a } = await fixture(t);
  const copy = structuredClone(a.release);
  copy.files = copy.files
    .reverse()
    .map((f) => ({
      mode: f.mode,
      bytes: f.bytes,
      sha256: f.sha256,
      path: f.path,
    }));
  copy.components = copy.components
    .reverse()
    .map((c) => ({
      revision: c.revision,
      ref: c.ref,
      repository: c.repository,
      root: c.root,
      version: c.version,
      name: c.name,
    }));
  assert.equal(releaseDigest(copy), a.release.identity.digest);
});

test("a signed archive cannot introduce symlinks, and an installed root cannot be replaced by one", async (t) => {
  const { root, a, adapter, pub } = await fixture(t);
  const target = await adapter.prepare(a.release.identity, context);
  const installed = await adapter.installed(target);
  const moved = installed.root + "-retained";
  await rename(installed.root, moved);
  await symlink(moved, installed.root, "dir");
  assert.equal(
    await verifyReleaseTree(installed.root, installed.release),
    false,
  );
  await assert.rejects(
    adapter.prepare(a.release.identity, context),
    /local_source_changes/,
  );
  const archiveRoot = join(root, "linked-archive");
  await mkdir(join(archiveRoot, "package"), { recursive: true });
  await symlink("../outside", join(archiveRoot, "package", "server.mjs"));
  const archive = join(root, "linked.tgz");
  await tar.c({ gzip: true, file: archive, cwd: archiveRoot }, ["package"]);
  const bytes = await readFile(archive);
  const release = structuredClone(a.release);
  release.identity.id = "linked";
  release.identity.digest = releaseDigest(release);
  release.artifact.sha256 = createHash("sha256").update(bytes).digest("hex");
  release.artifact.bytes = bytes.length;
  pub.publish([{ ...a, release, bytes }], 1);
  pub.resources.set(
    "/channel.json",
    Buffer.from(JSON.stringify(pub.envelope([release], "linked"))),
  );
  await assert.rejects(
    adapter.prepare(release.identity, context),
    /archive_inventory_mismatch/,
  );
});
