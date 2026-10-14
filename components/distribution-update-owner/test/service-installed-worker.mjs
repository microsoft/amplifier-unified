import assert from "node:assert/strict";
import { readFile, writeFile, unlink, mkdir, realpath } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { randomBytes } from "node:crypto";
import {
  SignedReleaseAdapter,
  runSupervisor,
  connectHostControlFile,
  connectSupervisorFile,
  createOwnedServiceHandoffSource,
  launchExistingStateHandoff,
} from "@amplifier/unified-distribution-update-owner";
const c = JSON.parse(await readFile(process.argv[2], "utf8")),
  root = await realpath(c.root),
  scope = "fixture-scope";
const hostFile = join(root, "host.json"),
  hostTokenFile = join(root, "host-token"),
  superFile = join(root, "supervisor.json"),
  hostKey = randomBytes(32).toString("hex");
await writeFile(hostTokenFile, hostKey, { mode: 0o600 });
await writeFile(join(root, "busy"), "busy");
const host = connectHostControlFile(hostFile, scope);
const releaseOptions = {
  directory: join(root, "releases"),
  channelUrl: c.origin + "/channel.json",
  trustedKeys: c.keys,
  accessScope: "fixture",
  allowedArtifactOrigins: [c.origin],
  allowLoopbackHttp: true,
  launchEnv: {
    HOST_MODULE: pathToFileURL(
      join(c.consumer, "node_modules/@amplifier/unified-host/dist/index.js"),
    ).href,
    OWNER_MODULE: pathToFileURL(
      join(
        c.consumer,
        "node_modules/@amplifier/unified-distribution-update-owner/dist/index.js",
      ),
    ).href,
    TRUSTED_KEYS: JSON.stringify(c.keys),
    HOST_TOKEN: hostKey,
    HOST_TOKEN_FILE: hostTokenFile,
    HOST_CONNECTION: hostFile,
    SUPERVISOR_CONNECTION: superFile,
    PARTICIPANT_FILE: join(root, "held.json"),
    PARTICIPANT_LOG: join(root, "participant.jsonl"),
    BUSY_FILE: join(root, "busy"),
    HOST_STATE: join(root, "host-state"),
    TEST_ROOT: root,
  },
};
const resolveSources = async () =>
  await (await fetch(c.origin + "/sources")).json();
const adapter = new SignedReleaseAdapter({ ...releaseOptions, resolveSources }),
  initial = await adapter.prepare(c.first, {
    commandId: "initial",
    signal: new AbortController().signal,
  });
let claimed = false;
const config = {
  schema: "distribution-supervisor-v1",
  dataDirectory: join(root, "supervisor"),
  dataScope: scope,
  tokenFile: join(root, "super-token"),
  discoveryFile: superFile,
  initial,
  release: releaseOptions,
  serviceLifecycle: {
    installationId: "fixture-installation",
    ownerId: "fixture-owner",
  },
};
const ports = {
  resolveSources,
  inspect: () => host.inspect(),
  admitRestart: host.admitRestart,
  reconcileAdmission: host.reconcileAdmission,
  service: host.service,
  initialProvisioning: {
    claim: async (r) => {
      assert.equal(claimed, false);
      claimed = true;
      return {
        kind: "pristine-installation",
        installationId: "fixture-installation",
        commandId: r.commandId,
        instanceId: r.instanceId,
        dataScope: r.dataScope,
        targetDigest: r.target.identity.digest,
      };
    },
  },
};
let s = await runSupervisor(config, { startInitial: true, ports });
let client = await connectSupervisorFile(superFile);
const events = [];
let off = client.subscribe((e) => events.push(e));
try {
  const first = (await client.service.inspect()).identity;
  await client.service.stop({ commandId: "busy", expected: first });
  assert.equal((await s.service.waitFor("busy")).status, "refused");
  assert.equal(s.lifecycle.processes.inspect().state, "running");
  await assert.rejects(s.close(), /confirmed_service_stop_required/);
  await unlink(join(root, "busy"));
  await client.service.stop({ commandId: "stop", expected: first });
  const stopped = await s.service.waitFor("stop");
  assert.equal(stopped.status, "stopped");
  assert.equal(stopped.exitProof.ownerId, "fixture-owner");
  assert.equal(s.lifecycle.processes.inspect().state, "exited");
  assert.equal((await client.service.inspect()).state, "stopped");
  let resumed;
  const resumeId=c.handoff?'handoff':'resume';
  off();client.close();
  if(c.handoff){
    const old=s;
    const existing=join(root,'existing-application');await mkdir(existing);
    await writeFile(join(existing,'transcript.jsonl'),'saved history fixture\n');
    const launchConfig=join(root,'existing-paths.json');
    await writeFile(launchConfig,JSON.stringify({state:releaseOptions.launchEnv.HOST_STATE,history:existing,dataScope:scope}));
    const bindings=[{id:'application',path:existing,kind:'directory'},
      {id:'host-state',path:await realpath(releaseOptions.launchEnv.HOST_STATE),kind:'directory'},
      {id:'configuration',path:launchConfig,kind:'file'}];
    const source=createOwnedServiceHandoffSource({service:old.service,bindings});
    const destinationFile=join(root,'new-supervisor.json');
    s=await runSupervisor({...config,dataDirectory:join(root,'new-supervisor'),
      tokenFile:join(root,'new-super-token'),discoveryFile:destinationFile,
      release:{...releaseOptions,launchEnv:{...releaseOptions.launchEnv,SUPERVISOR_CONNECTION:destinationFile}}},
      {ports:{...ports,initialProvisioning:undefined}});
    assert.equal(s.lifecycle.ownedPid,null);client=await connectSupervisorFile(destinationFile);
    off=client.subscribe(e=>events.push(e));
    await launchExistingStateHandoff({destination:s.service,source,bindings,command:{commandId:resumeId,stoppedCommandId:'stop',
      expected:first,target:c.first,participantIds:['held-fixture','second-fixture']}});
    resumed=await s.service.waitFor(resumeId);
    assert.equal((await old.service.inspect()).state,'retired');assert.equal(old.service.blocksUpdates(),true);
    assert.equal(old.service.resume({commandId:'retired-resume',expected:first,stoppedCommandId:'stop'}).errorCode,'service_authority_retired');
    assert.equal(await readFile(join(existing,'transcript.jsonl'),'utf8'),'saved history fixture\n');
    assert.deepEqual(resumed.exitProof,stopped.exitProof);await old.close();
  }else{
    // Genuine stopped receipt from disk, no adoption or initial provisioning.
    await s.close();s=await runSupervisor(config,{ports});
    assert.equal((await s.service.inspect()).state,'stopped');assert.equal(s.lifecycle.ownedPid,null);
    client=await connectSupervisorFile(superFile);off=client.subscribe(e=>events.push(e));
    await client.service.resume({commandId:resumeId,expected:first,stoppedCommandId:'stop'});
    resumed=await s.service.waitFor(resumeId);
  }
  assert.equal(resumed.status, "ready");
  assert.equal(resumed.admissionSettlement.state, "settled");
  assert.notEqual(first.instanceId, resumed.observed.instanceId);
  assert.equal(first.releaseDigest, resumed.observed.releaseDigest);
  const actual = await host.inspect();
  assert.equal(actual.instanceId, resumed.observed.instanceId);
  assert.equal(
    (await host.service.inspectServiceLifecycle()).intakeClosed,
    false,
  );
  assert.equal(
    (await client.owner.inspect()).actionReadiness.state,
    "available",
  );
  assert.equal(
    (
      await client.service.adopt({
        commandId: "adopt",
        expected: resumed.observed,
      })
    ).status,
    "refused",
  );
  await new Promise((r) => setTimeout(r, 30));
  assert.ok(
    events.some(
      (e) =>
        e.serviceReceipt?.commandId === resumeId &&
        e.serviceReceipt.admissionSettlement?.state === "settled",
    ),
  );
  const logs = (await readFile(join(root, "participant.jsonl"), "utf8"))
    .trim()
    .split("\n")
    .map(JSON.parse);
  assert.equal(logs.filter((e) => e.event === "reconciled").length, 2);
  assert.equal(
    logs.find((e) => e.event === "reconciled").proof.kind,
    "service-lifecycle",
  );
  if(c.handoff){
    await writeFile(join(root,'busy'),'busy');
    await client.service.stop({commandId:'new-busy',expected:resumed.observed});
    assert.equal((await s.service.waitFor('new-busy')).status,'refused');await unlink(join(root,'busy'));
    await client.service.stop({commandId:'new-stop',expected:resumed.observed});
    assert.equal((await s.service.waitFor('new-stop')).status,'stopped');
    await client.service.resume({commandId:'new-resume',expected:resumed.observed,stoppedCommandId:'new-stop'});
    const again=await s.service.waitFor('new-resume');assert.equal(again.status,'ready');assert.equal(again.admissionSettlement.state,'settled');
    assert.notEqual(again.observed.instanceId,resumed.observed.instanceId);
  }
  await writeFile(
    join(root, "acceptance.json"),
    JSON.stringify(
      {
        schema: "distribution-service-installed-v1",
        node: process.version,
        platform: process.platform,
        independentInstalledOwnerAndHost: true,
        signedAppIdentity: true,
        explicitStopResume: true,
        serviceFenceReconciled: true,
        busyRefusal: true,
        actualExitProof: true,
        freshInstance: true,
        pushedServiceProgress: true,
        noAdoption: true,
        reopenedStoppedSupervisor: !c.handoff,
        existingStateHandoff: c.handoff,
        qualifiedFixtureOwners: ["held-fixture","second-fixture"],
        existingHistoryPreserved: !!c.handoff,
        sourceRetired: !!c.handoff,
        productionOwnerCoverage: false,
        managedSystemService: false,
      },
      null,
      2,
    ),
  );
} finally {
  off();
  client.close();
  const final = await s.stopService("cleanup");
  assert.equal(final.status, "stopped");
  await s.close();
  host.close();
}
