import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile, readFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import {
  createPristineInstallation,
  createProductionSupervisorPorts,
  OwnedProcessLifecycle,
} from "../dist/index.js";
const initial = {
  id: "v1",
  version: "1.0.0",
  revision: "1".repeat(40),
  digest: "a".repeat(64),
};
const target = { identity: initial, handle: "release:" + initial.digest };
async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "production-ports-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const paths = await createPristineInstallation({
    directory: join(root, "installation"),
    dataScope: "fixture",
    initial,
  });
  const config = {
    ...paths,
    provisioningAuthorityFile: paths.authorityFile,
    resolveSources: async () => [],
  };
  const ports = createProductionSupervisorPorts(config);
  t.after(() => ports.close());
  return { root, paths, ports, config };
}
const request = (id = "initial") => ({
  commandId: id,
  instanceId: "instance-" + id,
  dataScope: "fixture",
  previousInstanceId: null,
  target,
  signal: new AbortController().signal,
});
test("new namespace authority is explicit, private and consumed once; ordinary inspection stays strict", async (t) => {
  const { paths, ports } = await fixture(t);
  await assert.rejects(
    createPristineInstallation({
      directory: paths.directory,
      dataScope: "fixture",
      initial,
    }),
    { code: "EEXIST" },
  );
  await assert.rejects(ports.inspect(), /host_control_unavailable/);
  const proof = await ports.initialProvisioning.claim(request());
  assert.equal(proof.targetDigest, initial.digest);
  assert.equal(
    JSON.parse(
      await readFile(
        join(paths.directory, "initial-provisioning.claim"),
        "utf8",
      ),
    ).instanceId,
    "instance-initial",
  );
  await assert.rejects(ports.initialProvisioning.claim(request("again")), {
    code: "EEXIST",
  });
  await assert.rejects(ports.inspect(), /host_control_unavailable/);
});
test("scope, target, namespace and existing application or discovery refuse first launch", async (t) => {
  const { paths, ports, config } = await fixture(t);
  for (const patch of [
    { dataScope: "other" },
    { previousInstanceId: "old" },
    {
      target: {
        identity: { ...initial, digest: "b".repeat(64) },
        handle: "release:" + "b".repeat(64),
      },
    },
  ])
    await assert.rejects(
      ports.initialProvisioning.claim({ ...request(), ...patch }),
      /initial_authority_mismatch/,
    );
  const wrong = createProductionSupervisorPorts({
    ...config,
    dataDirectory: join(paths.directory, "wrong"),
  });
  t.after(() => wrong.close());
  await assert.rejects(
    wrong.initialProvisioning.claim(request()),
    /initial_authority_mismatch/,
  );
  await mkdir(paths.applicationStateDirectory);
  await assert.rejects(
    ports.initialProvisioning.claim(request()),
    /not_pristine/,
  );
  await rm(paths.applicationStateDirectory, { recursive: true });
  await writeFile(paths.hostDiscoveryFile, "{}");
  await assert.rejects(
    ports.initialProvisioning.claim(request()),
    /not_pristine/,
  );
});
test("partial durable claim and cancelled claim never silently reauthorize", async (t) => {
  const { paths, ports } = await fixture(t),
    controller = new AbortController();
  controller.abort();
  await assert.rejects(
    ports.initialProvisioning.claim({
      ...request(),
      signal: controller.signal,
    }),
    { name: "AbortError" },
  );
  await writeFile(join(paths.directory, "initial-provisioning.claim"), "");
  await assert.rejects(ports.initialProvisioning.claim(request()), {
    code: "EEXIST",
  });
});
test("independent processes race for exactly one initial claim", async (t) => {
  const { paths, config } = await fixture(t),
    module = new URL("../dist/index.js", import.meta.url).href;
  const script = `import {createProductionSupervisorPorts} from ${JSON.stringify(module)};const config=JSON.parse(process.argv[1]);const ports=createProductionSupervisorPorts({...config,resolveSources:async()=>[]});try{await ports.initialProvisioning.claim({...${JSON.stringify({ ...request(), signal: undefined })},signal:new AbortController().signal});process.stdout.write('claimed');}catch{process.stdout.write('refused');}finally{ports.close();}`;
  // Signal is constructed in the child; only inert request data crosses argv.
  const childScript = script;
  const run = () =>
    new Promise((resolve, reject) => {
      const child = spawn(
        process.execPath,
        ["--input-type=module", "-e", childScript, JSON.stringify(config)],
        { stdio: ["ignore", "pipe", "pipe"] },
      );
      let out = "";
      child.stdout.on("data", (b) => (out += b));
      child.once("error", reject);
      child.once("exit", (code) =>
        code === 0 ? resolve(out) : reject(Error("child_failed")),
      );
    });
  assert.deepEqual((await Promise.all([run(), run()])).sort(), [
    "claimed",
    "refused",
  ]);
  assert.ok(
    JSON.parse(
      await readFile(
        join(paths.directory, "initial-provisioning.claim"),
        "utf8",
      ),
    ).commandId,
  );
});
test("uncertain initial spawn consumes authority and missing-host ordinary restart never uses it", async (t) => {
  const { paths, ports, config } = await fixture(t);
  let resolved = 0;
  const make = (p) =>
    new OwnedProcessLifecycle({
      resolve: async () => {
        resolved++;
        return {
          command: process.execPath,
          args: ["-e", "setInterval(()=>{},1000)"],
        };
      },
      ...p,
      readinessMs: 100,
    });
  const lifecycle = make(ports);
  t.after(() => lifecycle.close());
  await assert.rejects(
    lifecycle.restart(request("ordinary")),
    /host_control_unavailable/,
  );
  await assert.rejects(
    readFile(join(paths.directory, "initial-provisioning.claim")),
    { code: "ENOENT" },
  );
  await assert.rejects(
    lifecycle.startInitial(target, "fixture"),
    /readiness_unconfirmed/,
  );
  assert.ok(lifecycle.ownedPid);
  await assert.rejects(
    lifecycle.startInitial(target, "fixture"),
    /owned_process_exists/,
  );
  await lifecycle.close();
  const reopened = createProductionSupervisorPorts(config),
    later = make(reopened);
  t.after(() => {
    reopened.close();
    return later.close();
  });
  await assert.rejects(later.startInitial(target, "fixture"), {
    code: "EEXIST",
  });
  assert.equal(later.ownedPid, null);
  assert.equal(resolved, 3);
});
test("failed executable launch remains claimed; source resolution is required and forwarded", async (t) => {
  const { paths, config } = await fixture(t);
  assert.throws(
    () =>
      createProductionSupervisorPorts({ ...config, resolveSources: undefined }),
    /source_resolver_required/,
  );
  let passed;
  const ports = createProductionSupervisorPorts({
    ...config,
    resolveSources: async (...args) => {
      passed = args;
      return [];
    },
  });
  t.after(() => ports.close());
  const components = [
      { repository: "https://fixture.invalid/component.git", ref: "main" },
    ],
    context = {
      commandId: "source-check",
      signal: new AbortController().signal,
    };
  await ports.resolveSources(components, context);
  assert.equal(passed[0], components);
  assert.equal(passed[1], context);
  const lifecycle = new OwnedProcessLifecycle({
    resolve: async () => ({
      command: join(paths.directory, "does-not-exist"),
      args: [],
    }),
    ...ports,
  });
  t.after(() => lifecycle.close());
  await assert.rejects(
    lifecycle.startInitial(target, "fixture"),
    /launch_failed/,
  );
  await assert.rejects(lifecycle.startInitial(target, "fixture"), {
    code: "EEXIST",
  });
});

test("production inspection refuses even an authenticated empty running response", async (t) => {
  const { paths, ports } = await fixture(t);
  const server = createServer((_req, res) => {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ ok: true, result: null }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(
    () =>
      new Promise((resolve) => {
        server.closeAllConnections();
        server.close(resolve);
      }),
  );
  await writeFile(paths.hostTokenFile, "a".repeat(64), { mode: 0o600 });
  await writeFile(
    paths.hostDiscoveryFile,
    JSON.stringify({
      schema: "distribution-host-control-v1",
      url: `http://127.0.0.1:${server.address().port}/`,
      tokenFile: paths.hostTokenFile,
      dataScope: "fixture",
    }),
    { mode: 0o600 },
  );
  await assert.rejects(ports.inspect(), /host_control_unavailable/);
});
