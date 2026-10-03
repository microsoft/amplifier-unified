import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  writeFile,
  readFile,
  readdir,
  lstat,
  cp,
  rm,
  realpath,
} from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { tmpdir } from "node:os";
import { createHash, generateKeyPairSync, sign, randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { once } from "node:events";
import { createRequire } from "node:module";
import { createHTTPSGitFixture } from "./https-git-fixture.mjs";
const fixture = JSON.parse(await readFile(process.argv[2], "utf8")),
  execute = promisify(execFile),
  hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const appEntry = import.meta.resolve("@amplifier/unified"),
  packageRoot = dirname(dirname(fileURLToPath(appEntry))),
  require = createRequire(appEntry);
const { connectSupervisorFile, connectHostControlFile, releaseDigest, runProductionSupervisor } =
  await import(require.resolve("@amplifier/unified-distribution-update-owner"));
const { WebSocket } = require("ws");
const {installProductionDistribution,createGitSourceResolver}=await import("@amplifier/unified");
async function inventory(root) {
  const files = [];
  async function visit(prefix = "") {
    for (const name of await readdir(join(root, prefix))) {
      const path = prefix ? prefix + "/" + name : name,
        info = await lstat(join(root, path));
      if (info.isDirectory()) await visit(path);
      else {
        assert.ok(info.isFile());
        const bytes = await readFile(join(root, path));
        files.push({
          path,
          sha256: hash(bytes),
          bytes: bytes.length,
          mode: info.mode & 0o777,
        });
      }
    }
  }
  await visit();
  return files.sort((a, b) => a.path.localeCompare(b.path));
}
async function peer(url) {
  const socket = new WebSocket(url.replace(/^http/, "ws") + "/ahp", {
    origin: url,
  });
  await once(socket, "open");
  let next = 0;
  const pending = new Map();
  socket.on("message", (raw) => {
    const row = JSON.parse(raw),
      p = pending.get(row.id);
    if (p) {
      pending.delete(row.id);
      clearTimeout(p.timer);
      row.error ? p.reject(Error(row.error.message)) : p.resolve(row.result);
    }
  });
  const request = (method, params) =>
    new Promise((resolve, reject) => {
      const id = ++next,
        timer = setTimeout(() => {
          pending.delete(id);
          reject(Error("Fixture request timed out"));
        }, 15000);
      pending.set(id, { resolve, reject, timer });
      socket.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
    });
  await request("initialize", {
    channel: "ahp-root://",
    clientId: randomUUID(),
    protocolVersions: ["0.9.0"],
    initialSubscriptions: ["ahp-root://"],
  });
  return {
    socket,
    request,
    action: async (operation, args = {}, commandId = randomUUID()) => {
      try {
        return (
          await request("x-amplifier/capabilityAction", {
            channel: "ahp-root://",
            version: 1,
            topic: "application-updates",
            operation: "updates.application." + operation,
            args,
            commandId,
          })
        ).result;
      } catch (error) {
        throw Error(operation + ": " + error.message);
      }
    },
  };
}
const root = await realpath(
    await mkdtemp(join(tmpdir(), "installer-live-fixture-")),
  ),
  git = await createHTTPSGitFixture(),
  resources = new Map();
const { publicKey, privateKey } = generateKeyPairSync("ed25519"),
  keys = { fixture: publicKey.export({ type: "spki", format: "pem" }) };
const publisher = createServer((req, res) => {
  const bytes = resources.get(req.url);
  if (!bytes) {
    res.writeHead(404);
    res.end();
  } else res.end(bytes);
});
await new Promise((resolve) => publisher.listen(0, "127.0.0.1", resolve));
const origin = "http://127.0.0.1:" + publisher.address().port;
let running, reopened,
  supervisor,
  host,
  a,
  b, serviceProcess;
try {
  const workspace = join(root, "workspace"),
    web = join(root, "web");
  await mkdir(workspace);
  await mkdir(web);
  await writeFile(
    join(web, "index.html"),
    "<html><body>Owned installer fixture</body></html>",
  );
  async function candidate(version, revision) {
    const directory = join(root, "candidate-" + version);
    await mkdir(directory);
    await cp(fixture.sourcePackageRoot, join(directory, "package"), {
      recursive: true,
    });
    const content = join(directory, "package"),
      entry = join(content, "src/cli.js");
    await writeFile(
      entry,
      (await readFile(entry, "utf8")) +
        "\n// Installer fixture revision " +
        version +
        "\n",
    );
    const path = join(root, "release-" + version + ".tgz");
    await execute("tar", ["-czf", path, "-C", directory, "package"], {
      env: { ...process.env, COPYFILE_DISABLE: "1" },
    });
    const bytes = await readFile(path),
      files = await inventory(content),
      components = [];
    for (const file of files.filter(
      (row) =>
        row.path === "package.json" ||
        /\bnode_modules\/(?:@[^/]+\/)?[^/]+\/package.json$/.test(row.path),
    )) {
      const pkg = JSON.parse(await readFile(join(content, file.path), "utf8"));
      components.push({
        name: pkg.name,
        version: pkg.version,
        root: file.path === "package.json" ? "" : dirname(file.path),
        repository: git.repository,
        ref: "main",
        revision,
      });
    }
    const release = {
      identity: {
        id: "installer-fixture-" + version,
        version: version + ".0.0",
        revision,
        digest: "",
      },
      artifact: {
        url: origin + "/release-" + version + ".tgz",
        sha256: hash(bytes),
        bytes: bytes.length,
      },
      entrypoint: "src/cli.js",
      platform: "any",
      arch: "any",
      files,
      components,
    };
    release.identity.digest = releaseDigest(release);
    resources.set("/release-" + version + ".tgz", bytes);
    return release;
  }
  const first = await candidate(1, git.first),
    second = await candidate(2, git.second);
  const publish = (release) => {
    const payload = Buffer.from(
      JSON.stringify({
        schema: "distribution-channel-v1",
        expiresAt: Date.now() + 600000,
        recommendedId: release.identity.id,
        releases: [first, second],
        releaseNotes:{schema:'distribution-release-notes-publication-v1',entries:[first,second].map(item=>({version:item.identity.version,title:'Immediate manual updates',changes:['Manual requests dispatch directly.'],notices:[{id:'immediate-checks',title:'Faster update checks',detail:'Manual checks bypass cached completed results.',action:'Use Check for updates.'}]}))},
      }),
    );
    resources.set(
      "/channel.json",
      Buffer.from(
        JSON.stringify({
          schema: "distribution-signed-channel-v1",
          keyId: "fixture",
          payload: payload.toString("base64"),
          signature: sign(null, payload, privateKey).toString("base64"),
        }),
      ),
    );
  };
  publish(first);
  const reserve = createServer();
  await new Promise((resolve) => reserve.listen(0, "127.0.0.1", resolve));
  const port = reserve.address().port;
  await new Promise((resolve) => reserve.close(resolve));
  const engineFile=join(root,'controlled-acp.mjs'),startedFile=join(root,'prompt-started'),finishFile=join(root,'prompt-finish');
  await writeFile(engineFile,`import {createInterface} from 'node:readline';
import {writeFile,access} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
const send=v=>process.stdout.write(JSON.stringify({jsonrpc:'2.0',...v})+'\\n');
let active=false,retired=false;
createInterface({input:process.stdin}).on('line',async line=>{
 const {id,method,params}=JSON.parse(line);if(id===undefined)return;
 if(method==='initialize')return send({id,result:{protocolVersion:1,agentCapabilities:{promptCapabilities:{embeddedContext:true},sessionCapabilities:{resume:{},close:{}},_meta:{'amplifier.dev/native':{lifecycle:{version:1,gracefulRetire:true}}}},authMethods:[]}});
 if(method==='session/new')return send({id,result:{sessionId:randomUUID(),configOptions:[]}});
 if(['session/resume','session/close'].includes(method))return send({id,result:{}});
 if(method==='_amplifier/retire'){if(active)return send({id,result:{retired:false,reason:'owned_work'}});retired=true;return send({id,result:{retired:true}});}
 if(method==='session/prompt'){
  if(retired)return send({id,error:{code:-32000,message:'Fixture retired'}});active=true;
  await writeFile(process.env.FIXTURE_STARTED,'started');
  while(true){try{await access(process.env.FIXTURE_FINISH);break;}catch{await new Promise(r=>setTimeout(r,10));}}
  active=false;return send({id,result:{stopReason:'end_turn'}});
 }
 send({id,error:{code:-32601,message:'Fixture method unavailable'}});
});`);
  const configuration = {
    schema: "unified-installation-v1",
    directory: join(root, "installation"),
    dataScope: "fixture-installation",
    serviceLifecycle: {enabled:true},
    release: {
      channelUrl: origin + "/channel.json",
      trustedKeys: keys,
      accessScope: "fixture",
      allowedArtifactOrigins: [origin],
      allowLoopbackHttp: true,
    },
    sourceTracking: {
      sources: [{ repository: git.repository, ref: "main" }],
      env: git.env,
    },
    application: {
      account: "fixture",
      webDirectory: web,
      defaultWorkspace: workspace,
      allowedWorkspaceRoots: [workspace],
      gateway: { port },
      engines: [
        {
          id: "unused",
          command: process.execPath,
          args: [engineFile],
          env: {FIXTURE_STARTED:startedFile,FIXTURE_FINISH:finishFile},
        },
      ],
    },
  };

  const configuredOwners=['resources','application-updates'];
  if(fixture.fullOwners){
    const {python,provider}=fixture.fullOwners,home=join(root,'native-home'),appHome=join(root,'native-app'),nativeConfig=join(root,'native.json');
    await mkdir(home);await writeFile(join(home,'settings.yaml'),'bundle:\n  app: []\n');
    const context=(await execute(python,['-I','-c','import importlib.util,pathlib;print(pathlib.Path(importlib.util.find_spec("amplifier_module_context_simple").origin).parent)'])).stdout.trim();
    const bundle=join(root,'native-fixture.yaml');await writeFile(bundle,`bundle:\n  name: service-all-owners\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: ${context}\nproviders:\n  - module: provider-fixture\n    source: ${provider}\n`);
    await writeFile(nativeConfig,JSON.stringify({home,appHome,bundle,adminWorkspaceRoots:[workspace],adminMaintenance:true,transferAuthorityDirectory:join(configuration.directory,'application/capabilities/portability'),transferWorkspaceRoots:[workspace],maintenanceExternalWriters:'foundation-cooperative'}));
    const application=configuration.application;
    application.engines.push({id:'native',command:python,args:['-I','-m','amplifier_acp','--config',nativeConfig]});
    Object.assign(application,{nativeAdmin:{engine:'native'},maintenance:{},recovery:{authorization:'local-account'},historyImport:{},portability:{python,engines:['native'],stageDir:join(workspace,'stages'),exchangeDir:join(workspace,'exchange')},catalogProcess:{command:python,args:['-I','-m','amplifier_session_catalog','serve','--db',join(root,'catalog.sqlite'),'--home',home,'--app-home',appHome,'--scan-interval','0','--workspace-check-interval','0']}});
    for(const name of ['operations','notifications','diagnostics','coordination','recall','publishing','worktrees','feedback','workspaces','mcp','media'])application[name]={python};
    configuredOwners.push('native-administration','media','mcp','notifications','diagnostics','operations','coordination','worktree','publishing','recall','feedback','workspaces','portability','recovery','history');
  }

  running=await installProductionDistribution(configuration);
  let current=running.supervisor;
  const dir=configuration.directory,app=JSON.parse(await readFile(running.applicationFile,'utf8'));
  const supervisorConfig=JSON.parse(await readFile(running.supervisorFile,'utf8'));
  assert.deepEqual(app.supervision.serviceLifecycle,supervisorConfig.serviceLifecycle);
  assert.equal(supervisorConfig.serviceLifecycle.installationId,
   JSON.parse(await readFile(join(dir,'initial-provisioning.json'),'utf8')).installationId);
  assert.ok(supervisorConfig.serviceLifecycle.ownerId);
  supervisor=await connectSupervisorFile(join(dir,'supervisor.json'));
  host=connectHostControlFile(join(dir,'host-control.json'),'fixture-installation');
  const original=(await supervisor.service.inspect()).identity;
  assert.deepEqual((await host.service.inspectServiceLifecycle()).identity,original);
  const notifications=[];let off=supervisor.subscribe(e=>notifications.push(e));
  a=await peer('http://127.0.0.1:'+port);
  const notes=await a.action('releaseNotes',{limit:1},'notes');
  assert.equal(notes.currentVersion,first.identity.version);assert.equal(notes.entries.length,1);
  const note=notes.entries[0],review={version:note.version,noticeId:note.notices[0].id,contentDigest:note.notices[0].contentDigest};
  assert.equal((await a.action('reviewNotice',review,'review-release-note')).receipt.status,'succeeded');
  if(fixture.fullOwners)await a.request('createSession',{channel:'ahp-session:/'+randomUUID(),provider:'native',workingDirectories:[pathToFileURL(workspace).href]});
  const session='ahp-session:/'+randomUUID(),chat=session.replace('ahp-session:','ahp-chat:');
  await a.request('createSession',{channel:session,provider:'unused',workingDirectories:[pathToFileURL(workspace).href]});
  await a.request('dispatchAction',{channel:chat,clientSeq:1,action:{type:'chat/turnStarted',turnId:randomUUID(),startedAt:new Date().toISOString(),message:{text:'Wait for the fixture release',origin:{kind:'user'}}}});
  for(let i=0;;i++){try{await lstat(startedFile);break;}catch{assert.ok(i<500,'Fixture prompt did not begin');await new Promise(r=>setTimeout(r,10));}}
  await supervisor.service.stop({commandId:'busy-stop',expected:original});
  const busy=await current.service.waitFor('busy-stop');
  assert.equal(busy.status,'refused',JSON.stringify(busy));assert.equal(busy.noEffect,true);
  assert.equal((await host.inspect()).instanceId,original.instanceId);
  assert.equal((await host.service.serviceStopReceipt('busy-stop')).admitted,false);
  assert.equal((await host.service.inspectServiceLifecycle()).intakeClosed,false);
  await writeFile(finishFile,'finish');
  // Observe a terminal turn through the public history snapshot. This test-only
  // sampling does not dispatch work or become an application scheduling policy.
  for(let i=0;;i++){
   const state=(await a.request('subscribe',{channel:chat})).snapshot.state;
   if(state.turns?.at(-1)?.state==='complete')break;
   assert.ok(i<500,'Fixture turn did not complete');await new Promise(r=>setTimeout(r,10));
  }
  // Perform a real application update through the same optional process owner,
  // proving its child stop guard accepts a held update fence as well as service stop.
  await git.advance(git.second);publish(second);
  await a.action('check',{},'check');const checked=await current.owner.waitFor('check');assert.equal(checked.status,'succeeded');
  await a.action('prepare',{},'stage-update');
  const stagedReceipt=await current.owner.waitFor('stage-update');assert.equal(stagedReceipt.phase,'prepared');
  assert.equal((await host.inspect()).instanceId,original.instanceId,'Preparation must not replace the running app');
  const staged=(await a.action('inspect')).staged;
  await a.action('activate',{preparedCommandId:staged.commandId,targetDigest:staged.target.digest,expectedCurrentId:staged.expectedCurrentId},'install');
  const updated=await current.owner.waitFor('install');assert.equal(updated.status,'succeeded',JSON.stringify(updated));
  assert.equal(updated.admissionSettlement.state,'settled');a.socket.terminate();a=undefined;
  const before=(await supervisor.service.inspect()).identity;
  assert.notEqual(before.instanceId,original.instanceId);assert.equal(before.releaseDigest,second.identity.digest);
  await supervisor.service.stop({commandId:'stop',expected:before});
  const stopped=await current.service.waitFor('stop');assert.equal(stopped.status,'stopped',JSON.stringify(stopped));
  assert.equal(stopped.exitProof.instanceId,before.instanceId);
  off();supervisor.close();await current.close();running=undefined;
  // Reopening the external supervisor starts nothing. Only the durable explicit
  // resume command can create a new child from the retained verified release.
  resources.delete('/channel.json');const requestsBeforeResume=git.requests;
  const serviceEntry=join(packageRoot,'src/service-cli.js');
  serviceProcess=spawn(process.execPath,[serviceEntry,'serve','--directory',dir],{stdio:['ignore','pipe','pipe']});
  let serviceOutput='',serviceError='';serviceProcess.stderr.on('data',v=>serviceError+=v);
  await new Promise((resolve,reject)=>{
   serviceProcess.once('error',reject);serviceProcess.once('exit',code=>reject(Error('Service runner exited '+code+' '+serviceError)));
   serviceProcess.stdout.on('data',v=>{serviceOutput+=v;if(serviceOutput.includes('\n')){try{const line=JSON.parse(serviceOutput.split('\n')[0]);assert.equal(line.supervisorOnly,true);assert.equal(line.service.state,'stopped');resolve();}catch(e){reject(e);}}});
  });
  const command=async(name,flags=[])=>JSON.parse((await execute(process.execPath,[serviceEntry,name,'--directory',dir,...flags],{maxBuffer:1024*1024})).stdout);
  assert.equal((await command('status')).state,'stopped');
  // A second public runner cannot acquire the already owned ledger.
  await assert.rejects(execute(process.execPath,[serviceEntry,'serve','--directory',dir]),error=>error.stderr.includes('owner_already_running'));
  supervisor=await connectSupervisorFile(join(dir,'supervisor.json'));off=supervisor.subscribe(e=>notifications.push(e));
  const waitReceipt=(id,service=true)=>new Promise((resolve,reject)=>{
   let done=false;const complete=r=>{if(!r||done)return;const terminal=service?['stopped','ready','refused','unknown'].includes(r.status):['succeeded','failed','unknown'].includes(r.status);if(terminal&&r.admissionSettlement?.state!=='pending'){done=true;clearTimeout(timer);unsubscribe();resolve(r);}};
   const unsubscribe=supervisor.subscribe(e=>complete(service?e.serviceReceipt?.commandId===id?e.serviceReceipt:null:e.receipt?.id===id?e.receipt:null));
   const timer=setTimeout(()=>{done=true;unsubscribe();reject(Error('Fixture receipt wait exceeded'));},30000);
   (service?supervisor.service:supervisor.owner).receipt(id).then(complete,reject);
  });
  current={service:{waitFor:id=>waitReceipt(id)},owner:{waitFor:id=>waitReceipt(id,false)},
   stopService:async id=>{const v=await supervisor.service.inspect();if(v.state==='stopped')return supervisor.service.receipt(v.stoppedReceiptId);await command('stop',['--command-id',id,'--expected',JSON.stringify(v.identity)]);return waitReceipt(id);},
   close:async()=>{const exit=once(serviceProcess,'exit');serviceProcess.kill('SIGTERM');const [code]=await exit;assert.equal(code,0,serviceError);serviceProcess=undefined;},
  };reopened=current;
  await assert.rejects(host.service.releaseServiceStop({fenceId:stopped.fenceId,commandId:'stop',outcome:'resumed',resumeCommandId:'forged',evidence:{verified:true}}));
  await command('resume',['--command-id','resume','--stopped-command-id','stop','--expected',JSON.stringify(before)]);
  const resumed=await current.service.waitFor('resume');assert.equal(resumed.status,'ready',JSON.stringify(resumed));
  assert.equal(resumed.admissionSettlement.state,'settled');
  assert.notEqual(resumed.observed.instanceId,before.instanceId);assert.equal(resumed.observed.releaseDigest,before.releaseDigest);
  assert.equal((await host.inspect()).instanceId,resumed.observed.instanceId);
  assert.equal((await host.service.inspectServiceLifecycle()).intakeClosed,false);
  assert.equal(git.requests,requestsBeforeResume,'Explicit resume uses retained qualified bytes offline');
  assert.equal((await supervisor.service.adopt({commandId:'adopt',expected:resumed.observed})).status,'refused');
  const exact=await command('reconcile',['--command-id','resume']);assert.equal(exact.admissionSettlement.state,'settled');
  assert.equal((await host.inspect()).instanceId,resumed.observed.instanceId);
  b=await peer('http://127.0.0.1:'+port);assert.equal((await b.action('running')).instanceId,resumed.observed.instanceId);
  const retainedReview=(await b.action('receipt',{commandId:'review-release-note'},'read-review')).receipt;
  assert.deepEqual(retainedReview.noticeReview,review);assert.equal(retainedReview.status,'succeeded');
  const retainedNotes=await b.action('releaseNotes',{},'retained-notes');
  assert.equal(retainedNotes.currentVersion,second.identity.version);assert.equal(retainedNotes.unreviewedCount,1);
  await b.action('preferences',{autoCheck:false,autoInstall:false,intervalMs:3600000},'post-resume-intake');
  assert.equal((await current.owner.waitFor('post-resume-intake')).status,'succeeded');
  await new Promise(r=>setTimeout(r,30));assert.ok(notifications.some(e=>e.serviceReceipt?.commandId==='resume'&&e.serviceReceipt.admissionSettlement?.state==='settled'));
  off();b.socket.terminate();b=undefined;
  assert.equal((await current.stopService('cleanup')).status,'stopped');await current.close();reopened=undefined;
  await writeFile(fixture.receiptFile,JSON.stringify({schema:'unified-service-composition-acceptance-v1',node:process.version,platform:process.platform,
   actualInstalledDistributionCLI:true,actualInstallerComposition:true,privateBindingPersisted:true,actualSignedUpdate:true,
   signedOfflineReleaseNotes:true,publicReviewAction:true,reviewReceiptSurvivesUpdateAndResume:true,
   busyStopRefused:true,actualChildExitProven:true,reopenedStoppedSupervisor:true,explicitOfflineResume:true,
   publicInstalledServiceCLI:true,duplicateRunnerRefused:true,explicitStagedActivation:true,
   authenticatedServiceRelease:true,applicationFacadeResumed:true,pushedProgress:true,noAdoption:true,
   configuredOwners,allConfiguredOwnersServiceLifecycleQualified:Boolean(fixture.fullOwners),managedSystemService:false,
   nativeAgentAcceptance:fixture.fullOwners?'Core/Foundation initialization and graceful retirement; no inference':false,manualCheckMs:checked.updatedAt-checked.createdAt,installMs:updated.updatedAt-updated.createdAt,graphSha256:fixture.assembledArchiveSha256,componentCount:first.components.length},null,2));
} catch(error){
 console.error(JSON.stringify({failure:error.message,diagnostics:supervisor?await supervisor.owner.diagnostics().catch(()=>null):null}));throw error;
} finally{
 a?.socket.terminate();b?.socket.terminate();supervisor?.close();
 for(const candidate of [reopened,running?.supervisor])if(candidate){
  const stopped=await candidate.stopService('cleanup-failure').catch(()=>null);
  if(stopped?.status==='stopped')await candidate.close();
 }
 host?.close();publisher.closeAllConnections();await new Promise(r=>publisher.close(r));await git.close();await rm(root,{recursive:true,force:true});
}
