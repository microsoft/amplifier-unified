import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile, chmod } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import { createServer } from "node:http";
import {
  serveHostControl,
  HostControlClient,
  connectHostControlFile,
  readHostControlConnection,
  createHostReleaseVerifier,
} from "../dist/index.js";
const a = {
  id: "one",
  version: "1.0.0",
  revision: "a".repeat(40),
  digest: "a".repeat(64),
};
const b = {
  id: "two",
  version: "2.0.0",
  revision: "b".repeat(40),
  digest: "b".repeat(64),
};
const original = {
  identity: a,
  instanceId: "first",
  dataScope: "scope",
  ready: true,
};
const context = {
  commandId: "install",
  purpose: "distribution-update",
  dataScope: "scope",
  signal: new AbortController().signal,
};
async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "host-control-")),
    key = randomBytes(32).toString("hex");
  let current = original,
    held = null,
    busy = false,
    unknown = false,
    calls = 0,
    receipt = null;
  const host = {
    inspectQuiescence: () => ({
      enabled: true,
      intakeClosed: !!held,
      fence: held,
    }),
    quiescenceReceipt: () => receipt,
    admitQuiescence: async (request) => {
      calls++;
      assert.equal(request.retryRefused, true);
      if (busy)
        return (receipt = {
          admitted: false,
          executed: false,
          intakeClosed: false,
          commandId: request.commandId,
          fenceId: "busy",
        });
      held = {
        fenceId: "fence",
        commandId: request.commandId,
        purpose: request.purpose,
        instanceId: current.instanceId,
        dataScope: current.dataScope,
        phase: unknown ? "unknown" : "held",
      };
      return (receipt = unknown
        ? { admitted: false, intakeClosed: true, fence: held }
        : {
            admitted: true,
            commandId: request.commandId,
            fenceId: held.fenceId,
            evidence: {
              activeWork: 0,
              intakeClosed: true,
              instanceId: current.instanceId,
              dataScope: current.dataScope,
              observedAt: Date.now(),
            },
          });
    },
    releaseQuiescence: async (request) => {
      assert.equal(request.fenceId, held.fenceId);
      assert.equal(request.commandId, held.commandId);
      if (request.outcome === "unknown") {
        held.phase = "unknown";
        return (receipt = { admitted: false, intakeClosed: true, fence: held });
      }
      held = null;
      return (receipt = {
        released: true,
        intakeClosed: false,
        fenceId: request.fenceId,
        commandId: request.commandId,
        outcome: request.outcome,
        instanceId: current.instanceId,
        receiptId: request.commandId,
      });
    },
  };
  const file = join(root, "host.json"),
    tokenFile = join(root, "token");
  await writeFile(tokenFile, key, { mode: 0o600 });
  const options = {
    host,
    inspectRunning: () => current,
    token: key,
    discovery: { file, tokenFile, dataScope: "scope" },
  };
  let server = await serveHostControl(options);
  const client = connectHostControlFile(file, "scope");
  t.after(async () => {
    client.close();
    await server.close();
    await rm(root, { recursive: true, force: true });
  });
  return {
    client,
    host,
    file,
    key,
    get server() {
      return server;
    },
    calls: () => calls,
    setBusy: (v) => (busy = v),
    setUnknown: (v) => (unknown = v),
    setRunning: (v) => (current = v),
    restart: async () => {
      await server.close();
      current = { ...original, instanceId: "second", identity: b };
      server = await serveHostControl(options);
    },
  };
}
test("host control distinguishes definitive busy from uncertain admission and follows replacement discovery", async (t) => {
  const f = await fixture(t);
  f.setBusy(true);
  assert.equal(await f.client.admitRestart(context), null);
  f.setBusy(false);
  const lease = await f.client.admitRestart(context);
  assert.equal(lease.evidence.fenceId, "fence");
  await f.restart();
  assert.equal((await f.client.inspect()).instanceId, "second");
  await lease.release("ready");
  await f.client.reconcileAdmission({
    ...context,
    outcome: "ready",
    observed: await f.client.inspect(),
  });
  assert.equal((await f.client.inspectQuiescence()).intakeClosed, false);
  f.setUnknown(true);
  await assert.rejects(
    f.client.admitRestart({ ...context, commandId: "uncertain" }),
    /host_admission_unknown/,
  );
  assert.equal((await f.client.inspectQuiescence()).intakeClosed, true);
});
test("host control rejects wrong credentials, browser origins, scope and oversized input", async (t) => {
  const f = await fixture(t);
  for (const headers of [
    {},
    {
      Authorization: "Bearer " + f.key,
      "X-Amplifier-Host-Control": "1",
      Origin: "https://example.org",
    },
  ]) {
    const r = await fetch(f.server.url + "v1/host-control", {
      method: "POST",
      headers,
      body: "{}",
    });
    assert.equal(r.status, 401);
  }
  const client = new HostControlClient({
    dataScope: "foreign",
    connect: () => ({ url: f.server.url, token: f.key, dataScope: "foreign" }),
  });
  await assert.rejects(
    client.admitRestart({ ...context, dataScope: "foreign" }),
    /outcome_unknown/,
  );
  assert.equal(f.calls(), 0);
  const r = await fetch(f.server.url + "v1/host-control", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + f.key,
      "X-Amplifier-Host-Control": "1",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ padding: "x".repeat(20000) }),
  });
  assert.equal(r.status, 400);
  await chmod(f.file, 0o644);
  await assert.rejects(
    readHostControlConnection(f.file),
    /private_file_required/,
  );
});
test("idle wakes are pushed; closing the client stops observation without polling", async (t) => {
  const f = await fixture(t);
  let wake = 0;
  const received = new Promise((resolve) => {
    f.client.onIdle(() => {
      wake++;
      if (wake === 2) resolve();
    });
  });
  for (let i = 0; i < 100 && !wake; i++)
    await new Promise((r) => setTimeout(r, 5));
  assert.equal(wake, 1);
  f.server.notifyMayBeIdle();
  await received;
  assert.equal(f.calls(), 0);
  f.client.close();
  f.server.notifyMayBeIdle();
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(wake, 2);
});
test("a lost host admission reply remains fenced and is never automatically retried", async (t) => {
  const f = await fixture(t);
  let requests = 0;
  const proxy = createServer(async (req, res) => {
    let data = "";
    for await (const chunk of req) data += chunk;
    requests++;
    await fetch(f.server.url + "v1/host-control", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + f.key,
        "X-Amplifier-Host-Control": "1",
        "Content-Type": "application/json",
      },
      body: data,
    });
    res.destroy();
  });
  await new Promise((r) => proxy.listen(0, "127.0.0.1", r));
  t.after(
    () =>
      new Promise((r) => {
        proxy.closeAllConnections();
        proxy.close(r);
      }),
  );
  const lost = new HostControlClient({
    dataScope: "scope",
    connect: () => ({
      url: `http://127.0.0.1:${proxy.address().port}/`,
      token: f.key,
      dataScope: "scope",
    }),
  });
  await assert.rejects(lost.admitRestart(context), /outcome_unknown/);
  assert.equal(requests, 1);
  assert.equal(f.calls(), 1);
  assert.equal((await f.client.inspectQuiescence()).intakeClosed, true);
});
test("release verification reads supervisor authority and rejects asserted or mismatched proof", async () => {
  let reads = 0;
  const proof = {
    schema: "distribution-restart-proof-v1",
    commandId: "install",
    purpose: "distribution-update",
    dataScope: "scope",
    status: "succeeded",
    phase: "ready",
    target: b,
    instanceId: "second",
    previousInstanceId: "first",
    admission: {
      fenceId: "fence",
      instanceId: "first",
      dataScope: "scope",
      activeWork: 0,
      intakeClosed: true,
    },
    admittedRunning: original,
  };
  let actual = { ...original, identity: b, instanceId: "second" };
  const verifier = createHostReleaseVerifier({
    supervisor: {
      restartProof: async () => {
        reads++;
        return proof;
      },
    },
    inspectRunning: () => actual,
  });
  const request = {
    ...context,
    fenceId: "fence",
    instanceId: "first",
    outcome: "ready",
    evidence: { ready: true },
  };
  assert.equal((await verifier(request)).verified, true);
  for (const change of [
    { fenceId: "wrong" },
    { commandId: "wrong" },
    { dataScope: "foreign" },
    { purpose: "recovery" },
  ])
    await assert.rejects(
      verifier({ ...request, ...change }),
      /proof_unconfirmed/,
    );
  actual = { ...actual, identity: a };
  await assert.rejects(verifier(request), /proof_unconfirmed/);
  actual = original;
  proof.status = "failed";
  proof.phase = "pre_restart_refused";
  proof.instanceId = null;
  assert.equal(
    (await verifier({ ...request, outcome: "unchanged" })).verified,
    true,
  );
  proof.phase = "admission_requested";
  await assert.rejects(
    verifier({ ...request, outcome: "unchanged" }),
    /proof_unconfirmed/,
  );
  assert.ok(reads > 1);
});
