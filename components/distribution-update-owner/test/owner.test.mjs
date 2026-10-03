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
    return original(context);
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
