import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, readFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  DistributionUpdateOwner,
  SignedReleaseAdapter,
} from "../dist/index.js";
import { publisher, artifact } from "./release-fixtures.mjs";

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "activation-currency-"));
  const pub = await publisher();
  const first = await artifact(root, 1, pub.origin);
  const second = await artifact(root, 2, pub.origin);
  const observations = [],
    releases = [];
  let busy = true,
    held = false,
    restarts = 0,
    lostRestart = false;
  const phases = new Map();
  const context = {
    commandId: "baseline",
    signal: new AbortController().signal,
  };
  const adapter = new SignedReleaseAdapter({
    directory: join(root, "candidates"),
    channelUrl: pub.origin + "/channel.json",
    trustedKeys: pub.keys,
    accessScope: "fixture",
    allowedArtifactOrigins: [pub.origin],
    allowLoopbackHttp: true,
    resolveSources: async (_, ctx) => {
      observations.push({ ...ctx });
      if (ctx.reason === "activation") assert.equal(held, true);
      return (
        await fetch(pub.origin + "/sources", { signal: ctx.signal })
      ).json();
    },
  });
  pub.publish([first], 1);
  const initial = await adapter.prepare(first.release.identity, context);
  let running = {
    identity: first.release.identity,
    instanceId: "initial",
    dataScope: "fixture",
    ready: true,
  };
  let owner;
  const lifecycle = {
    inspect: async () => running,
    admitRestart: async () => {
      if (busy) return null;
      held = true;
      return {
        evidence: {
          activeWork: 0,
          intakeClosed: true,
          instanceId: running.instanceId,
          dataScope: "fixture",
          observedAt: Date.now(),
        },
        release: async (outcome) => {
          const receipt = owner.receipt("install");
          if (outcome === "unchanged")
            assert.equal(receipt.phase, "pre_restart_refused");
          releases.push(outcome);
          held = outcome === "unknown";
        },
      };
    },
    restart: async (request) => {
      assert.equal(held, true);
      restarts++;
      running = {
        identity: request.target.identity,
        instanceId: request.instanceId,
        dataScope: "fixture",
        ready: true,
      };
      if (lostRestart) throw Error("lost restart response");
    },
  };
  owner = new DistributionUpdateOwner({
    directory: join(root, "owner"),
    dataScope: "fixture",
    initial,
    releases: adapter,
    lifecycle,
    preferences: { autoCheck: false, autoInstall: false, intervalMs: 1000 },
    onChange: (receipt) => {
      phases.get(receipt.phase)?.(receipt);
    },
  });
  t.after(async () => {
    await owner.close();
    await pub.close();
    await rm(root, { recursive: true, force: true });
  });
  pub.publish([first, second], 2);
  owner.check("check");
  await owner.waitFor("check");
  const waiting = new Promise((resolve) => phases.set("waiting_idle", resolve));
  owner.install("install");
  await waiting;
  const target = {
    identity: second.release.identity,
    handle: "release:" + second.release.identity.digest,
  };
  const installed = await adapter.installed(target),
    receiptPath = join(installed.root, "..", "receipt.json");
  const receiptBytes = await readFile(receiptPath);
  return {
    owner,
    pub,
    adapter,
    first,
    second,
    target,
    receiptPath,
    receiptBytes,
    observations,
    releases,
    get restarts() {
      return restarts;
    },
    get held() {
      return held;
    },
    loseRestart() {
      lostRestart = true;
    },
    resume() {
      busy = false;
      owner.notifyIdle();
      return owner.waitFor("install");
    },
  };
}

for (const [scenario, code, change] of [
  [
    "source advances",
    "source_advanced",
    (f) => f.pub.setSources({ version: 3 }),
  ],
  [
    "source becomes protected",
    "source_preserved",
    (f) => f.pub.setSources({ protected: true }),
  ],
  [
    "release is superseded",
    "release_superseded",
    (f) => f.pub.publish([f.first], 1),
  ],
  [
    "source response is lost",
    "adapter_failed",
    (f) => f.pub.setSources({ disconnect: true }),
  ],
])
  test(`waiting candidate refuses activation when ${scenario}`, async (t) => {
    const f = await fixture(t);
    assert.equal(f.owner.receipt("install").phase, "waiting_idle");
    assert.equal(
      f.observations.filter((r) => r.reason === "activation").length,
      0,
    );
    await change(f);
    const receipt = await f.resume();
    assert.equal(receipt.status, "failed");
    assert.equal(receipt.phase, "pre_restart_refused");
    assert.equal(receipt.errorCode, code);
    assert.ok(receipt.activation.startedAt);
    assert.equal(receipt.activation.completedAt, undefined);
    assert.equal(f.restarts, 0);
    assert.equal(f.held, false);
    assert.deepEqual(f.releases, ["unchanged"]);
    assert.deepEqual(await readFile(f.receiptPath), f.receiptBytes);
    const observed = f.observations.length;
    f.owner.notifyIdle();
    assert.deepEqual(f.owner.install("install"), receipt);
    await f.owner.reconcile("install");
    assert.equal(f.restarts, 0);
    assert.equal(f.observations.length, observed);
  });

test("unchanged waiting candidate observes fresh refs once under admission; offline rollback skips the gate", async (t) => {
  const f = await fixture(t),
    receipt = await f.resume();
  assert.equal(receipt.status, "succeeded");
  assert.equal(f.restarts, 1);
  assert.ok(receipt.activation.completedAt >= receipt.activation.startedAt);
  const activation = f.observations.filter((r) => r.reason === "activation");
  assert.equal(activation.length, 1);
  assert.equal(activation[0].fresh, true);
  assert.ok(f.observations.every((r) => r.fresh === true));
  const events = f.owner.diagnostics().events.filter((r) => r.id === "install");
  const phases = events.map((r) => r.phase);
  assert.ok(
    phases.indexOf("admitted") < phases.indexOf("qualifying_activation"),
  );
  assert.ok(
    phases.indexOf("qualifying_activation") <
      phases.indexOf("activation_qualified"),
  );
  assert.ok(
    phases.indexOf("activation_qualified") <
      phases.indexOf("restart_requested"),
  );
  assert.deepEqual(await readFile(f.receiptPath), f.receiptBytes);
  const count = f.observations.length;
  f.pub.resources.delete("/channel.json");
  f.pub.setSources({ disconnect: true });
  f.owner.rollback("rollback", f.second.release.identity.id);
  const rollback = await f.owner.waitFor("rollback");
  assert.equal(rollback.status, "succeeded");
  assert.equal(rollback.activation, undefined);
  assert.equal(f.restarts, 2);
  assert.equal(f.observations.length, count);
});

test("lost restart response after fresh qualification remains unknown without repeating qualification or restart", async (t) => {
  const f = await fixture(t);
  f.loseRestart();
  const receipt = await f.resume();
  assert.equal(receipt.status, "unknown");
  assert.equal(receipt.phase, "restart_requested");
  assert.ok(receipt.activation.completedAt);
  assert.equal(f.restarts, 1);
  const count = f.observations.length;
  assert.equal(f.owner.install("install").status, "unknown");
  f.owner.notifyIdle();
  assert.equal(f.owner.inspect().restartUnresolved, true);
  f.owner.install("blocked");
  assert.equal(
    (await f.owner.waitFor("blocked")).errorCode,
    "restart_unresolved",
  );
  f.pub.resources.delete("/channel.json");
  f.pub.setSources({ disconnect: true });
  assert.equal((await f.owner.reconcile("install")).status, "succeeded");
  assert.equal(f.restarts, 1);
  assert.equal(f.observations.length, count);
});
