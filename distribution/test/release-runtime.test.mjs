import test from 'node:test';
import assert from 'node:assert/strict';
import {chmod, mkdir, mkdtemp, readFile, rm, symlink, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createHash} from 'node:crypto';
import {bindReleaseConfiguration, inventoryMcpRuntime} from '../src/release-runtime.mjs';
import {OWNERS, assertOwnerCensus} from '../src/validate-config.mjs';
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

async function ownerFixture(t) {
  const f=await nativeFixture(t), source=join(f.root,'owner-sources');
  await mkdir(source);
  for(const module of ['amplifier_acp','amplifier_session_catalog','amplifier_unified_media']){
    await mkdir(join(source,module));
    await writeFile(join(source,module,'__init__.py'),'# immutable qualified source\n');
    await writeFile(join(source,module,module==='amplifier_unified_media'?'worker.py':'__main__.py'),'# qualified executed module\n');
  }
  const python={tree:'environment',path:'bin/python'};
  const qualification={schema:'unified-python-runtime-qualification-v1',profile:'native-catalog-media-v1',
    python,launches:[
      {role:'native',module:'amplifier_acp'},
      {role:'catalog',module:'amplifier_session_catalog'},
      {role:'media',module:'amplifier_unified_media.worker'},
    ].map(({role,module})=>({role,module,flags:['-I','-B'],
      moduleFile:{tree:'owner-sources',path:module.split('.')[0]+(role==='media'?'/worker.py':'/__main__.py')},
      importPaths:[f.env,f.interpreter,source],noRuntimeWrites:true,editableInstalls:false}))};
  const qualificationPath=join(f.packageRoot,'release-inputs/owner-qualification.json');
  await writeFile(qualificationPath,JSON.stringify(qualification));
  const {inventoryPythonRuntime}=await import('../src/release-runtime.mjs');
  assert.equal(typeof inventoryPythonRuntime,'function','new owner inventory API must exist');
  const ownerManifest=await inventoryPythonRuntime({
    trees:[{id:'environment',root:f.env},{id:'interpreter',root:f.interpreter},{id:'owner-sources',root:source}],
    python,qualificationReceiptSha256:hash(await readFile(qualificationPath))});
  f.configuration.application.catalogProcess={command:'/fixture/python',
    args:['-I','-B','-m','amplifier_session_catalog','serve','--db','/fixture/catalog.sqlite',
      '--home','/fixture/shared','--app-home','/fixture/native','--workspace','/fixture/workspace',
      '--scan-interval','0','--workspace-check-interval','0'],env:{KEEP:'catalog'}};
  f.configuration.application.media={python:'/fixture/python',enableNative:true,settings:{keep:true}};
  f.configuration.application.engines.push({id:'other',command:'keep-other',args:['unchanged']});
  f.descriptor.schema='unified-release-runtime-v3';
  f.descriptor.ownerRuntime={profile:'native-catalog-media-v1',engineId:'amplifier',
    manifest:'release-inputs/owner-python.json',qualificationReceipt:'release-inputs/owner-qualification.json',
    mediaMode:'installed'};
  const writeOwner=async()=>{
    await writeFile(qualificationPath,JSON.stringify(qualification));
    ownerManifest.qualificationReceiptSha256=hash(await readFile(qualificationPath));
    await writeFile(join(f.packageRoot,f.descriptor.ownerRuntime.manifest),JSON.stringify(ownerManifest));
    f.args.configurationBytes=Buffer.from(JSON.stringify(f.configuration));
    f.descriptor.baseConfigurationSha256=hash(f.args.configurationBytes);
    await f.write();
  };
  await writeOwner();
  return {...f,source,qualification,qualificationPath,ownerManifest,writeOwner};
}

test('owner runtime changes only exact Python slots, fixed media mode and approved native config',async t=>{
  const f=await ownerFixture(t), before=structuredClone(f.configuration), result=await f.bind();
  const expected=structuredClone(before), python=join(f.env,'bin/python');
  expected.application.engines[0].command=python;
  expected.application.engines[0].args[5]=f.candidatePath;
  expected.application.catalogProcess.command=python;
  expected.application.media.python=python;
  expected.application.media.pythonMode='installed';
  expected.application.webDirectory=join(f.packageRoot,'web');
  expected.application.mcp.python=python;
  assert.deepEqual(result.configuration,expected);
  assert.deepEqual(f.configuration,before);
  assert.notEqual(result.configuration.application.engines[0].command,join(f.interpreter,'python'));
  assert.equal(result.binding.ownerRuntime.manifestSha256,hash(await readFile(join(f.packageRoot,f.descriptor.ownerRuntime.manifest))));
  assert.equal(result.binding.ownerRuntime.qualificationReceiptSha256,hash(await readFile(f.qualificationPath)));
  await result.verify();
});

test('signed message census admits the separately fenced owner without changing the private base',async t=>{
  const f=await ownerFixture(t);
  f.configuration.expectedOwners=[...OWNERS];
  f.descriptor.ownerCensus={profile:'native-message-metadata-v1'};
  await f.writeOwner();
  const before=structuredClone(f.configuration), bytes=Buffer.from(f.args.configurationBytes);
  const actual=[...OWNERS,'native-message-metadata'];
  // Causal baseline: the new, correctly separate participant failed the old gate.
  assert.throws(()=>assertOwnerCensus(actual,f.configuration.expectedOwners),/configured_owner_census_mismatch/);
  const result=await f.bind();
  assertOwnerCensus(actual,result.expectedOwners);
  assert.deepEqual(result.expectedOwners,actual);
  assert.equal(Object.isFrozen(result.expectedOwners),true);
  assert.deepEqual(result.binding.ownerCensus,{profile:'native-message-metadata-v1',expectedOwners:actual});
  assert.deepEqual(f.configuration,before);
  assert.deepEqual(result.configuration.expectedOwners,OWNERS);
  assert.deepEqual(f.args.configurationBytes,bytes);
  for(const observed of [OWNERS,actual.slice(1),[...actual,'unexpected'],[...actual,actual[0]]])
    assert.throws(()=>assertOwnerCensus(observed,result.expectedOwners),/configured_owner_census_mismatch/);
  await result.verify();
  f.descriptor.ownerCensus.profile='other';await f.write();
  await assert.rejects(result.verify(),/release_runtime_binding_invalid/);
});

test('message census accepts only its fixed v3 profile and never arbitrary owner lists',async t=>{
  const f=await ownerFixture(t);f.configuration.expectedOwners=[...OWNERS];
  for(const profile of [null,{},[],{profile:'other'}, {profile:'native-message-metadata-v1',owners:['anything']},
    {profile:'native-message-metadata-v1',remove:['native-admin']}]){
    f.descriptor.ownerCensus=profile;await f.writeOwner();
    await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  }
  f.descriptor.ownerCensus={profile:'native-message-metadata-v1'};
  for(const base of [undefined,[],[...OWNERS,OWNERS[0]],[...OWNERS,'native-message-metadata']]){
    f.configuration.expectedOwners=base;await f.writeOwner();
    await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  }
  for(const make of [fixture,nativeFixture]){
    const legacy=await make(t);legacy.configuration.expectedOwners=[...OWNERS];
    legacy.args.configurationBytes=Buffer.from(JSON.stringify(legacy.configuration));
    legacy.descriptor.baseConfigurationSha256=hash(legacy.args.configurationBytes);
    legacy.descriptor.ownerCensus={profile:'native-message-metadata-v1'};await legacy.write();
    await assert.rejects(legacy.bind(),/release_runtime_binding_invalid/);
  }
});

test('source and v3 without the profile retain the original owner census',async t=>{
  const f=await ownerFixture(t);f.configuration.expectedOwners=[...OWNERS];await f.writeOwner();
  const original=await f.bind();assert.equal(original.expectedOwners,undefined);
  assert.deepEqual(original.configuration.expectedOwners,OWNERS);
  f.descriptor.ownerCensus={profile:'native-message-metadata-v1'};await f.writeOwner();
  const source=await bindReleaseConfiguration({...f.args,source:true,runtime:{identity:f.old}});
  assert.equal(source.expectedOwners,undefined);assert.equal(source.binding,null);
  assert.deepEqual(source.configuration.expectedOwners,OWNERS);
});

test('owner profile rejects arbitrary override fields and unrecognized modes',async t=>{
  const f=await ownerFixture(t), baseline=structuredClone(f.descriptor.ownerRuntime);
  for(const change of [
    d=>{d.profile='arbitrary';},d=>{d.engineId='other';},d=>{d.mediaMode='bundled';},
    d=>{d.args=['-c','anything'];},d=>{d.env={PYTHONPATH:'/override'};},
    d=>{d.principal='caller';},d=>{d.command='/other';},d=>{d.manifest='../outside';},
    d=>{delete d.qualificationReceipt;},
  ]){
    f.descriptor.ownerRuntime=structuredClone(baseline);change(f.descriptor.ownerRuntime);
    await f.write();await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  }
});

test('owner binding requires existing agreeing slots and exact native/catalog launch shapes',async t=>{
  const f=await ownerFixture(t), baseline=structuredClone(f.configuration.application);
  for(const change of [
    a=>{delete a.catalogProcess;},a=>{delete a.media;},
    a=>{a.catalogProcess.command='/different';},a=>{a.media.python='/different';},
    a=>{a.engines[0].command='python3';},a=>{a.engines[0].args[1]='-E';},
    a=>{a.catalogProcess.args[1]='-E';},a=>{a.catalogProcess.args[3]='another';},
    a=>{a.catalogProcess.args[4]='other';},a=>{a.catalogProcess.args.push('--unknown','value');},
    a=>{a.catalogProcess.args.push('--db','/different');},
    a=>{a.media.pythonMode='arbitrary';},a=>{a.media.command='/bypass';},
    a=>{a.media.broker={command:'/bypass'};},
  ]){
    f.configuration.application=structuredClone(baseline);change(f.configuration.application);
    await f.writeOwner();await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  }
});

test('owner qualification requires recorded isolated module loads from inventoried roots',async t=>{
  const f=await ownerFixture(t), baseline=structuredClone(f.qualification);
  for(const change of [
    q=>{q.launches.pop();},q=>{q.launches[0].flags=['-m'];},
    q=>{q.launches[0].noRuntimeWrites=false;},q=>{q.launches[1].editableInstalls=true;},
    q=>{q.launches[2].module='other';},q=>{q.launches[2].role='native';},
    q=>{q.launches[0].moduleFile.path='../escape';},
    q=>{q.launches[0].moduleFile.path='missing/amplifier_acp/__main__.py';},
    q=>{q.launches[0].moduleFile.path='amplifier_acp/__init__.py';},
    q=>{q.launches[0].moduleFile.tree='unknown';},
    q=>{q.launches[0].importPaths.push('/uninventoried');},
    q=>{q.python.path='other';},q=>{q.arbitrary='no';},
  ]){
    for(const key of Object.keys(f.qualification))delete f.qualification[key];
    Object.assign(f.qualification,structuredClone(baseline));change(f.qualification);
    await f.writeOwner();await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  }
});

test('readiness detects owner source, manifest and qualification drift',async t=>{
  for(const kind of ['source','manifest','receipt','bytecode']){
    const f=await ownerFixture(t),result=await f.bind();
    if(kind==='source')await writeFile(join(f.source,'amplifier_acp/__init__.py'),'changed');
    if(kind==='manifest')await writeFile(join(f.packageRoot,f.descriptor.ownerRuntime.manifest),'{}');
    if(kind==='receipt')await writeFile(f.qualificationPath,'{}');
    if(kind==='bytecode')await mkdir(join(f.source,'amplifier_acp/__pycache__'));
    await assert.rejects(result.verify(),/release_runtime_binding_invalid/);
  }
});

test('Python owner inventory is separate from unchanged MCP v1 acceptance',async t=>{
  const f=await ownerFixture(t);
  const {verifyMcpRuntime,verifyPythonRuntime}=await import('../src/release-runtime.mjs');
  await assert.rejects(verifyMcpRuntime(f.ownerManifest),/release_runtime_binding_invalid/);
  await assert.rejects(verifyPythonRuntime(f.manifest),/release_runtime_binding_invalid/);
  assert.equal(await verifyMcpRuntime(f.manifest),join(f.env,'bin/python'));
  assert.equal(await verifyPythonRuntime(f.ownerManifest),join(f.env,'bin/python'));
  await rm(join(f.source,'amplifier_acp/__init__.py'));
  await symlink('/bin/sh',join(f.source,'amplifier_acp/__init__.py'));
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
});

test('owner descriptor never applies to source bootstrap or downgrades into v1/v2',async t=>{
  const f=await ownerFixture(t);
  const source=await bindReleaseConfiguration({...f.args,source:true,runtime:{identity:f.old}});
  assert.equal(source.configuration,f.configuration);
  for(const schema of ['unified-release-runtime-v1','unified-release-runtime-v2']){
    f.descriptor.schema=schema;await f.write();
    await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  }
});

test('owner receipt hash, isolated paths, permission drift and original runtime modes are enforced',async t=>{
  const f=await ownerFixture(t);
  await writeFile(f.qualificationPath,JSON.stringify({...f.qualification,extra:true}));
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  await f.writeOwner();
  const result=await f.bind();
  await chmod(join(f.source,'amplifier_acp/__init__.py'),0o666);
  await assert.rejects(result.verify(),/release_runtime_binding_invalid/);
  await chmod(join(f.source,'amplifier_acp/__init__.py'),0o644);
  f.configuration.application.media.pythonMode='installed';await f.writeOwner();
  await f.bind();
  f.configuration.application.media.pythonMode='bundled';await f.writeOwner();
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
});

test('binding keeps reviewed base command environments intact and refuses mismatch before owners start',async t=>{
  const f=await ownerFixture(t);
  f.configuration.application.engines[0].env={PRESERVE:'native'};
  f.configuration.application.catalogProcess.env={PRESERVE:'catalog'};
  f.configuration.application.media.env={PRESERVE:'media'};
  await f.writeOwner();
  const result=await f.bind();
  assert.deepEqual(result.configuration.application.engines[0].env,{PRESERVE:'native'});
  assert.deepEqual(result.configuration.application.catalogProcess.env,{PRESERVE:'catalog'});
  assert.deepEqual(result.configuration.application.media.env,{PRESERVE:'media'});
  f.descriptor.ownerRuntime.engineId='other';await f.write();
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
});

test('owner inventory allows bounded separate code roots without widening MCP v1',async t=>{
  const f=await fixture(t);
  const {inventoryPythonRuntime,verifyPythonRuntime,verifyMcpRuntime}=await import('../src/release-runtime.mjs');
  const trees=[{id:'environment',root:f.env},{id:'interpreter',root:f.interpreter}];
  for(let i=0;i<30;i++){
    const root=join(f.root,'checkout-'+i);await mkdir(root);await writeFile(join(root,'module.py'),'# sealed\n');
    trees.push({id:'checkout-'+i,root});
  }
  const options={trees,python:{tree:'environment',path:'bin/python'},qualificationReceiptSha256:'a'.repeat(64)};
  await assert.rejects(inventoryMcpRuntime(options),/release_runtime_binding_invalid/);
  const manifest=await inventoryPythonRuntime(options);
  assert.equal(await verifyPythonRuntime(manifest),join(f.env,'bin/python'));
  await assert.rejects(verifyMcpRuntime({...manifest,schema:'unified-mcp-runtime-v1'}),/release_runtime_binding_invalid/);
  const extra=join(f.root,'checkout-extra');await mkdir(extra);trees.push({id:'extra',root:extra});
  await assert.rejects(inventoryPythonRuntime(options),/release_runtime_binding_invalid/);
  await assert.rejects(verifyPythonRuntime({...manifest,trees:[...manifest.trees,{id:'extra',root:extra,entries:[]}]}),/release_runtime_binding_invalid/);
});

test('registry bookkeeping outside sealed code roots remains owner state',async t=>{
  const f=await ownerFixture(t),result=await f.bind();
  const registryState=join(f.root,'registry-state');await mkdir(registryState);
  await writeFile(join(registryState,'install-state.json'),'{"recorded":true}');
  await result.verify();
  // No exclusions are allowed within a code tree, even for similar file names.
  await writeFile(join(f.source,'install-state.json'),'{"unexpected":true}');
  await assert.rejects(result.verify(),/release_runtime_binding_invalid/);
});

async function evidenceFixture(t){
  const f=await ownerFixture(t), original=structuredClone(f.qualification);
  for(const key of Object.keys(f.qualification))delete f.qualification[key];
  Object.assign(f.qualification,{
    schema:'unified-python-runtime-qualification-v2',profile:original.profile,python:original.python,
    nativeMountedOriginsReceiptSha256:'d'.repeat(64),
    importCensus:{kind:'isolated-python-import-census',argvPrefix:['-I','-B','-c'],
      importPaths:original.launches[0].importPaths,
      entrypoints:original.launches.map(launch=>({role:launch.role,moduleFile:launch.moduleFile})),
      editableInstalls:false},
    launches:original.launches.map(launch=>({
      role:launch.role,module:launch.module,argvPrefix:['-I','-B','-m',launch.module],
      readiness:'protocol-response',moduleFile:launch.moduleFile,moduleFileEvidence:'entrypoint-resolution',
      workerImportPaths:null,noRuntimeWrites:true,
    })),
  });
  await f.writeOwner();
  return f;
}

test('separate import census never claims unobserved worker sys.path',async t=>{
  const f=await evidenceFixture(t), result=await f.bind();
  assert.equal(result.binding.ownerRuntime.qualificationReceiptSha256,hash(await readFile(f.qualificationPath)));
  assert.equal(f.qualification.launches[0].workerImportPaths,null);
  assert.deepEqual(f.qualification.importCensus.argvPrefix,['-I','-B','-c']);
  assert.deepEqual(f.qualification.launches[0].argvPrefix,['-I','-B','-m','amplifier_acp']);
  await result.verify();
});

test('v2 rejects substituted launch methods, missing readiness and mislabeled path evidence',async t=>{
  const f=await evidenceFixture(t), baseline=structuredClone(f.qualification);
  for(const change of [
    q=>{q.launches[0].argvPrefix=['-I','-B','-c','runpy.run_module'];},
    q=>{q.launches[0].argvPrefix=['-I','-m','amplifier_acp'];},
    q=>{q.launches[0].readiness='imported';},
    q=>{q.launches[0].noRuntimeWrites=false;},
    q=>{delete q.launches[0].workerImportPaths;},
    q=>{q.launches[0].workerImportPaths=['/uninventoried'];},
    q=>{q.launches[0].workerImportPaths=[];},
    q=>{q.launches[0].moduleFileEvidence='guessed';},
    q=>{q.launches[0].importPaths=q.importCensus.importPaths;},
    q=>{q.importCensus.kind='actual-worker-paths';},
    q=>{q.importCensus.argvPrefix=['-I','-B','-m'];},
    q=>{q.importCensus.importPaths.push('/uninventoried');},
    q=>{q.importCensus.editableInstalls=true;},
    q=>{delete q.nativeMountedOriginsReceiptSha256;},
    q=>{q.nativeMountedOriginsReceiptSha256='not-recorded';},
    q=>{q.importCensus.entrypoints.pop();},
    q=>{q.importCensus.entrypoints[0].moduleFile.path='amplifier_acp/__init__.py';},
    q=>{q.importCensus.entrypoints[0].moduleFile.path='missing/amplifier_acp/__main__.py';},
    q=>{q.importCensus.entrypoints[0].role='catalog';},
    q=>{q.launches[0].moduleFile.path='amplifier_acp/__init__.py';},
    q=>{q.launches[0].moduleFile={tree:'interpreter',path:'amplifier_acp/__main__.py'};},
  ]){
    for(const key of Object.keys(f.qualification))delete f.qualification[key];
    Object.assign(f.qualification,structuredClone(baseline));change(f.qualification);
    await f.writeOwner();await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  }
});

test('observed worker paths supplement the independent census and stay inventoried',async t=>{
  const f=await evidenceFixture(t);
  f.qualification.launches[2].moduleFileEvidence='worker-inspection';
  f.qualification.launches[2].workerImportPaths=[f.env,f.interpreter,f.source];
  await f.writeOwner();await f.bind();
  f.qualification.launches[2].workerImportPaths.push('/untracked-dynamic-code');
  await f.writeOwner();await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
});

test('receipt v1 is unchanged and cannot silently accept v2 census semantics',async t=>{
  const f=await ownerFixture(t);await f.bind();
  f.qualification.launches[0].importPaths=null;await f.writeOwner();
  await assert.rejects(f.bind(),/release_runtime_binding_invalid/);
  const g=await evidenceFixture(t);g.qualification.schema='unified-python-runtime-qualification-v1';
  await g.writeOwner();await assert.rejects(g.bind(),/release_runtime_binding_invalid/);
});

test('v2 preserves receipt hash, source byte and readiness revalidation',async t=>{
  const f=await evidenceFixture(t),result=await f.bind();
  await writeFile(f.qualificationPath,JSON.stringify({...f.qualification,unrecorded:true}));
  await assert.rejects(result.verify(),/release_runtime_binding_invalid/);
  await f.writeOwner();await result.verify();
  await writeFile(join(f.source,'amplifier_acp/__main__.py'),'changed entrypoint');
  await assert.rejects(result.verify(),/release_runtime_binding_invalid/);
});
