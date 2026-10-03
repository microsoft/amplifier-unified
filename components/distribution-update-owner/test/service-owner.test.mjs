import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  ServiceLifecycleOwner,
  PosixOwnedProcessLifecycle,
  createHostServiceReleaseVerifier,
  serveSupervisor,
  SupervisorClient,
  DistributionUpdateOwner,
} from "../dist/index.js";
const release = {
  id: "fixture",
  version: "1.0.0",
  revision: "a".repeat(40),
  digest: "a".repeat(64),
};
const target = { identity: release, handle: "fixture-target" };
const binding = {
  installationId: "test-installation",
  ownerId: "test-owner",
  dataScope: "test-scope",
};
const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
};
async function until(fn) {
  for (let i = 0; i < 300; i++) {
    const v = await fn();
    if (v) return v;
    await new Promise((r) => setTimeout(r, 10));
  }
  throw Error("fixture_deadline");
}
async function fixture(t, options = {}) {
  const directory = await mkdtemp(join(tmpdir(), "service-owner-")),
    entry = join(directory, "app.mjs"),
    state = join(directory, "running.json");
  await writeFile(
    entry,
    `import {writeFileSync} from 'node:fs'; writeFileSync(process.argv[2],JSON.stringify({instanceId:process.env.AMPLIFIER_DISTRIBUTION_INSTANCE_ID,dataScope:process.env.AMPLIFIER_DISTRIBUTION_DATA_SCOPE}));process.on('SIGTERM',()=>{${options.stopCode ?? "process.exit(0)"}});setTimeout(()=>process.exit(0),8000);`,
  );
  let service,
    external = null,
    fence = null,
    failRelease = false,
    releaseGate = null,
    verify = true;
  const calls = { admit: 0, release: 0, launch: 0 };
  const inspect = async () => {
    if (external) return external;
    const own = lifecycle.processes.inspect();
    if (own.state !== "running") return null;
    try {
      const v = JSON.parse(await readFile(state, "utf8"));
      if (v.instanceId !== own.identity.instanceId) return null;
      return { ...v, identity: release, ready: true };
    } catch {
      return null;
    }
  };
  const lifecycle = new PosixOwnedProcessLifecycle({
    ownerId: binding.ownerId,
    stopMs: options.stopMs ?? 1000,
    readinessMs: options.readinessMs ?? 1000,
    resolve: async () => {
      calls.launch++;
      return { command: process.execPath, args: [entry, state] };
    },
    inspect,
    admitRestart: async () => {
      throw Error("wrong_update_admission");
    },
    initialProvisioning: {
      claim: async (r) => ({
        kind: "pristine-installation",
        installationId: binding.installationId,
        commandId: r.commandId,
        instanceId: r.instanceId,
        dataScope: r.dataScope,
        targetDigest: r.target.identity.digest,
      }),
    },
  });
  const verifier = createHostServiceReleaseVerifier({
    service: { proof: (id) => service.proof(id) },
    inspectRunningService: async () => {
      const v = await lifecycle.inspectOwned();
      return {
        ...binding,
        instanceId: v.instanceId,
        releaseDigest: v.identity.digest,
      };
    },
  });
  const host = {
    admitServiceStop: async (request) => {
      calls.admit++;
      if (options.busy)
        return {
          admitted: false,
          executed: false,
          intakeClosed: false,
          purpose: "service-stop",
          expected: request.expected,
        };
      fence = {
        fenceId: "fixture-fence",
        commandId: request.commandId,
        purpose: "service-stop",
        instanceId: request.expected.instanceId,
        dataScope: binding.dataScope,
        serviceIdentity: request.expected,
        phase: "held",
      };
      return {
        admitted: true,
        intakeClosed: true,
        ...fence,
        expected: request.expected,
        evidence: {
          activeWork: 0,
          intakeClosed: true,
          instanceId: request.expected.instanceId,
          dataScope: binding.dataScope,
          observedAt: Date.now(),
        },
      };
    },
    inspectServiceLifecycle: async () => ({ fence }),
    serviceStopReceipt: async () => fence,
    releaseServiceStop: async (request) => {
      calls.release++;
      if (releaseGate) await releaseGate.promise;
      if (failRelease) throw Error("reply_lost");
      const proof = await verifier({
        ...request,
        instanceId: fence.instanceId,
        dataScope: binding.dataScope,
        purpose: "service-stop",
        serviceIdentity: fence.serviceIdentity,
      });
      return {
        released: true,
        intakeClosed: false,
        purpose: "service-stop",
        fenceId: request.fenceId,
        commandId: request.commandId,
        outcome: request.outcome,
        expected: proof.expected,
        observed: proof.observed,
        receiptId: proof.receiptId,
        resumeCommandId: request.resumeCommandId,
      };
    },
  };
  const create = () =>
    new ServiceLifecycleOwner({
      directory: join(directory, "owner"),
      ...binding,
      host,
      lifecycle,
      releases: { verify: async () => verify },
      currentRelease: () => target,
      updateMutationBlocked: options.updateMutationBlocked,
      onChange: (r) => options.onChange?.(r),
    });
  service = create();
  t.after(async () => {
    await service.close();
    await lifecycle.close().catch(() => {});
    await until(() => lifecycle.processes.inspect().state !== "running");
    await rm(directory, { recursive: true, force: true });
  });
  await lifecycle.startInitial(target, binding.dataScope);
  const expected = (await service.inspect()).identity;
  return {
    directory,
    expected,
    lifecycle,
    host,
    calls,
    verifier,
    get service() {
      return service;
    },
    setVerify: (v) => (verify = v),
    setExternal: (v) => (external = v),
    setReleaseFailure: (v) => (failRelease = v),
    setReleaseGate: (v) => (releaseGate = v),
    reopen: async () => {
      await service.close();
      service = create();
    },
  };
}
async function stop(f, id = "stop") {
  f.service.stop({ commandId: id, expected: f.expected });
  return f.service.waitFor(id);
}
async function resume(f, id = "resume") {
  f.service.resume({
    commandId: id,
    expected: f.expected,
    stoppedCommandId: "stop",
  });
  return f.service.waitFor(id);
}

test("explicit stop and resume use separate durable service proof, exact exit and a new instance", async (t) => {
  const f = await fixture(t);
  const stopped = await stop(f);
  assert.equal(stopped.status, "stopped");
  assert.ok(stopped.exitProof.ownerReceiptId);
  assert.equal(f.service.blocksUpdates(), true);
  assert.equal((await f.service.inspect()).state, "stopped");
  assert.equal(f.calls.launch, 1);
  const next = await resume(f);
  assert.equal(next.status, "ready");
  assert.equal(next.admissionSettlement.state, "settled");
  assert.notEqual(next.observed.instanceId, f.expected.instanceId);
  assert.equal(next.observed.releaseDigest, f.expected.releaseDigest);
  assert.equal(f.calls.launch, 2);
  assert.equal(f.service.blocksUpdates(), false);
  assert.equal(
    f.service.resume({
      commandId: "resume",
      expected: f.expected,
      stoppedCommandId: "stop",
    }).status,
    "ready",
  );
  assert.equal(f.calls.launch, 2);
  assert.equal((await resume(f, "duplicate")).status, "refused");
  assert.equal(f.calls.launch, 2);
});
test("busy host and active updater refuse service stop without process effects", async (t) => {
  for (const options of [
    { busy: true },
    { updateMutationBlocked: () => true },
  ]) {
    const f = await fixture(t, options);
    assert.equal((await stop(f)).status, "refused");
    assert.equal(f.lifecycle.processes.inspect().state, "running");
    assert.equal(f.calls.release, 0);
    assert.equal(f.calls.launch, 1);
  }
});
test("adoption and changed command arguments never authorize an unowned process", async (t) => {
  const f = await fixture(t);
  assert.equal(
    f.service.adopt({ commandId: "adopt", expected: f.expected }).status,
    "refused",
  );
  assert.equal(f.calls.admit, 0);
  assert.throws(
    () =>
      f.service.adopt({
        commandId: "adopt",
        expected: { ...f.expected, instanceId: "other" },
      }),
    /command_identity_conflict/,
  );
  assert.throws(
    () =>
      f.service.stop({
        commandId: "foreign",
        expected: { ...f.expected, ownerId: "other" },
      }),
    /binding_conflict/,
  );
});
test("lost stop reply is unknown; passive late-exit reconciliation never resends stop", async (t) => {
  const f = await fixture(t, {
    stopCode: "setTimeout(()=>process.exit(0),300)",
    stopMs: 100,
  });
  const r = await stop(f);
  assert.equal(r.status, "unknown");
  assert.equal(f.service.blocksUpdates(), true);
  await assert.rejects(
    f.lifecycle.processes.stop(f.expected),
    /stop_unconfirmed/,
  );
  await until(() => f.lifecycle.processes.exitProof(f.expected));
  await tick();
  assert.equal((await f.service.reconcile("stop")).status, "stopped");
  assert.equal(f.calls.admit, 1);
});
test("ready remains distinct from settled; competing effects wait until release completes", async (t) => {
  const f = await fixture(t);
  await stop(f);
  const gate = deferred();
  f.setReleaseGate(gate);
  f.service.resume({
    commandId: "resume",
    expected: f.expected,
    stoppedCommandId: "stop",
  });
  await until(() => f.service.receipt("resume").status === "ready");
  assert.equal(f.service.blocksUpdates(), true);
  assert.equal(
    f.service.receipt("resume").admissionSettlement.state,
    "pending",
  );
  const competing = f.service.stop({
    commandId: "too-early",
    expected: f.service.receipt("resume").observed,
  });
  assert.equal(competing.status, "refused");
  gate.resolve();
  const done = await f.service.waitFor("resume");
  assert.equal(done.admissionSettlement.state, "settled");
  assert.equal(f.service.blocksUpdates(), false);
});
test("lost fence release preserves ready and reconciliation releases without relaunch", async (t) => {
  const f = await fixture(t);
  await stop(f);
  f.setReleaseFailure(true);
  const r = await resume(f);
  assert.equal(r.status, "ready");
  assert.equal(r.admissionSettlement.state, "unknown");
  assert.equal(f.service.blocksUpdates(), true);
  await tick();
  f.setReleaseFailure(false);
  assert.equal(
    (await f.service.reconcile("resume")).admissionSettlement.state,
    "settled",
  );
  assert.equal(f.calls.launch, 2);
  assert.equal(f.calls.release, 2);
});
test("concurrent reconciliation cannot downgrade confirmed service settlement", async (t) => {
  const f = await fixture(t);
  await stop(f);
  f.setReleaseFailure(true);
  await resume(f);
  await tick();
  f.setReleaseFailure(false);
  const original = f.host.releaseServiceStop,
    late = deferred();
  let calls = 0;
  f.host.releaseServiceStop = async (request) => {
    if (++calls === 1) {
      await late.promise;
      throw Error("late_lost_reply");
    }
    return original(request);
  };
  const first = f.service.reconcile("resume");
  await until(() => calls === 1);
  assert.equal(
    (await f.service.reconcile("resume")).admissionSettlement.state,
    "settled",
  );
  late.resolve();
  assert.equal((await first).admissionSettlement.state, "settled");
  assert.equal(f.service.blocksUpdates(), false);
  assert.equal(f.calls.launch, 2);
});

test("retained stopped receipt survives owner reopen and explicit resume verifies retained bytes", async (t) => {
  const f = await fixture(t);
  await stop(f);
  await f.reopen();
  assert.equal((await f.service.inspect()).state, "stopped");
  assert.equal(f.calls.launch, 1);
  f.setVerify(false);
  assert.equal((await resume(f, "invalid")).status, "refused");
  assert.equal(f.calls.launch, 1);
  f.setVerify(true);
  assert.equal((await resume(f)).status, "ready");
  assert.equal(f.calls.launch, 2);
});
test("existing service is not replaced by resume and an uncertain resume cannot repeat launch", async (t) => {
  const f = await fixture(t);
  await stop(f);
  f.setExternal({
    identity: release,
    ready: true,
    dataScope: binding.dataScope,
    instanceId: "unowned",
  });
  assert.equal((await resume(f)).status, "unknown");
  assert.equal(f.calls.launch, 1);
  assert.equal((await resume(f, "again")).status, "refused");
  await tick();
  assert.equal((await f.service.reconcile("resume")).status, "unknown");
  assert.equal(f.calls.launch, 1);
  f.setExternal(null);
});
test("host verifier rejects update proof, wrong installation, and unbound resume", async (t) => {
  const f = await fixture(t);
  await stop(f);
  const r = await resume(f);
  const req = {
    fenceId: "fixture-fence",
    commandId: "stop",
    purpose: "service-stop",
    instanceId: f.expected.instanceId,
    dataScope: f.expected.dataScope,
    serviceIdentity: f.expected,
    outcome: "resumed",
    resumeCommandId: "resume",
    evidence: {},
  };
  assert.equal((await f.verifier(req)).kind, "service-lifecycle");
  for (const change of [
    { purpose: "distribution-update" },
    { resumeCommandId: "unknown" },
    { serviceIdentity: { ...f.expected, installationId: "wrong" } },
    { instanceId: r.observed.instanceId },
  ])
    await assert.rejects(f.verifier({ ...req, ...change }));
});
