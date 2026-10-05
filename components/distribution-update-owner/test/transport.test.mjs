import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import {
  DistributionUpdateOwner,
  SupervisorClient,
  serveSupervisor,
} from "../dist/index.js";
async function fixture(t, {observeStatus,service}={}) {
  const directory = await mkdtemp(join(tmpdir(), "supervisor-transport-"));
  let transport,
    calls = 0;
  const owner = new DistributionUpdateOwner({
    directory,
    dataScope: "fixture",
    observeStatus,
    releases: {
      check: async () => {
        calls++;
        return { releases: [], recommendedId: null };
      },
      prepare: async () => {
        throw Error("unused");
      },
      verify: async () => false,
    },
    lifecycle: {
      inspect: async () => null,
      admitRestart: async () => null,
      restart: async () => {
        throw Error("unused");
      },
    },
    onChange: (value) => transport?.publish(value),
  });
  const token = randomBytes(32).toString("hex");
  transport = await serveSupervisor({ owner, token, service });
  const client = new SupervisorClient({ url: transport.url, token });
  t.after(async () => {
    client.close();
    await transport.close();
    await owner.close();
    await rm(directory, { recursive: true, force: true });
  });
  return { owner, client, transport, token, calls: () => calls };
}
test("authenticated RPC matches the public facade and notifications are event-driven", async (t) => {
  const { client, owner, calls } = await fixture(t);
  const received = [];
  let completed; const completion = new Promise(resolve => completed = resolve);
  const reset = new Promise((resolve) =>
    client.subscribe((event) => {
      received.push(event);
      if(event.receipt?.id === "manual" && event.receipt.status === "succeeded") completed();
      if (event.reset) resolve();
    }),
  );
  await reset;
  const accepted = await client.owner.check("manual");
  assert.ok(["queued", "running", "succeeded"].includes(accepted.status));
  await owner.waitFor("manual");
  await completion;
  assert.ok(
    received.some(
      (event) =>
        event.receipt?.id === "manual" && event.receipt.status === "succeeded",
    ),
  );
  assert.equal((await client.owner.receipt("manual")).status, "succeeded");
  await client.owner.check("manual");
  assert.equal(calls(), 1);
  await assert.rejects(
    client.owner.check("manual", false),
    /command_identity_conflict/,
  );
});
test("missing/wrong authentication and browser origin never reach commands", async (t) => {
  const { transport, token, calls } = await fixture(t);
  for (const headers of [
    {},
    {
      Authorization: "Bearer " + "0".repeat(64),
      "X-Amplifier-Supervisor": "1",
    },
    {
      Authorization: "Bearer " + token,
      "X-Amplifier-Supervisor": "1",
      Origin: "http://127.0.0.1",
    },
  ]) {
    const response = await fetch(transport.url + "v1/rpc", {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ operation: "check", commandId: "bad" }),
    });
    assert.equal(response.status, 401);
  }
  assert.equal(calls(), 0);
});
test("unknown RPC, extra arguments and oversized messages are bounded", async (t) => {
  const { client, transport, token } = await fixture(t);
  await assert.rejects(client.rpc("execute-shell"), /unknown_operation/);
  await assert.rejects(
    client.rpc("check", { path: "anything" }, "bad"),
    /invalid_request/,
  );
  const response = await fetch(transport.url + "v1/rpc", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + token,
      "X-Amplifier-Supervisor": "1",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ padding: "x".repeat(20000) }),
  });
  assert.equal(response.status, 400);
});

for (const partial of [false, true])
  test(`a lost mutation response (partial=${partial}) remains inspectable without retries`, async (t) => {
    const { createServer } = await import("node:http");
    const { client, owner, transport, token, calls } = await fixture(t);
    let forwarded = 0;
    const proxy = createServer(async (req, res) => {
      let raw = "";
      for await (const chunk of req) raw += chunk;
      forwarded++;
      await fetch(transport.url + "v1/rpc", {
        method: "POST",
        headers: {
          Authorization: "Bearer " + token,
          "X-Amplifier-Supervisor": "1",
          "Content-Type": "application/json",
        },
        body: raw,
      });
      if (partial) {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.write('{"ok":');
        setTimeout(() => res.destroy(), 20);
      } else res.destroy();
    });
    await new Promise((resolve) => proxy.listen(0, "127.0.0.1", resolve));
    t.after(
      () =>
        new Promise((resolve) => {
          proxy.closeAllConnections();
          proxy.close(resolve);
        }),
    );
    const lost = new SupervisorClient({
      url: `http://127.0.0.1:${proxy.address().port}/`,
      token,
    });
    t.after(() => lost.close());
    await assert.rejects(lost.owner.check("lost-reply"), /outcome_unknown/);
    await owner.waitFor("lost-reply");
    assert.equal(
      (await client.owner.receipt("lost-reply")).status,
      "succeeded",
    );
    assert.equal(forwarded, 1);
    assert.equal(calls(), 1);
  });


test('observed status is additive over authenticated supervisor RPC and never substitutes for running proof',async t=>{
 let scope='fixture',calls=0;
 const expected={schema:'distribution-observed-status-v1',runtime:{schema:'distribution-runtime-observation-v1',binding:{identity:{id:'v1',version:'1.0.0',revision:'a'.repeat(40),digest:'a'.repeat(64)},instanceId:'observed',dataScope:scope},observedAt:1,readyObserved:true,integrity:{fresh:false,lastVerifiedAt:null,lastCheck:null}},quiescence:{enabled:true,intakeClosed:true}};
 const f=await fixture(t,{observeStatus:async()=>{calls++;return {...expected,runtime:{...expected.runtime,binding:{...expected.runtime.binding,dataScope:scope}}};}});
 assert.deepEqual(await f.client.owner.observeStatus(),expected);
 assert.equal(await f.client.owner.inspectRunning(),null);
 assert.equal(calls,1);
 scope='foreign';await assert.rejects(f.client.owner.observeStatus());
 const unsupported=await fixture(t);assert.equal(await unsupported.client.owner.observeStatus(),null);
 const response=await fetch(f.transport.url+'v1/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:'observed-status'})});
 assert.equal(response.status,401);assert.equal(calls,2);
});

test('service-proof uses authenticated owner proof rather than the public receipt projection',async t=>{
  const receipt={commandId:'initial',operation:'start',status:'ready'};
  const {client}=await fixture(t,{service:{receipt:()=>receipt,proof:()=>null}});
  assert.deepEqual(await client.service.receipt('initial'),receipt);
  assert.equal(await client.service.proof('initial'),null);
});
