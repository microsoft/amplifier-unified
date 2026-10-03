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
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { createHash } from "node:crypto";
import { publisher, artifact } from "./release-fixtures.mjs";
const execute = promisify(execFile),
  packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const hostArchive = process.env.DISTRIBUTION_HOST_ARCHIVE;
const hostCommit = "fc28680e55d63f911df51277506ecd3f03bf508c";
const hostSha256 =
  "d31ddfc7c203a369c76164f3e429be3aa75d93f7aee86f01f2cc24911cfd92f1";
const appCode = `import {readFile,writeFile,unlink,appendFile} from 'node:fs/promises';
import {writeFileSync} from 'node:fs';
process.on('uncaughtException',error=>{writeFileSync(process.env.TEST_ROOT+'/child-error.txt',String(error.stack??error));process.exit(1)});
import {createServer} from 'node:http';
import component from 'fixture-component';
const {createHost}=await import(process.env.HOST_MODULE);
const {createRuntimeIdentity,SupervisorClient,serveHostControl,createHostReleaseVerifier}=await import(process.env.OWNER_MODULE);
let initialized=false;
const actual=await createRuntimeIdentity({entrypointUrl:import.meta.url,trustedKeys:JSON.parse(process.env.TRUSTED_KEYS),isReady:()=>initialized});
if(component!=='installed-component')throw Error('installed_graph_invalid');
const supervisor=new SupervisorClient(JSON.parse(await readFile(process.env.SUPERVISOR_CONNECTION,'utf8')));
const heldPath=process.env.PARTICIPANT_FILE;
const log=event=>appendFile(process.env.PARTICIPANT_LOG,JSON.stringify(event)+'\\n');
let control;
const participant={id:'held-fixture',acquire:async context=>{try{await readFile(process.env.BUSY_FILE);return null;}catch(error){if(error.code!=='ENOENT')throw error;}
 await writeFile(heldPath,JSON.stringify(context),{mode:0o600});await log({event:'acquired',...context});
 return {ownerId:'held-fixture',fenceId:context.fenceId,release:async(outcome,proof)=>{if(outcome==='unknown')return;const held=JSON.parse(await readFile(heldPath,'utf8'));if(held.fenceId!==context.fenceId||proof.verified!==true)throw Error('fixture_release_invalid');await unlink(heldPath);await log({event:'released',outcome,proof});}};},
 reconcileRelease:async request=>{const held=JSON.parse(await readFile(heldPath,'utf8'));if(held.fenceId!==request.fenceId||held.commandId!==request.commandId||request.proof.verified!==true)throw Error('fixture_reconcile_invalid');await unlink(heldPath);await log({event:'reconciled',outcome:request.outcome,proof:request.proof});}};
const host=await createHost({stateDirectory:process.env.HOST_STATE,allowedWorkspaceRoots:[process.env.TEST_ROOT],engines:[{id:'unused-fixture',command:process.execPath,args:['-e','process.exit(2)']}],quiescence:{instanceId:actual.instanceId,dataScope:actual.dataScope,requiredOwners:['held-fixture'],coverage:{},participants:[participant],verifyRelease:createHostReleaseVerifier({supervisor:supervisor.owner,inspectRunning:actual.inspectRunning}),onMayBeIdle:()=>control?.notifyMayBeIdle()}});
initialized=true;
control=await serveHostControl({host,inspectRunning:actual.inspectRunning,token:process.env.HOST_TOKEN,discovery:{file:process.env.HOST_CONNECTION,tokenFile:process.env.HOST_TOKEN_FILE,dataScope:actual.dataScope}});
const driver=createServer(async(req,res)=>{if(req.headers.authorization!=='Bearer '+process.env.HOST_TOKEN){res.writeHead(403);res.end();return;}
 if(req.url==='/idle'){await unlink(process.env.BUSY_FILE);control.notifyMayBeIdle();res.end('{}');return;}
 if(req.url==='/state'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({pid:process.pid,...host.inspectQuiescence()}));return;}
 if(req.url==='/stop'){res.end('{}');await stop();return;}
 res.writeHead(404);res.end();});
let stopped=false;async function stop(){if(stopped)return;stopped=true;driver.closeAllConnections();driver.close();await control.close();await host.close();supervisor.close();process.exit(0);}
process.on('SIGTERM',()=>void stop());
driver.listen(0,'127.0.0.1',()=>writeFile(process.env.DRIVER_CONNECTION,JSON.stringify({port:driver.address().port}),{mode:0o600}));
`;
test(
  "independently installed real host controls signed process replacement and reconciles held participants",
  { skip: !hostArchive },
  async (t) => {
    assert.equal(
      createHash("sha256")
        .update(await readFile(hostArchive))
        .digest("hex"),
      hostSha256,
    );
    const root = await mkdtemp(join(tmpdir(), "installed-host-control-")),
      pub = await publisher();
    t.after(async () => {
      await pub.close();
      await rm(root, { recursive: true, force: true });
    });
    const first = await artifact(root, 1, pub.origin, appCode),
      second = await artifact(root, 2, pub.origin, appCode);
    pub.publish([first], 1);
    const packed = JSON.parse(
      (
        await execute(
          "npm",
          ["pack", "--ignore-scripts", "--json", "--pack-destination", root],
          { cwd: packageRoot },
        )
      ).stdout,
    )[0];
    const consumer = join(root, "consumer");
    await mkdir(consumer);
    await writeFile(
      join(consumer, "package.json"),
      JSON.stringify({
        name: "host-control-consumer",
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
      join(packageRoot, "test/host-control-worker.mjs"),
      join(consumer, "worker.mjs"),
    );
    const config = {
      root,
      consumer,
      origin: pub.origin,
      keys: pub.keys,
      first: first.release.identity,
      second: second.release.identity,
    };
    await writeFile(join(root, "config.json"), JSON.stringify(config), {
      mode: 0o600,
    });
    const child = spawn(
      process.execPath,
      [join(consumer, "worker.mjs"), join(root, "config.json")],
      { cwd: consumer, stdio: ["ignore", "pipe", "pipe", "ipc"] },
    );
    let output = "";
    child.stdout.on("data", (x) => (output += x));
    child.stderr.on("data", (x) => (output += x));
    child.on("message", (message) => {
      if (message.advance) {
        pub.publish([first, second], 2);
        child.send({ advanced: true });
      }
    });
    const status = await new Promise((resolve) => child.once("exit", resolve));
    const childError = await readFile(
      join(root, "child-error.txt"),
      "utf8",
    ).catch(() => "");
    assert.equal(status, 0, output + childError);
    const receipt = JSON.parse(
      await readFile(join(root, "acceptance.json"), "utf8"),
    );
    assert.equal(receipt.heldParticipantReconciled, true);
    const retained = process.env.DISTRIBUTION_HOST_CONTROL_ACCEPTANCE_DIR;
    if (retained) {
      await mkdir(retained, { recursive: true });
      await copyFile(
        join(root, packed.filename),
        join(retained, packed.filename),
      );
      await writeFile(
        join(retained, "host-control-acceptance.json"),
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
