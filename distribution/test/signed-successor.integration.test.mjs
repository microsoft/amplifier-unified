import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,readdir,lstat,realpath,copyFile,cp,chmod,symlink,unlink} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL} from 'node:url';
import {createHash,generateKeyPairSync,sign,randomBytes,randomUUID} from 'node:crypto';
import {createServer} from 'node:http';
import {request} from 'node:https';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {once} from 'node:events';
import {WebSocket} from 'ws';
import * as api from '@amplifier/unified-distribution-update-owner';
import {createGitSourceResolver} from '../src/source-tracking.js';
import {OWNERS} from '../src/validate-config.mjs';
import {inventoryMcpRuntime,inventoryPythonRuntime} from '../src/release-runtime.mjs';
import {createHTTPSGitFixture} from './https-git-fixture.mjs';

const execute=promisify(execFile),hash=b=>createHash('sha256').update(b).digest('hex');
const basePackage=process.env.SUCCESSOR_BASE_PACKAGE,basePython=process.env.SUCCESSOR_BASE_PYTHON;
assert.ok([undefined,'legacy','current'].includes(process.env.SIGNED_SUCCESSOR_PROFILE),'Unknown signed successor profile');
const currentProfile=process.env.SIGNED_SUCCESSOR_PROFILE==='current';
const enabled=process.env.SIGNED_SUCCESSOR_ACCEPTANCE==='1';
async function until(read){for(let n=0;n<1200;n++){const v=await read();if(v)return v;await new Promise(r=>setTimeout(r,50));}throw Error('Isolated successor did not settle');}
async function inventory(root){const files=[];async function visit(prefix=''){for(const name of await readdir(join(root,prefix))){const path=prefix?prefix+'/'+name:name,info=await lstat(join(root,path));if(info.isDirectory())await visit(path);else{assert.ok(info.isFile());const b=await readFile(join(root,path));files.push({path,bytes:b.length,sha256:hash(b),mode:info.mode&0o777});}}}await visit();return files.sort((a,b)=>a.path.localeCompare(b.path));}
async function sealCopy(root){for(const name of await readdir(root)){const path=join(root,name),stat=await lstat(path);if(stat.isSymbolicLink())continue;if(stat.isDirectory())await sealCopy(path);await chmod(path,(stat.mode&0o777)&~0o022);}await chmod(root,0o755);}
async function unusedPort(){const s=createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;}
async function https(url,ca,{cookie,body}={}){return new Promise((resolve,reject)=>{const u=new URL(url),req=request(u,{ca,method:body?'POST':'GET',headers:{Origin:u.origin,...(body?{'Content-Type':'application/json'}:{}),...(cookie?{Cookie:cookie}:{})}},res=>{const chunks=[];res.on('data',b=>chunks.push(b));res.on('end',()=>resolve({status:res.statusCode,text:Buffer.concat(chunks).toString(),cookie:res.headers['set-cookie']?.[0]?.split(';')[0]}));});req.on('error',reject);req.end(body?JSON.stringify(body):undefined);});}
async function peer(origin,ca,cookie){const socket=new WebSocket(origin.replace('https:','wss:')+'/ahp',{origin,ca,headers:{Cookie:cookie}});await once(socket,'open');let next=0;const pending=new Map();socket.on('message',raw=>{const m=JSON.parse(raw),p=pending.get(m.id);if(p){pending.delete(m.id);clearTimeout(p.timer);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}});const request=(method,params)=>new Promise((resolve,reject)=>{const id=++next,timer=setTimeout(()=>reject(Error('RPC timeout '+method)),90000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});await request('initialize',{channel:'ahp-root://',clientId:'successor-fixture',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});return {request,async close(){const closed=once(socket,'close');socket.close();await closed;}};}
test(currentProfile?'current signed 21-owner composition updates and rolls back through the same supervisor':'unchanged old composition activates signed MCP/web successor and rolls back through the same supervisor',
 {skip:!enabled,timeout:600000},async()=>{
 assert.ok(basePackage&&basePython,'Explicit qualified baseline package and Python required');
 const root=await realpath(await mkdtemp(join(tmpdir(),'u-next-'))),git=await createHTTPSGitFixture(),assets=new Map();
 const publisher=createServer((req,res)=>{const b=assets.get(req.url);if(b)res.end(b);else{res.writeHead(404);res.end();}});
 await new Promise(r=>publisher.listen(0,'127.0.0.1',r));const releaseOrigin='http://127.0.0.1:'+publisher.address().port;
 const {publicKey,privateKey}=generateKeyPairSync('ed25519'),keys={fixture:publicKey.export({type:'spki',format:'pem'})};
 let supervisor,observer,client,host;const receipts=[];
 const privateWrite=(p,v)=>writeFile(p,v,{mode:0o600});
 try{
  const backendPort=await unusedPort(),port=await unusedPort(),origin='https://127.0.0.1:'+port;
  const installationDirectory=join(root,'installation');
  const workspace=join(root,'workspace'),state=currentProfile?join(installationDirectory,'application'):join(root,'state'),home=join(root,'home'),appHome=join(root,'app-home'),web=join(root,'old-web');
  for(const p of [workspace,...(currentProfile?[]:[state,join(root,'receipts')]),home,appHome,web])await mkdir(p);
  await writeFile(join(web,'index.html'),'<!doctype html><title>Old web fixture</title>');
  // Independent immutable copies: no writes to a live interpreter or environment.
  const envRoot=join(root,'python'),interpreterRoot=join(root,'interpreter'),python=join(envRoot,'bin/python');
  await cp(dirname(dirname(basePython)),envRoot,{recursive:true,verbatimSymlinks:true});
  const originalInterpreter=dirname(dirname(await realpath(basePython)));
  await cp(originalInterpreter,interpreterRoot,{recursive:true,verbatimSymlinks:true});
  for(const name of ['python','python3','python3.13']){
   const p=join(envRoot,'bin',name);try{await unlink(p);}catch(e){if(e.code!=='ENOENT')throw e;}
   await symlink(join(interpreterRoot,'bin', 'python3.13'),p);
  }
  await chmod(join(envRoot,'.lock'),0o600);
  const cfg=join(envRoot,'pyvenv.cfg');await writeFile(cfg,(await readFile(cfg,'utf8')).replaceAll(originalInterpreter,interpreterRoot));
  if(currentProfile){await sealCopy(envRoot);await sealCopy(interpreterRoot);}
  const safeEnv={...process.env,PYTHONDONTWRITEBYTECODE:'1',AMPLIFIER_HOME:home,AMPLIFIER_WEB_HOME:appHome,AMPLIFIER_SESSION_STATE_HOME:join(root,'writers'),AMPLIFIER_SOURCE_STORE:join(root,'sources'),XDG_CACHE_HOME:join(root,'cache'),UV_CACHE_DIR:join(root,'uv-cache')};
  for(const k of Object.keys(safeEnv))if(/API_KEY|TOKEN|SECRET|PASSWORD/.test(k))delete safeEnv[k];
  const provider=join(root,'provider'),module=join(provider,'amplifier_module_provider_successor_fixture');await mkdir(module,{recursive:true});
  await writeFile(join(provider,'pyproject.toml'),'[project]\nname="amplifier-module-provider-successor-fixture"\nversion="0.1.0"\n');
  // Record actual requests in this disposable provider. Automatic naming is a
  // separate legitimate call, so a total call count cannot detect turn replay.
  const providerSource=await readFile(new URL('fixtures/transfer_provider.py',import.meta.url),'utf8');
  const auditLine="with Path(self.config['normalAudit']).open('a') as output:output.write('one conversation request\\n')";
  assert.ok(providerSource.includes(auditLine));
  await writeFile(join(module,'__init__.py'),providerSource.replace(auditLine,`with Path(self.config['normalAudit']).open('a') as output:output.write(request.model_dump_json()+'\\n')
        if any(isinstance(m.content,str) and m.content.startswith('You generate names and descriptions for conversation sessions.') for m in request.messages):
            return ChatResponse(content=[TextBlock(text=json.dumps({'action':'set','name':'Preserved update work','description':'Signed update rollback fixture'}))],finish_reason='stop')`));
  const context=(await execute(python,['-I','-B','-c','import pathlib,importlib.util;print(pathlib.Path(importlib.util.find_spec("amplifier_module_context_simple").origin).parent)'],{env:safeEnv})).stdout.trim();
  const normalAudit=join(root,'normal-provider-calls.txt');
  const bundle=join(root,'bundle.yaml');await writeFile(bundle,'bundle:\n  name: successor-fixture\n  version: 1.0.0\nsession:\n  orchestrator:\n    module: loop-live\n  context:\n    module: context-simple\n    source: '+context+'\nproviders:\n  - module: provider-successor-fixture\n    source: '+provider+'\n    config:\n      normalAudit: '+normalAudit+'\n');
  await writeFile(join(home,'settings.yaml'),'bundle:\n  app: []\n');
  const nativeFile=join(root,'native.json'),managed=join(workspace,'.managed');
  await privateWrite(nativeFile,JSON.stringify({home,appHome,bundle,managedSessionRoots:[managed],adminWorkspaceRoots:[workspace],adminMaintenance:true,transferAuthorityDirectory:join(state,'capabilities/portability'),transferWorkspaceRoots:[workspace],maintenanceExternalWriters:'foundation-cooperative'}));
  const savedId=randomUUID(),saved=join(home,'projects',workspace.replaceAll('/','-'),'sessions',savedId);
  await execute(python,['-I','-B','-c',"from pathlib import Path;import sys;from amplifier_foundation.session.history import SessionHistoryStore;SessionHistoryStore(Path(sys.argv[1]),session_id=sys.argv[2]).save([{'role':'user','content':'Preserved successor fixture history'}],{'session_id':sys.argv[2],'working_dir':sys.argv[3],'status':'idle'})",saved,savedId,workspace],{env:safeEnv});
  const historyFile=join(saved,'transcript.jsonl'),history=await readFile(historyFile);
  const token=randomBytes(32).toString('hex'),code=randomBytes(40).toString('hex'),keyFile=join(root,'key.pem'),certFile=join(root,'cert.pem');
  await execute('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',keyFile,'-out',certFile,'-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1']);
  await chmod(keyFile,0o600);const cert=await readFile(certFile);
  const compositionFile=join(root,'composition.json'),hostFile=join(root,'host.json'),hostTokenFile=join(root,'host-token'),supervisorFile=join(root,'supervisor.json');
  const authorityRoot=currentProfile?installationDirectory:root;
  const authority={installationId:currentProfile?randomUUID():'successor-fixture',ownerId:'successor-owner',dataScope:'successor-scope',sourceDirectory:join(authorityRoot,'source'),claimDirectory:join(authorityRoot,'claim'),supervisorDirectory:join(authorityRoot,'supervisor'),supervisorDiscoveryFile:currentProfile?join(authorityRoot,'supervisor.json'):supervisorFile,supervisorTokenFile:join(authorityRoot,'supervisor-token'),hostDiscoveryFile:currentProfile?join(authorityRoot,'host-control.json'):hostFile,hostTokenFile:currentProfile?join(authorityRoot,'host-token'):hostTokenFile};
  if(!currentProfile)await privateWrite(hostTokenFile,token);await privateWrite(join(root,'trust.json'),JSON.stringify(keys));await privateWrite(join(root,'access-code'),code);
  const application={account:'successor-fixture',webDirectory:web,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],stateDirectory:state,host:{managedSessionRoot:managed},manualIngress:{stateDirectory:join(authorityRoot,'ingress')},gateway:{host:'127.0.0.1',port:backendPort,origin},
   engines:[{id:'amplifier',command:python,args:['-I','-B','-m','amplifier_acp','--config',nativeFile],env:safeEnv}],
   nativeAdmin:{engine:'amplifier'},maintenance:{},recovery:{authorization:'local-account',credentials:false},conversationPresentation:{authorization:'local-account'},historyImport:{},historyCleanup:true,managedFiles:true,applicationUpdates:true,portability:{python,engines:['amplifier'],stageDir:join(workspace,'stages'),exchangeDir:join(workspace,'exchange')},
   ...Object.fromEntries(['operations','notifications','diagnostics','coordination','recall','publishing','worktrees','feedback','workspaces','mcp','media'].map(name=>[name,{python}])),
   catalogProcess:{command:python,args:['-I','-B','-m','amplifier_session_catalog','serve','--db',join(root,'catalog.sqlite'),'--home',home,'--app-home',appHome,'--workspace',workspace,'--scan-interval','0','--workspace-check-interval','0']}};
  if(currentProfile){
   assert.ok(process.env.SUCCESSOR_UV_PATH,'Explicit installer required to seal editable fixture dependencies');
   // Build from copies, never mutate the preview's configured source roots.
   const convert=`import json,sys,pathlib,shutil
site=pathlib.Path(sys.argv[1]);destination=pathlib.Path(sys.argv[2]);destination.mkdir()
rows=[];remove=[]
for metadata in site.glob('*.dist-info/direct_url.json'):
 data=json.loads(metadata.read_text())
 if data.get('dir_info',{}).get('editable'):
  from urllib.parse import urlparse,unquote
  source=pathlib.Path(unquote(urlparse(data['url']).path));target=destination/metadata.parent.name
  if not source.exists():
   name=next(line[6:].strip() for line in (metadata.parent/'METADATA').read_text().splitlines() if line.startswith('Name: '))
   if name not in ['amplifier-module-provider-distribution-fixture','amplifier-module-provider-transfer-fixture']:raise RuntimeError('Missing retained dependency: '+name)
   remove.append(name);continue
  shutil.copytree(source,target,symlinks=True,ignore=shutil.ignore_patterns('.git','__pycache__','.venv','node_modules'))
  rows.append(str(target))
print(json.dumps({'projects':rows,'remove':remove}))`;
   const site=(await execute(python,['-I','-B','-c','import sysconfig;print(sysconfig.get_paths()["purelib"])'],{env:safeEnv})).stdout.trim();
   const converted=JSON.parse((await execute(python,['-I','-B','-c',convert,site,join(root,'fixture-wheel-sources')],{env:safeEnv})).stdout);
   if(converted.remove.length)await execute(process.env.SUCCESSOR_UV_PATH,['pip','uninstall','--python',python,...converted.remove],{env:safeEnv});
   const projects=converted.projects;
   if(projects.length)await execute(process.env.SUCCESSOR_UV_PATH,['pip','install','--python',python,'--no-deps','--reinstall',...projects],{env:safeEnv,timeout:180000,maxBuffer:1048576});
   await sealCopy(envRoot);await sealCopy(interpreterRoot);
  }
  const provenance=JSON.parse(await readFile(join(basePackage,'components.json'),'utf8')).components['@amplifier/unified-distribution-update-owner'];
  const c={schema:currentProfile?'unified-full-owner-fresh-composition-v1':'unified-full-owner-composition-v1',expectedOwners:OWNERS,application,authority,
   review:{status:'approved',combinedLinuxReceiptSha256:'a'.repeat(64),catalogWriterConcurrency:'qualified',nativeModeProjection:'qualified',operationsPortabilityResolver:'qualified'},
   release:{entrypoint:'src/full-owner-launcher.mjs',trustedKeysFile:join(root,'trust.json'),updateOwnerVersion:provenance.version,updateOwnerRevision:provenance.revision},
   access:{origin,host:'127.0.0.1',port,backendPort,keyFile,certFile,codeFile:join(root,'access-code')},
   receiptDirectory:join(authorityRoot,'receipts'),sourcePolicy:[{repository:git.repository,ref:'main'}],
   storageInventory:{externalCoverage:{'manual-preview-ingress':'declared'},externalRoots:[{id:'ingress',ownerIds:['manual-preview-ingress'],path:application.manualIngress.stateDirectory,coverage:'authoritative',capture:'tree'}]}};
  // The historical test requires an actual old package. Current packages must
  // use a signed owner profile and genuine one-shot pristine authority.
  let compositionBytes;
  if(currentProfile){
   c.release.initial={id:'successor-1',version:'1.0.0',revision:git.first};
   await privateWrite(compositionFile,JSON.stringify(c));compositionBytes=await readFile(compositionFile);
  }
  async function currentDescriptor(pkg,version,mcpManifest){
   const inputs=join(pkg,'release-inputs');await mkdir(inputs,{recursive:true});
   const census=JSON.parse((await execute(python,['-I','-B','-c',
    'import sys,json,importlib.util;print(json.dumps({"paths":sys.path,"modules":{m:importlib.util.find_spec(m).origin for m in ["amplifier_acp.__main__","amplifier_session_catalog.__main__","amplifier_unified_media.worker"]}}))'],{env:safeEnv})).stdout);
   const ref=file=>{for(const [tree,base] of [['environment',envRoot],['interpreter',interpreterRoot]])if(file.startsWith(base+'/'))return {tree,path:file.slice(base.length+1)};throw Error('Unsealed owner entrypoint');};
   const qualification={schema:'unified-python-runtime-qualification-v1',profile:'native-catalog-media-v1',python:{tree:'environment',path:'bin/python'},
    launches:[['native','amplifier_acp'],['catalog','amplifier_session_catalog'],['media','amplifier_unified_media.worker']].map(([role,module])=>({role,module,flags:['-I','-B'],moduleFile:ref(census.modules[role==='media'?module:module+'.__main__']),importPaths:census.paths,noRuntimeWrites:true,editableInstalls:false}))};
   const qb=Buffer.from(JSON.stringify(qualification));await writeFile(join(inputs,'owner-qualification.json'),qb);
   const args={trees:[{id:'environment',root:envRoot},{id:'interpreter',root:interpreterRoot}],python:qualification.python,qualificationReceiptSha256:hash(qb)};
   await writeFile(join(inputs,'owner-python.json'),JSON.stringify(await inventoryPythonRuntime(args)));
   await writeFile(join(inputs,'mcp-runtime.json'),JSON.stringify(mcpManifest??await inventoryMcpRuntime(args)));
   const nb=await readFile(nativeFile);await writeFile(join(inputs,'native.json'),JSON.stringify({...JSON.parse(nb),adminGenerations:true,runtimeImmutable:true}));
   await mkdir(join(pkg,'web'),{recursive:true});await writeFile(join(pkg,'web/index.html'),'<!doctype html><title>'+(version===1?'Old':'New')+' web fixture</title>');
   await writeFile(join(pkg,'release-runtime.json'),JSON.stringify({schema:'unified-release-runtime-v3',release:{id:'successor-'+version,version:version+'.0.0',revision:version===1?git.first:git.second},baseConfigurationSha256:hash(compositionBytes),webDirectory:'web',mcpRuntime:'release-inputs/mcp-runtime.json',
    nativeLauncher:{engineId:'amplifier',configuration:'release-inputs/native.json',baseConfigurationSha256:hash(nb),grants:{adminGenerations:true,runtimeImmutable:true},qualificationReceiptSha256:hash('isolated successor fixture only')},
    ownerRuntime:{profile:'native-catalog-media-v1',engineId:'amplifier',manifest:'release-inputs/owner-python.json',qualificationReceipt:'release-inputs/owner-qualification.json',mediaMode:'installed'},ownerCensus:{profile:'native-message-metadata-v1'}}));
  }
  async function candidate(version,configure){
   const staging=join(root,'candidate-'+version),pkg=join(staging,'package');await mkdir(staging);await cp(basePackage,pkg,{recursive:true});
   if(configure)await configure(pkg);
   if(currentProfile){
    const entry=join(pkg,'src/full-owner-launcher.mjs');
    const body=await readFile(entry,'utf8');
    // Diagnostics are private and fixture-only; never alter the shipped launcher.
    await writeFile(entry,body.replace("process.stderr.write('full_owner_startup_unconfirmed\\n');",`await writeFile(join(dirname(process.argv[2]),'fixture-start-error-${version}.txt'),error.stack,{mode:0o600});process.stderr.write('full_owner_startup_unconfirmed\\n');`));
   }
   console.log('Fixture candidate '+version+' assembled');
   const files=await inventory(pkg),components=[];
   for(const row of files.filter(f=>f.path==='package.json'||/\bnode_modules\/(?:@[^/]+\/)?[^/]+\/package.json$/.test(f.path))){
    const p=JSON.parse(await readFile(join(pkg,row.path),'utf8'));components.push({name:p.name,version:p.version,root:row.path==='package.json'?'':dirname(row.path),repository:git.repository,ref:'main',revision:version===1?git.first:git.second});
   }
   const archive=join(root,'release-'+version+'.tgz');await execute('tar',['-czf',archive,'-C',staging,'package']);
   const bytes=await readFile(archive),release={identity:{id:'successor-'+version,version:version+'.0.0',revision:version===1?git.first:git.second,digest:''},entrypoint:'src/full-owner-launcher.mjs',platform:process.platform,arch:process.arch,files,components,artifact:{url:releaseOrigin+'/release-'+version+'.tgz',sha256:hash(bytes),bytes:bytes.length}};
   release.identity.digest=api.releaseDigest(release);assets.set('/release-'+version+'.tgz',bytes);return release;
  }
  const first=await candidate(1,currentProfile?pkg=>currentDescriptor(pkg,1):undefined);let second;
  function publish(recommended){const payload=Buffer.from(JSON.stringify({schema:'distribution-channel-v1',expiresAt:Date.now()+3600000,recommendedId:recommended.identity.id,releases:[first,...(second?[second]:[])]}));assets.set('/channel.json',Buffer.from(JSON.stringify({schema:'distribution-signed-channel-v1',keyId:'fixture',payload:payload.toString('base64'),signature:sign(null,payload,privateKey).toString('base64')})));}
  publish(first);
  const resolveSources=createGitSourceResolver({sources:c.sourcePolicy,env:git.env}),releaseOptions={directory:join(authorityRoot,'releases'),channelUrl:releaseOrigin+'/channel.json',trustedKeys:keys,accessScope:'successor-fixture',allowedArtifactOrigins:[releaseOrigin],allowLoopbackHttp:true,launchArgs:[compositionFile],launchEnv:safeEnv,resolveSources};
  const installation=currentProfile?await api.createPristineInstallation({directory:installationDirectory,dataScope:authority.dataScope,initial:first.identity,plannedInstallationId:authority.installationId}):null;
  if(currentProfile){await privateWrite(authority.hostTokenFile,token);await mkdir(c.receiptDirectory);}
  const adapter=new api.SignedReleaseAdapter(releaseOptions),target=await adapter.prepare(first.identity,{commandId:'fixture-initial',signal:new AbortController().signal});
  if(!currentProfile){c.release.prepared=target;await privateWrite(compositionFile,JSON.stringify(c));compositionBytes=await readFile(compositionFile);}
  if(currentProfile){const {bindReleaseConfiguration}=await import('../src/release-runtime.mjs');const prepared=await adapter.installed(target);await bindReleaseConfiguration({configuration:c,configurationBytes:compositionBytes,runtime:{identity:first.identity},releaseRoot:prepared.root,installationInitial:first.identity});}
  host=api.connectHostControlFile(authority.hostDiscoveryFile,authority.dataScope);let claimed=false;
  supervisor=await api.runSupervisor({schema:'distribution-supervisor-v1',dataDirectory:authority.supervisorDirectory,dataScope:authority.dataScope,tokenFile:authority.supervisorTokenFile,discoveryFile:authority.supervisorDiscoveryFile,initial:target,release:releaseOptions,serviceLifecycle:{installationId:authority.installationId,ownerId:authority.ownerId}},
   {startInitial:true,ports:currentProfile?api.createProductionSupervisorPorts({dataScope:authority.dataScope,dataDirectory:authority.supervisorDirectory,releaseDirectory:installation.releaseDirectory,hostDiscoveryFile:authority.hostDiscoveryFile,provisioningAuthorityFile:installation.authorityFile,resolveSources}):{resolveSources,inspect:()=>host.inspect(),admitRestart:host.admitRestart,reconcileAdmission:host.reconcileAdmission,service:host.service,onIdle:host.onIdle,
    initialProvisioning:{claim:async request=>{assert.equal(claimed,false);claimed=true;return {kind:'pristine-installation',installationId:authority.installationId,commandId:request.commandId,instanceId:request.instanceId,dataScope:request.dataScope,targetDigest:request.target.identity.digest};}}}});
  observer=await api.connectSupervisorFile(authority.supervisorDiscoveryFile);
  await observer.owner.setPreferences('fixture-preferences',{autoCheck:false,autoInstall:false,intervalMs:60000});
  const before=await observer.owner.inspectRunning();assert.equal(before.identity.id,first.identity.id);assert.equal(before.ready,true);
  console.log('Current fixture initial launch ready');
  const initialLogin=await https(origin+'/preview/login',cert,{body:{code}});assert.equal(initialLogin.status,204);
  assert.match((await https(origin+'/',cert,{cookie:initialLogin.cookie})).text,/Old web fixture/);
  client=await peer(origin,cert,initialLogin.cookie);
  const session='ahp-session:/'+randomUUID();await client.request('createSession',{channel:session,provider:'amplifier',workingDirectories:[pathToFileURL(workspace).href]});
  const worker=await client.request('x-amplifier/capabilityAction',{channel:'ahp-root://',topic:'maintenance',operation:'updates.runtime.worker',version:1,args:{sessionId:session,surface:'mounted',limit:2},commandId:'fixture-worker'});
  assert.equal(worker.result.resident,true);
  // Persist through the public APIs, then revise after activation. A rollback
  // that restores the pre-update snapshot must fail these assertions.
  const action=async(topic,operation,args={},channel='ahp-root://')=>{
   const response=await client.request('x-amplifier/capabilityAction',{channel,topic,operation,version:1,args,commandId:randomUUID()});
   assert.equal(response.accepted,true,JSON.stringify(response));return response.result;
  };
  const originalArtifact=(await action('canvas','canvas.show',{kind:'markdown',title:'Before update',content:'Original saved artifact.'},session)).artifact;
  assert.equal(originalArtifact.revision,1);
  const mcpRoot=join(root,'next-mcp'),mcpAudit=join(root,'mcp-calls.txt');await mkdir(join(mcpRoot,'bin'),{recursive:true});
  // The wrapper is a harmless fixture discriminator, not a production runtime.
  await writeFile(join(mcpRoot,'bin/python'),'#!/bin/sh\nprintf "successor MCP\\n" >> '+JSON.stringify(mcpAudit)+'\nexec '+JSON.stringify(python)+' "$@"\n',{mode:0o755});
  // Native initialization may install fixture providers in its own environment.
  // The successor MCP has independent sealed copies, never shared with that owner.
  const mcpSource=process.env.SUCCESSOR_MCP_PYTHON??(currentProfile?python:basePython);
  const mcpEnv=join(root,'mcp-environment'),mcpInterpreter=join(root,'mcp-interpreter');
  await cp(dirname(dirname(mcpSource)),mcpEnv,{recursive:true,verbatimSymlinks:true});
  const mcpOriginal=dirname(dirname(await realpath(mcpSource)));
  await cp(mcpOriginal,mcpInterpreter,{recursive:true,verbatimSymlinks:true});
  for(const name of ['python','python3','python3.13']){
    const p=join(mcpEnv,'bin',name);try{await unlink(p);}catch(e){if(e.code!=='ENOENT')throw e;}
    await symlink(join(mcpInterpreter,'bin','python3.13'),p);
  }
  await chmod(join(mcpEnv,'.lock'),0o600);
  const mcpCfg=join(mcpEnv,'pyvenv.cfg');await writeFile(mcpCfg,(await readFile(mcpCfg,'utf8')).replaceAll(mcpOriginal,mcpInterpreter));
  await writeFile(join(mcpRoot,'bin/python'),'#!/bin/sh\nprintf "successor MCP\\n" >> '+JSON.stringify(mcpAudit)+'\nexec '+JSON.stringify(join(mcpEnv,'bin/python'))+' "$@"\n',{mode:0o755});
  await sealCopy(mcpEnv);await sealCopy(mcpInterpreter);
  const manifest=await inventoryMcpRuntime({trees:[{id:'mcp',root:mcpRoot},{id:'environment',root:mcpEnv},{id:'interpreter',root:mcpInterpreter}],python:{tree:'mcp',path:'bin/python'},qualificationReceiptSha256:hash('fixture qualification only')});
  second=await candidate(2,async pkg=>{
   for(const file of ['full-owner-launcher.mjs','full-owner-ready.mjs','release-runtime.mjs','mcp.js'])await copyFile(new URL('../src/'+file,import.meta.url),join(pkg,'src',file));
   await mkdir(join(pkg,'web'),{recursive:true});await writeFile(join(pkg,'web/index.html'),'<!doctype html><title>New web fixture</title>');
   await mkdir(join(pkg,'release-inputs'),{recursive:true});await writeFile(join(pkg,'release-inputs/mcp-runtime.json'),JSON.stringify(manifest));
   if(currentProfile){await currentDescriptor(pkg,2,manifest);return;}
   await writeFile(join(pkg,'release-runtime.json'),JSON.stringify({schema:'unified-release-runtime-v1',release:{id:'successor-2',version:'2.0.0',revision:git.second},baseConfigurationSha256:hash(compositionBytes),webDirectory:'web',mcpRuntime:'release-inputs/mcp-runtime.json'}));
  });
  await git.advance(git.second);publish(second);
  const done=async(id,settlement=false)=>until(async()=>{const r=await observer.owner.receipt(id);
   if(!r||!['succeeded','failed','unknown'].includes(r.status))return null;
   return settlement&&r.status==='succeeded'&&(!r.admissionSettlement||r.admissionSettlement.state==='pending')?null:r;
  });
  await observer.owner.check('check-next',true);assert.equal((await done('check-next')).status,'succeeded');
  await observer.owner.prepare('prepare-next',second.identity.id);const prepared=await done('prepare-next');assert.equal(prepared.status,'succeeded',JSON.stringify(prepared));receipts.push(prepared);
  assert.equal((await observer.owner.inspectRunning()).instanceId,before.instanceId,'prepare does not replace active process');
  const staged=(await observer.owner.inspect()).staged;assert.equal(staged.commandId,'prepare-next');
  await observer.owner.activate('activate-next',{preparedCommandId:'prepare-next',targetDigest:second.identity.digest,expectedCurrentId:first.identity.id});
  // Waiting can be brief: idle notifications may immediately start another
  // admission inspection. Use the durable public event, not a timing race on
  // the latest receipt status, to establish connected-browser refusal.
  const busy=await until(async()=>{const d=await observer.owner.diagnostics();return d.events.find(e=>e.id==='activate-next'&&e.status==='waiting'&&e.phase==='waiting_idle');});
  receipts.push(busy);assert.equal((await observer.owner.inspectRunning()).instanceId,before.instanceId,'connected browser refuses update admission');
  await client.close();client=null;
  console.log('Fixture browser disconnected; waiting for activation');
  const activated=await done('activate-next',true);assert.equal(activated.status,'succeeded',JSON.stringify(activated));assert.equal(activated.admissionSettlement.state,'settled');receipts.push(activated);
  const after=await observer.owner.inspectRunning();assert.equal(after.identity.id,second.identity.id);assert.notEqual(after.instanceId,before.instanceId);
  const ready=JSON.parse(await readFile(join(c.receiptDirectory,after.instanceId+'-ready.json'),'utf8'));assert.deepEqual([...ready.owners].sort(),[...OWNERS,...(currentProfile?['native-message-metadata']:[])].sort());assert.ok(ready.releaseBinding);
  assert.match(await readFile(mcpAudit,'utf8'),/successor MCP/,'real MCP owner initialization uses the successor Python');
  const nextLogin=await https(origin+'/preview/login',cert,{body:{code}});assert.equal(nextLogin.status,204);assert.match((await https(origin+'/',cert,{cookie:nextLogin.cookie})).text,/New web fixture/);
  assert.deepEqual(await readFile(historyFile),history);assert.deepEqual(await readFile(compositionFile),compositionBytes);
  client=await peer(origin,cert,nextLogin.cookie);
  const newText='Retain this work created after the signed update.';
  const changedArtifact=(await action('canvas','canvas.versions.revise',{id:originalArtifact.id,expectedRevision:1,title:'Changed after update',content:newText},session)).artifact;
  assert.equal(changedArtifact.revision,2);
  const newArtifact=(await action('canvas','canvas.show',{kind:'json',title:'New after update',content:JSON.stringify({retained:'cobalt',count:41})},session)).artifact;
  const memory=await action('recall','memory.create',{scope:'workspace',text:'Remember the cobalt rollback marker.'},session);
  const beforeSettings=await action('notifications','notifications.get');
  await action('notifications','notifications.save',{expectedRevision:beforeSettings.revision,patch:{enabled:false,preview:!beforeSettings.preview,topic:'fixture-private-topic',token:'fixture-private-credential'}});
  const changedSettings=await action('notifications','notifications.get');
  assert.notEqual(changedSettings.revision,beforeSettings.revision);
  assert.equal(JSON.stringify(changedSettings).includes('fixture-private-credential'),false);
  const turnId=randomUUID(),chat=session.replace('ahp-session:','ahp-chat:');
  await client.request('dispatchAction',{channel:chat,clientSeq:1,action:{type:'chat/turnStarted',turnId,startedAt:new Date().toISOString(),message:{text:newText,origin:{kind:'user'}}}});
  const completed=await until(async()=>{
   const response=await client.request('subscribe',{channel:chat,view:{turns:10}});
   const turn=response.snapshot.state.turns.find(row=>row.id===turnId);
   if(turn?.state==='error')assert.fail(JSON.stringify(turn));
   return turn?.state==='complete'?turn:null;
  });
  assert.match(JSON.stringify(completed.responseParts),/Retained native transfer answer/);
  await until(async()=> (await client.request('subscribe',{channel:session})).snapshot.state.title==='Preserved update work');
  const callsAfterWrite=await readFile(normalAudit,'utf8'),requests=callsAfterWrite.trim().split('\n').map(line=>JSON.parse(line));
  const userText=message=>typeof message.content==='string'?message.content:(message.content??[]).map(block=>block.text??'').join('');
  const namingCalls=requests.filter(request=>request.messages.length===1&&userText(request.messages[0]).startsWith('You generate names and descriptions for conversation sessions.'));
  const conversationCalls=requests.filter(request=>!namingCalls.includes(request));
  assert.equal(namingCalls.length,1);assert.equal(conversationCalls.length,1);
  assert.equal(conversationCalls[0].messages.filter(message=>message.role==='user'&&userText(message)===newText).length,1);
  await client.close();client=null;
  await observer.owner.rollback('rollback-old',second.identity.id);
  const rolled=await done('rollback-old',true);assert.equal(rolled.status,'succeeded',JSON.stringify(rolled));assert.equal(rolled.admissionSettlement.state,'settled');receipts.push(rolled);
  const restored=await observer.owner.inspectRunning();assert.equal(restored.identity.id,first.identity.id);
  const restoredLogin=await https(origin+'/preview/login',cert,{body:{code}});assert.match((await https(origin+'/',cert,{cookie:restoredLogin.cookie})).text,/Old web fixture/);
  assert.deepEqual(await readFile(historyFile),history);assert.deepEqual(await readFile(compositionFile),compositionBytes);
  client=await peer(origin,cert,restoredLogin.cookie);
  assert.equal((await client.request('subscribe',{channel:session})).snapshot.state.title,'Preserved update work');
  const retained=(await client.request('subscribe',{channel:chat,view:{turns:10}})).snapshot.state.turns;
  assert.equal(retained.filter(row=>row.id===turnId).length,1);
  assert.equal(retained.find(row=>row.id===turnId).message.text,newText);
  assert.match(JSON.stringify(retained.find(row=>row.id===turnId).responseParts),/Retained native transfer answer/);
  for(const [artifact,content]of [[changedArtifact,newText],[newArtifact,JSON.stringify({retained:'cobalt',count:41})]]){
   const saved=await action('canvas','canvas.versions.inspect',{id:artifact.id,includeSource:true},session);
   assert.equal(saved.artifact.revision,artifact.revision);assert.equal(saved.artifact.title,artifact.title);assert.equal(saved.body.content,content);
  }
  const originalVersion=await action('canvas','canvas.versions.inspect',{id:originalArtifact.id,version:1,includeSource:true},session);
  assert.equal(originalVersion.body.content,'Original saved artifact.');
  assert.equal((await action('recall','memory.read',{id:memory.id},session)).text,'Remember the cobalt rollback marker.');
  assert.deepEqual(await action('notifications','notifications.get'),changedSettings);
  assert.equal(await readFile(normalAudit,'utf8'),callsAfterWrite,'Rollback or passive readback replayed inference');
  await client.close();client=null;
  const receipt={schema:'signed-successor-acceptance-v1',root,first:first.identity,second:second.identity,realTLS:true,publicPrepareActivate:true,sameSupervisor:true,actualNativeInitialization:true,fullOwnerCount:currentProfile?21:20,currentSignedProfile:currentProfile,openWebSocketRefused:true,explicitDisconnect:true,nativeGracefulRetirement:true,actualMcpPythonSelected:true,rollbackOldLauncher:true,baseCompositionUnchanged:true,canonicalHistoryPreserved:true,paidInference:false,
   postActivationWrites:{turnId,providerCalls:requests.length,conversationCalls:conversationCalls.length,namingCalls:namingCalls.length,titleRetained:true,replayed:false,changedArtifactId:changedArtifact.id,newArtifactId:newArtifact.id,originalArtifactVersionRetained:true,memoryId:memory.id,notificationRevision:changedSettings.revision,notificationCredentialsRedacted:true},
   limits:['Same-schema candidate releases with actual 21-owner activation; not monolithic legacy migration or a storage-schema downgrade.','New chat, artifact versions, memory and notification writes are read through public APIs after rollback; other owners are not claimed to have new-write coverage.'],receipts};
  await writeFile(join(root,'acceptance.json'),JSON.stringify(receipt,null,2));console.log('successor_receipt='+join(root,'acceptance.json'));
  if(process.env.SIGNED_SUCCESSOR_RECEIPT)await writeFile(process.env.SIGNED_SUCCESSOR_RECEIPT,JSON.stringify(receipt,null,2));
 }catch(error){console.error('Successor qualification failed:',error.stack);throw error;}finally{
  await client?.close().catch(()=>{});
  let cleanupError;
  try{
   if(supervisor){
    const status=await supervisor.service.inspect();
    if(status.state==='running'){
     await supervisor.service.stop({commandId:'fixture-final-stop',expected:status.identity});
     const stopped=await supervisor.service.waitFor('fixture-final-stop');
     assert.equal(stopped.status,'stopped',JSON.stringify(stopped));
    }
   }
  }catch(error){cleanupError=error;}
  finally{
   try{await supervisor?.close();}catch(error){cleanupError??=error;}
   finally{
    observer?.close();host?.close?.();publisher.closeAllConnections();
    await new Promise(r=>publisher.close(r));await git.close();
   }
  }
  if(cleanupError)console.error('Fixture cleanup requires inspection:',cleanupError.message);
  console.log('Retained isolated successor evidence: '+root);
 }
});
