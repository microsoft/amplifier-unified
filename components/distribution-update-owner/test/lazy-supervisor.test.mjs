import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtemp,
  rm,
  mkdir,
  writeFile,
  rename,
  unlink,
  chmod,
  symlink,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { randomBytes } from "node:crypto";
import { createServer } from "node:http";
import {
  DistributionUpdateOwner,
  serveSupervisor,
  connectSupervisorFileLazy,
} from "../dist/index.js";
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(predicate) {
  for (let n = 0; n < 200; n++) {
    if (await predicate()) return;
    await sleep(10);
  }
  throw Error("fixture_condition_timeout");
}
async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "lazy-supervisor-")),
    file = join(root, "pending", "discovery.json");
  const client = connectSupervisorFileLazy(file),
    servers = [];
  t.after(async () => {
    client.close();
    for (const s of servers) {
      await s.transport.close();
      await s.owner.close();
    }
    await rm(root, { recursive: true, force: true });
  });
  const server = async (name) => {
    let calls = 0,
      transport;
    const key = randomBytes(32).toString("hex");
    const owner = new DistributionUpdateOwner({
      directory: join(root, name),
      dataScope: "fixture",
      preferences: { autoCheck: false, autoInstall: false, intervalMs: 1000 },
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
      onChange: (r) => transport?.publish(r),
    });
    transport = await serveSupervisor({ owner, token: key });
    const entry = { owner, transport, key, calls: () => calls };
    servers.push(entry);
    return entry;
  };
  const publish = async (s, url = s.transport.url) => {
    await mkdir(dirname(file), { recursive: true, mode: 0o700 });
    const keyFile = join(root, "key-" + s.key.slice(0, 8));
    await writeFile(keyFile, s.key, { mode: 0o600 });
    await writeFile(
      file + ".next",
      JSON.stringify({
        schema: "distribution-supervisor-connection-v1",
        url,
        tokenFile: keyFile,
      }),
      { mode: 0o600 },
    );
    await rename(file + ".next", file);
  };
  return { root, file, client, server, publish };
}
test("lazy supervisor is passive before discovery and receives pushed events after provisioning", async (t) => {
  const { root, file, client, server, publish } = await fixture(t),
    events = [];
  assert.equal(client.outlivesDistribution, true);
  const stop = client.subscribe((e) => events.push(e));
  await assert.rejects(client.owner.inspect(), /supervisor_unreachable/);
  await assert.rejects(client.owner.install("not-accepted"), /outcome_unknown/);
  const s = await server("one");
  await publish(s);
  await until(() => events.some((e) => e.reset));
  assert.equal(s.owner.receipt("not-accepted"), null);
  await client.owner.check("manual");
  await s.owner.waitFor("manual");
  await until(() =>
    events.some(
      (e) => e.receipt?.id === "manual" && e.receipt.status === "succeeded",
    ),
  );
  assert.equal(s.calls(), 1);
  const count = events.length;
  stop();
  s.owner.check("after-unsubscribe");
  await s.owner.waitFor("after-unsubscribe");
  await writeFile(join(root, "unrelated"), "unrelated");
  await sleep(50);
  assert.equal(events.length, count);
  await unlink(file);
  await assert.rejects(client.owner.inspect(), /supervisor_unreachable/);
});
test("lazy supervisor follows atomic endpoint and credential replacement while the previous stream is alive", async (t) => {
  const { client, server, publish } = await fixture(t),
    events = [];
  client.subscribe((e) => events.push(e));
  const first = await server("one");
  await publish(first);
  await until(() => events.some((e) => e.reset));
  const epoch = events.find((e) => e.reset).cursor.split(":")[0];
  const second = await server("two");
  await publish(second);
  await until(() =>
    events.some((e) => e.reset && e.cursor.split(":")[0] !== epoch),
  );
  await client.owner.check("replacement");
  await second.owner.waitFor("replacement");
  await until(() =>
    events.some(
      (e) =>
        e.receipt?.id === "replacement" && e.receipt.status === "succeeded",
    ),
  );
  assert.equal(first.calls(), 0);
  assert.equal(second.calls(), 1);
  const count = events.length;
  first.owner.check("old-stream");
  await first.owner.waitFor("old-stream");
  await sleep(50);
  assert.equal(events.length, count);
});
test("lazy discovery remains private and never retries a lost mutation when discovery changes", async (t) => {
  const { root, file, client, server, publish } = await fixture(t),
    s = await server("one");
  let forwarded = 0;
  const proxy = createServer(async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    forwarded++;
    await fetch(s.transport.url + "v1/rpc", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + s.key,
        "X-Amplifier-Supervisor": "1",
        "Content-Type": "application/json",
      },
      body,
    });
    res.writeHead(200, { "Content-Type": "application/json" });
    res.write('{"ok":');
    setTimeout(() => res.destroy(), 20);
  });
  await new Promise((resolve) => proxy.listen(0, "127.0.0.1", resolve));
  t.after(
    () =>
      new Promise((resolve) => {
        proxy.closeAllConnections();
        proxy.close(resolve);
      }),
  );
  await publish(s, `http://127.0.0.1:${proxy.address().port}/`);
  await assert.rejects(client.owner.check("lost"), /outcome_unknown/);
  await s.owner.waitFor("lost");
  await publish(s);
  assert.equal((await client.owner.receipt("lost")).status, "succeeded");
  await sleep(50);
  assert.equal(forwarded, 1);
  assert.equal(s.calls(), 1);
  if (process.platform !== "win32") {
    await chmod(file, 0o644);
    await assert.rejects(client.owner.inspect(), /unreachable/);
    await chmod(file, 0o600);
  }
  await rename(file, file + ".saved");
  await symlink(file + ".saved", file);
  await assert.rejects(client.owner.inspect(), /unreachable/);
  await unlink(file);
  await rename(file + ".saved", file);
  await writeFile(
    file,
    JSON.stringify({
      schema: "wrong",
      url: s.transport.url,
      tokenFile: join(root, "missing"),
    }),
  );
  await assert.rejects(client.owner.inspect(), /unreachable/);
  assert.equal(s.calls(), 1);
});
