import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, readFile, writeFile, cp, chmod } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { SignedReleaseAdapter, releaseDigest } from "../dist/index.js";
import { publisher, artifact } from "./release-fixtures.mjs";
const execute = promisify(execFile);
const appCode = `import {writeFile} from 'node:fs/promises';
const {createRuntimeIdentity}=await import(process.env.OWNER_MODULE);
try {
 let ready=false,reads=0;
 const runtime=await createRuntimeIdentity({entrypointUrl:process.env.WRONG_ENTRY||import.meta.url,trustedKeys:JSON.parse(process.env.TRUSTED_KEYS),isReady:()=>{reads++;return ready;}});
 const first=await runtime.inspectRunning();
 ready=true;
 const [second,joined]=await Promise.all([runtime.inspectRunning(),runtime.inspectRunning()]);
 if(process.env.CASE==='mutate')await writeFile(new URL('node_modules/fixture-component/index.js',import.meta.url),'changed');
 if(process.env.CASE==='receipt')await writeFile(process.env.AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT,'{}');
 if(process.env.CASE==='bad-ready')ready='yes';
 // Caller mutations cannot alter the private bound identity.
 second.identity.digest='f'.repeat(64);
 const final=await runtime.inspectRunning();
 console.log(JSON.stringify({first,final,reads,joined:second===joined,frozen:Object.isFrozen(runtime.identity)}));
}catch(error){console.log(JSON.stringify({error:error.message}));}
`;
async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "runtime-identity-")),
    pub = await publisher();
  t.after(async () => {
    await pub.close();
    await rm(root, { recursive: true, force: true });
  });
  const a = await artifact(root, 1, pub.origin, appCode);
  pub.publish([a], 1);
  const adapter = new SignedReleaseAdapter({
    directory: join(root, "candidates"),
    channelUrl: pub.origin + "/channel.json",
    trustedKeys: pub.keys,
    accessScope: "fixture",
    allowedArtifactOrigins: [pub.origin],
    allowLoopbackHttp: true,
    resolveSources: async () =>
      await (await fetch(pub.origin + "/sources")).json(),
  });
  const target = await adapter.prepare(a.release.identity, {
    commandId: "fixture",
    signal: new AbortController().signal,
  });
  const launch = await adapter.resolveLaunch(target),
    installed = await adapter.installed(target);
  const env = {
    ...process.env,
    ...launch.env,
    OWNER_MODULE: pathToFileURL(resolve("dist/index.js")).href,
    TRUSTED_KEYS: JSON.stringify(pub.keys),
    AMPLIFIER_DISTRIBUTION_INSTANCE_ID: "fixture-instance",
    AMPLIFIER_DISTRIBUTION_DATA_SCOPE: "fixture-scope",
  };
  const run = async (overrides = {}, entry = launch.args[0]) =>
    JSON.parse(
      (
        await execute(process.execPath, [entry], {
          env: { ...env, ...overrides },
        })
      ).stdout.trim(),
    );
  return { root, pub, a, installed, env, run };
}
test("runtime identity derives signed installed identity, joins readers and separates actual readiness", async (t) => {
  const { a, env, pub, run } = await fixture(t);
  // Expired discovery is still valid installed/rollback evidence.
  await writeFile(
    env.AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT,
    JSON.stringify({
      schema: "distribution-candidate-v1",
      releaseId: a.release.identity.id,
      signed: pub.envelope([a.release], a.release.identity.id, 0),
    }),
  );
  const result = await run();
  assert.equal(result.error, undefined);
  assert.deepEqual(result.final.identity, a.release.identity);
  assert.equal(result.first.ready, false);
  assert.equal(result.final.ready, true);
  assert.equal(result.final.instanceId, "fixture-instance");
  assert.equal(result.final.dataScope, "fixture-scope");
  assert.equal(result.reads, 3);
  assert.equal(result.joined, true);
  assert.equal(result.frozen, true);
});
test("runtime identity rejects a decoy signed tree, wrong process entrypoint and invalid launch binding", async (t) => {
  const { root, env, installed, run } = await fixture(t);
  const decoy = join(root, "decoy");
  await cp(join(installed.root, ".."), decoy, { recursive: true });
  assert.equal(
    (
      await run({
        AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT: join(decoy, "receipt.json"),
      })
    ).error,
    "runtime_entrypoint_mismatch",
  );
  assert.equal(
    (
      await run({
        WRONG_ENTRY: pathToFileURL(join(installed.root, "package.json")).href,
      })
    ).error,
    "runtime_entrypoint_mismatch",
  );
  const launcher = join(root, "launcher.mjs");
  await writeFile(
    launcher,
    `import ${JSON.stringify(pathToFileURL(join(installed.root, "server.mjs")).href)};`,
  );
  assert.equal((await run({}, launcher)).error, "runtime_entrypoint_mismatch");
  assert.equal(
    (await run({ AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT: "relative.json" }))
      .error,
    "runtime_launch_invalid",
  );
  assert.equal(
    (await run({ AMPLIFIER_DISTRIBUTION_INSTANCE_ID: "" })).error,
    "runtime_identity_unconfirmed",
  );
  assert.equal(
    (await run({ AMPLIFIER_DISTRIBUTION_DATA_SCOPE: "" })).error,
    "runtime_identity_unconfirmed",
  );
  assert.equal(
    (await run({ TRUSTED_KEYS: "{}" })).error,
    "runtime_identity_unconfirmed",
  );
  if (process.platform !== "win32") {
    await chmod(env.AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT, 0o644);
    assert.equal((await run()).error, "runtime_receipt_invalid");
  }
});
test("runtime identity rechecks installed dependency bytes and receipt after startup", async (t) => {
  const { env, installed, run } = await fixture(t);
  const path = join(installed.root, "node_modules/fixture-component/index.js"),
    saved = await readFile(path);
  assert.equal(
    (await run({ CASE: "mutate" })).error,
    "runtime_inventory_mismatch",
  );
  await writeFile(path, saved);
  const receipt = await readFile(env.AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT);
  assert.equal(
    (await run({ CASE: "receipt" })).error,
    "runtime_identity_changed",
  );
  await writeFile(env.AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT, receipt);
  assert.equal(
    (await run({ CASE: "bad-ready" })).error,
    "runtime_readiness_invalid",
  );
});
test("runtime identity rejects valid signatures for incompatible or nonmatching installed descriptors", async (t) => {
  const { a, pub, env, run } = await fixture(t);
  const replace = async (release) => {
    release.identity.digest = releaseDigest(release);
    await writeFile(
      env.AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT,
      JSON.stringify({
        schema: "distribution-candidate-v1",
        releaseId: release.identity.id,
        signed: pub.envelope([release], release.identity.id),
      }),
    );
  };
  const other = structuredClone(a.release);
  other.platform = process.platform === "linux" ? "darwin" : "linux";
  await replace(other);
  assert.equal((await run()).error, "runtime_platform_mismatch");
  const changed = structuredClone(a.release);
  changed.files.find((f) => f.path === "server.mjs").sha256 = "e".repeat(64);
  await replace(changed);
  assert.equal((await run()).error, "runtime_inventory_mismatch");
});
