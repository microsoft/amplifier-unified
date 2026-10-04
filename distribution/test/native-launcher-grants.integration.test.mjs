import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, writeFile, rm, realpath, cp, access} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {spawn, execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {createInterface} from 'node:readline';
import {once} from 'node:events';
import {bindReleaseConfiguration, inventoryMcpRuntime} from '../src/release-runtime.mjs';

const python=process.env.NATIVE_GRANTS_TEST_PYTHON;
const immutablePython=process.env.NATIVE_IMMUTABLE_TEST_PYTHON;
const execute=promisify(execFile);
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
async function broker(engine, env) {
  const child=spawn(engine.command,engine.args,{env:{...env,...engine.env},stdio:'pipe'});
  let next=0,stderr='';const pending=new Map();
  child.stderr.on('data',bytes=>{stderr=(stderr+String(bytes)).slice(-4000);});
  const lines=createInterface({input:child.stdout});
  lines.on('line',line=>{
    const row=JSON.parse(line),p=pending.get(row.id);
    if(p){pending.delete(row.id);clearTimeout(p.timer);row.error?p.reject(Object.assign(Error(row.error.message),row.error)):p.resolve(row.result);}
  });
  child.on('exit',()=>{for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('Native fixture exited: '+stderr));}pending.clear();});
  const request=(method,params)=>new Promise((resolve,reject)=>{
    const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error('Native fixture timed out'));},20000);
    pending.set(id,{resolve,reject,timer});child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
  });
  return {request,async close(){
    if(child.exitCode!==null)return;
    const exited=once(child,'exit');child.stdin.end();await exited;lines.close();
  }};
}

test('signed grant binding reaches the installed native broker without changing base config',
 {skip:!python,timeout:60000},async t=>{
  const root=await realpath(await mkdtemp(join(tmpdir(),'native-grants-')));
  t.after(()=>rm(root,{recursive:true,force:true}));
  const pkg=join(root,'package'),home=join(root,'home'),appHome=join(root,'app'),cwd=join(root,'workspace'),mcp=join(root,'mcp');
  for(const p of [pkg,home,appHome,cwd,mcp,join(pkg,'web'),join(pkg,'release-inputs')])await mkdir(p);
  await writeFile(join(pkg,'web/index.html'),'fixture');
  // MCP is not launched by this native grant test; its bytes remain validated.
  await writeFile(join(mcp,'python'),'not executed',{mode:0o755});
  const manifest=await inventoryMcpRuntime({trees:[{id:'mcp',root:mcp}],python:{tree:'mcp',path:'python'},qualificationReceiptSha256:'a'.repeat(64)});
  await writeFile(join(pkg,'release-inputs/mcp.json'),JSON.stringify(manifest));
  const baseFile=join(root,'native.json');
  const native={home,appHome,adminWorkspaceRoots:[cwd],adminMaintenance:true,adminVoicePreferences:true};
  const nativeBytes=Buffer.from(JSON.stringify(native));
  await writeFile(baseFile,nativeBytes,{mode:0o600});
  const engine={id:'amplifier',command:python,args:['-I','-B','-m','amplifier_acp','--config',baseFile]};
  const original={id:'old',version:'1.0.0',revision:'1'.repeat(40),digest:'1'.repeat(64)};
  const identity={id:'new',version:'2.0.0',revision:'2'.repeat(40),digest:'2'.repeat(64)};
  const configuration={release:{prepared:{identity:original}},application:{
    engines:[engine],nativeAdmin:{engine:'amplifier'},applicationUpdates:true,maintenance:{},mcp:{python:'old'},webDirectory:'old'}};
  const configurationBytes=Buffer.from(JSON.stringify(configuration));
  const grants={adminVoiceCredentials:true,adminGenerations:true};
  await writeFile(join(pkg,'release-inputs/native.json'),JSON.stringify({...native,...grants}));
  await writeFile(join(pkg,'release-runtime.json'),JSON.stringify({
    schema:'unified-release-runtime-v2',release:{id:identity.id,version:identity.version,revision:identity.revision},
    baseConfigurationSha256:hash(configurationBytes),webDirectory:'web',mcpRuntime:'release-inputs/mcp.json',
    nativeLauncher:{engineId:'amplifier',baseConfigurationSha256:hash(nativeBytes),configuration:'release-inputs/native.json',grants,qualificationReceiptSha256:'b'.repeat(64)}
  }));
  const env={PATH:process.env.PATH,HOME:home,AMPLIFIER_HOME:home,AMPLIFIER_WEB_HOME:appHome,
    AMPLIFIER_SESSION_STATE_HOME:join(root,'session-state'),XDG_CACHE_HOME:join(root,'cache')};
  const initialize={protocolVersion:1,clientCapabilities:{_meta:{'amplifier.dev/native':{version:1}}}};
  const old=await broker(engine,env);
  try {
    const reply=await old.request('initialize',initialize),admin=reply.agentCapabilities._meta['amplifier.dev/native'].admin;
    assert.equal(admin.generations,undefined);assert.equal(admin.voice.credentials,false);
    await assert.rejects(old.request('_amplifier/admin',{cwd,operation:'generations.check',args:{commandId:'fixture-refusal'}}),/explicit trusted launcher enablement/);
  } finally {await old.close();}
  const result=await bindReleaseConfiguration({configuration,configurationBytes,runtime:{identity},releaseRoot:pkg});
  const current=await broker(result.configuration.application.engines[0],env);
  try {
    const reply=await current.request('initialize',initialize),admin=reply.agentCapabilities._meta['amplifier.dev/native'].admin;
    assert.equal(admin.generations.version,1);assert.equal(admin.voice.credentials,true);
    const receipt=await current.request('_amplifier/admin',{cwd,operation:'generations.receipt',args:{commandId:'fixture-refusal'}});
    assert.deepEqual(receipt,{receipt:null});
    const voice=await current.request('_amplifier/admin',{cwd,operation:'voice.configuration',args:{}});
    assert.equal(voice.credentialAccess,'enabled');assert.equal(voice.available,false);
    assert.equal(voice.environmentAvailable,false);assert.equal(voice.privateKeyAvailable,false);
    await result.verify();
  } finally {await current.close();}
  assert.deepEqual(await readFile(baseFile),nativeBytes);
  assert.deepEqual(Buffer.from(JSON.stringify(configuration)),configurationBytes);
  assert.equal(result.configuration.application.applicationUpdates,true);
});

// This crosses the real installed broker and fresh Core/Foundation worker. The
// offline provider is only a mount fixture; no core/native source is overlaid.
// The release binder assumes signature verification by its caller, so this is
// not acceptance of the publisher, full owner composition, or live deployment.
test('signed immutable grant reaches a fresh installed worker despite inherited env zero',
 {skip:!immutablePython,timeout:60000},async t=>{
  const root=await realpath(await mkdtemp(join(tmpdir(),'native-immutable-grant-')));
  t.after(()=>rm(root,{recursive:true,force:true}));
  const pkg=join(root,'package'),home=join(root,'home'),appHome=join(root,'app'),cwd=join(root,'workspace');
  const mcp=join(root,'mcp'),bin=join(root,'bin'),provider=join(root,'provider');
  for(const p of [pkg,home,appHome,cwd,mcp,bin,join(pkg,'web'),join(pkg,'release-inputs')])await mkdir(p);
  await cp(fileURLToPath(new URL('./fixtures/immutable-native-provider',import.meta.url)),provider,{recursive:true});
  const installerAudit=join(root,'installer-called'),completionAudit=join(root,'completion-called');
  await writeFile(join(bin,'uv'),'#!/bin/sh\nprintf attempted > "$NATIVE_TEST_INSTALLER_AUDIT"\nexit 97\n',{mode:0o700});
  const env={PATH:bin+':'+process.env.PATH,HOME:home,AMPLIFIER_HOME:home,AMPLIFIER_WEB_HOME:appHome,
    AMPLIFIER_SESSION_STATE_HOME:join(root,'session-state'),XDG_CACHE_HOME:join(root,'cache'),
    PYTHONDONTWRITEBYTECODE:'1',AMPLIFIER_RUNTIME_IMMUTABLE:'0',NATIVE_TEST_INSTALLER_AUDIT:installerAudit};
  const inspectPython=async()=>JSON.parse((await execute(immutablePython,['-I','-B','-c',`
import hashlib, importlib.util, json, sys
from pathlib import Path
prefix=Path(sys.prefix)
files={str(p.relative_to(prefix)):hashlib.sha256(p.read_bytes()).hexdigest()
       for p in prefix.rglob('*') if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts}
print(json.dumps({'executable':sys.executable,'prefix':str(prefix),'files':files,
 'context':str(Path(importlib.util.find_spec('amplifier_module_context_simple').origin).parent)}))
`],{env,maxBuffer:8*1024*1024})).stdout);
  const installedBefore=await inspectPython();
  const fixtureConfig={completionAudit};
  const bundleFile=join(root,'fixture.yaml'),settingsFile=join(home,'settings.yaml');
  await writeFile(bundleFile,JSON.stringify({bundle:{name:'offline-immutable-grant',version:'1.0.0+amplifier-unified.snapshot.1'},
    session:{orchestrator:{module:'loop-live'},context:{module:'context-simple',source:installedBefore.context}},
    providers:[{module:'provider-fixture',source:provider,config:fixtureConfig}]}));
  const settingsBytes=Buffer.from(JSON.stringify({bundle:{app:[]},config:{providers:[
    {id:'fixture',module:'provider-fixture',source:provider,config:fixtureConfig}]}}));
  await writeFile(settingsFile,settingsBytes);
  await writeFile(join(pkg,'web/index.html'),'fixture');
  await writeFile(join(mcp,'python'),'not executed',{mode:0o755});
  const manifest=await inventoryMcpRuntime({trees:[{id:'mcp',root:mcp}],python:{tree:'mcp',path:'python'},qualificationReceiptSha256:'a'.repeat(64)});
  await writeFile(join(pkg,'release-inputs/mcp.json'),JSON.stringify(manifest));
  const baseFile=join(root,'private-native.json');
  const native={home,appHome,bundle:bundleFile,startupTimeout:30,adminWorkspaceRoots:[cwd],
    workerCommand:[immutablePython,'-I','-B','-m','amplifier_acp.native.runtime_worker']};
  // Deliberately no generation marker and no policy setting in the private base.
  const nativeBytes=Buffer.from(JSON.stringify(native));
  await writeFile(baseFile,nativeBytes,{mode:0o600});
  const engine={id:'amplifier',command:immutablePython,args:['-I','-B','-m','amplifier_acp','--config',baseFile],
    env:{AMPLIFIER_RUNTIME_IMMUTABLE:'0'}};
  const original={id:'old',version:'1.0.0',revision:'1'.repeat(40),digest:'1'.repeat(64)};
  const identity={id:'new',version:'2.0.0',revision:'2'.repeat(40),digest:'2'.repeat(64)};
  const configuration={release:{prepared:{identity:original}},application:{engines:[engine],
    nativeAdmin:{engine:'amplifier'},applicationUpdates:true,maintenance:{},mcp:{python:'old'},webDirectory:'old'}};
  const configurationBytes=Buffer.from(JSON.stringify(configuration)),grants={runtimeImmutable:true};
  await writeFile(join(pkg,'release-inputs/native.json'),JSON.stringify({...native,...grants}));
  await writeFile(join(pkg,'release-runtime.json'),JSON.stringify({
    schema:'unified-release-runtime-v2',release:{id:identity.id,version:identity.version,revision:identity.revision},
    baseConfigurationSha256:hash(configurationBytes),webDirectory:'web',mcpRuntime:'release-inputs/mcp.json',
    nativeLauncher:{engineId:'amplifier',baseConfigurationSha256:hash(nativeBytes),configuration:'release-inputs/native.json',grants,
      qualificationReceiptSha256:'b'.repeat(64)}
  }));
  const result=await bindReleaseConfiguration({configuration,configurationBytes,runtime:{identity},releaseRoot:pkg});
  const boundEngine=result.configuration.application.engines[0];
  assert.deepEqual(boundEngine.env,engine.env);
  assert.equal(boundEngine.command,engine.command);
  assert.deepEqual(result.binding.nativeLauncher.grants,grants);
  assert.equal(result.binding.nativeLauncher.runtimeImmutable,true);
  const current=await broker(boundEngine,env);
  const marker={version:1,immutable:true,dependencyInstallation:false,policy:'foundation-dependency-installation'};
  let runtimeInspection,initializeRuntimePreparation;
  try {
    const reply=await current.request('initialize',{protocolVersion:1,clientCapabilities:{_meta:{'amplifier.dev/native':{version:1}}}});
    initializeRuntimePreparation=reply.agentCapabilities._meta['amplifier.dev/native'].runtimePreparation;
    assert.deepEqual(initializeRuntimePreparation,marker);
    const {sessionId}=await current.request('session/new',{cwd,mcpServers:[]});
    const mounted=await current.request('_amplifier/native',{sessionId,operation:'configuration.providers',args:{}});
    assert.equal(mounted.error,undefined);
    runtimeInspection=await current.request('_amplifier/runtime',{sessionId,cwd,surface:'mounted',refresh:true});
    assert.deepEqual(runtimeInspection.runtimePreparation,marker);
    assert.equal(runtimeInspection.generation,null);
    assert.equal(runtimeInspection.resident,true);
    assert.equal(runtimeInspection.available,true);
    assert.equal(runtimeInspection.scope,'resident-native-worker');
    assert.equal(typeof runtimeInspection.instanceId,'string');
    assert.ok(runtimeInspection.instanceId.length>0);
    assert.equal(runtimeInspection.python.executable,installedBefore.executable);
    assert.ok(runtimeInspection.items.some(row=>row.kind==='providers'&&row.id==='fixture'));
    const schema=await current.request('_amplifier/admin',{cwd,operation:'providers.schema',args:{id:'fixture',module:'provider-fixture'}});
    assert.equal(schema.error,undefined);
    const defaults=await current.request('_amplifier/admin',{cwd,operation:'configuration.defaults',args:{bundle:bundleFile}});
    assert.equal(defaults.error,undefined);
    await result.verify();
  } finally {await current.close();}
  assert.deepEqual(await readFile(baseFile),nativeBytes);
  assert.deepEqual(await readFile(settingsFile),settingsBytes);
  assert.deepEqual(Buffer.from(JSON.stringify(configuration)),configurationBytes);
  assert.deepEqual(await inspectPython(),installedBefore);
  await assert.rejects(access(installerAudit),{code:'ENOENT'});
  await assert.rejects(access(completionAudit),{code:'ENOENT'});
  if(process.env.NATIVE_IMMUTABLE_ACCEPTANCE)await writeFile(process.env.NATIVE_IMMUTABLE_ACCEPTANCE,JSON.stringify({
    scope:'installed-native-signed-binder-offline-fixture',binding:result.binding.nativeLauncher,
    initializeRuntimePreparation,runtimeInspection,inheritedImmutable:'0',generationMarkerAbsent:true,
    privateBaseBytesUnchanged:true,settingsBytesUnchanged:true,compositionBytesUnchanged:true,
    interpreterRegularFileCount:Object.keys(installedBefore.files).length,interpreterRegularFilesUnchanged:true,
    installerInvoked:false,providerCompletionInvoked:false,
    limits:['signature verification is a caller precondition','synthetic provider mount; no account requests',
      'interpreter inventory excludes symlinks and bytecode; external dependency trees not inventoried here',
      'runtime inspection is policy and mount evidence, not loaded-code attestation','no live deployment']
  },null,2)+'\n',{mode:0o600});
});
