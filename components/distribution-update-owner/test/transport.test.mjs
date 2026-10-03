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
async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), "supervisor-transport-"));
  let transport,
    calls = 0;
  const owner = new DistributionUpdateOwner({
    directory,
    dataScope: "fixture",
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
  transport = await serveSupervisor({ owner, token });
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
  const reset = new Promise((resolve) =>
    client.subscribe((event) => {
      received.push(event);
      if (event.reset) resolve();
    }),
  );
  await reset;
  const accepted = await client.owner.check("manual");
  assert.ok(["queued", "running", "succeeded"].includes(accepted.status));
  await owner.waitFor("manual");
  await new Promise((resolve) => setTimeout(resolve, 20));
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
