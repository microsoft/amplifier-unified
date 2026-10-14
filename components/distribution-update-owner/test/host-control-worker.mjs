import assert from "node:assert/strict";
import { readFile, writeFile, lstat } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { randomBytes } from "node:crypto";
import { once } from "node:events";
import {
  SignedReleaseAdapter,
  DistributionUpdateOwner,
  OwnedProcessLifecycle,
  serveSupervisor,
  SupervisorClient,
  connectHostControlFile,
} from "@amplifier/unified-distribution-update-owner";
const config = JSON.parse(await readFile(process.argv[2], "utf8"));
const until = async (predicate) => {
  for (let i = 0; i < 500; i++) {
    if (await predicate()) return;
    await new Promise((r) => setTimeout(r, 10));
  }
  throw Error("fixture_condition_timeout");
};
const root = config.root,
  hostFile = join(root, "host-connection.json"),
  hostKey = randomBytes(32).toString("hex"),
  superKey = randomBytes(32).toString("hex"),
  hostTokenFile = join(root, "host-token"),
  superFile = join(root, "supervisor.json");
await writeFile(hostTokenFile, hostKey, { mode: 0o600 });
await writeFile(join(root, "busy"), "busy");
const host = connectHostControlFile(hostFile, "fixture-scope");
const releases = new SignedReleaseAdapter({
  directory: join(root, "releases"),
  channelUrl: config.origin + "/channel.json",
  trustedKeys: config.keys,
  accessScope: "fixture",
  allowedArtifactOrigins: [config.origin],
  allowLoopbackHttp: true,
  resolveSources: async () =>
    await (await fetch(config.origin + "/sources")).json(),
  launchEnv: {
    HOST_MODULE: pathToFileURL(
      join(
        config.consumer,
        "node_modules/@amplifier/unified-host/dist/index.js",
      ),
    ).href,
    OWNER_MODULE: pathToFileURL(
      join(
        config.consumer,
        "node_modules/@amplifier/unified-distribution-update-owner/dist/index.js",
      ),
    ).href,
    TRUSTED_KEYS: JSON.stringify(config.keys),
    HOST_TOKEN: hostKey,
    HOST_TOKEN_FILE: hostTokenFile,
    HOST_CONNECTION: hostFile,
    SUPERVISOR_CONNECTION: superFile,
    PARTICIPANT_FILE: join(root, "participant.json"),
    PARTICIPANT_LOG: join(root, "participant.jsonl"),
    BUSY_FILE: join(root, "busy"),
    HOST_STATE: join(root, "host-state"),
    TEST_ROOT: root,
    DRIVER_CONNECTION: join(root, "driver.json"),
  },
});
const context = { commandId: "initial", signal: new AbortController().signal };
const initial = await releases.prepare(config.first, context);
// This fixture owns an empty bootstrap directory and its own children. Missing
// discovery before first launch is not used to adopt or kill a foreign service.
const lifecycle = new OwnedProcessLifecycle({
  resolve: (r) => releases.resolveLaunch(r),
  inspect: async () => {
    try {
      await lstat(hostFile);
    } catch (e) {
      if (e.code === "ENOENT") return null;
      throw e;
    }
    return host.inspect();
  },
  admitRestart: host.admitRestart,
  reconcileAdmission: host.reconcileAdmission,
});
let transport;
const owner = new DistributionUpdateOwner({
  directory: join(root, "owner"),
  dataScope: "fixture-scope",
  initial,
  releases,
  lifecycle,
  preferences: { autoCheck: false, autoInstall: false, intervalMs: 1000 },
  onChange: (r) => transport?.publish(r),
});
transport = await serveSupervisor({ owner, token: superKey });
await writeFile(
  superFile,
  JSON.stringify({ url: transport.url, token: superKey }),
  { mode: 0o600 },
);
const client = new SupervisorClient({ url: transport.url, token: superKey });
const idle = host.onIdle(() => owner.notifyIdle());
const driver = async (path) => {
  const d = JSON.parse(await readFile(join(root, "driver.json"), "utf8"));
  return (
    await fetch(`http://127.0.0.1:${d.port}` + path, {
      headers: { Authorization: "Bearer " + hostKey },
      signal: AbortSignal.timeout(3000),
    })
  ).json();
};
try {
  await lifecycle.startInitial(initial, "fixture-scope");
  const first = await host.inspect(),
    pid1 = lifecycle.ownedPid;
  assert.equal(first.identity.digest, config.first.digest);
  process.send({ advance: true });
  await once(process, "message");
  await client.owner.check("check");
  await owner.waitFor("check");
  await client.owner.install("install");
  await until(() => owner.receipt("install").phase === "waiting_idle");
  assert.equal(lifecycle.ownedPid, pid1);
  await driver("/idle");
  assert.equal((await owner.waitFor("install")).status, "succeeded");
  await until(async () => !(await host.inspectQuiescence()).intakeClosed);
  const second = await host.inspect(),
    pid2 = lifecycle.ownedPid;
  assert.notEqual(second.instanceId, first.instanceId);
  assert.notEqual(pid2, pid1);
  assert.equal(second.identity.digest, config.second.digest);
  assert.equal(second.dataScope, "fixture-scope");
  assert.equal(
    (await client.owner.restartProof("install")).admission.fenceId,
    (await host.quiescenceReceipt("install")).fenceId,
  );
  await client.owner.reconcile("install");
  assert.equal(lifecycle.ownedPid, pid2);
  await client.owner.rollback("rollback", config.second.id);
  assert.equal((await owner.waitFor("rollback")).status, "succeeded");
  await until(async () => !(await host.inspectQuiescence()).intakeClosed);
  const third = await host.inspect();
  assert.equal(third.identity.digest, config.first.digest);
  assert.notEqual(third.instanceId, second.instanceId);
  const events = (await readFile(join(root, "participant.jsonl"), "utf8"))
    .trim()
    .split("\n")
    .map(JSON.parse);
  assert.equal(events.filter((e) => e.event === "acquired").length, 2);
  assert.equal(events.filter((e) => e.event === "reconciled").length, 2);
  for (const e of events.filter((e) => e.event === "reconciled"))
    assert.equal(e.proof.verified, true);
  await writeFile(
    join(root, "acceptance.json"),
    JSON.stringify(
      {
        schema: "distribution-host-control-installed-v1",
        node: process.version,
        platform: process.platform,
        independentOwnerInstallation: true,
        independentRealHostInstallation: true,
        actualSeparateProcessReplacement: true,
        actualRollback: true,
        exactReleaseDigestAndDataScope: true,
        busyThenEventDrivenRetry: true,
        heldParticipantReconciled: true,
        runtimeIdentityHelperVerified: true,
        authenticatedSupervisorReceiptVerification: true,
        participantKind: "durable-held-fixture",
        productionOwnerCoverage: false,
        systemServiceAcceptance: false,
        releaseDigests: [first.identity.digest, second.identity.digest],
      },
      null,
      2,
    ),
  );
} finally {
  idle();
  host.close();
  client.close();
  await lifecycle.close();
  await transport.close();
  await owner.close();
}
