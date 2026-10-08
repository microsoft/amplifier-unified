import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,readdir,lstat,realpath,copyFile,unlink} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {tmpdir,homedir} from 'node:os';
import {pathToFileURL} from 'node:url';
import {createHash,generateKeyPairSync,sign,randomBytes,randomUUID} from 'node:crypto';
import {createServer} from 'node:http';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {once} from 'node:events';
import {createRequire} from 'node:module';
import {createHTTPSGitFixture} from './https-git-fixture.mjs';

const execute=promisify(execFile),hash=value=>createHash('sha256').update(value).digest('hex');
const entry=process.env.UNIFIED_DISTRIBUTION_ENTRY,sourceArchive=process.env.UNIFIED_DISTRIBUTION_ARCHIVE;
const require=createRequire(entry?pathToFileURL(entry):import.meta.url);
const api=await import(pathToFileURL(require.resolve('@amplifier/unified-distribution-update-owner')));
const {WebSocket}=require('ws');
const python=process.env.AMPLIFIER_ACP_PYTHON,ownerPython=process.env.UNIFIED_OWNERS_PYTHON,catalogPython=process.env.UNIFIED_CATALOG_PYTHON,provider=process.env.RECOVERY_NATIVE_PROVIDER;
const mode=process.env.FULL_OWNER_SERVICE_MODE??'owned',manual=mode==='manual-systemd';
const enabled=process.env.FULL_OWNER_SERVICE==='1';
const expectedOwners=['portability','capability:attachments','workspaces','native-admin','native-message-metadata','application-updates','capability:voice','capability:connectors','notifications','diagnostics','capability:observations','capability:coordination','capability:worktrees','capability:publishing','capability:recall','capability:feedback','recovery','history-import','history-cleanup','managed-files','manual-preview-ingress'];
async function until(read){for(let n=0;n<900;n++){const value=await read();if(value)return value;await new Promise(r=>setTimeout(r,50));}throw Error('Owned fixture did not become ready');}
async function jsonReady(path){return until(async()=>{try{return JSON.parse(await readFile(path,'utf8'));}catch{return null;}});}
async function inventory(root){const files=[];async function visit(prefix=''){for(const name of await readdir(join(root,prefix))){const path=prefix?prefix+'/'+name:name,info=await lstat(join(root,path));if(info.isDirectory())await visit(path);else{assert.ok(info.isFile());const bytes=await readFile(join(root,path));files.push({path,bytes:bytes.length,sha256:hash(bytes),mode:info.mode&0o777});}}}await visit();return files.sort((a,b)=>a.path.localeCompare(b.path));}
async function peer(url){const socket=new WebSocket(url.replace(/^http/,'ws')+'/ahp',{origin:url});await once(socket,'open');let id=0;const pending=new Map();socket.on('message',raw=>{const m=JSON.parse(raw),p=pending.get(m.id);if(p){pending.delete(m.id);clearTimeout(p.timer);m.error?p.reject(Error(m.error.message)):p.resolve(m.result);}});const request=(method,params)=>new Promise((resolve,reject)=>{const key=++id,timer=setTimeout(()=>reject(Error('RPC timeout '+method)),30000);pending.set(key,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id:key,method,params}));});await request('initialize',{channel:'ahp-root://',clientId:'full-service-fixture',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});return {request,close:async()=>{const closed=once(socket,'close');socket.close();await closed;}};}

test('installed signed twenty-owner '+mode+' service preserves state and settles exact owner fences',
 {skip:!enabled,timeout:300000},async t=>{
 assert.ok(entry&&sourceArchive&&python&&ownerPython&&catalogPython&&provider,'Explicit installed distribution archive/entry, native/catalog/owner interpreters and provider source are required');
 assert.ok(['owned','manual-systemd'].includes(mode));if(manual)assert.equal(process.platform,'linux','Manual systemd acceptance requires Linux; never substitute a process observer');
 const root=await realpath(await mkdtemp(join(tmpdir(),'u20-'))),git=await createHTTPSGitFixture(),assets=new Map();
 let supervisor,client,unitFile,sourceStarted=false,publisher;const commands=[];
 const ctl=(...args)=>execute('systemctl',['--user',...args],{maxBuffer:1024*1024});
 try{
  const web=join(root,'web'),workspace=join(root,'workspace'),home=join(root,'native-home'),appHome=join(root,'native-app'),state=join(root,'application');
  for(const path of [web,workspace,home,appHome,state])await mkdir(path);
  const preparedIngress=await api.createManualIngressGate({directory:join(root,'ingress'),id:'manual-preview-ingress'});preparedIngress.close();
  await writeFile(join(web,'index.html'),'<!doctype html><title>Owned service qualification</title>');await writeFile(join(home,'settings.yaml'),'bundle:\n  app: []\n');
  const context=(await execute(python,['-I','-c','import pathlib,importlib.util;print(pathlib.Path(importlib.util.find_spec("amplifier_module_context_simple").origin).parent)'])).stdout.trim();
  const bundle=join(root,'fixture.yaml');await writeFile(bundle,'bundle:\n  name: full-owner-service\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: '+context+'\nproviders:\n  - module: provider-fixture\n    source: '+provider+'\n');
  const nativeFile=join(root,'native.json'),managed=join(workspace,'.managed'),native={home,appHome,bundle,managedSessionRoots:[managed],adminWorkspaceRoots:[workspace],adminMaintenance:true,transferAuthorityDirectory:join(state,'capabilities/portability'),transferWorkspaceRoots:[workspace],maintenanceExternalWriters:'foundation-cooperative'};
  await writeFile(nativeFile,JSON.stringify(native),{mode:0o600});
  const savedId=randomUUID(),savedDirectory=join(home,'projects',workspace.replaceAll('/','-'),'sessions',savedId);
  await execute(python,['-I','-c',"from pathlib import Path;import sys;from amplifier_foundation.session.history import SessionHistoryStore;SessionHistoryStore(Path(sys.argv[1]),session_id=sys.argv[2]).save([{'role':'user','content':'Preserved canonical pre-service history'}],{'session_id':sys.argv[2],'working_dir':sys.argv[3],'status':'idle'})",savedDirectory,savedId,workspace]);
  const historyFile=join(savedDirectory,'transcript.jsonl'),history=await readFile(historyFile);
  const application={account:'full-service-fixture',webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],stateDirectory:state,host:{managedSessionRoot:managed},manualIngress:{stateDirectory:join(root,'ingress')},
   engines:[{id:'amplifier',command:python,args:['-I','-m','amplifier_acp','--config',nativeFile],env:{AMPLIFIER_HOME:home,AMPLIFIER_WEB_HOME:appHome,AMPLIFIER_SESSION_STATE_HOME:join(root,'writers')}}],
   nativeAdmin:{engine:'amplifier'},maintenance:{},recovery:{authorization:'local-account'},historyImport:{},historyCleanup:true,managedFiles:true,applicationUpdates:true,portability:{python:ownerPython,engines:['amplifier'],stageDir:join(workspace,'stages'),exchangeDir:join(workspace,'exchange')},
   ...Object.fromEntries(['operations','notifications','diagnostics','coordination','recall','publishing','worktrees','feedback','workspaces','mcp','media'].map(name=>[name,{python:ownerPython}])),
   catalogProcess:{command:catalogPython,args:['-I','-m','amplifier_session_catalog','serve','--db',join(root,'catalog.sqlite'),'--home',home,'--app-home',appHome,'--workspace',workspace,'--scan-on-start','--scan-interval','0','--workspace-check-interval','0']}};
  const candidate=join(root,'candidate');await mkdir(candidate);await execute('tar',['-xzf',sourceArchive,'-C',candidate]);
  for(const name of ['full-owner-service-fixture.mjs','counted-ingress-fixture.mjs'])await copyFile(new URL(name,import.meta.url),join(candidate,'package/src',name));
  const files=await inventory(join(candidate,'package')),components=[];
  for(const file of files.filter(row=>row.path==='package.json'||/\bnode_modules\/(?:@[^/]+\/)?[^/]+\/package.json$/.test(row.path))){const pkg=JSON.parse(await readFile(join(candidate,'package',file.path),'utf8'));components.push({name:pkg.name,version:pkg.version,root:file.path==='package.json'?'':dirname(file.path),repository:git.repository,ref:'main',revision:git.first});}
  const archive=join(root,'qualified.tgz');await execute('tar',['-czf',archive,'-C',candidate,'package'],{env:{...process.env,COPYFILE_DISABLE:'1'}});const bytes=await readFile(archive);
  publisher=createServer((req,res)=>{const body=assets.get(req.url);if(!body){res.writeHead(404);res.end();}else res.end(body);});await new Promise(r=>publisher.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+publisher.address().port;
  const release={identity:{id:'full-owner-fixture',version:'1.0.0',revision:git.first,digest:''},entrypoint:'src/full-owner-service-fixture.mjs',platform:process.platform,arch:process.arch,files,components,artifact:{url:origin+'/release.tgz',sha256:hash(bytes),bytes:bytes.length}};release.identity.digest=api.releaseDigest(release);assets.set('/release.tgz',bytes);
  const {publicKey,privateKey}=generateKeyPairSync('ed25519'),keys={fixture:publicKey.export({type:'spki',format:'pem'})},payload=Buffer.from(JSON.stringify({schema:'distribution-channel-v1',expiresAt:Date.now()+3600000,recommendedId:release.identity.id,releases:[release]}));
  assets.set('/channel.json',Buffer.from(JSON.stringify({schema:'distribution-signed-channel-v1',keyId:'fixture',payload:payload.toString('base64'),signature:sign(null,payload,privateKey).toString('base64')})));
  const {createGitSourceResolver}=await import(pathToFileURL(entry));
  const resolveSources=createGitSourceResolver({sources:[{repository:git.repository,ref:'main'}],env:git.env});
  const hostFile=join(root,'host.json'),hostTokenFile=join(root,'host-token'),hostToken=randomBytes(32).toString('hex'),supervisorFile=join(root,'supervisor.json'),configuration=join(root,'fixture.json');
  await writeFile(hostTokenFile,hostToken,{mode:0o600});
  const releaseOptions={directory:join(root,'releases'),channelUrl:origin+'/channel.json',trustedKeys:keys,accessScope:'owned-fixture',allowedArtifactOrigins:[origin],allowLoopbackHttp:true,resolveSources,launchArgs:[configuration],launchEnv:{}};
  const adapter=new api.SignedReleaseAdapter(releaseOptions),target=await adapter.prepare(release.identity,{commandId:'prepare',signal:new AbortController().signal});
  const unit='amplifier-full-owner-'+root.split('/').at(-1)+'.service',sourceDirectory=join(root,'source'),bindings=[{id:'application',kind:'directory',path:state},{id:'ingress',kind:'directory',path:join(root,'ingress')},{id:'native',kind:'directory',path:home},{id:'native-app',kind:'directory',path:appHome},{id:'workspace',kind:'directory',path:workspace},{id:'configuration',kind:'file',path:configuration},{id:'native-configuration',kind:'file',path:nativeFile}];
  const common={root,application,keys,target,bindings,sourceDirectory,unit,observerPython:process.env.SYSTEMD_OBSERVER_PYTHON??'/usr/bin/python3',installationId:'full-owner-fixture',ownerId:'full-owner-owner',hostFile,hostTokenFile,hostToken,supervisorFile,historyFile,expectedOwners};
  await writeFile(configuration,JSON.stringify(common),{mode:0o600});
  const host=api.connectHostControlFile(hostFile,'full-owner-scope');let claimed=false;
  const supervisorConfig={schema:'distribution-supervisor-v1',dataDirectory:join(root,'supervisor'),dataScope:'full-owner-scope',tokenFile:join(root,'supervisor-token'),discoveryFile:supervisorFile,initial:target,release:releaseOptions,serviceLifecycle:{installationId:common.installationId,ownerId:common.ownerId}};
  const ports={resolveSources,inspect:()=>host.inspect(),admitRestart:host.admitRestart,reconcileAdmission:host.reconcileAdmission,service:host.service,...(!manual?{initialProvisioning:{claim:async request=>{assert.equal(claimed,false);claimed=true;return {kind:'pristine-installation',installationId:common.installationId,commandId:request.commandId,instanceId:request.instanceId,dataScope:request.dataScope,targetDigest:request.target.identity.digest};}}}:{})};
  supervisor=await api.runSupervisor(supervisorConfig,{startInitial:!manual,ports});
  let initial,ready;
  if(manual){
   const launch=await adapter.resolveLaunch(target),unitDirectory=join(homedir(),'.config/systemd/user');await mkdir(unitDirectory,{recursive:true});unitFile=join(unitDirectory,unit);
   const sourceInstance=randomUUID(),env={...launch.env,UNIFIED_MANUAL_SOURCE:'1',AMPLIFIER_DISTRIBUTION_INSTANCE_ID:sourceInstance,AMPLIFIER_DISTRIBUTION_DATA_SCOPE:'full-owner-scope'};
   const quote=s=>'"'+String(s).replaceAll('\\','\\\\').replaceAll('"','\\"').replaceAll('%','%%')+'"';
   const command=['/usr/bin/env','-i','PATH=/usr/bin:/bin','XDG_RUNTIME_DIR=/run/user/'+process.getuid(),...Object.entries(env).filter(([,v])=>v!==undefined).map(([k,v])=>k+'='+v),launch.command,...launch.args].map(quote).join(' ');
   await writeFile(unitFile,'[Unit]\nDescription=Owned full product handoff fixture\n[Service]\nType=simple\nExecStart='+command+'\nRestart=no\nKillMode=control-group\nTimeoutStopSec=infinity\nSendSIGKILL=no\nUMask=0077\n[Install]\nWantedBy=default.target\n',{flag:'wx',mode:0o600});
   await ctl('daemon-reload');await ctl('start',unit);sourceStarted=true;ready=await jsonReady(join(root,'source-ready.json'));initial=ready.expected;
  }else{ready=await jsonReady(join(root,'destination-ready.json'));initial=(await supervisor.service.inspect()).identity;}
  assert.deepEqual([...ready.owners].sort(),[...expectedOwners].sort());assert.equal(ready.expected.releaseDigest,release.identity.digest);
  client=await peer(ready.url);
  // A genuine initialized native session and all native administration owners
  // participate. No paid/provider request is sent.
  await client.request('createSession',{channel:'ahp-session:/'+randomUUID(),provider:'amplifier',workingDirectories:[pathToFileURL(workspace).href]});
  if(!manual){await supervisor.service.stop({commandId:'busy-network',expected:initial});const busy=await supervisor.service.waitFor('busy-network');assert.equal(busy.status,'refused',JSON.stringify(busy));commands.push(busy);}
  else{const busy=await host.service.admitServiceStop({commandId:'busy-network',expected:initial});assert.equal(busy.admitted,false);assert.equal(busy.executed,false);commands.push(busy);}
  await client.close();client=null;await new Promise(r=>setTimeout(r,50));
  let result;
  if(manual){
   const observer=api.createLinuxSystemdSourceObserver({unit,python:common.observerPython}),source=api.createManualSystemdHandoffSource({sourceDirectory,claimDirectory:join(root,'claim'),bindings,observer});
   await api.launchExistingStateHandoff({destination:supervisor.service,source,bindings,command:{commandId:'handoff',stoppedCommandId:'source-stop',expected:initial,target:target.identity,participantIds:expectedOwners}});
   result=await supervisor.service.waitFor('handoff');assert.equal(result.status,'ready',JSON.stringify(result));assert.equal(result.admissionSettlement.state,'settled');
   await ctl('start',unit).catch(()=>{});await until(async()=>Number((await ctl('show','-p','MainPID','--value',unit)).stdout.trim())===0);assert.match((await ctl('show','-p','Result','--value',unit)).stdout,/exit-code/);
  }else{
   await supervisor.service.stop({commandId:'stop',expected:initial});const stopped=await supervisor.service.waitFor('stop');assert.equal(stopped.status,'stopped',JSON.stringify(stopped));assert.deepEqual([...stopped.qualifiedOwners].sort(),[...expectedOwners].sort());commands.push(stopped);
   await supervisor.service.resume({commandId:'resume',expected:initial,stoppedCommandId:'stop'});result=await supervisor.service.waitFor('resume');assert.equal(result.status,'ready',JSON.stringify(result));assert.equal(result.admissionSettlement.state,'settled');
  }
  commands.push(result);assert.notEqual(result.observed.instanceId,initial.instanceId);assert.equal(result.observed.releaseDigest,initial.releaseDigest);assert.equal((await host.service.inspectServiceLifecycle()).intakeClosed,false);
  assert.equal(hash(await readFile(historyFile)),hash(history));
  const after=await jsonReady(join(root,'destination-ready.json'));assert.equal(after.expected.instanceId,result.observed.instanceId);assert.equal(after.history,history.toString());
  assert.equal((await fetch(after.url+'/health')).status,200);
  await supervisor.service.stop({commandId:'final-stop',expected:result.observed});const stopped=await supervisor.service.waitFor('final-stop');assert.equal(stopped.status,'stopped',JSON.stringify(stopped));assert.deepEqual([...stopped.qualifiedOwners].sort(),[...expectedOwners].sort());commands.push(stopped);
  const receipt={schema:'full-owner-service-acceptance-v1',platform:process.platform,node:process.version,mode,root,release:target.identity,archiveSha256:hash(bytes),configuredOwners:expectedOwners,signedRuntime:true,actualNativeInitialization:true,inference:false,heldNetworkRefusal:true,authenticatedServiceRelease:true,sourceKernelExit:manual,manualSourceRelaunchRefused:manual,canonicalHistoryPreserved:true,commands};
  await writeFile(join(root,'acceptance.json'),JSON.stringify(receipt,null,2));console.log(JSON.stringify({receipt:join(root,'acceptance.json'),mode,owners:expectedOwners.length}));
  if(process.env.FULL_OWNER_SERVICE_RECEIPT)await copyFile(join(root,'acceptance.json'),process.env.FULL_OWNER_SERVICE_RECEIPT);
 }finally{
  await client?.close().catch(()=>{});
  if(supervisor){try{const status=await supervisor.service.inspect();if(status.state==='running'){await supervisor.service.stop({commandId:'fixture-cleanup',expected:status.identity});await supervisor.service.waitFor('fixture-cleanup');}await supervisor.close();}catch(error){console.error('Preserved unresolved fixture:',root,String(error));}}
  if(unitFile){if(sourceStarted)await ctl('stop',unitFile.split('/').at(-1)).catch(()=>{});await unlink(unitFile);await ctl('daemon-reload');}
  publisher?.closeAllConnections();if(publisher)await new Promise(r=>publisher.close(r));await git.close();
  console.log('Retained isolated service evidence:',root);
 }
});
