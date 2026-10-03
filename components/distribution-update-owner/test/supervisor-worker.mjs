// Run only from an independently installed consumer directory.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import { once } from "node:events";
import {
  SignedReleaseAdapter,
  connectSupervisorFile,
} from "@amplifier/unified-distribution-update-owner";
const execute = promisify(execFile),
  config = JSON.parse(await readFile(process.argv[2], "utf8"));
const module = import.meta.resolve(
  "@amplifier/unified-distribution-update-owner",
);
assert.match(
  module,
  /node_modules\/@amplifier\/unified-distribution-update-owner\/dist\/index.js$/,
);
const cli = fileURLToPath(new URL("supervisor-cli.js", module));
const releaseOptions = {
  directory: join(config.root, "releases"),
  channelUrl: config.origin + "/channel.json",
  trustedKeys: config.keys,
  accessScope: "fixture",
  allowedArtifactOrigins: [config.origin],
  allowLoopbackHttp: true,
  launchEnv: {
    OWNER_MODULE: module,
    TRUSTED_KEYS: JSON.stringify(config.keys),
    ENDPOINT_FILE: config.endpoint,
    FENCE_FILE: config.fence,
    READINESS_SECRET: config.secret,
    SUPERVISOR_DISCOVERY: join(config.root, "connection.json"),
  },
};
const releases = new SignedReleaseAdapter({
  ...releaseOptions,
  resolveSources: async () =>
    await (await fetch(config.origin + "/sources")).json(),
});
const context = {
  commandId: "bootstrap",
  signal: new AbortController().signal,
};
const initial = await releases.prepare(config.first, context);
const daemonConfig = {
  schema: "distribution-supervisor-v1",
  dataDirectory: join(config.root, "supervisor"),
  dataScope: "fixture",
  tokenFile: join(config.root, "token"),
  discoveryFile: join(config.root, "connection.json"),
  adapterModule: join(config.consumer, "ports.mjs"),
  adapterConfig: {
    endpoint: config.endpoint,
    secret: config.secret,
    origin: config.origin,
  },
  release: releaseOptions,
  initial,
};
await writeFile(
  join(config.root, "daemon.json"),
  JSON.stringify(daemonConfig),
  { mode: 0o600 },
);
const daemon = spawn(
  process.execPath,
  [
    cli,
    "serve",
    "--config",
    join(config.root, "daemon.json"),
    "--start-initial",
  ],
  { stdio: ["ignore", "pipe", "pipe"] },
);
let client,
  stderr = "";
daemon.stderr.on("data", (chunk) => (stderr += chunk.toString()));
const app = async (path, value) => {
  const endpoint = JSON.parse(await readFile(config.endpoint, "utf8"));
  const result = await fetch(`http://127.0.0.1:${endpoint.port}${path}`, {
    method: value ? "POST" : "GET",
    headers: {
      Authorization: "Bearer " + config.secret,
      "Content-Type": "application/json",
    },
    body: value ? JSON.stringify(value) : undefined,
    signal: AbortSignal.timeout(5000),
  });
  return result.json();
};
try {
  await Promise.race([
    once(daemon.stdout, "data"),
    once(daemon, "exit").then(() => {
      throw Error("supervisor exited: " + stderr);
    }),
  ]);
  client = await connectSupervisorFile(daemonConfig.discoveryFile);
  const first = await app("/ready");
  assert.equal(first.identity.id, config.first.id);
  const childStartup = await app("/supervisor-observed");
  assert.equal(childStartup.constructedWithoutDiscovery, true);
  assert.equal(childStartup.readUnavailable, true);
  assert.equal(childStartup.mutationUnavailable, true);
  assert.equal(await client.owner.receipt("pre-provisioning"), null);
  // A first observation connection sends a reset, not historical events. Wait
  // for the child's initial subscription before testing a subsequent push;
  // otherwise a fast check can finish before discovery's coalesced wakeup.
  let childConnected = false;
  for (let attempt = 0; attempt < 200; attempt++) {
    const seen = await app("/supervisor-observed");
    if (seen.events.some((event) => event.reset)) {
      childConnected = true;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.equal(childConnected, true);
  const notifications = [];
  await new Promise((resolve) =>
    client.subscribe((event) => {
      notifications.push(event);
      if (event.reset) resolve();
    }),
  );
  const advanced = once(process, "message");
  process.send({ advance: true });
  await advanced;
  const waitReceipt = (id) =>
    new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        stop();
        reject(Error("fixture receipt deadline"));
      }, 10000);
      const done = (receipt) => {
        if (
          receipt?.id === id &&
          ["succeeded", "failed", "unknown"].includes(receipt.status)
        ) {
          clearTimeout(timer);
          stop();
          resolve(receipt);
        }
      };
      const stop = client.subscribe((event) => done(event.receipt));
      client.owner.receipt(id).then(done, reject);
    });
  // Call the separately installed CLI as well as the public client API.
  const checked = waitReceipt("manual-check");
  await execute(process.execPath, [
    cli,
    "call",
    "--connection",
    daemonConfig.discoveryFile,
    "--operation",
    "check",
    "--command-id",
    "manual-check",
  ]);
  assert.equal((await checked).status, "succeeded");
  let childReceived = false;
  for (let attempt = 0; attempt < 200; attempt++) {
    const seen = await app("/supervisor-observed");
    if (
      seen.events.some(
        (e) =>
          e.receipt?.id === "manual-check" && e.receipt.status === "succeeded",
      )
    ) {
      childReceived = true;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.equal(childReceived, true);
  const installed = waitReceipt("install");
  await client.owner.install("install");
  assert.equal((await installed).status, "succeeded", stderr);
  const second = await app("/ready");
  assert.equal(second.identity.id, config.second.id);
  assert.notEqual(first.pid, second.pid);
  assert.notEqual(first.instanceId, second.instanceId);
  assert.equal(second.dataScope, "fixture");
  assert.equal(
    (await client.owner.inspect()).current.digest,
    config.second.digest,
  );
  const rolled = waitReceipt("rollback");
  await client.owner.rollback("rollback", config.second.id);
  assert.equal((await rolled).status, "succeeded");
  const third = await app("/ready");
  assert.equal(third.identity.id, config.first.id);
  assert.notEqual(second.pid, third.pid);
  await client.owner.rollback("rollback", config.second.id);
  await client.owner.reconcile("rollback");
  assert.equal((await app("/ready")).pid, third.pid);
  const gate = JSON.parse(await readFile(config.fence, "utf8"));
  assert.equal(gate.status, "released");
  assert.equal(gate.commandId, "rollback");
  assert.ok(
    notifications.some(
      (n) => n.receipt?.id === "install" && n.receipt.phase === "ready",
    ),
  );
  const receipt = {
    schema: "distribution-supervisor-installed-acceptance-v1",
    runtimeIdentityHelperVerified: true,
    childConstructedBeforeSupervisorDiscovery: true,
    childReceivedSupervisorNotifications: true,
    preProvisioningMutationWasNotQueued: true,
    packageVersion: JSON.parse(
      await readFile(
        new URL(
          "../package.json",
          import.meta.resolve("@amplifier/unified-distribution-update-owner"),
        ),
        "utf8",
      ),
    ).version,
    independentInstallation: true,
    separateSupervisorProcess: true,
    separateClientProcess: true,
    installedCliCommand: true,
    authenticatedTransport: true,
    eventNotifications: true,
    signedReleaseChannel: true,
    exactArchiveAndComponents: true,
    latestSourceInventory: true,
    actualProcessPromotion: true,
    exactDigestAndDataScope: true,
    actualRollback: true,
    idempotentCommandAndReconciliation: true,
    hostQuiescencePort: "fixture-public-port",
    productionHostQuiescenceAcceptance: false,
    systemServiceAcceptance: false,
    productionAcceptance: false,
    node: process.version,
    platform: process.platform,
  };
  await writeFile(config.receipt, JSON.stringify(receipt, null, 2) + "\n");
  console.log(JSON.stringify(receipt));
} finally {
  client?.close();
  await app("/stop", {}).catch(() => {});
  if (daemon.exitCode === null && daemon.signalCode === null) {
    daemon.kill("SIGTERM");
    // This legacy fixture deliberately has no qualified service owner. A signal
    // must be refused even after its fixture app endpoint has been stopped;
    // endpoint absence is not production lifecycle authority. Reap only this
    // test's retained supervisor child after asserting the refusal.
    for (
      let attempt = 0;
      attempt < 200 && !stderr.includes("service_lifecycle_not_configured");
      attempt++
    )
      await new Promise((resolve) => setTimeout(resolve, 10));
    assert.match(stderr, /service_lifecycle_not_configured/);
    daemon.kill("SIGKILL");
    await once(daemon, "exit");
  }
}
