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
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import { randomBytes, createHash } from "node:crypto";
import { publisher, artifact } from "./release-fixtures.mjs";
const execute = promisify(execFile),
  packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const serverCode = `import http from 'node:http';
import {readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import component from 'fixture-component';
const {readSignedChannel,verifyReleaseTree}=await import(process.env.OWNER_MODULE);
const receipt=JSON.parse(await readFile(process.env.AMPLIFIER_DISTRIBUTION_RELEASE_RECEIPT,'utf8'));
const {channel}=readSignedChannel(receipt.signed,JSON.parse(process.env.TRUSTED_KEYS),false);
const release=channel.releases.find(r=>r.identity.id===receipt.releaseId);
if(!release||!await verifyReleaseTree(fileURLToPath(new URL('./',import.meta.url)),release)||component!=='installed-component')process.exit(2);
const running={identity:release.identity,instanceId:process.env.AMPLIFIER_DISTRIBUTION_INSTANCE_ID,dataScope:process.env.AMPLIFIER_DISTRIBUTION_DATA_SCOPE,ready:true,pid:process.pid};
const server=http.createServer(async(req,res)=>{
 if(req.headers.authorization!=='Bearer '+process.env.READINESS_SECRET){res.writeHead(403);res.end();return}
 const reply=value=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(value))};
 if(req.url==='/ready'){reply(running);return}
 let input='';for await(const chunk of req)input+=chunk;const args=input?JSON.parse(input):{};
 if(req.url==='/admit'){
  if(args.purpose!=='distribution-update'||args.dataScope!==running.dataScope||!args.commandId){res.writeHead(409);reply(null);return}
  let old;try{old=JSON.parse(await readFile(process.env.FENCE_FILE,'utf8'))}catch{}
  if(old?.status==='held'){reply(null);return}
  const fence={fenceId:randomUUID(),commandId:args.commandId,status:'held',previousInstanceId:running.instanceId};await writeFile(process.env.FENCE_FILE,JSON.stringify(fence),{mode:0o600});
  reply({evidence:{activeWork:0,intakeClosed:true,instanceId:running.instanceId,dataScope:running.dataScope,observedAt:Date.now()}});return;
 }
 if(req.url==='/release'||req.url==='/reconcile'){
  const gate=JSON.parse(await readFile(process.env.FENCE_FILE,'utf8'));
  if(gate.commandId!==args.commandId||args.outcome==='ready'&&gate.previousInstanceId===running.instanceId||args.outcome==='unchanged'&&gate.previousInstanceId!==running.instanceId){res.writeHead(409);reply({error:'fence_mismatch'});return}
  if(args.outcome!=='unknown'){gate.status='released';await writeFile(process.env.FENCE_FILE,JSON.stringify(gate))}reply({released:gate.status==='released'});return;
 }
 if(req.url==='/stop'){reply({stopped:true});server.closeAllConnections();server.close(()=>process.exit(0));return}
 res.writeHead(404);reply({error:'not_found'});
});
server.listen(0,'127.0.0.1',()=>writeFile(process.env.ENDPOINT_FILE,JSON.stringify({port:server.address().port}),{mode:0o600}));
process.on('SIGTERM',()=>{server.closeAllConnections();server.close(()=>process.exit(0))});
`;
const portCode = `import {readFile} from 'node:fs/promises';
export function createSupervisorPorts(config){
 const call=async(path,args)=>{let endpoint;try{endpoint=JSON.parse(await readFile(config.endpoint,'utf8'))}catch{return null}const result=await fetch('http://127.0.0.1:'+endpoint.port+path,{method:args?'POST':'GET',headers:{Authorization:'Bearer '+config.secret,'Content-Type':'application/json'},body:args?JSON.stringify(args):undefined,signal:AbortSignal.timeout(3000)});if(!result.ok)throw Error('fixture_host_rejected');return result.json()};
 return {inspect:async()=>{try{return await call('/ready')}catch{return null}},
  resolveSources:async()=>await(await fetch(config.origin+'/sources')).json(),
  admitRestart:async context=>{if(!context)throw Error('admission_context_required');const proof=await call('/admit',{commandId:context.commandId,purpose:context.purpose,dataScope:context.dataScope});return proof?{evidence:proof.evidence,release:outcome=>call('/release',{commandId:context.commandId,outcome})}:null},
  reconcileAdmission:request=>call('/reconcile',{commandId:request.commandId,outcome:request.outcome})};
}
`;

test("installed supervisor CLI and independent client qualify signed release promotion and rollback", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "installed-supervisor-")),
    pub = await publisher();
  t.after(async () => {
    await pub.close();
    await rm(root, { recursive: true, force: true });
  });
  const first = await artifact(root, 1, pub.origin, serverCode),
    second = await artifact(root, 2, pub.origin, serverCode);
  pub.publish([first], 1);
  const packed = JSON.parse(
      (
        await execute(
          "npm",
          ["pack", "--ignore-scripts", "--json", "--pack-destination", root],
          { cwd: packageRoot },
        )
      ).stdout,
    )[0],
    packageFile = join(root, packed.filename);
  const consumer = join(root, "consumer");
  await mkdir(consumer);
  await writeFile(
    join(consumer, "package.json"),
    JSON.stringify({
      name: "independent-supervisor-client",
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
      packageFile,
    ],
    { cwd: consumer },
  );
  await copyFile(
    join(packageRoot, "test", "supervisor-worker.mjs"),
    join(consumer, "worker.mjs"),
  );
  await writeFile(join(consumer, "ports.mjs"), portCode);
  const config = {
    root,
    consumer,
    origin: pub.origin,
    keys: pub.keys,
    first: first.release.identity,
    second: second.release.identity,
    endpoint: join(root, "endpoint.json"),
    fence: join(root, "fence.json"),
    secret: randomBytes(32).toString("hex"),
    receipt: join(root, "receipt.json"),
  };
  await writeFile(join(root, "config.json"), JSON.stringify(config), {
    mode: 0o600,
  });
  const child = spawn(
    process.execPath,
    [join(consumer, "worker.mjs"), join(root, "config.json")],
    { cwd: consumer, stdio: ["ignore", "pipe", "pipe", "ipc"] },
  );
  let out = "",
    error = "";
  child.stdout.on("data", (chunk) => (out += chunk));
  child.stderr.on("data", (chunk) => (error += chunk));
  child.on("message", (message) => {
    if (message.advance) {
      pub.publish([first, second], 2);
      child.send({ advanced: true });
    }
  });
  const status = await new Promise((resolve) => child.once("exit", resolve));
  assert.equal(status, 0, error + "\n" + out);
  const receipt = JSON.parse(await readFile(config.receipt, "utf8"));
  assert.equal(receipt.actualRollback, true);
  const output = process.env.DISTRIBUTION_SUPERVISOR_ACCEPTANCE_DIR;
  if (output) {
    await mkdir(output, { recursive: true, mode: 0o700 });
    await copyFile(packageFile, join(output, packed.filename));
    await writeFile(
      join(output, "supervisor-acceptance.json"),
      JSON.stringify(
        {
          ...receipt,
          sourceArtifact: packed.filename,
          artifactSha256: createHash("sha256")
            .update(await readFile(packageFile))
            .digest("hex"),
          releaseDigests: [
            first.release.identity.digest,
            second.release.identity.digest,
          ],
        },
        null,
        2,
      ) + "\n",
    );
  }
});
