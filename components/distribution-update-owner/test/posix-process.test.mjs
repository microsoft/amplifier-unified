import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { PosixProcessOwner } from "../dist/index.js";
const identity = {
  instanceId: "first",
  dataScope: "fixture",
  releaseDigest: "a".repeat(64),
};
const tick = () => new Promise((resolve) => setTimeout(resolve, 10));
async function until(read) {
  for (let i = 0; i < 300; i++) {
    const v = await read();
    if (v) return v;
    await tick();
  }
  throw Error("fixture_observation_deadline");
}
async function fixture(t, source = "", options = {}) {
  const directory = await mkdtemp(join(tmpdir(), "owned-process-")),
    entry = join(directory, "entry.mjs"),
    state = join(directory, "state");
  await writeFile(
    entry,
    `import {writeFileSync} from 'node:fs';\nwriteFileSync(process.argv[2],process.env.AMPLIFIER_DISTRIBUTION_INSTANCE_ID);\nprocess.on('SIGTERM',()=>{${source || "process.exit(0);"}});\nsetTimeout(()=>process.exit(0),1500);\n`,
  );
  const owner = new PosixProcessOwner({ ownerId: "fixture-owner", ...options });
  t.after(async () => {
    const r = owner.inspect();
    if (r.state === "running") await owner.stop(r.identity).catch(() => {});
    await until(() => owner.inspect().state !== "running");
    await rm(directory, { recursive: true, force: true });
  });
  await owner.start(
    { command: process.execPath, args: [entry, state] },
    identity,
    new AbortController().signal,
  );
  await until(() => readFile(state, "utf8").catch(() => null));
  return { owner, entry, state };
}
test("POSIX stop uses inherited connection and authoritative child exit, never PID lookup", async (t) => {
  const { owner } = await fixture(t);
  assert.equal(owner.inspect().state, "running");
  const proof = await owner.stop(identity);
  assert.equal(proof.ownerId, "fixture-owner");
  assert.equal(proof.instanceId, "first");
  assert.equal(owner.inspect().state, "exited");
  assert.equal(owner.exitProof(identity).ownerReceiptId, proof.ownerReceiptId);
  await assert.rejects(owner.stop(identity), /ownership_unproven/);
});
test("unowned or mismatched identity and reused numeric PID cannot authorize signaling", async (t) => {
  const { owner } = await fixture(t),
    other = new PosixProcessOwner({ ownerId: "fixture-owner" });
  await assert.rejects(
    other.stop({ ...identity, pid: owner.ownedPid }),
    /ownership_unproven/,
  );
  for (const bad of [
    { ...identity, instanceId: "other" },
    { ...identity, dataScope: "other" },
    { ...identity, releaseDigest: "b".repeat(64) },
  ])
    await assert.rejects(owner.stop(bad), /ownership_unproven/);
  assert.equal(owner.inspect().state, "running");
});
test("lost stop completion preserves ownership and only the observed late exit supplies proof", async (t) => {
  const { owner } = await fixture(t, "setTimeout(()=>process.exit(0),350);", {
    stopMs: 100,
  });
  await assert.rejects(owner.stop(identity), /stop_unconfirmed/);
  assert.equal(owner.exitProof(identity), null);
  assert.equal(owner.inspect().state, "running");
  const proof = await until(() => owner.exitProof(identity));
  assert.equal(proof.instanceId, identity.instanceId);
});
test("explicit new launch gets a new instance and cannot reuse a stopped one", async (t) => {
  const { owner, entry, state } = await fixture(t);
  await owner.stop(identity);
  await assert.rejects(
    owner.start(
      { command: process.execPath, args: [entry, state] },
      identity,
      new AbortController().signal,
    ),
    /instance_already_used/,
  );
  const next = { ...identity, instanceId: "second" };
  await owner.start(
    { command: process.execPath, args: [entry, state] },
    next,
    new AbortController().signal,
  );
  await until(async () => (await readFile(state, "utf8")) === "second");
  assert.equal(owner.inspect().identity.instanceId, "second");
});
test("unsupported platform refuses construction instead of claiming process ownership", () => {
  const script = `Object.defineProperty(process,'platform',{value:'win32'}); const {PosixProcessOwner}=await import(${JSON.stringify(new URL("../dist/index.js", import.meta.url).href)}); new PosixProcessOwner({ownerId:'fixture'});`;
  const result = spawnSync(
    process.execPath,
    ["--input-type=module", "-e", script],
    { encoding: "utf8" },
  );
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /service_platform_unsupported/);
});
