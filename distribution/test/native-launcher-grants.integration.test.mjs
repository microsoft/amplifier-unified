import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, writeFile, rm, realpath} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createInterface} from 'node:readline';
import {once} from 'node:events';
import {bindReleaseConfiguration, inventoryMcpRuntime} from '../src/release-runtime.mjs';

const python=process.env.NATIVE_GRANTS_TEST_PYTHON;
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
