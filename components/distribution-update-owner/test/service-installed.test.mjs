import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  writeFile,
  readFile,
  copyFile,
  rm,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname, resolve, basename } from "node:path";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createHash } from "node:crypto";
import { publisher, artifact } from "./release-fixtures.mjs";
const execute = promisify(execFile),
  packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const hostArchive = process.env.DISTRIBUTION_SERVICE_HOST_ARCHIVE;
const hostCommit = "30ecbb83311bacc7eabe9a94b44046367213638d";
const hostSha256 =
  "fac1d4c986eaad85605115cfaa520caab65d2046205594870b0b4cd03b866d17";
const appCode = `import {readFile,writeFile,unlink,appendFile} from 'node:fs/promises';
import {writeFileSync} from 'node:fs';
process.on('uncaughtException',e=>{writeFileSync(process.env.TEST_ROOT+'/child-error',String(e.stack??e));process.exit(1)});
const {createHost}=await import(process.env.HOST_MODULE);
const {createRuntimeIdentity,connectSupervisorFileLazy,serveHostControl,createHostServiceReleaseVerifier,createHostReleaseVerifier}=await import(process.env.OWNER_MODULE);
let initialized=false;
const actual=await createRuntimeIdentity({entrypointUrl:import.meta.url,trustedKeys:JSON.parse(process.env.TRUSTED_KEYS),isReady:()=>initialized});
const supervisor=connectSupervisorFileLazy(process.env.SUPERVISOR_CONNECTION);
const serviceIdentity={installationId:process.env.AMPLIFIER_DISTRIBUTION_INSTALLATION_ID,ownerId:process.env.AMPLIFIER_DISTRIBUTION_OWNER_ID,instanceId:actual.instanceId,dataScope:actual.dataScope,releaseDigest:actual.identity.digest};
const log=e=>appendFile(process.env.PARTICIPANT_LOG,JSON.stringify(e)+'\\n');
const makeParticipant=id=>({id,serviceStop:{version:1},acquire:async c=>{
 try{await readFile(process.env.BUSY_FILE);return null;}catch(e){if(e.code!=='ENOENT')throw e;}
 await writeFile(process.env.PARTICIPANT_FILE+'.'+id,JSON.stringify(c),{mode:0o600});await log({event:'acquired',ownerId:id,purpose:c.purpose});
 return {ownerId:id,fenceId:c.fenceId,release:async(outcome,proof)=>{if(outcome==='unknown')return;if(proof?.kind==='admission-refused'){await unlink(process.env.PARTICIPANT_FILE+'.'+id);return;}throw Error('replacement_must_reconcile');}};
},reconcileRelease:async r=>{const held=JSON.parse(await readFile(process.env.PARTICIPANT_FILE+'.'+id,'utf8'));if(held.fenceId!==r.fenceId||held.commandId!==r.commandId||r.proof.verified!==true||r.proof.kind!=='service-lifecycle')throw Error('fixture_proof_invalid');await unlink(process.env.PARTICIPANT_FILE+'.'+id);await log({event:'reconciled',ownerId:id,proof:r.proof});}});
const participants=['held-fixture','second-fixture'].map(makeParticipant);
const host=await createHost({stateDirectory:process.env.HOST_STATE,allowedWorkspaceRoots:[process.env.TEST_ROOT],engines:[{id:'unused-fixture',command:process.execPath,args:['-e','process.exit(2)']}],quiescence:{instanceId:actual.instanceId,dataScope:actual.dataScope,requiredOwners:participants.map(p=>p.id),coverage:{},participants,verifyRelease:createHostReleaseVerifier({supervisor:supervisor.owner,inspectRunning:actual.inspectRunning}),serviceLifecycle:{identity:serviceIdentity,verifyRelease:createHostServiceReleaseVerifier({service:supervisor.service,inspectRunningService:()=>serviceIdentity})}}});
initialized=true;
const control=await serveHostControl({host,inspectRunning:actual.inspectRunning,token:process.env.HOST_TOKEN,discovery:{file:process.env.HOST_CONNECTION,tokenFile:process.env.HOST_TOKEN_FILE,dataScope:actual.dataScope}});
let closing=false;process.on('SIGTERM',async()=>{if(closing)return;closing=true;await control.close();await host.close();supervisor.close();process.exit(0);});
`;
for (const handoff of [false,true]) test(
  handoff ? "installed existing-state handoff preserves data and qualifies two real host owners" : "installed POSIX supervisor stops and resumes signed real host through persistent service fence",
  { skip: !hostArchive },
  async (t) => {
    assert.equal(
      createHash("sha256")
        .update(await readFile(hostArchive))
        .digest("hex"),
      hostSha256,
    );
    const root = await mkdtemp(join(tmpdir(), "installed-service-owner-")),
      pub = await publisher();
    t.after(async () => {
      await pub.close();
      await rm(root, { recursive: true, force: true });
    });
    const first = await artifact(root, 1, pub.origin, appCode);
    pub.publish([first], 1);
    const suppliedOwner = process.env.DISTRIBUTION_SERVICE_OWNER_ARCHIVE;
    const packed = suppliedOwner ? {filename:basename(suppliedOwner)} : JSON.parse(
      (
        await execute(
          "npm",
          ["pack", "--ignore-scripts", "--json", "--pack-destination", root],
          { cwd: packageRoot },
        )
      ).stdout,
    )[0];
    if (suppliedOwner) await copyFile(suppliedOwner,join(root,packed.filename));
    const consumer = join(root, "consumer");
    await mkdir(consumer);
    await writeFile(
      join(consumer, "package.json"),
      JSON.stringify({
        name: "service-consumer",
        private: true,
        type: "module",
      }),
    );
    await execute(
      "npm",
      [
        "install",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "--omit=dev",
        join(root, packed.filename),
        hostArchive,
      ],
      { cwd: consumer },
    );
    await copyFile(
      join(packageRoot, "test/service-installed-worker.mjs"),
      join(consumer, "worker.mjs"),
    );
    await writeFile(
      join(root, "config.json"),
      JSON.stringify({
        root,
        handoff,
        consumer,
        origin: pub.origin,
        keys: pub.keys,
        first: first.release.identity,
      }),
      { mode: 0o600 },
    );
    let result, error;
    try {
      result = await execute(
        process.execPath,
        [join(consumer, "worker.mjs"), join(root, "config.json")],
        { cwd: consumer, timeout: 30000 },
      );
    } catch (e) {
      error = e;
    }
    const childError = await readFile(join(root, "child-error"), "utf8").catch(
      () => "",
    );
    assert.equal(error, undefined, (error?.stderr ?? "") + childError);
    const receipt = JSON.parse(
      await readFile(join(root, "acceptance.json"), "utf8"),
    );
    assert.equal(receipt.explicitStopResume, true);
    if (handoff) assert.equal(receipt.existingStateHandoff,true);
    assert.equal(receipt.serviceFenceReconciled, true);
    const retained = process.env.DISTRIBUTION_SERVICE_ACCEPTANCE_DIR;
    if (retained) {
      await mkdir(retained, { recursive: true });
      await copyFile(
        join(root, packed.filename),
        join(retained, packed.filename),
      );
      await writeFile(
        join(retained, handoff ? "handoff-acceptance.json" : "service-acceptance.json"),
        JSON.stringify(
          {
            ...receipt,
            hostCommit,
            hostArchiveSha256: hostSha256,
            artifactSha256: createHash("sha256")
              .update(await readFile(join(root, packed.filename)))
              .digest("hex"),
          },
          null,
          2,
        ) + "\n",
      );
    }
  },
);
