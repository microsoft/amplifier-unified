// Executed from an independent npm installation, never from the source checkout.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import {
  DistributionUpdateOwner,
  OwnedProcessLifecycle,
} from "@amplifier/unified-distribution-update-owner";

assert.match(
  import.meta.resolve("@amplifier/unified-distribution-update-owner"),
  /node_modules\/@amplifier\/unified-distribution-update-owner\/dist\/index.js$/,
);
const config = JSON.parse(await readFile(process.argv[2], "utf8"));
const execute = promisify(execFile);
const installed = (target) =>
  join(
    config.root,
    target.handle,
    "node_modules",
    "owned-distribution-fixture",
  );
const hash = (data) => createHash("sha256").update(data).digest("hex");
let gateHeld = false,
  restarts = 0;
const inspect = async () => {
  try {
    const endpoint = JSON.parse(await readFile(config.endpoint, "utf8"));
    const result = await fetch(`http://127.0.0.1:${endpoint.port}/ready`, {
      headers: { Authorization: `Bearer ${config.secret}` },
      signal: AbortSignal.timeout(1000),
    });
    return result.ok ? await result.json() : null;
  } catch {
    return null;
  }
};
const lifecycle = new OwnedProcessLifecycle({
  inspect,
  admitRestart: async () => {
    assert.equal(gateHeld, false);
    gateHeld = true;
    return {
      evidence: {
        activeWork: 0,
        intakeClosed: true,
        instanceId: (await inspect())?.instanceId ?? null,
        dataScope: "fixture",
        observedAt: Date.now(),
      },
      release(outcome) {
        if (outcome !== "unknown") gateHeld = false;
      },
    };
  },
  resolve: async (target) => ({
    command: process.execPath,
    args: [join(installed(target), "server.mjs")],
    env: { ENDPOINT_FILE: config.endpoint, READINESS_SECRET: config.secret },
  }),
  readinessMs: 10000,
  stopMs: 3000,
});
const [a, b] = config.releases;
const candidates = new Map(
  config.releases.map((identity) => [
    identity.id,
    { identity, handle: identity.id },
  ]),
);
const verify = async (target) => {
  const root = installed(target);
  const identity = JSON.parse(
    await readFile(join(root, "release.json"), "utf8"),
  );
  return (
    JSON.stringify(identity) === JSON.stringify(target.identity) &&
    hash(await readFile(join(root, "server.mjs"))) === config.serverHash
  );
};
const install = async (release) => {
  const target = candidates.get(release.id),
    tarball = config.artifacts[release.id];
  assert.equal(hash(await readFile(tarball.file)), tarball.sha256);
  await execute(config.npm, [
    "install",
    "--prefix",
    join(config.root, target.handle),
    "--ignore-scripts",
    "--no-audit",
    "--no-fund",
    "--omit=dev",
    tarball.file,
  ]);
  return target;
};
let owner;
try {
  await install(a);
  assert.equal(await verify(candidates.get(a.id)), true);
  await lifecycle.startInitial(candidates.get(a.id), "fixture");
  const first = await inspect();
  assert.equal(first.identity.id, a.id);
  const ports = {
    directory: join(config.root, "owner-ledger"),
    dataScope: "fixture",
    initial: candidates.get(a.id),
    preferences: { autoCheck: false, autoInstall: false, intervalMs: 1000 },
    onChange: (receipt) => {
      if (receipt.status === "succeeded" && receipt.phase === "ready")
        gateHeld = false;
    },
    releases: {
      check: async () => ({ releases: [a, b], recommendedId: b.id }),
      prepare: install,
      verify,
    },
    lifecycle: {
      inspect,
      admitRestart: () => lifecycle.admitRestart(),
      restart: async (request) => {
        assert.equal(gateHeld, true);
        restarts++;
        await lifecycle.restart(request);
      },
    },
  };
  owner = new DistributionUpdateOwner(ports);
  owner.check("manual-check");
  await owner.waitFor("manual-check");
  owner.install("promote");
  assert.equal((await owner.waitFor("promote")).status, "succeeded");
  const second = await inspect();
  assert.equal(second.identity.id, b.id);
  assert.notEqual(first.pid, second.pid);
  assert.notEqual(first.instanceId, second.instanceId);
  assert.equal(owner.inspect().current.id, b.id);
  assert.equal(owner.inspect().previous.id, a.id);
  // Drop the reply after a real rollback restart. Passive reconciliation must
  // acknowledge the new process without replaying that external effect.
  ports.lifecycle.restart = async (request) => {
    assert.equal(gateHeld, true);
    restarts++;
    await lifecycle.restart(request);
    throw Error("fixture-lost-reply");
  };
  owner.rollback("rollback", b.id);
  assert.equal((await owner.waitFor("rollback")).status, "unknown");
  assert.equal(gateHeld, true);
  const third = await inspect();
  assert.equal(third.identity.id, a.id);
  assert.notEqual(second.pid, third.pid);
  await owner.close();
  owner = new DistributionUpdateOwner(ports);
  assert.equal(owner.receipt("rollback").status, "unknown");
  assert.equal((await owner.reconcile("rollback")).status, "succeeded");
  assert.equal(restarts, 2);
  assert.equal(owner.inspect().current.id, a.id);
  assert.equal(owner.inspect().previous.id, b.id);
  assert.equal(owner.rollback("rollback", b.id).status, "succeeded");
  assert.equal(restarts, 2);
  assert.equal(gateHeld, false);
  const receipt = {
    schema: "distribution-owner-installed-acceptance-v1",
    package: "@amplifier/unified-distribution-update-owner",
    version: "0.3.0",
    independentInstallation: true,
    actualOwnedProcesses: true,
    qualifiedPromotion: true,
    exactReadiness: true,
    newProcessIdentity: true,
    rollback: true,
    lostReplyReconciledWithoutReplay: true,
    serviceManagerAcceptance: false,
    productionAcceptance: false,
  };
  await writeFile(config.receipt, JSON.stringify(receipt, null, 2) + "\n", {
    mode: 0o600,
  });
  console.log(JSON.stringify(receipt));
} finally {
  await owner?.close();
  await lifecycle.close();
}
