import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import {
  DistributionUpdateOwner,
  AvailabilityCache,
  DownloadScheduler,
  OwnedStartupError,
  startupFailure,
} from "../dist/index.js";

const a = {
  id: "v1",
  version: "1.0.0",
  revision: "a".repeat(40),
  digest: "a".repeat(64),
};
const b = {
  id: "v2",
  version: "2.0.0",
  revision: "b".repeat(40),
  digest: "b".repeat(64),
};
const candidate = (identity) => ({ identity, handle: identity.id });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};
const tick = () => new Promise((resolve) => setImmediate(resolve));
async function fixture(t, overrides = {}) {
  const directory = await mkdtemp(join(tmpdir(), "update-owner-test-"));
  let running = {
      identity: a,
      instanceId: "first",
      dataScope: "fixture",
      ready: true,
    },
    busy = false;
  const calls = {
    check: 0,
    prepare: 0,
    verify: 0,
    restart: 0,
    admission: 0,
    release: 0,
  };
  const options = {
    directory,
    dataScope: "fixture",
    initial: candidate(a),
    preferences: { autoCheck: false, autoInstall: false, intervalMs: 1000 },
    releases: {
      check: async () => {
        calls.check++;
        return { releases: [a, b], recommendedId: b.id };
      },
      prepare: async (release) => {
        calls.prepare++;
        return candidate(release);
      },
      verify: async () => {
        calls.verify++;
        return true;
      },
    },
    lifecycle: {
      inspect: async () => running,
      admitRestart: async () => {
        calls.admission++;
        return busy
          ? null
          : {
              evidence: {
                activeWork: 0,
                intakeClosed: true,
                instanceId: running.instanceId,
                dataScope: "fixture",
                observedAt: Date.now(),
              },
              release: () => {
                calls.release++;
              },
            };
      },
      restart: async (request) => {
        calls.restart++;
        running = {
          identity: request.target.identity,
          instanceId: request.instanceId,
          dataScope: request.dataScope,
          ready: true,
        };
      },
    },
    ...overrides,
  };
  const owner = new DistributionUpdateOwner(options);
  t.after(async () => {
    await owner.close();
    await rm(directory, { recursive: true, force: true });
  });
  return {
    owner,
    options,
    calls,
    directory,
    setBusy: (value) => (busy = value),
    setRunning: (value) => (running = value),
    getRunning: () => running,
  };
}

test("construction and inspection are passive; manual check/install and receipts are immediate and idempotent", async (t) => {
  const { owner, calls, directory } = await fixture(t);
  assert.equal(owner.inspect().current.id, "v1");
  assert.equal(calls.check, 0);
  assert.equal(owner.check("check").status, "queued");
  assert.equal((await owner.waitFor("check")).status, "succeeded");
  owner.install("install");
  assert.equal((await owner.waitFor("install")).phase, "ready");
  assert.equal(calls.restart, 1);
  assert.equal(owner.install("install").status, "succeeded");
  assert.throws(
    () => owner.install("install", "v1"),
    /command_identity_conflict/,
  );
  owner.install("noop");
  assert.equal((await owner.waitFor("noop")).phase, "already_current");
  assert.equal(calls.restart, 1);
  assert.equal(
    (await stat(join(directory, "updates.sqlite3"))).mode & 0o777,
    0o600,
  );
});

test("install submitted during a check continues as soon as that check finishes", async (t) => {
  const gate = deferred(),
    { owner, options, calls } = await fixture(t);
  options.releases.check = async () => gate.promise;
  owner.check("check");
  await tick();
  owner.install("install");
  assert.equal(calls.prepare, 0);
  gate.resolve({ releases: [a, b], recommendedId: b.id });
  assert.equal((await owner.waitFor("install")).status, "succeeded");
  assert.equal(calls.restart, 1);
});

test("failed fresh check invalidates old inventory and blocks queued installation", async (t) => {
  const { owner, options, calls } = await fixture(t);
  owner.check("first");
  await owner.waitFor("first");
  options.releases.check = async () => {
    throw Error("private host and secret never reach diagnostics");
  };
  owner.check("failed");
  owner.install("install");
  assert.equal((await owner.waitFor("failed")).status, "failed");
  assert.equal((await owner.waitFor("install")).errorCode, "check_required");
  assert.equal(calls.prepare, 0);
  assert.equal(owner.inspect().catalog, null);
  assert.doesNotMatch(
    JSON.stringify(owner.diagnostics()),
    /private host|secret never/,
  );
});

test("busy admission is event-driven and held through readiness", async (t) => {
  const { owner, calls, setBusy } = await fixture(t);
  setBusy(true);
  owner.check("check");
  await owner.waitFor("check");
  owner.install("install");
  await tick();
  await tick();
  assert.equal(owner.receipt("install").phase, "waiting_idle");
  assert.equal(calls.admission, 1);
  await tick();
  assert.equal(calls.admission, 1);
  setBusy(false);
  owner.notifyIdle();
  assert.equal((await owner.waitFor("install")).phase, "ready");
  assert.equal(calls.prepare, 1);
  assert.equal(calls.verify, 2);
  assert.equal(calls.release, 1);
});

test("idle notification racing with admission is retained", async (t) => {
  const { owner, options, calls, getRunning } = await fixture(t);
  let first = true;
  options.lifecycle.admitRestart = async () => {
    if (first) {
      first = false;
      owner.notifyIdle();
      return null;
    }
    return {
      evidence: {
        activeWork: 0,
        intakeClosed: true,
        instanceId: getRunning().instanceId,
        dataScope: "fixture",
        observedAt: Date.now(),
      },
      release() {},
    };
  };
  owner.check("check");
  await owner.waitFor("check");
  owner.install("install");
  assert.equal((await owner.waitFor("install")).status, "succeeded");
  assert.equal(calls.restart, 1);
});

test("lost restart response stays unknown; reconciliation is passive and never replays", async (t) => {
  const { owner, options, calls, setRunning } = await fixture(t);
  let request;
  options.lifecycle.restart = async (value) => {
    calls.restart++;
    request = value;
    throw Error("lost response");
  };
  owner.check("check");
  await owner.waitFor("check");
  owner.install("install");
  assert.equal((await owner.waitFor("install")).status, "unknown");
  assert.equal(owner.inspect().restartUnresolved, true);
  owner.install("blocked");
  assert.equal(
    (await owner.waitFor("blocked")).errorCode,
    "restart_unresolved",
  );
  assert.equal((await owner.reconcile("install")).status, "unknown");
  assert.equal(calls.restart, 1);
  setRunning({
    identity: b,
    instanceId: request.instanceId,
    dataScope: "fixture",
    ready: true,
  });
  assert.equal((await owner.reconcile("install")).status, "succeeded");
  assert.equal(calls.restart, 1);
  assert.equal(owner.inspect().current.id, "v2");
  assert.equal(owner.inspect().previous.id, "v1");
});

test("startup diagnostics survive receipts and reopen without authorizing a retry", async (t) => {
  const events = [];
  const f = await fixture(t, { onChange: (receipt) => events.push(receipt) });
  let request;
  f.options.lifecycle.restart = async (value) => {
    f.calls.restart++;
    request = value;
    const error = new OwnedStartupError(startupFailure("dependency_unavailable", "initialization", "child-bootstrap"));
    error.startupFailure.guidance = "PRIVATE-CREDENTIAL";
    error.startupFailure.environment = { KEY: "PRIVATE-CREDENTIAL" };
    throw error;
  };
  f.owner.check("check");
  await f.owner.waitFor("check");
  f.owner.install("install");
  const r = await f.owner.waitFor("install");
  assert.equal(r.status, "unknown");
  assert.equal(r.errorCode, "effect_unconfirmed");
  assert.equal(r.startupFailure.reason, "dependency_unavailable");
  assert.doesNotMatch(JSON.stringify(r), /PRIVATE-CREDENTIAL|environment/);
  assert.ok(events.some((event) => event.startupFailure?.reason === "dependency_unavailable"));
  assert.doesNotMatch(JSON.stringify(events), /PRIVATE-CREDENTIAL|environment/);
  await f.owner.close();
  const reopened = new DistributionUpdateOwner(f.options);
  t.after(() => reopened.close());
  assert.deepEqual(reopened.receipt("install").startupFailure, r.startupFailure);
  assert.equal(reopened.install("install").status, "unknown");
  assert.equal((await reopened.reconcile("install")).status, "unknown");
  assert.equal(f.calls.restart, 1);
  f.setRunning({ identity: b, instanceId: request.instanceId, dataScope: "fixture", ready: true });
  const recovered = await reopened.reconcile("install");
  assert.equal(recovered.status, "succeeded");
  assert.equal(recovered.startupFailure, undefined);
  assert.equal(f.calls.restart, 1);
});

for (const field of ["instanceId", "dataScope", "digest"])
  test(`readiness rejects wrong ${field}`, async (t) => {
    const { owner, options, setRunning } = await fixture(t);
    options.lifecycle.restart = async (request) => {
      const running = {
        identity: { ...b },
        instanceId: request.instanceId,
        dataScope: "fixture",
        ready: true,
      };
      if (field === "digest") running.identity.digest = "c".repeat(64);
      else running[field] = "wrong";
      setRunning(running);
    };
    owner.check("check");
    await owner.waitFor("check");
    owner.install("install");
    assert.equal((await owner.waitFor("install")).status, "unknown");
    assert.equal(owner.inspect().current.id, "v1");
  });

test("rollback requires an exact current identity and turns off automatic installation", async (t) => {
  const { owner, calls } = await fixture(t);
  owner.check("check");
  await owner.waitFor("check");
  owner.install("install");
  await owner.waitFor("install");
  owner.setPreferences("prefs", {
    autoCheck: true,
    autoInstall: true,
    intervalMs: 1000,
  });
  await owner.waitFor("prefs");
  owner.rollback("conflict", "v1");
  assert.equal(
    (await owner.waitFor("conflict")).errorCode,
    "rollback_conflict",
  );
  owner.rollback("rollback", "v2");
  assert.equal((await owner.waitFor("rollback")).status, "succeeded");
  assert.equal(calls.restart, 2);
  assert.equal(owner.inspect().current.id, "v1");
  assert.equal(owner.inspect().previous.id, "v2");
  assert.equal(owner.inspect().preferences.autoInstall, false);
});

test("automatic install follows completed checks without timer polling, and can remain disabled", async (t) => {
  const { owner, calls } = await fixture(t, {
    preferences: { autoCheck: true, autoInstall: true, intervalMs: 1000 },
  });
  owner.check("manual");
  await owner.waitFor("manual");
  await tick();
  const install = owner
    .inspect()
    .operations.find((op) => op.command === "install");
  assert.ok(install);
  assert.equal((await owner.waitFor(install.id)).status, "succeeded");
  assert.equal(calls.restart, 1);
});

test("catalog and diagnostics are bounded; notifications do not own update truth", async (t) => {
  const { owner, options } = await fixture(t, {
    onChange: () => {
      throw Error("subscriber failure");
    },
  });
  options.releases.check = async () => ({
    releases: new Array(101).fill(a),
    recommendedId: null,
  });
  owner.check("large");
  assert.equal((await owner.waitFor("large")).errorCode, "catalog_limit");
  for (let i = 0; i < 55; i++) {
    owner.setPreferences(`prefs-${i}`, {
      autoCheck: false,
      autoInstall: false,
      intervalMs: 1000,
    });
    await owner.waitFor(`prefs-${i}`);
  }
  assert.equal(owner.inspect().operations.length, 50);
  assert.equal(owner.diagnostics().events.length, 100);
});

test("dead owner recovery marks commands unknown and never reconstructs the queue", async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "update-owner-crash-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const moduleURL = new URL("../dist/index.js", import.meta.url).href;
  const code = `import {DistributionUpdateOwner} from ${JSON.stringify(moduleURL)};
    const owner=new DistributionUpdateOwner({directory:process.argv[1],dataScope:'fixture',releases:{check:()=>new Promise(()=>{}),prepare:()=>{},verify:()=>{}},lifecycle:{inspect:async()=>null,admitRestart:async()=>null,restart:async()=>{} }});
    owner.check('interrupted');setTimeout(()=>process.exit(0),20);`;
  const child = spawnSync(
    process.execPath,
    ["--input-type=module", "-e", code, directory],
    { encoding: "utf8" },
  );
  assert.equal(child.status, 0, child.stderr);
  let calls = 0;
  const owner = new DistributionUpdateOwner({
    directory,
    dataScope: "fixture",
    releases: {
      check: async () => {
        calls++;
        return { releases: [], recommendedId: null };
      },
      prepare: async () => candidate(a),
      verify: async () => true,
    },
    lifecycle: {
      inspect: async () => null,
      admitRestart: async () => null,
      restart: async () => {
        calls++;
      },
    },
  });
  t.after(() => owner.close());
  assert.equal(owner.receipt("interrupted").status, "unknown");
  assert.equal(owner.check("interrupted").status, "unknown");
  await tick();
  assert.equal(calls, 0);
  assert.throws(
    () =>
      new DistributionUpdateOwner({
        directory,
        dataScope: "fixture",
        releases: {},
        lifecycle: {},
      }),
    /owner_already_running/,
  );
});

test("availability cache joins live queries, distinguishes access scope, bypasses TTL and drops failed data", async () => {
  const cache = new AvailabilityCache(2);
  let count = 0;
  const get = (scope = "access", fresh = false, loader = async () => ++count) =>
    cache.get("source", scope, loader, { fresh, ttlMs: 10000 });
  assert.deepEqual(await Promise.all([get(), get()]), [1, 1]);
  assert.equal(await get(), 1);
  assert.equal(await get("access", true), 2);
  assert.equal(await get("other"), 3);
  await assert.rejects(
    get("access", true, async () => {
      throw Error("offline");
    }),
    /offline/,
  );
  assert.equal(await get(), 4);
});

test("downloads share global concurrency and per-target ordering across batches", async () => {
  const scheduler = new DownloadScheduler(2);
  let active = 0,
    peak = 0;
  const targets = new Set(),
    order = [];
  const job = (target, index) => ({
    target,
    run: async () => {
      assert.ok(!targets.has(target));
      targets.add(target);
      active++;
      peak = Math.max(active, peak);
      await tick();
      order.push(index);
      active--;
      targets.delete(target);
      return index;
    },
  });
  const results = await Promise.all([
    scheduler.run([job("a", 1), job("b", 2), job("a", 3)]),
    scheduler.run([job("a", 4), job("c", 5)]),
  ]);
  assert.deepEqual(results, [
    [1, 2, 3],
    [4, 5],
  ]);
  assert.equal(peak, 2);
  assert.ok(
    order.indexOf(1) < order.indexOf(3) && order.indexOf(3) < order.indexOf(4),
  );
});

test("download failure cancels and settles siblings before propagating the original error", async () => {
  const scheduler = new DownloadScheduler(2),
    gate = deferred(),
    primary = Error("primary");
  let settled = false;
  await assert.rejects(
    scheduler.run([
      {
        target: "first",
        run: async () => {
          await gate.promise;
          throw primary;
        },
      },
      {
        target: "second",
        run: async (signal) => {
          gate.resolve();
          await new Promise((resolve) =>
            signal.addEventListener("abort", resolve, { once: true }),
          );
          await tick();
          settled = true;
        },
      },
    ]),
    (error) => error === primary,
  );
  assert.equal(settled, true);
});

test("unproven quiescence refuses restart and releases admission as unchanged", async (t) => {
  const { owner, options, calls } = await fixture(t);
  let outcome;
  options.lifecycle.admitRestart = async () => ({
    evidence: {
      activeWork: 1,
      intakeClosed: true,
      instanceId: "first",
      dataScope: "fixture",
      observedAt: Date.now(),
    },
    release: (value) => {
      outcome = value;
    },
  });
  owner.check("check");
  await owner.waitFor("check");
  owner.install("install");
  assert.equal(
    (await owner.waitFor("install")).errorCode,
    "admission_unproven",
  );
  assert.equal(calls.restart, 0);
  assert.equal(outcome, "unchanged");
});

test("uncertain restart tells the host to retain its intake fence", async (t) => {
  const { owner, options } = await fixture(t);
  let outcome;
  options.lifecycle.admitRestart = async () => ({
    evidence: {
      activeWork: 0,
      intakeClosed: true,
      instanceId: "first",
      dataScope: "fixture",
      observedAt: Date.now(),
    },
    release: (value) => {
      outcome = value;
    },
  });
  options.lifecycle.restart = async () => {
    throw Error("unknown effect");
  };
  owner.check("check");
  await owner.waitFor("check");
  owner.install("install");
  assert.equal((await owner.waitFor("install")).status, "unknown");
  assert.equal(outcome, "unknown");
});

test("candidate mismatch and failed qualification cannot reach restart", async (t) => {
  const { owner, options, calls } = await fixture(t);
  owner.check("check");
  await owner.waitFor("check");
  options.releases.prepare = async () => candidate(a);
  owner.install("mismatch");
  assert.equal(
    (await owner.waitFor("mismatch")).errorCode,
    "candidate_mismatch",
  );
  options.releases.prepare = async () => candidate(b);
  options.releases.verify = async () => false;
  owner.install("unverified");
  assert.equal(
    (await owner.waitFor("unverified")).errorCode,
    "candidate_unverified",
  );
  assert.equal(calls.restart, 0);
});

test("shutdown interrupts preparation without restart or automatic replay on reopen", async (t) => {
  const { owner, options, calls } = await fixture(t);
  owner.check("check");
  await owner.waitFor("check");
  options.releases.prepare = async (release, { signal }) => {
    await new Promise((resolve) =>
      signal.addEventListener("abort", resolve, { once: true }),
    );
    return candidate(release);
  };
  owner.install("install");
  await tick();
  await owner.close();
  assert.equal(calls.restart, 0);
  const reopened = new DistributionUpdateOwner(options);
  t.after(() => reopened.close());
  assert.equal(reopened.receipt("install").status, "unknown");
  assert.equal(reopened.install("install").status, "unknown");
  await tick();
  assert.equal(calls.restart, 0);
});

test("manual check supersedes a due background wake instead of checking twice", async (t) => {
  const { owner, calls } = await fixture(t, {
    preferences: { autoCheck: true, autoInstall: false, intervalMs: 1000 },
    onChange: async () => {
      throw Error("subscriber offline");
    },
  });
  owner.start();
  owner.check("manual");
  await owner.waitFor("manual");
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(calls.check, 1);
});

test("admission and passive fence reconciliation bind the durable command identity", async (t) => {
  const { owner, options, getRunning } = await fixture(t);
  let admitted, reconciled;
  const original = options.lifecycle.admitRestart;
  options.lifecycle.admitRestart = async (context) => {
    admitted = context;
    const lease = await original(context);
    return {
      ...lease,
      release() {
        throw Error("lost release response");
      },
    };
  };
  options.lifecycle.reconcileAdmission = async (value) => {
    reconciled = value;
  };
  owner.check("check");
  await owner.waitFor("check");
  owner.install("durable-install");
  await owner.waitFor("durable-install");
  assert.equal(admitted.commandId, "durable-install");
  assert.equal(admitted.purpose, "distribution-update");
  assert.equal(admitted.dataScope, "fixture");
  assert.ok(admitted.signal instanceof AbortSignal);
  await owner.reconcile("durable-install");
  assert.equal(reconciled.commandId, "durable-install");
  assert.equal(reconciled.outcome, "ready");
  assert.equal(reconciled.observed.instanceId, getRunning().instanceId);
});

test("pre-restart refusal is durable before unchanged host release is requested", async (t) => {
  const { owner, options, setRunning, calls } = await fixture(t);
  let released;
  options.lifecycle.admitRestart = async () => ({
    evidence: {
      activeWork: 0,
      intakeClosed: true,
      instanceId: "first",
      dataScope: "fixture",
      observedAt: Date.now(),
      fenceId: "held",
    },
    release: (outcome) => {
      released = { outcome, proof: owner.restartProof("refuse") };
      throw Error("lost release response");
    },
  });
  setRunning({
    identity: b,
    instanceId: "first",
    dataScope: "fixture",
    ready: true,
  });
  owner.check("check");
  await owner.waitFor("check");
  owner.install("refuse");
  assert.equal((await owner.waitFor("refuse")).phase, "pre_restart_refused");
  assert.equal(released.outcome, "unchanged");
  assert.equal(released.proof.status, "failed");
  assert.equal(released.proof.phase, "pre_restart_refused");
  assert.equal(released.proof.instanceId, null);
  assert.equal(released.proof.admission.fenceId, "held");
  assert.equal(calls.restart, 0);

  // A lost release reply can be reconciled without repeating the restart or
  // trusting an in-memory lease from before the supervisor was replaced.
  let reconciled;
  options.lifecycle.reconcileAdmission = async (request) => {
    reconciled = request;
    throw Error("still unavailable");
  };
  await owner.reconcile("refuse");
  assert.equal(reconciled.commandId, "refuse");
  assert.equal(reconciled.outcome, "unchanged");
  assert.equal(reconciled.observed.instanceId, "first");
  assert.deepEqual(reconciled.observed.identity, b);
  reconciled = null;
  setRunning({
    identity: b,
    instanceId: "unexpected-replacement",
    dataScope: "fixture",
    ready: true,
  });
  await assert.rejects(owner.reconcile("refuse"), /readiness_unconfirmed/);
  assert.equal(reconciled, null);
  assert.equal(calls.restart, 0);
});
test("uncertain admission remains unknown and idle wakes never retry it", async (t) => {
  const { owner, options, calls } = await fixture(t);
  let admissions = 0;
  options.lifecycle.admitRestart = async () => {
    admissions++;
    throw Error("reply lost after host fenced");
  };
  owner.check("check");
  await owner.waitFor("check");
  owner.install("admission");
  assert.equal((await owner.waitFor("admission")).status, "unknown");
  assert.equal(owner.receipt("admission").phase, "admission_requested");
  assert.equal(owner.inspect().restartUnresolved, true);
  owner.notifyIdle();
  await tick();
  assert.equal(admissions, 1);
  assert.equal(calls.restart, 0);
  owner.install("later");
  assert.equal((await owner.waitFor("later")).errorCode, "restart_unresolved");
});

for (const interruptedPhase of [
  "qualifying_activation",
  "activation_qualified",
]) {
  test(`interrupted ${interruptedPhase} retains admission uncertainty and cannot replay`, async (t) => {
    const directory = await mkdtemp(join(tmpdir(), "activation-interruption-"));
    const moduleURL = new URL("../dist/index.js", import.meta.url).href;
    const code = `import {DistributionUpdateOwner} from ${JSON.stringify(moduleURL)};
      const a=${JSON.stringify(a)},b=${JSON.stringify(b)};
      const owner=new DistributionUpdateOwner({directory:process.argv[1],dataScope:'fixture',initial:{identity:a,handle:'v1'},
        preferences:{autoCheck:false,autoInstall:false,intervalMs:1000},
        releases:{check:async()=>({releases:[a,b],recommendedId:b.id}),prepare:async(identity)=>({identity,handle:identity.id}),verify:async()=>true,qualifyActivation:async()=>{}},
        lifecycle:{inspect:async()=>({identity:a,instanceId:'initial',dataScope:'fixture',ready:true}),
          admitRestart:async()=>({evidence:{activeWork:0,intakeClosed:true,instanceId:'initial',dataScope:'fixture',observedAt:Date.now()},release:()=>{}}),
          restart:async()=>{throw Error('must not restart');}},
        onChange:r=>{if(r.phase===${JSON.stringify(interruptedPhase)})process.exit(0);}});
      owner.check('check');await owner.waitFor('check');owner.install('install');await owner.waitFor('install');process.exit(1);`;
    const child = spawnSync(
      process.execPath,
      ["--input-type=module", "-e", code, directory],
      { encoding: "utf8", timeout: 10000 },
    );
    assert.equal(child.status, 0, child.stderr);
    let effects = 0;
    const owner = new DistributionUpdateOwner({
      directory,
      dataScope: "fixture",
      releases: {
        check: async () => {
          throw Error("not expected");
        },
        prepare: async () => {
          effects++;
        },
        verify: async () => true,
        qualifyActivation: async () => {
          effects++;
        },
      },
      lifecycle: {
        inspect: async () => null,
        admitRestart: async () => {
          effects++;
          return null;
        },
        restart: async () => {
          effects++;
        },
      },
    });
    t.after(async () => {
      await owner.close();
      await rm(directory, { recursive: true, force: true });
    });
    const receipt = owner.receipt("install");
    assert.equal(receipt.phase, interruptedPhase);
    assert.equal(receipt.status, "unknown");
    assert.ok(receipt.activation.startedAt);
    assert.equal(owner.inspect().restartUnresolved, true);
    assert.equal(owner.install("install").status, "unknown");
    owner.notifyIdle();
    assert.equal((await owner.reconcile("install")).status, "unknown");
    owner.install("another");
    assert.equal(
      (await owner.waitFor("another")).errorCode,
      "restart_unresolved",
    );
    assert.equal(effects, 0);
  });
}

test("ready proof precedes settlement, but waiters and mutation readiness do not", async (t) => {
  const { owner, options, calls } = await fixture(t);
  const released = deferred(),
    original = options.lifecycle.admitRestart;
  options.lifecycle.admitRestart = async (context) => ({
    ...(await original(context)),
    release: () => released.promise,
  });
  owner.check("check");
  await owner.waitFor("check");
  owner.install("install");
  let completed = false;
  const waiting = owner.waitFor("install").then((r) => {
    completed = true;
    return r;
  });
  await tick();
  await tick();
  assert.equal(owner.restartProof("install").phase, "ready");
  assert.equal(owner.receipt("install").admissionSettlement.state, "pending");
  assert.deepEqual(owner.inspect().actionReadiness, {
    state: "busy",
    commandId: "install",
  });
  assert.equal(completed, false);
  released.resolve();
  assert.equal((await waiting).admissionSettlement.state, "settled");
  assert.equal(owner.inspect().actionReadiness.state, "available");
  owner.rollback("rollback", "v2");
  assert.equal(
    (await owner.waitFor("rollback")).admissionSettlement.state,
    "settled",
  );
  assert.equal(calls.restart, 2);
});

test("lost intake release remains visible and blocks new effects until passive reconciliation", async (t) => {
  const { owner, options, calls } = await fixture(t),
    original = options.lifecycle.admitRestart;
  options.lifecycle.admitRestart = async (context) => ({
    ...(await original(context)),
    release: () => {
      throw Error("lost");
    },
  });
  owner.check("check");
  await owner.waitFor("check");
  owner.install("install");
  const r = await owner.waitFor("install");
  assert.equal(r.status, "succeeded");
  assert.equal(r.admissionSettlement.state, "unknown");
  assert.equal(
    owner.inspect().actionReadiness.state,
    "reconciliation_required",
  );
  owner.rollback("blocked", "v2");
  assert.equal(
    (await owner.waitFor("blocked")).errorCode,
    "restart_unresolved",
  );
  let reconciled = 0;
  options.lifecycle.reconcileAdmission = async () => {
    reconciled++;
  };
  assert.equal(
    (await owner.reconcile("install")).admissionSettlement.state,
    "settled",
  );
  await owner.reconcile("install");
  assert.equal(reconciled, 1);
  assert.equal(calls.restart, 1);
  assert.equal(owner.inspect().actionReadiness.state, "available");
});

test("racing settled reconciliation cannot be downgraded by late release failure", async (t) => {
  const { owner, options } = await fixture(t),
    original = options.lifecycle.admitRestart,
    release = deferred();
  options.lifecycle.admitRestart = async (context) => ({
    ...(await original(context)),
    release: () => release.promise,
  });
  options.lifecycle.reconcileAdmission = async () => {};
  owner.check("check");
  await owner.waitFor("check");
  owner.install("install");
  await tick();
  await tick();
  assert.equal(
    (await owner.reconcile("install")).admissionSettlement.state,
    "settled",
  );
  release.reject(Error("late loss"));
  await tick();
  assert.equal(owner.receipt("install").admissionSettlement.state, "settled");
});

test("persisted pending settlement recovers as unknown without replaying a ready update", async (t) => {
  const { owner, options, directory, calls } = await fixture(t),
    original = options.lifecycle.admitRestart;
  options.lifecycle.admitRestart = async (context) => ({
    ...(await original(context)),
    release: () => {
      throw Error("lost");
    },
  });
  owner.check("check");
  await owner.waitFor("check");
  owner.install("install");
  await owner.waitFor("install");
  await owner.close();
  const { DatabaseSync } = await import("node:sqlite");
  const db = new DatabaseSync(join(directory, "updates.sqlite3"));
  db.exec(
    "UPDATE operations SET value=json_set(value,'$.admissionSettlement.state','pending') WHERE id='install'",
  );
  db.close();
  const replacement = new DistributionUpdateOwner(options);
  assert.equal(replacement.receipt("install").status, "succeeded");
  assert.equal(
    replacement.receipt("install").admissionSettlement.state,
    "unknown",
  );
  assert.equal(
    replacement.inspect().actionReadiness.state,
    "reconciliation_required",
  );
  assert.equal(calls.restart, 1);
  await replacement.close();
});

test('prepare is terminal without admission; exact activation survives reopen and pauses automatic install',async t=>{
 const f=await fixture(t);f.owner.check('check');await f.owner.waitFor('check');
 f.owner.prepare('stage');assert.equal((await f.owner.waitFor('stage')).phase,'prepared');
 assert.equal(f.calls.admission,0);assert.equal(f.calls.restart,0);
 assert.deepEqual(f.owner.inspect().staged,{commandId:'stage',target:b,expectedCurrentId:a.id});
 f.owner.setPreferences('auto',{autoCheck:true,autoInstall:true,intervalMs:1000});await f.owner.waitFor('auto');
 f.owner.check('after-stage');await f.owner.waitFor('after-stage');await tick();assert.equal(f.calls.restart,0);
 await f.owner.close();const resumed=new DistributionUpdateOwner(f.options);t.after(()=>resumed.close());
 const args={preparedCommandId:'stage',targetDigest:b.digest,expectedCurrentId:a.id};
 resumed.activate('activate',args);assert.equal((await resumed.waitFor('activate')).phase,'ready');
 assert.equal(f.calls.prepare,1);assert.equal(f.calls.restart,1);assert.equal(resumed.inspect().staged,null);
 assert.equal(resumed.activate('activate',args).status,'succeeded');assert.equal(f.calls.restart,1);
 assert.ok(resumed.restartProof('activate'));assert.equal(resumed.restartProof('stage'),null);
});

test('staged activation refuses wrong digest, baseline and superseded preparation before admission',async t=>{
 const f=await fixture(t);f.owner.check('check');await f.owner.waitFor('check');
 f.owner.prepare('stage');await f.owner.waitFor('stage');
 for(const [id,patch] of [['digest',{targetDigest:a.digest}],['baseline',{expectedCurrentId:'other'}]]){
  f.owner.activate(id,{preparedCommandId:'stage',targetDigest:b.digest,expectedCurrentId:a.id,...patch});
  assert.equal((await f.owner.waitFor(id)).errorCode,'staged_candidate_conflict');
 }
 f.owner.prepare('new-stage');await f.owner.waitFor('new-stage');
 f.owner.activate('superseded',{preparedCommandId:'stage',targetDigest:b.digest,expectedCurrentId:a.id});
 assert.equal((await f.owner.waitFor('superseded')).errorCode,'staged_candidate_conflict');
 assert.equal(f.calls.admission,0);assert.equal(f.calls.restart,0);
});

test('staged activation rechecks saved bytes and latest sources, without fallback or repeated prepare',async t=>{
 const f=await fixture(t);f.owner.check('check');await f.owner.waitFor('check');
 f.owner.prepare('stage');await f.owner.waitFor('stage');
 const args={preparedCommandId:'stage',targetDigest:b.digest,expectedCurrentId:a.id};
 f.options.releases.verify=async()=>false;f.owner.activate('changed-bytes',args);
 assert.equal((await f.owner.waitFor('changed-bytes')).errorCode,'candidate_unverified');assert.equal(f.calls.admission,0);
 f.options.releases.verify=async()=>true;
 f.options.releases.qualifyActivation=async()=>{throw Error('source_advanced');};
 f.owner.activate('advanced-source',args);const r=await f.owner.waitFor('advanced-source');
 assert.equal(r.errorCode,'source_advanced');assert.equal(r.phase,'pre_restart_refused');
 assert.equal(r.admissionSettlement.state,'settled');assert.equal(f.calls.restart,0);assert.equal(f.calls.prepare,1);
});

test('idle wake activates exactly once',async t=>{
 const f=await fixture(t);f.owner.check('check');await f.owner.waitFor('check');
 f.owner.prepare('stage');await f.owner.waitFor('stage');f.setBusy(true);
 f.owner.activate('activate',{preparedCommandId:'stage',targetDigest:b.digest,expectedCurrentId:a.id});
 await tick();assert.equal(f.owner.receipt('activate').phase,'waiting_idle');assert.equal(f.calls.restart,0);
 f.setBusy(false);f.owner.notifyIdle();assert.equal((await f.owner.waitFor('activate')).phase,'ready');
 assert.equal(f.calls.restart,1);assert.equal(f.owner.inspect().staged,null);
});

test('lost staged activation response reconciles exact process without another restart',async t=>{
 const f=await fixture(t);f.owner.check('check');await f.owner.waitFor('check');f.owner.prepare('stage');await f.owner.waitFor('stage');
 const original=f.options.lifecycle.restart;f.options.lifecycle.restart=async request=>{await original(request);throw Error('lost response');};
 const args={preparedCommandId:'stage',targetDigest:b.digest,expectedCurrentId:a.id};
 f.owner.activate('activate',args);assert.equal((await f.owner.waitFor('activate')).status,'unknown');
 assert.equal(f.owner.activate('activate',args).status,'unknown');
 const r=await f.owner.reconcile('activate');assert.equal(r.status,'succeeded');assert.equal(f.calls.restart,1);
 assert.equal(f.owner.inspect().staged,null);
});
