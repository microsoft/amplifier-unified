import test from 'node:test';
import assert from 'node:assert/strict';
import {chmod, mkdir, mkdtemp, readFile, rm, symlink, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createHash} from 'node:crypto';
import {bindReleaseConfiguration, inventoryMcpRuntime} from '../src/release-runtime.mjs';
const hash = b => createHash('sha256').update(b).digest('hex');

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'successor-binding-'));
  t.after(() => rm(root, {recursive:true, force:true}));
  const packageRoot = join(root, 'package'), env = join(root, 'environment'), interpreter = join(root, 'interpreter');
  for (const p of [packageRoot, env, interpreter, join(packageRoot, 'web'), join(packageRoot, 'release-inputs'), join(env,'bin')]) await mkdir(p);
  await writeFile(join(interpreter, 'python'), 'fixture interpreter, never executed', {mode:0o755});
  await symlink(join(interpreter,'python'), join(env,'bin/python'));
  await writeFile(join(env, 'mcp.py'), 'fixture new MCP');
  await writeFile(join(packageRoot,'web/index.html'), 'fixture new web');
  const old = {id:'old', version:'1.0.0', revision:'1'.repeat(40), digest:'1'.repeat(64)};
  const current = {id:'new', version:'2.0.0', revision:'2'.repeat(40), digest:'2'.repeat(64)};
  const configuration = {release:{prepared:{identity:old}}, authority:{scope:'unchanged'},
    application:{webDirectory:'old-web',mcp:{python:'old-python',stateDirectory:'preserve-mcp-state'},engines:[{oldNative:true}]}};
  const configurationBytes = Buffer.from(JSON.stringify(configuration));
  const manifest = await inventoryMcpRuntime({trees:[{id:'environment',root:env},{id:'interpreter',root:interpreter}],
    python:{tree:'environment',path:'bin/python'}, qualificationReceiptSha256:'3'.repeat(64)});
  const descriptor = {schema:'unified-release-runtime-v1',release:{id:current.id,version:current.version,revision:current.revision},
    baseConfigurationSha256:hash(configurationBytes),webDirectory:'web',mcpRuntime:'release-inputs/mcp.json'};
  const write = async () => {
    await writeFile(join(packageRoot,'release-runtime.json'), JSON.stringify(descriptor));
    await writeFile(join(packageRoot,'release-inputs/mcp.json'), JSON.stringify(manifest));
  };
  await write();
  const args={configuration, configurationBytes, runtime:{identity:current}, releaseRoot:packageRoot};
  return {root, packageRoot, env, interpreter, old, current, configuration, descriptor, manifest, write, args,
    bind:()=>bindReleaseConfiguration(args)};
}

test('successor changes only web and MCP Python, and revalidates external bytes',async t=>{
  const f=await fixture(t), result=await f.bind();
  assert.equal(result.configuration.application.webDirectory,join(f.packageRoot,'web'));
  assert.equal(result.configuration.application.mcp.python,join(f.env,'bin/python'));
  assert.equal(result.configuration.application.mcp.stateDirectory,'preserve-mcp-state');
  assert.deepEqual(result.configuration.authority,f.configuration.authority);
  assert.deepEqual(result.configuration.release,f.configuration.release);
  assert.deepEqual(result.configuration.application.engines,f.configuration.application.engines);
  assert.equal(f.configuration.application.mcp.python,'old-python');
  await result.verify();
  await writeFile(join(f.env,'mcp.py'),'changed same length?');
  await assert.rejects(result.verify(),/release_runtime_binding_invalid/);
});
test('source/bootstrap still requires exact prepared identity and never applies successor overlay',async t=>{
  const f=await fixture(t);
  await assert.rejects(bindReleaseConfiguration({...f.args,source:true}),/prepared_release_identity_mismatch/);
  const result=await bindReleaseConfiguration({...f.args,source:true,runtime:{identity:f.old}});
  assert.equal(result.configuration,f.configuration);
  assert.equal(result.binding,null);
});
test('descriptor absence allows only the original prepared identity for rollback',async t=>{
  const f=await fixture(t);await rm(join(f.packageRoot,'release-runtime.json'));
  await assert.rejects(f.bind(),/release_runtime_binding_required/);
  assert.equal((await bindReleaseConfiguration({...f.args,runtime:{identity:f.old}})).configuration,f.configuration);
});
test('wrong release identity and changed base configuration cannot launch',async t=>{
  const f=await fixture(t); f.descriptor.release.revision='4'.repeat(40);await f.write();
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  f.descriptor.release.revision=f.current.revision; await f.write();
  await assert.rejects(bindReleaseConfiguration({...f.args,configurationBytes:Buffer.from('different')}),/release_runtime_binding_invalid/);
});
test('arbitrary configuration override is rejected rather than merged',async t=>{
  const f=await fixture(t);f.descriptor.authority={scope:'different'};await f.write();
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
});
test('unlisted, missing or changed runtime files refuse launch',async t=>{
  const f=await fixture(t);
  await writeFile(join(f.env,'unexpected.py'),'surprise');
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  await rm(join(f.env,'unexpected.py'));await rm(join(f.env,'mcp.py'));
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  await writeFile(join(f.env,'mcp.py'),'changed');
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
});
test('Python link escapes, altered links and writable runtime files refuse',async t=>{
  const f=await fixture(t);
  await rm(join(f.env,'bin/python'));await symlink('/bin/sh',join(f.env,'bin/python'));
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  await assert.rejects(inventoryMcpRuntime({trees:[{id:'environment',root:f.env}],
    python:{tree:'environment',path:'bin/python'},qualificationReceiptSha256:'a'.repeat(64)}),/release_runtime_binding_invalid/);
  await rm(join(f.env,'bin/python'));await symlink(join(f.interpreter,'python'),join(f.env,'bin/python'));
  await chmod(join(f.env,'mcp.py'),0o666);await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
});
test('packaged asset paths cannot escape through traversal or symlinks',async t=>{
  const f=await fixture(t);f.descriptor.webDirectory='../environment';await f.write();
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  await symlink(join(f.packageRoot,'web'),join(f.packageRoot,'linked-web'));
  f.descriptor.webDirectory='linked-web';await f.write();
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
});
test('runtime manifest and descriptor remain bound after launch preflight',async t=>{
  const f=await fixture(t),result=await f.bind();
  f.manifest.qualificationReceiptSha256='4'.repeat(64);await f.write();
  await assert.rejects(result.verify(),/release_runtime_binding_invalid/);
});

test('an explicit command or external broker cannot bypass the signed MCP runtime',async t=>{
  const f=await fixture(t);
  for(const key of ['command','broker']){
    f.configuration.application.mcp[key]=key==='command'?'other-python':{command:'other-python'};
    const bytes=Buffer.from(JSON.stringify(f.configuration));
    f.descriptor.baseConfigurationSha256=hash(bytes);await f.write();
    await assert.rejects(bindReleaseConfiguration({...f.args,configurationBytes:bytes}),/release_runtime_binding_invalid/);
    delete f.configuration.application.mcp[key];
  }
});

async function nativeFixture(t) {
  const f = await fixture(t), path = join(f.root, 'native.json');
  const original = {home:'/fixture/shared', appHome:'/fixture/native', adminMaintenance:true,
    adminWorkspaceRoots:['/fixture/workspace'], moduleSources:{keep:'exact'}};
  const baseBytes = Buffer.from(JSON.stringify(original));
  await writeFile(path, baseBytes, {mode:0o600});
  f.configuration.application.nativeAdmin = {engine:'amplifier'};
  f.configuration.application.applicationUpdates = true;
  f.configuration.application.engines = [{id:'amplifier', command:'/fixture/python',
    args:['-I','-B','-m','amplifier_acp','--config',path], env:{FIXTURE:'unchanged'}}];
  f.args.configurationBytes = Buffer.from(JSON.stringify(f.configuration));
  f.descriptor.baseConfigurationSha256 = hash(f.args.configurationBytes);
  f.descriptor.schema = 'unified-release-runtime-v2';
  f.descriptor.nativeLauncher = {engineId:'amplifier', baseConfigurationSha256:hash(baseBytes),
    configuration:'release-inputs/native.json',
    grants:{adminVoiceCredentials:true, adminGenerations:true},
    qualificationReceiptSha256:'5'.repeat(64)};
  const candidate = {...original, ...f.descriptor.nativeLauncher.grants};
  const candidatePath = join(f.packageRoot, f.descriptor.nativeLauncher.configuration);
  await writeFile(candidatePath, JSON.stringify(candidate));
  await f.write();
  return {...f, path, original, baseBytes, candidate, candidatePath};
}

test('signed native grants change only the selected config argument and preserve base authority', async t => {
  const f = await nativeFixture(t), before = structuredClone(f.configuration), result = await f.bind();
  const engine = result.configuration.application.engines[0];
  assert.deepEqual(engine, {...before.application.engines[0],
    args:[...before.application.engines[0].args.slice(0,5),f.candidatePath]});
  assert.deepEqual(f.configuration, before);
  assert.deepEqual(await readFile(f.path), f.baseBytes);
  assert.equal(result.configuration.application.applicationUpdates, true);
  assert.deepEqual(result.binding.nativeLauncher.grants, {adminVoiceCredentials:true,adminGenerations:true});
  assert.equal(result.binding.nativeLauncher.configurationSha256, hash(await readFile(f.candidatePath)));
  await result.verify();
});

test('one explicit grant may be revoked without changing another permission', async t => {
  const f = await nativeFixture(t);
  f.descriptor.nativeLauncher.grants = {adminGenerations:false};
  await writeFile(f.candidatePath, JSON.stringify({...f.original,adminGenerations:false}));
  await f.write();
  const result = await f.bind();
  assert.deepEqual(result.binding.nativeLauncher.grants,{adminGenerations:false});
  assert.equal(JSON.parse(await readFile(f.candidatePath)).adminVoiceCredentials,undefined);
});

test('signed native descriptor rejects other grants, non-booleans and missing review evidence', async t => {
  const f = await nativeFixture(t), original = structuredClone(f.descriptor.nativeLauncher);
  for (const change of [
    d => {d.grants.adminMaintenance=true;}, d => {d.grants.home='/other';},
    d => {d.grants.adminGenerations='true';}, d => {d.grants={};},
    d => {delete d.qualificationReceiptSha256;}, d => {d.engineId='different';},
    d => {d.env={BYPASS:'yes'};}, d => {d.configuration='../native.json';},
    d => {d.baseConfigurationSha256='0'.repeat(64);},
  ]) {
    f.descriptor.nativeLauncher=structuredClone(original);change(f.descriptor.nativeLauncher);
    await f.write();await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  }
});

test('signed native config cannot alter homes, roots, sources, runtime or undeclared grants', async t => {
  const f = await nativeFixture(t);
  for (const change of [
    c => {c.home='/different';}, c => {c.adminWorkspaceRoots=['/'];},
    c => {c.moduleSources={};}, c => {c.runtimeManifest='/other';},
    c => {c.adminPermissions=true;}, c => {delete c.adminMaintenance;},
    c => {c.adminGenerations=false;},
  ]) {
    const candidate=structuredClone(f.candidate);change(candidate);
    await writeFile(f.candidatePath,JSON.stringify(candidate));
    await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  }
});

test('native binding requires an exact unambiguous trusted launch shape', async t => {
  const f = await nativeFixture(t), original=structuredClone(f.configuration.application);
  for (const change of [
    a => {a.engines.push(structuredClone(a.engines[0]));},
    a => {a.nativeAdmin.engine='other';},
    a => {a.engines[0].args.push('--config',f.path);},
    a => {a.engines[0].args[3]='other_module';},
    a => {a.engines[0].args[0]='-c';},
  ]) {
    f.configuration.application=structuredClone(original);change(f.configuration.application);
    f.args.configurationBytes=Buffer.from(JSON.stringify(f.configuration));
    f.descriptor.baseConfigurationSha256=hash(f.args.configurationBytes);await f.write();
    await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  }
});

test('native base remains private, canonical and byte-bound through readiness', async t => {
  const f=await nativeFixture(t), result=await f.bind();
  await chmod(f.path,0o644);await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  await assert.rejects(result.verify(),/release_runtime_binding_invalid/);await chmod(f.path,0o600);
  await writeFile(f.path,JSON.stringify({...f.original,adminGenerations:true}));
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  await assert.rejects(result.verify(),/release_runtime_binding_invalid/);
  await writeFile(f.path,f.baseBytes);
  await writeFile(f.candidatePath,JSON.stringify({...f.candidate,adminGenerations:false}));
  await assert.rejects(result.verify(),/release_runtime_binding_invalid/);
  await rm(f.candidatePath);await symlink(f.path,f.candidatePath);
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  await assert.rejects(result.verify(),/release_runtime_binding_invalid/);
});

test('native v2 is not applied to source bootstrap or silently accepted as v1', async t => {
  const f=await nativeFixture(t);
  const source=await bindReleaseConfiguration({...f.args,source:true,runtime:{identity:f.old}});
  assert.equal(source.configuration,f.configuration);
  assert.equal(source.binding,null);
  f.descriptor.schema='unified-release-runtime-v1';await f.write();
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
});
