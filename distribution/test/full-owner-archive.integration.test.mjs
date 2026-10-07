import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,readdir,lstat,copyFile,open,rm,realpath,chmod} from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import {join,dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash,generateKeyPairSync,sign} from 'node:crypto';
import {createServer} from 'node:http';
import {execFile,spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {promisify} from 'node:util';
import * as updates from '@amplifier/unified-distribution-update-owner';
import {createGitSourceResolver} from '@amplifier/unified';
import {createHTTPSGitFixture} from './https-git-fixture.mjs';
const execute=promisify(execFile),hash=v=>createHash('sha256').update(v).digest('hex');
const python=process.env.FULL_RECOVERY_PYTHON,operationsPython=process.env.OWNER_SNAPSHOT_PYTHON;
const legacySwitch=process.env.LEGACY_FULL_OWNER_SWITCH==='1',legacyPython=process.env.LEGACY_READBACK_PYTHON,legacySource=process.env.LEGACY_UNIFIED_SOURCE;
const fullJourney=process.env.FULL_OWNER_JOURNEY==='1';
const activateRestore=process.env.FULL_OWNER_RESTORE==='1';
async function files(root){const result=[];async function walk(p=''){for(const name of await readdir(join(root,p))){const path=p?p+'/'+name:name,st=await lstat(join(root,path));if(st.isDirectory())await walk(path);else{assert.ok(st.isFile());result.push({path,bytes:st.size,mode:st.mode&0o777,sha256:hash(await readFile(join(root,path)))});}}}await walk();return result.sort((a,b)=>a.path.localeCompare(b.path));}
function connect(config,env){const child=spawn(python,['-I','-B','-m','amplifier_acp','--config',config],{env:{...process.env,...env},stdio:['pipe','pipe','pipe']});let id=0,stderr='';const pending=new Map();child.stderr.on('data',v=>stderr+=v);const ended=new Promise(resolve=>child.once('exit',code=>{for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error(stderr));}pending.clear();resolve(code);}));const lines=createInterface({input:child.stdout});lines.on('line',line=>{const row=JSON.parse(line),p=pending.get(row.id);if(!p)return;pending.delete(row.id);clearTimeout(p.timer);row.error?p.reject(Error(JSON.stringify(row.error))):p.resolve(row.result);});return {request:(method,params)=>new Promise((resolve,reject)=>{const key=++id,timer=setTimeout(()=>{pending.delete(key);reject(Error('Native fixture timeout'));},60000);pending.set(key,{resolve,reject,timer});child.stdin.write(JSON.stringify({jsonrpc:'2.0',id:key,method,params})+'\n');}),close:async()=>{child.stdin.end();assert.equal(await ended,0,stderr);lines.close();}};}
test('actual signed 21-owner stopped capture and inactive restore retain native, message, media and original receipts',{skip:!python||!operationsPython||process.env.FULL_OWNER_ARCHIVE!=='1',timeout:600000},async t=>{
 const root=await realpath(await mkdtemp(join(tmpdir(),legacySwitch?'legacy-continuation-full-owner-':'full-owner-archive-'))),directory=join(root,'installation'),state=join(directory,'application'),workspace=join(root,'workspace'),home=join(root,legacySwitch?'candidate-native':'native-home'),appHome=join(root,legacySwitch?'candidate-app':'native-app'),web=join(root,'web');
 let seed;
 const legacyHelper=fileURLToPath(new URL('./legacy-switch-fixture.py',import.meta.url));
 if(legacySwitch){
  assert.ok(legacyPython&&legacySource&&process.env.LEGACY_HTTP_SOURCE,'Complete separate legacy runtime and source required');
  const context=(await execute(python,['-I','-B','-c','from pathlib import Path; import amplifier_module_context_simple as m; print(Path(m.__file__).parent)'])).stdout.trim();
  seed=JSON.parse((await execute(legacyPython,['-I','-B',legacyHelper,'seed',legacySource,root],{env:{...process.env,LEGACY_CANDIDATE_CONTEXT_SOURCE:context}})).stdout);
  // The signed child has a curated runtime environment. Prepare its offline
  // provider in the disposable test interpreter before launch; it must not
  // depend on an inherited installer executable or install during a turn.
  await execute(python,['-I','-B','-m','uv','--no-config','pip','install','--python',python,'--no-deps',join(root,'provider')]);
  await execute(python,['-I','-B','-c','from pathlib import Path;import yaml,sys;p=Path(sys.argv[1]);v=yaml.safe_load(p.read_text());v["providers"][0].pop("source");p.write_text(yaml.safe_dump(v))',join(root,'fixture.yaml')]);
  await writeFile(join(root,'legacy-seed.json'),JSON.stringify(seed));
  if(fullJourney)await execute(legacyPython,['-I','-B',fileURLToPath(new URL('./legacy-task-fixture.py',import.meta.url)),process.env.LEGACY_HTTP_SOURCE,root]);
 }
 const git=await createHTTPSGitFixture(),assets=new Map();let installed,publisher;const observed=[];let browserJourney,journeyEvidence,updateEvidence;
 t.after(async()=>{await browserJourney?.close();if(installed?.supervisor){const r=await installed.supervisor.service.inspect();if(r.state==='running'){await installed.supervisor.service.stop({commandId:'cleanup',expected:r.identity});await installed.supervisor.service.waitFor('cleanup');}await installed.supervisor.close();}publisher?.closeAllConnections();if(publisher)await new Promise(r=>publisher.close(r));await git.close();console.error('Retained signed full-owner fixture:',root);});
 for(const p of [workspace,home,appHome,web,join(workspace,'.amplifier'),join(root,'native-events'),join(root,'native-checkpoints'),join(root,'native-sources')])await mkdir(p,{recursive:true,mode:0o700});
 if(fullJourney){
  const {cp}=await import('node:fs/promises');
  await cp(process.env.REHEARSAL_WEB,web,{recursive:true});
  await writeFile(join(root,'full-owner-journey.json'),JSON.stringify({questions:process.env.LEGACY_QUESTIONS_SOURCE,schedules:process.env.LEGACY_SCHEDULES_SOURCE,observations:process.env.LEGACY_OBSERVATIONS_SOURCE}),{mode:0o600});
 }else await writeFile(join(web,'index.html'),'<!doctype html><title>Disposable recovery qualification</title>');await writeFile(join(home,'settings.yaml'),'bundle:\n  app: []\n');
 const bundle=join(root,legacySwitch?'fixture.yaml':'bundle.yaml');if(!legacySwitch)await writeFile(bundle,'bundle:\n  name: cold-full-owner\n  version: 1.0.0\nproviders: []\n');
 const sid=seed?.nativeId??'01111111-1111-4111-8111-111111111111',saved=seed?join(home,seed.relativeDirectory):join(home,'projects',workspace.replaceAll('/','-'),'sessions',sid);await mkdir(saved,{recursive:true});
 const legacyDatabase=join(state,'legacy-client/app.sqlite3'),privateClient={selectedSessionId:sid,drafts:{[sid]:'Unsent cobalt draft',new:'Another private draft'},attachments:{},view:{page:'chat'}};
 await writeFile(join(root,'legacy-client-fixture.json'),JSON.stringify(privateClient),{mode:0o600});
 const canonical=seed?Object.fromEntries(await Promise.all(['metadata.json','transcript.jsonl'].map(async name=>[name,await readFile(join(saved,name),'utf8')]))):{'metadata.json':JSON.stringify({session_id:sid,working_dir:workspace,status:'idle',name:'Preserved recovery fixture'}),'transcript.jsonl':JSON.stringify({role:'user',content:'Preserved voice text',metadata:{via:'call',message_id:'voice-message'}})+'\n','events.jsonl':'{"preserved":"canonical events"}\n'};if(!seed)for(const [name,body]of Object.entries(canonical))await writeFile(join(saved,name),body);
 const nativeFile=join(root,'native.json'),nativeEnv={PYTHONDONTWRITEBYTECODE:'1',AMPLIFIER_HOME:home,AMPLIFIER_WEB_HOME:appHome,AMPLIFIER_SESSION_STATE_HOME:join(root,'native-checkpoints'),AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH:join(root,'native-events'),AMPLIFIER_SOURCE_STORE:join(root,'native-sources')};
 const n={home,appHome,bundle,...(seed?{startupTimeout:90}:{workerCommand:['/impossible/full-owner-worker']}),adminWorkspaceRoots:[workspace],adminMaintenance:true,maintenanceFullNative:true,maintenanceRestoreRoots:{fixture:root},maintenanceExternalWriters:'stopped',maintenanceFullNativeRoots:{checkpoint:nativeEnv.AMPLIFIER_SESSION_STATE_HOME,events:nativeEnv.AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH,sources:nativeEnv.AMPLIFIER_SOURCE_STORE,bundle},transferAuthorityDirectory:join(state,'capabilities/portability'),transferWorkspaceRoots:[root]};await writeFile(nativeFile,JSON.stringify(n),{mode:0o600});
 // The legacy fixture may already have created this file; writeFile's mode
 // applies only on creation. The operator launch configuration is private.
 await chmod(nativeFile,0o600);
 const ownerPython=join(root,'owner-python');await writeFile(ownerPython,'#!/bin/sh\nexec '+"'"+python.replaceAll("'","'\\''")+"'"+' -I -B "$@"\n',{mode:0o700});
 const application={account:'full-recovery-fixture',webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[root],host:{managedSessionRoot:join(state,'managed')},manualIngress:{stateDirectory:join(state,'ingress')},engines:[{id:'amplifier',command:python,args:['-I','-B','-m','amplifier_acp','--config',nativeFile],env:nativeEnv}],nativeAdmin:{engine:'amplifier'},maintenance:{},recovery:{authorization:'local-account'},historyImport:{},historyCleanup:true,managedFiles:true,portability:{python:ownerPython,engines:['amplifier'],stageDir:join(state,'transfer-stages'),exchangeDir:join(state,'transfer-exchange')},...Object.fromEntries(['operations','notifications','diagnostics','coordination','recall','publishing','worktrees','feedback','workspaces','mcp','media'].map(k=>[k,{python:k==='operations'?operationsPython:ownerPython,env:{PYTHONDONTWRITEBYTECODE:'1'}}])),catalogProcess:{command:python,args:['-I','-B','-m','amplifier_session_catalog','serve','--db',join(state,'catalog.sqlite'),'--home',home,'--app-home',appHome,'--workspace',workspace,'--scan-interval','0','--workspace-check-interval','0'],env:{PYTHONDONTWRITEBYTECODE:'1'}}};
 if(fullJourney){const portServer=createServer();await new Promise(r=>portServer.listen(0,'127.0.0.1',r));application.gateway={host:'127.0.0.1',port:portServer.address().port};await new Promise(r=>portServer.close(r));}
 application.legacyClientState={account:application.account,database:legacyDatabase};
 if(seed)application.catalogProcess.args.push('--scan-on-start');
 const distribution=dirname(dirname(fileURLToPath(import.meta.url))),packed=JSON.parse((await execute('npm',['pack','--json','--pack-destination',root],{cwd:distribution,maxBuffer:1024*1024})).stdout)[0],candidate=join(root,'candidate');await mkdir(candidate);await execute('tar',['-xzf',join(root,packed.filename),'-C',candidate]);
 await copyFile(new URL('./full-owner-archive-fixture.mjs',import.meta.url),join(candidate,'package/src/full-owner-archive-fixture.mjs'));
 if(fullJourney)await copyFile(new URL('./full-owner-journey-migration.py',import.meta.url),join(candidate,'package/src/full-owner-journey-migration.py'));
 const {installProductionDistribution,createCoherentInstallationArchive,inspectInstallationArchive,restoreInstallationArchive,createNativeCoherentCaptureAdapter}=await import(pathToFileURL(join(candidate,'package/src/index.js')));
 const sourceFiles=await files(join(candidate,'package')),components=[];for(const f of sourceFiles.filter(r=>r.path==='package.json'||/\bnode_modules\/(?:@[^/]+\/)?[^/]+\/package.json$/.test(r.path))){const pkg=JSON.parse(await readFile(join(candidate,'package',f.path),'utf8'));components.push({name:pkg.name,version:pkg.version,root:f.path==='package.json'?'':dirname(f.path),repository:git.repository,ref:'main',revision:git.first});}
 const archive=join(root,'signed-fixture.tgz');await execute('tar',['-czf',archive,'-C',candidate,'package'],{env:{...process.env,COPYFILE_DISABLE:'1'}});const bytes=await readFile(archive);
 publisher=createServer((req,res)=>{const body=assets.get(req.url);if(!body)res.writeHead(404);res.end(body);});await new Promise(r=>publisher.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+publisher.address().port;
 const release={identity:{id:'full-recovery-fixture',version:'1.0.0',revision:git.first,digest:''},entrypoint:'src/full-owner-archive-fixture.mjs',platform:process.platform,arch:process.arch,files:sourceFiles,components,artifact:{url:origin+'/release.tgz',sha256:hash(bytes),bytes:bytes.length}};release.identity.digest=updates.releaseDigest(release);assets.set('/release.tgz',bytes);
 const {publicKey,privateKey}=generateKeyPairSync('ed25519'),keys={fixture:publicKey.export({type:'spki',format:'pem'})},payload=Buffer.from(JSON.stringify({schema:'distribution-channel-v1',expiresAt:Date.now()+3600000,recommendedId:release.identity.id,releases:[release]}));assets.set('/channel.json',Buffer.from(JSON.stringify({schema:'distribution-signed-channel-v1',keyId:'fixture',payload:payload.toString('base64'),signature:sign(null,payload,privateKey).toString('base64')})));
 installed=await installProductionDistribution({schema:'unified-installation-v1',directory,dataScope:'full-recovery-owned',serviceLifecycle:{enabled:true},release:{channelUrl:origin+'/channel.json',trustedKeys:keys,accessScope:'owned-fixture',allowedArtifactOrigins:[origin],allowLoopbackHttp:true},sourceTracking:{sources:[{repository:git.repository,ref:'main'}],env:git.env},application});
 const ready=JSON.parse(await readFile(join(root,'archive-ready.json'),'utf8'));assert.equal(ready.owners.length,21);assert.equal(ready.agents,0);assert.equal(ready.staged.status,'sealed');
 if(fullJourney){
  const {openBrowserJourney}=await import('./full-owner-browser-journey.mjs');
  browserJourney=await openBrowserJourney({root,url:ready.url,session:ready.continuation.session,playwright:process.env.REHEARSAL_PLAYWRIGHT});
  console.log('Journey: migrated records and browser drafts ready');
  // A distinct signed successor assembled from the same candidate closes the
  // lifecycle, without claiming compatibility with a different storage schema.
  await writeFile(join(candidate,'package/rehearsal-generation.json'),JSON.stringify({generation:2}));
  const successorFiles=await files(join(candidate,'package')),successorArchive=join(root,'signed-successor.tgz');
  await execute('tar',['-czf',successorArchive,'-C',candidate,'package'],{env:{...process.env,COPYFILE_DISABLE:'1'}});
  const successorBytes=await readFile(successorArchive);
  const successor={...release,identity:{id:'full-recovery-successor',version:'1.0.1',revision:git.second,digest:''},files:successorFiles,
   components:components.map(c=>({...c,revision:git.second})),artifact:{url:origin+'/successor.tgz',sha256:hash(successorBytes),bytes:successorBytes.length}};
  successor.identity.digest=updates.releaseDigest(successor);assets.set('/successor.tgz',successorBytes);
  await git.advance(git.second);
  const nextPayload=Buffer.from(JSON.stringify({schema:'distribution-channel-v1',expiresAt:Date.now()+3600000,recommendedId:successor.identity.id,releases:[release,successor]}));
  assets.set('/channel.json',Buffer.from(JSON.stringify({schema:'distribution-signed-channel-v1',keyId:'fixture',payload:nextPayload.toString('base64'),signature:sign(null,nextPayload,privateKey).toString('base64')})));
  const observer=updates.connectSupervisorFileLazy(join(directory,'supervisor.json'));
  const done=async(id,settlement=false)=>{for(let i=0;i<1800;i++){const r=await observer.owner.receipt(id);if(r&&['succeeded','failed','unknown'].includes(r.status)&&(!settlement||r.status!=='succeeded'||r.admissionSettlement?.state==='settled'))return r;await new Promise(r=>setTimeout(r,50));}throw Error('Journey update did not settle: '+id);};
  try{
   const before=await observer.owner.inspectRunning(),calls=await readFile(join(root,'provider-requests.jsonl'),'utf8');
   await observer.owner.check('journey-check',true);assert.equal((await done('journey-check')).status,'succeeded');
   await observer.owner.prepare('journey-prepare',successor.identity.id);const prepared=await done('journey-prepare');assert.equal(prepared.status,'succeeded',JSON.stringify(prepared));
   assert.equal((await observer.owner.inspectRunning()).instanceId,before.instanceId);
   await browserJourney.disconnect();
   await observer.owner.activate('journey-update',{preparedCommandId:'journey-prepare',targetDigest:successor.identity.digest,expectedCurrentId:release.identity.id});
   const activated=await done('journey-update',true);assert.equal(activated.status,'succeeded',JSON.stringify(activated));
   const after=await observer.owner.inspectRunning();assert.equal(after.identity.id,successor.identity.id);assert.notEqual(after.instanceId,before.instanceId);
   assert.equal(await readFile(join(root,'provider-requests.jsonl'),'utf8'),calls,'Update cannot replay inference');
   const current=JSON.parse(await readFile(join(root,'current-ready.json'),'utf8'));assert.equal(current.firstStart,false);assert.equal(current.owners.length,21);ready.inventory=current.inventory;
   updateEvidence={prepared,activated,before,after,owners:current.owners,inferenceReplayed:false};
   await browserJourney.afterUpdate();await browserJourney.disconnect();
   console.log('Journey: signed update and one explicit pending answer passed');
  }finally{observer.close();}
 }

 if(seed){
  assert.equal(ready.continuation.providerCalls,2);assert.equal(ready.continuation.toolEffects,1);assert.equal(ready.continuation.newInputOnce,true);
  // Capture the final canonical bytes after the explicit new work, then prove
  // neither stopped capture nor inactive restore changes those bytes.
  for(const name of ['metadata.json','transcript.jsonl','events.jsonl'])canonical[name]=await readFile(join(saved,name),'utf8').catch(e=>{if(e.code==='ENOENT')return undefined;throw e;});
  for(const name of Object.keys(canonical))if(canonical[name]===undefined)delete canonical[name];
 }
 const legacyBytes=await readFile(legacyDatabase);
 const status=await installed.supervisor.service.inspect(),expected=status.identity;await installed.supervisor.service.stopForMaintenance({commandId:'archive-stop',expected});const stopped=await installed.supervisor.service.waitFor('archive-stop');assert.equal(stopped.status,'stopped',JSON.stringify(stopped));assert.equal(stopped.maintenanceStop,true);assert.deepEqual([...stopped.qualifiedOwners].sort(),[...ready.owners].sort());await installed.supervisor.close();installed=undefined;
 const staging=join(root,'native-staged');await mkdir(staging);const outputFile=join(root,'complete.unified');
 const acquireWriterExclusion=async context=>{const path=join(root,'fixture-retirement.lock'),fd=await open(path,'wx',0o600);let held=true;return {evidence:{policy:'operator-reviewed-retirement',noncooperatingWriters:'not-independently-observed',reviewDigest:hash('unique fixture all controlled child/owners joined and stopped')},assertHeld:async()=>{assert.ok(held);await lstat(path);return {active:true,...context,leaseId:'owned-fixture-retirement'};},release:async()=>{held=false;await fd.close();await rm(path);return {released:true};}};};
 const adapter=createNativeCoherentCaptureAdapter({engineId:'amplifier',cwd:workspace,stagingDirectory:staging,connect:async()=>connect(nativeFile,nativeEnv)});
 const request={directory,compositionInventory:ready.inventory,inventoryDigest:ready.inventory.digest,expected,stoppedCommandId:'archive-stop',outputFile,privateContentReviewed:true,includeCredentials:true,credentialsReviewed:true,requireCompleteProduct:true};
 await assert.rejects(createCoherentInstallationArchive({...request,outputFile:join(root,'misbound.unified')},{acquireWriterExclusion,nativeCaptures:[{...adapter,engineId:'foreign'}]}),/plan_mismatch/);
 await assert.rejects(createCoherentInstallationArchive({...request,outputFile:join(root,'omitted.unified')},{acquireWriterExclusion,nativeCaptures:[]}),/plan_mismatch/);
 await assert.rejects(createCoherentInstallationArchive({...request,outputFile:join(root,'duplicate.unified')},{acquireWriterExclusion,nativeCaptures:[adapter,adapter]}),/scope_invalid/);
 const receipt=await createCoherentInstallationArchive(request,{acquireWriterExclusion,nativeCaptures:[adapter]});assert.equal(receipt.completeProduct,true);assert.equal(receipt.captureRelease.state,'released');
 const review=await inspectInstallationArchive(outputFile);assert.equal(review.manifest.proof.qualifiedOwners.length,21);
 const destination=join(root,'restored');const restored=await restoreInstallationArchive({archiveFile:outputFile,destination,...receipt,privateContentReviewed:true});assert.equal(restored.inactive,true);assert.equal(restored.workReplayed,false);
 const restoredApp=join(destination,'roots/application');assert.deepEqual(await readFile(join(restoredApp,'capabilities/media/receipts/historical.json')),await readFile(join(state,'capabilities/media/receipts/historical.json')));
 const restoredLegacy=join(restoredApp,'legacy-client/app.sqlite3');assert.deepEqual(await readFile(restoredLegacy),legacyBytes);assert.deepEqual(await readFile(legacyDatabase),legacyBytes);
 const {createClientMigration}=await import(pathToFileURL(join(candidate,'package/src/client-migration.js')));
 const clientMigration=createClientMigration({account:application.account,database:restoredLegacy,resolveNative:async()=>({[sid]:{status:'matched',uri:'ahp-session:/restored-chat'}})});
 try{
  const offered=await clientMigration.metadata({clientId:'same-browser',metadata:{'amplifier.dev/legacyClient':{id:'original-browser'}}});
  const copied=JSON.parse((await clientMigration.resourceProvider.read({uri:offered['amplifier.dev/clientMigration'].uri},{clientId:'same-browser'})).data);
  assert.deepEqual(copied.record,privateClient);assert.equal(copied.sessionMap[sid],'ahp-session:/restored-chat');
  await assert.rejects(clientMigration.resourceProvider.read({uri:offered['amplifier.dev/clientMigration'].uri},{clientId:'other-browser'}),/unavailable/);
 }finally{clientMigration.close();}
 const snapshotDir=hash('original-seven-stores');for(const file of await readdir(join(state,'snapshots',snapshotDir,'images')))assert.deepEqual(await readFile(join(restoredApp,'snapshots',snapshotDir,'images',file)),await readFile(join(state,'snapshots',snapshotDir,'images',file)));
 for(const [name,body]of Object.entries(canonical))assert.equal(hash(await readFile(join(saved,name))),hash(body));
 const descriptor=review.manifest.inventory.nativeArtifacts[0],nativeArchive=join(destination,'native',descriptor.id),nativeHash=createHash('sha256');for await(const chunk of createReadStream(nativeArchive))nativeHash.update(chunk);assert.equal(nativeHash.digest('hex'),descriptor.sha256);
 const verified=JSON.parse((await execute(python,['-I','-B','-c',`import tarfile,hashlib,json,sys
expected=json.loads(sys.argv[2]);seen={}
with tarfile.open(sys.argv[1]) as archive:
 for item in archive:
  if item.isfile() and '/${sid}/' in item.name and item.name.rsplit('/',1)[-1] in expected:
   name=item.name.rsplit('/',1)[-1];seen[name]=hashlib.sha256(archive.extractfile(item).read()).hexdigest()
assert seen==expected,(seen,expected)
print(json.dumps(seen))`,nativeArchive,JSON.stringify(Object.fromEntries(Object.entries(canonical).map(([name,body])=>[name,hash(body)])))])).stdout);assert.equal(Object.keys(verified).length,Object.keys(canonical).length);
 const evidence={schema:'actual-full-owner-recovery-v1',root,owners:ready.owners,stopped,staged:ready.staged,receipt,restored,legacyPrivateClient:{databaseSha256:hash(legacyBytes),originalUnchanged:true,restoredDraftsExact:true,clientBoundGrant:true},canonicalSha256:Object.fromEntries(Object.entries(canonical).map(([k,v])=>[k,hash(v)])),packageCount:components.length,packageFileCount:sourceFiles.length,nodeArchiveSha256:hash(bytes),workReplayed:false,limits:['Owned signed child fixture; not production platform-stop proof.','Python code is exact supplied wheels over read-only dependency bases, not a self-contained 23-repository code archive.','Immutable historical unknown media receipt retained; active or uncertain transcript/call cases are separately refused.','Inactive restore and selected draft import only; no complete legacy installation switch or rollback of new writes.']};await writeFile(join(root,'acceptance.json'),JSON.stringify(evidence,null,2));
 if(seed){
  let activation;
  if(activateRestore){
   const {activateRestoredFixture}=await import('./full-owner-restored-activation.mjs');
   installed={};
   const api=await import(pathToFileURL(join(candidate,'package/src/index.js')));
   activation=await activateRestoredFixture({root,directory,nativeFile,installed,expected,archiveFile:outputFile,archiveReceipt:receipt,api});
   assert.equal(activation.owners.length,21);assert.equal(activation.startupReplayed,false);assert.equal(activation.explicitNewInputs,1);
   for(const [name,body]of Object.entries(canonical))assert.equal(hash(await readFile(join(saved,name))),hash(body),'Restore changed original native authority');
   evidence.activation=activation;
   if(browserJourney){journeyEvidence=await browserJourney.afterRecovery();await browserJourney.disconnect();evidence.browserJourney=journeyEvidence;evidence.update=updateEvidence;console.log('Journey: browser recovery passed');}
   const current=await installed.supervisor.service.inspect();installed.supervisor.service.stopForMaintenance({commandId:'restored-stop',expected:current.identity});const ended=await installed.supervisor.service.waitFor('restored-stop');assert.equal(ended.status,'stopped',JSON.stringify(ended));
   await installed.supervisor.close();installed=undefined;
  }
  const {cp}=await import('node:fs/promises');await cp(activation?activation.restoredHome:home,join(root,'rollback-native'),{recursive:true,errorOnExist:true,force:false});
  if(fullJourney){
   const restoredConfig=JSON.parse(await readFile(nativeFile,'utf8'));
   await cp(restoredConfig.appHome,join(root,'rollback-app'),{recursive:true,errorOnExist:true,force:false});
   const task=JSON.parse(await readFile(join(root,'legacy-task.json'),'utf8'));
   assert.equal(hash(await readFile(task.source)),task.sourceSha256,'Original legacy task state changed');
  }
  const rollback=JSON.parse((await execute(legacyPython,['-I','-B',legacyHelper,'readback',legacySource,root])).stdout);
  // The old HTTP worker guard requires an explicit, completed candidate receipt.
  await writeFile(join(root,'acceptance.json'),JSON.stringify({...evidence,continuation:ready.continuation,rollback},null,2));
  const httpExecution=JSON.parse((await execute(legacyPython,['-I','-B',fileURLToPath(new URL('./legacy-http-execution.py',import.meta.url)),process.env.LEGACY_HTTP_SOURCE,root],{timeout:120000,maxBuffer:2*1024*1024})).stdout);
  assert.equal(httpExecution.result,'passed');assert.equal(httpExecution.childWorkers,1);assert.equal(httpExecution.toolEffects,1);assert.deepEqual(httpExecution.externalConnections,[]);
  const originals=JSON.parse(await readFile(join(root,'original-hashes.json'),'utf8'));
  for(const [name,digest]of Object.entries(originals))assert.equal(hash(await readFile(join(root,'original-native',name))),digest);
  await writeFile(join(root,'acceptance.json'),JSON.stringify({...evidence,continuation:ready.continuation,rollback,httpExecution,originalFilesUnchanged:Object.keys(originals).length,limits:[...evidence.limits.filter(v=>!v.startsWith('Inactive restore')),'Signed 21-owner candidate executed one explicit turn against an actual legacy chat; stopped capture and inactive restore preserve the new writes. A separate old HTTP worker then continued a private post-candidate copy.',activation?'The packaged same-installation recovery workflow and explicit service resume passed with 21 owners and one new input. New-machine restoration and migration of all legacy product databases remain unqualified. The old worker uses fixture bundle redirection.':'Restored full installation activation and migration of all legacy product databases remain unqualified. The old worker uses its fixture bundle redirection.']},null,2));
 }
});
