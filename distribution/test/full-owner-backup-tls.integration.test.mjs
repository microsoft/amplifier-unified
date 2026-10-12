// Installed production launcher qualification. No replacement application
// entrypoint, fabricated service receipt, provider call, or live user data.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,readdir,lstat,realpath,chmod,copyFile,open,rm} from 'node:fs/promises';
import {join,dirname,relative} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash,generateKeyPairSync,sign,randomUUID} from 'node:crypto';
import {createServer} from 'node:http';
import {request as httpsRequest} from 'node:https';
import {spawn,execFile} from 'node:child_process';
import {createInterface} from 'node:readline';
import {promisify} from 'node:util';
import {once} from 'node:events';
import {DatabaseSync} from 'node:sqlite';
import {WebSocket} from 'ws';
import {releaseDigest,connectSupervisorFileLazy} from '@amplifier/unified-distribution-update-owner';
import {inventoryPythonRuntime,inventoryMcpRuntime} from '../src/release-runtime.mjs';
import {connectInstallationBackupControl} from '../src/installation-backup-control.js';
import {inspectInstallationArchive} from '../src/installation-archive.js';
import {OWNERS} from '../src/validate-config.mjs';
import {createHTTPSGitFixture} from './https-git-fixture.mjs';
const exec=promisify(execFile),sha=v=>createHash('sha256').update(v).digest('hex');
const python=process.env.FULL_RECOVERY_PYTHON;
const writePrivate=(path,body)=>writeFile(path,body,{mode:0o600,flag:'wx'});
async function files(root){const rows=[];async function walk(prefix=''){for(const name of await readdir(join(root,prefix))){const path=prefix?prefix+'/'+name:name,s=await lstat(join(root,path));if(s.isDirectory())await walk(path);else{assert.ok(s.isFile());const bytes=await readFile(join(root,path));rows.push({path,mode:s.mode&0o777,bytes:bytes.length,sha256:sha(bytes)});}}}await walk();return rows.sort((a,b)=>a.path.localeCompare(b.path));}
async function port(){const s=createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;}
function connect(config,env){
 const child=spawn(python,['-I','-B','-m','amplifier_acp','--config',config],{env:{...process.env,...env},stdio:'pipe'}),pending=new Map();let serial=0,tail='';
 child.stderr.on('data',b=>{tail=(tail+b).slice(-8192);});
 const ended=new Promise(resolve=>child.once('exit',code=>{for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('Native capture exited: '+tail));}pending.clear();resolve(code);}));
 const lines=createInterface({input:child.stdout});lines.on('line',line=>{const row=JSON.parse(line),p=pending.get(row.id);if(p){pending.delete(row.id);clearTimeout(p.timer);row.error?p.reject(Error(JSON.stringify(row.error))):p.resolve(row.result);}});
 return {request:(method,params)=>new Promise((resolve,reject)=>{const id=++serial,timer=setTimeout(()=>{pending.delete(id);reject(Error('Native capture timed out'));},60000);pending.set(id,{resolve,reject,timer});child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');}),close:async()=>{child.stdin.end();assert.equal(await ended,0,tail);lines.close();}};
}
async function login(origin,ca,code){return new Promise((resolve,reject)=>{
 const q=httpsRequest(origin+'/preview/login',{method:'POST',ca,headers:{origin,'content-type':'application/json'}},s=>{s.resume();s.once('end',()=>{try{assert.equal(s.statusCode,204);resolve(s.headers['set-cookie'][0].split(';')[0]);}catch(e){reject(e);}});});q.once('error',reject);q.end(JSON.stringify({code}));
});}
async function peer(origin,ca,cookie){
 const socket=new WebSocket(origin.replace('https:','wss:')+'/ahp',{ca,origin,headers:{Cookie:cookie}});await once(socket,'open');let serial=0;const pending=new Map();
 socket.on('message',bytes=>{const row=JSON.parse(bytes),p=pending.get(row.id);if(p){pending.delete(row.id);clearTimeout(p.timer);row.error?p.reject(Error(JSON.stringify(row.error))):p.resolve(row.result);}});
 socket.on('close',()=>{for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('connection closed'));}pending.clear();});
 const request=(method,params)=>new Promise((resolve,reject)=>{const id=++serial,timer=setTimeout(()=>{pending.delete(id);reject(Error('TLS RPC timeout: '+method));},30000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});
 await request('initialize',{channel:'ahp-root://',clientId:'backup-reviewer',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});
 return {request,action:async(operation,args={},commandId='read-'+randomUUID())=>{
  const result=await request('x-amplifier/capabilityAction',{channel:'ahp-root://',topic:'application-updates',version:1,operation:'updates.application.backup.'+operation,args,commandId});assert.equal(result.accepted,true,JSON.stringify(result));return result.result;
 },close:async()=>{if(socket.readyState===WebSocket.CLOSED)return;const closed=once(socket,'close');socket.close();await closed;}};
}

test(process.env.FULL_OWNER_BACKUP_CAPTURE_FAILURE==='1'?'production HTTPS capture failure preserves the stopped app and closes its external controller':'fresh signed production HTTPS launcher captures and resumes all owners without replay',{
 skip:process.env.FULL_OWNER_BACKUP_TLS!=='1'||!python||!process.env.FULL_OWNER_RELEASE_ARCHIVE,timeout:600000,
},async t=>{
 const captureFailure=process.env.FULL_OWNER_BACKUP_CAPTURE_FAILURE==='1';
 const root=await realpath(await mkdtemp(join(tmpdir(),'backup-tls-'))),directory=join(root,'i'),workspace=join(root,'workspace'),home=join(root,'native'),appHome=join(root,'native-app'),controllerState=join(root,'backup'),staging=join(root,'native-staged');
 console.error('Retained production HTTPS backup fixture:',root);
 const git=await createHTTPSGitFixture(),assets=new Map();let child,supervisor,controller,beforePeer,afterPeer;
 const publisher=createServer((q,s)=>{const bytes=assets.get(q.url);s.writeHead(bytes?200:404);s.end(bytes??'missing');});await new Promise(r=>publisher.listen(0,'127.0.0.1',r));
 t.after(async()=>{
  try{
   await beforePeer?.close();await afterPeer?.close();controller?.close();
   if(supervisor){try{const state=await supervisor.service.inspect();if(state.state==='running'){await supervisor.service.stop({commandId:'fixture-shutdown',expected:state.identity});let stopped;for(let i=0;i<300;i++){stopped=await supervisor.service.receipt('fixture-shutdown');if(['stopped','refused','unknown'].includes(stopped?.status))break;await new Promise(r=>setTimeout(r,100));}assert.equal(stopped?.status,'stopped');}}finally{supervisor.close();}}
  }finally{
   try{if(child&&child.exitCode===null){child.kill('SIGTERM');for(let i=0;i<100&&child.exitCode===null;i++)await new Promise(r=>setTimeout(r,100));if(child.exitCode===null){child.stdout.destroy();child.unref();assert.fail('Owned controller shutdown unconfirmed; preserved '+root);}assert.equal(child.exitCode,0);}}
   finally{await writePrivate(join(root,'cleanup.json'),JSON.stringify({externalControllerExited:child?.exitCode===0}));publisher.closeAllConnections();await new Promise(r=>publisher.close(r));await git.close();}
  }
 });
 try{
 for(const path of [workspace,home,appHome,controllerState,staging,join(workspace,'.amplifier'),join(root,'events'),join(root,'checkpoints'),join(root,'sources')])await mkdir(path,{recursive:true,mode:0o700});
 const bundle=join(root,'bundle.yaml');await writePrivate(bundle,'bundle:\n  name: production-backup-qualification\n  version: 1.0.0\nproviders: []\n');await writePrivate(join(home,'settings.yaml'),'bundle:\n  app: []\n');
 const sid='21111111-1111-4111-8111-111111111111',history=join(home,'projects',workspace.replaceAll('/','-'),'sessions',sid);await mkdir(history,{recursive:true,mode:0o700});
 const canonical={'metadata.json':JSON.stringify({session_id:sid,working_dir:workspace,status:'idle',name:'Saved backup history'}),'transcript.jsonl':'{"role":"user","content":"Preserve this voice exchange","metadata":{"via":"call"}}\n','events.jsonl':'{"preserved":"original event"}\n'};
 for(const [name,body]of Object.entries(canonical))await writePrivate(join(history,name),body);
 const state=join(directory,'application'),nativeFile=join(root,'native.json'),compositionFile=join(root,'composition.json');
 const nativeEnv={PYTHONDONTWRITEBYTECODE:'1',AMPLIFIER_HOME:home,AMPLIFIER_WEB_HOME:appHome,AMPLIFIER_SESSION_STATE_HOME:join(root,'checkpoints'),AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH:join(root,'events'),AMPLIFIER_SOURCE_STORE:join(root,'sources')};
 const native={home,appHome,bundle,workerCommand:['/impossible/no-inference'],adminWorkspaceRoots:[workspace],adminMaintenance:true,maintenanceFullNative:true,maintenanceExternalWriters:'stopped',maintenanceFullNativeRoots:{checkpoint:nativeEnv.AMPLIFIER_SESSION_STATE_HOME,events:nativeEnv.AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH,sources:nativeEnv.AMPLIFIER_SOURCE_STORE,bundle},maintenanceRestoreRoots:{fixture:root},transferAuthorityDirectory:join(state,'capabilities/portability'),transferWorkspaceRoots:[root]};await writePrivate(nativeFile,JSON.stringify(native));
 const {publicKey,privateKey}=generateKeyPairSync('ed25519'),keys={fixture:publicKey.export({type:'spki',format:'pem'})};await writePrivate(join(root,'trust.json'),JSON.stringify(keys));const code='q'.repeat(64);await writePrivate(join(root,'access-code'),code);
 await writePrivate(join(root,'openssl.cnf'),'[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n');
 await exec('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',join(root,'key.pem'),'-out',join(root,'cert.pem'),'-config',join(root,'openssl.cnf')]);await chmod(join(root,'key.pem'),0o600);
 const origin='http://127.0.0.1:'+publisher.address().port,backendPort=await port(),tlsPort=await port(),tlsOrigin='https://127.0.0.1:'+tlsPort;
 const nativeRoots={'native-home':home,'app-home':appHome,...native.maintenanceFullNativeRoots,'workspace-settings':join(workspace,'.amplifier')};
 const candidate=join(root,'candidate');await mkdir(candidate);assert.equal(sha(await readFile(process.env.FULL_OWNER_RELEASE_ARCHIVE)),process.env.FULL_OWNER_RELEASE_SHA256);await exec('tar',['-xzf',process.env.FULL_OWNER_RELEASE_ARCHIVE,'-C',candidate]);const pkg=join(candidate,'package');
 // Overlay only the source under qualification before creating a new signed
 // fixture release. The production entrypoint remains the production file.
 for(const name of (await readdir(new URL('../src/',import.meta.url))).filter(n=>/\.(js|mjs)$/.test(n)))await copyFile(new URL('../src/'+name,import.meta.url),join(pkg,'src',name));
 const component=JSON.parse(await readFile(join(pkg,'components.json'))).components['@amplifier/unified-distribution-update-owner'];
 const initial={id:'production-backup-tls',version:'1.0.0',revision:git.first};
 const c={schema:'unified-full-owner-fresh-composition-v1',expectedOwners:[...OWNERS],authority:{installationId:randomUUID(),ownerId:'backup-tls-owner',dataScope:'backup-tls-scope',...Object.fromEntries(Object.entries({sourceDirectory:'source',claimDirectory:'claim',supervisorDirectory:'supervisor',supervisorDiscoveryFile:'supervisor.json',supervisorTokenFile:'supervisor-token',hostDiscoveryFile:'host-control.json',hostTokenFile:'host-token'}).map(([key,value])=>[key,join(directory,value)]))},
  review:{status:'approved',combinedLinuxReceiptSha256:sha('owned offline all-owner integration profile'),catalogWriterConcurrency:'qualified',nativeModeProjection:'qualified',operationsPortabilityResolver:'qualified'},
  release:{initial,entrypoint:'src/full-owner-launcher.mjs',trustedKeysFile:join(root,'trust.json'),updateOwnerVersion:component.version,updateOwnerRevision:component.revision,channelUrl:origin+'/channel.json',accessScope:'owned-backup-fixture',allowedArtifactOrigins:[origin],allowLoopbackHttp:true},
  receiptDirectory:join(directory,'receipts'),sourcePolicy:[{repository:git.repository,ref:'main'}],access:{origin:tlsOrigin,host:'127.0.0.1',port:tlsPort,backendPort,keyFile:join(root,'key.pem'),certFile:join(root,'cert.pem'),codeFile:join(root,'access-code')},
  storageInventory:{externalCoverage:{'native-admin':'declared','native-message-metadata':'declared',portability:'declared','manual-preview-ingress':'declared'},externalRoots:[{id:'ingress',path:join(directory,'ingress'),ownerIds:['manual-preview-ingress'],coverage:'authoritative',capture:'tree'},...Object.entries(nativeRoots).map(([id,path])=>({id,path,ownerIds:['native-admin','native-message-metadata'],coverage:'authoritative',capture:'native-artifact'}))],nativeCapturePlans:[{engineId:'amplifier',rootIds:Object.keys(nativeRoots)}]},
  application:{account:'backup-tls-fixture',stateDirectory:state,webDirectory:join(pkg,'web'),defaultWorkspace:workspace,allowedWorkspaceRoots:[root],host:{managedSessionRoot:join(state,'managed')},manualIngress:{stateDirectory:join(directory,'ingress')},gateway:{host:'127.0.0.1',port:backendPort,origin:tlsOrigin},engines:[{id:'amplifier',command:python,args:['-I','-B','-m','amplifier_acp','--config',nativeFile],env:nativeEnv}],nativeAdmin:{engine:'amplifier'},maintenance:{},applicationUpdates:{},conversationPresentation:{},recovery:{authorization:'local-account',credentials:true},installationBackup:{discoveryFile:join(controllerState,'control.json')},historyImport:{},historyCleanup:true,managedFiles:true,
   portability:{python,engines:['amplifier'],stageDir:join(state,'transfer-stage'),exchangeDir:join(state,'transfer-exchange')},...Object.fromEntries(['operations','notifications','diagnostics','coordination','recall','publishing','worktrees','feedback','workspaces','mcp','media'].map(k=>[k,{python,env:{PYTHONDONTWRITEBYTECODE:'1'}}])),catalogProcess:{command:python,args:['-I','-B','-m','amplifier_session_catalog','serve','--db',join(state,'catalog.sqlite'),'--home',home,'--app-home',appHome,'--workspace',workspace,'--scan-interval','0','--workspace-check-interval','0'],env:{PYTHONDONTWRITEBYTECODE:'1'}}},
 };await writePrivate(compositionFile,JSON.stringify(c));
 const census=JSON.parse((await exec(python,['-I','-B','-c','import json,sys,importlib.util;print(json.dumps(dict(prefix=sys.prefix,base=sys.base_prefix,paths=sys.path,modules={m:importlib.util.find_spec(m).origin for m in ["amplifier_acp.__main__","amplifier_session_catalog.__main__","amplifier_unified_media.worker"]})))'])).stdout);
 const trees=[{id:'environment',root:census.prefix},...(census.base===census.prefix?[]:[{id:'interpreter',root:census.base}])],pythonSlot={tree:'environment',path:relative(census.prefix,python)};
 const member=path=>{const tree=trees.find(t=>path.startsWith(t.root+'/'));assert.ok(tree,path);return {tree:tree.id,path:relative(tree.root,path)};};
 const qualification={schema:'unified-python-runtime-qualification-v1',profile:'native-catalog-media-v1',python:pythonSlot,launches:[['native','amplifier_acp','amplifier_acp.__main__'],['catalog','amplifier_session_catalog','amplifier_session_catalog.__main__'],['media','amplifier_unified_media.worker','amplifier_unified_media.worker']].map(([role,module,entry])=>({role,module,flags:['-I','-B'],moduleFile:member(census.modules[entry]),importPaths:census.paths,noRuntimeWrites:true,editableInstalls:false}))};
 await mkdir(join(pkg,'release-inputs'),{recursive:true});const qualificationBytes=Buffer.from(JSON.stringify(qualification));await writeFile(join(pkg,'release-inputs/qualification.json'),qualificationBytes);
 for(const [name,inventory]of [['mcp',inventoryMcpRuntime],['owner',inventoryPythonRuntime]])await writeFile(join(pkg,'release-inputs/'+name+'.json'),JSON.stringify(await inventory({trees,python:pythonSlot,qualificationReceiptSha256:sha(qualificationBytes)})));
 await writeFile(join(pkg,'release-inputs/native.json'),JSON.stringify({...native,runtimeImmutable:true}));
 await writeFile(join(pkg,'release-runtime.json'),JSON.stringify({schema:'unified-release-runtime-v3',release:initial,baseConfigurationSha256:sha(await readFile(compositionFile)),webDirectory:'web',mcpRuntime:'release-inputs/mcp.json',nativeLauncher:{engineId:'amplifier',configuration:'release-inputs/native.json',baseConfigurationSha256:sha(await readFile(nativeFile)),grants:{runtimeImmutable:true},qualificationReceiptSha256:sha(qualificationBytes)},ownerRuntime:{profile:'native-catalog-media-v1',engineId:'amplifier',manifest:'release-inputs/owner.json',qualificationReceipt:'release-inputs/qualification.json',mediaMode:'installed'},ownerCensus:{profile:'native-message-metadata-v1'}}));
 const members=await files(pkg),components=[];for(const f of members.filter(r=>r.path==='package.json'||/\bnode_modules\/(?:@[^/]+\/)?[^/]+\/package.json$/.test(r.path))){const p=JSON.parse(await readFile(join(pkg,f.path)));components.push({name:p.name,version:p.version,root:f.path==='package.json'?'':dirname(f.path),repository:git.repository,ref:'main',revision:git.first});}
 await exec('tar',['-czf',join(root,'release.tgz'),'-C',candidate,'package'],{env:{...process.env,COPYFILE_DISABLE:'1'}});const archive=await readFile(join(root,'release.tgz'));
 const release={identity:{...initial,digest:''},entrypoint:c.release.entrypoint,platform:process.platform,arch:process.arch,files:members,components,artifact:{url:origin+'/release.tgz',sha256:sha(archive),bytes:archive.length}};release.identity.digest=releaseDigest(release);assets.set('/release.tgz',archive);
 const payload=Buffer.from(JSON.stringify({schema:'distribution-channel-v1',expiresAt:Date.now()+3600000,recommendedId:initial.id,releases:[release]}));assets.set('/channel.json',Buffer.from(JSON.stringify({schema:'distribution-signed-channel-v1',keyId:'fixture',payload:payload.toString('base64'),signature:sign(null,payload,privateKey).toString('base64')})));
 const adapterModule=join(root,'backup-adapters.mjs'),signedNative=join(directory,'releases/releases',release.identity.digest,'package/release-inputs/native.json');
 const adapterBody=`import assert from 'node:assert/strict';import {spawn} from 'node:child_process';import {createInterface} from 'node:readline';import {readFile,open,lstat,rm} from 'node:fs/promises';import {join} from 'node:path';import {createNativeCoherentCaptureAdapter} from ${JSON.stringify(pathToFileURL(join(pkg,'src/native-capture.js')).href)};const python=${JSON.stringify(python)},root=${JSON.stringify(root)};${connect.toString()}
export async function createBackupAdapters({getSupervisor}){return {readCompositionInventory:async()=>{const current=await getSupervisor().service.inspect();return JSON.parse(await readFile(join(${JSON.stringify(directory)},'receipts',current.identity.instanceId+'-storage.json')));},acquireWriterExclusion:async context=>{if(${captureFailure})throw Error('fixture_capture_refused');const path=join(root,'operator-retirement.lock'),fd=await open(path,'wx',0o600);let held=true;return {evidence:{policy:'operator-reviewed-retirement',noncooperatingWriters:'not-independently-observed',reviewDigest:${JSON.stringify(sha('all fixture application writers are supervised and stopped; no external user data'))}},assertHeld:async()=>{assert.ok(held);await lstat(path);return {active:true,...context,leaseId:'owned-fixture-retirement'};},release:async()=>{held=false;await fd.close();await rm(path);return {released:true};}};},nativeCaptures:[createNativeCoherentCaptureAdapter({engineId:'amplifier',cwd:${JSON.stringify(workspace)},stagingDirectory:${JSON.stringify(staging)},connect:async()=>connect(${JSON.stringify(signedNative)},${JSON.stringify(nativeEnv)})})]};}`;
 await writePrivate(adapterModule,adapterBody);const backupFile=join(root,'backup.json');await writePrivate(backupFile,JSON.stringify({schema:'unified-installation-backup-service-v1',stateDirectory:controllerState,adapterModule,adapterSha256:sha(adapterBody)}));
 const inputFile=join(root,'install.json');await writePrivate(inputFile,JSON.stringify({schema:'unified-full-owner-installation-v1',directory,compositionFile}));
 const log=await open(join(root,'installer.stderr.log'),'wx',0o600);
 child=spawn(process.execPath,[fileURLToPath(new URL('../src/full-owner-install-cli.mjs',import.meta.url)),'--config',inputFile,'--backup-config',backupFile],{env:{...process.env,...git.env},stdio:['ignore','pipe',log.fd]});await log.close();
 const cliReady=await new Promise((resolve,reject)=>{const lines=createInterface({input:child.stdout}),timer=setTimeout(()=>reject(Error('Fresh installer readiness timed out; retained '+root)),180000);child.once('exit',code=>{clearTimeout(timer);reject(Error('Fresh installer exited '+code+'; retained '+root));});lines.on('line',line=>{try{const row=JSON.parse(line);if(row.ready){clearTimeout(timer);lines.close();resolve(row);}}catch{}});});assert.equal(cliReady.backupConfigured,true);
 supervisor=connectSupervisorFileLazy(join(directory,'supervisor.json'));controller=connectInstallationBackupControl({discoveryFile:c.application.installationBackup.discoveryFile,installationId:c.authority.installationId,dataScope:c.authority.dataScope});
 // Seed a private browser draft only after installation created its namespace.
 // It has no session action and cannot trigger execution.
 const drafts=join(state,'saved-draft-fixture.sqlite'),db=new DatabaseSync(drafts);db.exec('CREATE TABLE drafts(id TEXT PRIMARY KEY, body TEXT)');db.prepare('INSERT INTO drafts VALUES(?,?)').run(sid,'Keep this unsent cobalt draft');db.close();const draftBytes=await readFile(drafts);
 const ca=await readFile(join(root,'cert.pem')),originalCookie=await login(tlsOrigin,ca,code);beforePeer=await peer(tlsOrigin,ca,originalCookie);
 const reviewed=await beforePeer.action('review'),args={reviewId:reviewed.id,reviewDigest:reviewed.digest,privateContentReviewed:true,credentialsReviewed:true};assert.equal(reviewed.owners.length,21);
 const submitted=await beforePeer.action('create',args,'backup-production-tls');assert.equal(submitted.receipt.status,'accepted');
 let completed;for(let i=0;i<1200;i++){completed=await controller.receipt('backup-production-tls');if(['succeeded','failed','unknown'].includes(completed.status))break;await new Promise(r=>setTimeout(r,100));}if(captureFailure){
  assert.equal(completed.status,'unknown');assert.equal(completed.error,'fixture_capture_refused');assert.equal(completed.stopped.status,'stopped');assert.equal(completed.stopped.qualifiedOwners.length,21);
  assert.equal(completed.resumed,undefined);assert.equal(completed.archive,undefined);
  const savedConfig=JSON.parse(await readFile(join(directory,'supervisor-configuration.json')));
  if(process.platform==='linux')assert.equal((await exec('systemctl',['--user','show',savedConfig.serviceLifecycle.platform.unit,'-p','ActiveState','--value'])).stdout.trim(),'inactive');
  assert.equal((await controller.receipt('backup-production-tls')).status,'unknown');
  supervisor.close();supervisor=null; // The confirmed stopped app is preserved.
  for(const [name,body]of Object.entries(canonical))assert.equal(sha(await readFile(join(history,name))),sha(body));assert.deepEqual(await readFile(drafts),draftBytes);
  await writePrivate(join(root,'acceptance.json'),JSON.stringify({schema:'production-full-owner-tls-backup-failure-v1',owners:21,productionEntrypoint:true,freshExternalCLI:true,httpsAccess:true,clientSubmission:true,injectedCaptureFailure:true,originalUnknownReceiptPreserved:true,appConfirmedStopped:true,serviceResumed:false,workReplayed:false,canonicalHistoryUnchanged:true,privateDraftBytesUnchanged:true,completed},null,2));
  return;
 }
 assert.equal(completed.status,'succeeded',JSON.stringify(completed));
 const captured=await inspectInstallationArchive(join(controllerState,'commands/backup-production-tls/backup.unified'));assert.equal(captured.manifest.coverage.completeProduct,true);assert.equal(captured.manifest.proof.qualifiedOwners.length,21);
 const launchIds=captured.manifest.installedEvidence.launchInputRootIds;assert.equal(launchIds.length,5);
 for(const id of launchIds){const r=captured.manifest.inventory.roots.find(r=>r.id===id),entry=captured.manifest.entries.find(e=>e.name==='roots/'+id);assert.equal(entry.sha256,sha(await readFile(r.path)));}
 const after=await supervisor.service.inspect();assert.equal(after.state,'running');assert.notEqual(after.identity.instanceId,reviewed.expected.instanceId);
 afterPeer=await peer(tlsOrigin,ca,originalCookie);const original=(await afterPeer.action('receipt',{commandId:'backup-production-tls'})).receipt;assert.equal(original.status,'succeeded');
 // The host rejects an already admitted mutation before dispatch. Recovery is
 // the read-only receipt path; neither layer repeats the original capture.
 await assert.rejects(afterPeer.action('create',args,'backup-production-tls'),/Command already admitted: completed/);
 assert.equal((await afterPeer.action('receipt',{commandId:'backup-production-tls'})).receipt.status,'succeeded');assert.deepEqual((await supervisor.service.inspect()).identity,after.identity);
 const chunk=await afterPeer.request('resourceRead',{channel:'ahp-root://',uri:original.archive.resourceUri+'&offset=0&maxBytes=127',encoding:'base64'});assert.equal(Buffer.from(chunk.data,'base64').length,127);
 for(const [name,body]of Object.entries(canonical))assert.equal(sha(await readFile(join(history,name))),sha(body));assert.deepEqual(await readFile(drafts),draftBytes);
 await writePrivate(join(root,'acceptance.json'),JSON.stringify({schema:'production-full-owner-tls-backup-v1',owners:21,productionEntrypoint:true,freshExternalCLI:true,httpsAccess:true,clientSubmission:true,ingressDrained:true,serviceResumed:true,originalReceiptAfterReconnect:true,boundedDownload:true,duplicateSubmissionReplayed:false,canonicalHistoryUnchanged:true,privateDraftBytesUnchanged:true,releaseSha256:sha(archive),completed,limits:['Disposable signed offline fixture; no real account or physical voice acceptance.','External-writer retirement is an explicit operator assumption, not an OS-level exclusion proof.']},null,2));
 }catch(error){console.error('Retained backup rehearsal failure:',error.stack);throw error;}
});
