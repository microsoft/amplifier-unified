import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,rm,realpath,symlink} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {assembleSourceResolutionManifest,verifySourceAssembly,verifySourceAssemblyBeforeReady} from '../src/source-assembly.mjs';
import {inventoryPythonRuntime} from '../src/release-runtime.mjs';

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const execute = promisify(execFile);
const nativePath = 'site-packages/amplifier_acp/__init__.py';
const catalogPath = 'site-packages/amplifier_session_catalog/__init__.py';
const managedPath = 'site-packages/amplifier_module_context_managed/__init__.py';
const simplePath = 'site-packages/amplifier_module_context_simple/__init__.py';
const licensePath = 'site-packages/amplifier_acp-0.1.0.dist-info/licenses/LICENSE';

async function fixture(t,{behavior='success'}={}) {
  const root = await realpath(await mkdtemp(join(tmpdir(),'source-assembly-')));
  t.after(() => rm(root,{recursive:true,force:true}));
  const runtime = join(root,'runtime'), state = join(root,'owned-state');
  await mkdir(runtime); await mkdir(state);
  const manifest = JSON.parse(await readFile(new URL('./fixtures/source-assembly/native-v1.json',import.meta.url)));
  for (const row of manifest.sources) {
    row.sourceRoot = runtime; row.activePath = runtime;
    if (row.requestedUri.startsWith('file:')) row.requestedUri = 'file://'+runtime;
  }
  const contents = {[nativePath]:'native-before',[catalogPath]:'catalog-before',[simplePath]:'simple-reviewed',[managedPath]:'managed-reviewed',[licensePath]:'MIT attribution retained'};
  for (const [path,bytes] of Object.entries(contents)) {await mkdir(join(runtime,path,'..'),{recursive:true}); await writeFile(join(runtime,path),bytes,{mode:0o644});}
  const artifactPath = join(root,'native34.whl'), artifactBytes = 'independently reviewed archive fixture';
  await writeFile(artifactPath,artifactBytes,{mode:0o644});
  const reviewedArtifacts = [{artifact:{path:artifactPath,sha256:hash(artifactBytes),revision:'7'.repeat(40)},materialization:{sourceRoot:runtime,files:[
    {path:nativePath,sha256:hash('native-after'),bytes:12,mode:420},
    {path:licensePath,sha256:hash(contents[licensePath]),bytes:contents[licensePath].length,mode:420},
  ]}}];
  await writeFile(join(runtime,nativePath),'native-after');
  const assembled = assembleSourceResolutionManifest({manifest,reviewedArtifacts});
  const sourcePath = join(runtime,'source-policy.json');
  await writeFile(sourcePath,JSON.stringify(assembled.manifest));
  const nativeConfiguration = {path:join(root,'native.json')};
  const config = {home:state,appHome:state,runtimeImmutable:true,sourceResolutionManifest:{path:sourcePath,sha256:hash(await readFile(sourcePath))}};
  await writeFile(nativeConfiguration.path,JSON.stringify(config),{mode:0o600});
  nativeConfiguration.sha256 = hash(await readFile(nativeConfiguration.path));
  const audit = join(state,'requests.json'), command = join(runtime,'python');
  // Deliberately synthetic broker: validates exact argv/env/config and records
  // every request. This is causal gate coverage, not installed Native acceptance.
  const broker = `#!${process.execPath}\nimport fs from 'node:fs';
const args=process.argv.slice(2);
if(JSON.stringify(args.slice(0,5))!==JSON.stringify(['-I','-B','-m','amplifier_acp','--config']))process.exit(9);
const config=JSON.parse(fs.readFileSync(args[5]));
if(!config.runtimeImmutable||process.env.AUDIT!==${JSON.stringify(audit)})process.exit(10);
let input='',requests=[];
process.stdin.on('data',bytes=>{input+=bytes;while(input.includes('\\n')){
 const i=input.indexOf('\\n'),row=JSON.parse(input.slice(0,i));input=input.slice(i+1);requests.push(row);fs.writeFileSync(process.env.AUDIT,JSON.stringify(requests));
 if(row.method!=='initialize'||requests.length!==1)process.exit(11);
 const behavior=${JSON.stringify(behavior)};
 if(behavior==='hang')return;
 if(behavior==='exit')process.exit(12);
 if(behavior==='mutate')fs.writeFileSync(${JSON.stringify(join(runtime,nativePath))},'unauthorized');
 if(behavior==='stderr')process.stderr.write('fixture warning');
 if(behavior==='error')process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:1,error:{code:-1,message:'refused'}})+'\\n');
 else process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:1,result:{protocolVersion:behavior==='wrong-version'?2:1}})+'\\n');
}});
process.stdin.on('end',()=>process.exit(0));\n`;
  await writeFile(command,broker,{mode:0o755});
  const ownerRuntime = await inventoryPythonRuntime({trees:[{id:'runtime',root:runtime}],python:{tree:'runtime',path:'python'},qualificationReceiptSha256:'a'.repeat(64)});
  const native = {command,args:['-I','-B','-m','amplifier_acp','--config',nativeConfiguration.path],cwd:state,env:{AUDIT:audit},initialize:{protocolVersion:1,clientCapabilities:{_meta:{'amplifier.dev/native':{version:1}}}}};
  const plan = {manifest,reviewedArtifacts,ownerRuntime,nativeConfiguration,native};
  return {root,runtime,state,plan,assembled,artifactPath,sourcePath,audit};
}

test('one physical inventory projects approved bytes across URI rows; schema, membership, RCs and legal attribution stay exact',async t => {
  const f = await fixture(t), before = structuredClone(f.plan);
  const result = assembleSourceResolutionManifest(f.plan);
  assert.equal(result.inventory.length,5);
  for (let i=0;i<before.manifest.sources.length;i++) {
    const old = before.manifest.sources[i], current = result.manifest.sources[i];
    assert.deepEqual({...current,files:old.files},old);
    assert.deepEqual(Object.keys(current.files),Object.keys(old.files));
    assert.equal(current.files[nativePath],hash('native-after'));
    for (const path of [catalogPath,simplePath,managedPath,licensePath]) if (Object.hasOwn(old.files,path)) assert.equal(current.files[path],old.files[path]);
  }
  assert.deepEqual(f.plan,before); await verifySourceAssembly(f.plan);
});

test('conflicting donor declarations fail before approved artifact can hide them and before initialize',async t => {
  const f = await fixture(t); f.plan.manifest.sources[1].files[nativePath] = hash('contradictory donor');
  assert.throws(() => assembleSourceResolutionManifest(f.plan),/expected_digest_conflict/);
  await assert.rejects(verifySourceAssemblyBeforeReady(f.plan),/expected_digest_conflict/);
  await assert.rejects(readFile(f.audit),{code:'ENOENT'});
});

test('conflicting reviewed materializations and undeclared membership fail',async t => {
  const f = await fixture(t), second = structuredClone(f.plan.reviewedArtifacts[0]);
  second.materialization.files[0].sha256 = hash('other reviewed bytes'); f.plan.reviewedArtifacts.push(second);
  assert.throws(() => assembleSourceResolutionManifest(f.plan),/expected_digest_conflict/);
  second.materialization.files[0].sha256 = f.plan.reviewedArtifacts[0].materialization.files[0].sha256;
  second.materialization.files[0].bytes++;
  assert.throws(() => assembleSourceResolutionManifest(f.plan),/expected_digest_conflict/);
  f.plan.reviewedArtifacts.pop(); f.plan.reviewedArtifacts[0].materialization.files[0].path = 'new-member.py';
  assert.throws(() => assembleSourceResolutionManifest(f.plan),/membership_change_requires_review/);
});

function nestedRoots({secondDigest=hash('before')}={}) {
  const row = (uri,sourceRoot,path,digest) => ({requestedUri:uri,basePath:null,
    sourceRoot,activePath:sourceRoot,resolvedCommit:null,approval:'reviewed-fixture',
    files:{[path]:digest},admissionFiles:[path],packages:[]});
  return {manifest:{version:1,sources:[
    row('outer','/qualified','pkg/module.py',hash('before')),
    row('nested','/qualified/pkg','module.py',secondDigest),
  ]},reviewedArtifacts:[]};
}
const nestedReplacement = (sourceRoot,path,overrides={}) => ({
  artifact:{path:'/reviewed/native.whl',sha256:hash('reviewed archive'),revision:'7'.repeat(40)},
  materialization:{sourceRoot,files:[{path,sha256:hash('after'),bytes:5,mode:420,...overrides}]},
});

test('nested source roots with contradictory physical-file expectations refuse before replacement',() => {
  const input = nestedRoots({secondDigest:hash('contradiction')});
  input.reviewedArtifacts.push(nestedReplacement('/qualified','pkg/module.py'));
  assert.throws(() => assembleSourceResolutionManifest(input),/expected_digest_conflict/);
});

test('equal nested-root expectations coalesce into one deterministic inventory independent of row order',() => {
  const input = nestedRoots(), before = structuredClone(input);
  const result = assembleSourceResolutionManifest(input);
  assert.deepEqual(result.inventory,[{sourceRoot:'/qualified',path:'pkg/module.py',sha256:hash('before')}]);
  assert.deepEqual(result.manifest,input.manifest);
  input.manifest.sources.reverse();
  assert.deepEqual(assembleSourceResolutionManifest(input).inventory,result.inventory);
  input.manifest.sources.reverse(); assert.deepEqual(input,before);
});

test('approved replacement under either nested root updates every original row spelling',() => {
  for (const [root,path] of [['/qualified','pkg/module.py'],['/qualified/pkg','module.py']]) {
    const input = nestedRoots(), original = structuredClone(input.manifest);
    input.reviewedArtifacts.push(nestedReplacement(root,path));
    const result = assembleSourceResolutionManifest(input);
    assert.deepEqual(result.inventory,[{sourceRoot:'/qualified',path:'pkg/module.py',sha256:hash('after')}]);
    for (let i=0;i<original.sources.length;i++) {
      const row=result.manifest.sources[i], old=original.sources[i];
      assert.deepEqual({...row,files:old.files},old);
      assert.deepEqual(Object.keys(row.files),Object.keys(old.files));
      assert.deepEqual(Object.values(row.files),[hash('after')]);
    }
  }
});

test('nested-root reviewed materializations compare hashes, sizes and modes by physical file',() => {
  for (const overrides of [{sha256:hash('other')},{bytes:6},{mode:493}]) {
    const input = nestedRoots();
    input.reviewedArtifacts.push(nestedReplacement('/qualified','pkg/module.py'),nestedReplacement('/qualified/pkg','module.py',overrides));
    assert.throws(() => assembleSourceResolutionManifest(input),/expected_digest_conflict/);
  }
  const input = nestedRoots();
  input.reviewedArtifacts.push(nestedReplacement('/qualified','pkg/module.py'),nestedReplacement('/qualified/pkg','module.py'));
  const result = assembleSourceResolutionManifest(input);
  input.reviewedArtifacts.reverse();
  assert.deepEqual(assembleSourceResolutionManifest(input),result);
});

test('nested-root replacement verifies the single final physical file against reviewed bytes',async t => {
  const f=await fixture(t), parent=f.plan.manifest.sources[0];
  f.plan.manifest.sources.push({...structuredClone(parent),requestedUri:'nested-installed-package',
    sourceRoot:join(f.runtime,'site-packages/amplifier_acp'),activePath:join(f.runtime,'site-packages/amplifier_acp'),
    files:{'__init__.py':parent.files[nativePath]},admissionFiles:['__init__.py'],packages:[]});
  const result=await verifySourceAssembly(f.plan);
  assert.equal(result.inventory.length,5);
  assert.equal(result.manifest.sources.at(-1).files['__init__.py'],hash('native-after'));
});

test('unapproved observed-byte drift and archive substitution refuse; no disk digest becomes authority',async t => {
  const f = await fixture(t);
  await writeFile(join(f.runtime,managedPath),'managed drift');
  await assert.rejects(verifySourceAssemblyBeforeReady(f.plan),/observed_drift/);
  await writeFile(join(f.runtime,managedPath),'managed-reviewed');
  await writeFile(f.artifactPath,'different archive');
  await assert.rejects(verifySourceAssemblyBeforeReady(f.plan),/artifact_drift/);
  await assert.rejects(readFile(f.audit),{code:'ENOENT'});
});

test('stale final emitted manifest or changed exact configuration fails before broker starts',async t => {
  const f = await fixture(t);
  await writeFile(f.sourcePath,JSON.stringify(f.plan.manifest));
  await assert.rejects(verifySourceAssemblyBeforeReady(f.plan),/release_runtime_binding_invalid/);
  await writeFile(f.sourcePath,JSON.stringify(f.assembled.manifest));
  await writeFile(f.plan.nativeConfiguration.path,'{}',{mode:0o600});
  await assert.rejects(verifySourceAssemblyBeforeReady(f.plan),/native_configuration_drift/);
  await assert.rejects(readFile(f.audit),{code:'ENOENT'});
});

test('aliases cannot bypass physical-file identity',async t => {
  const f = await fixture(t), alias = join(f.root,'alias'); await symlink(f.runtime,alias);
  for (const row of f.plan.manifest.sources) {row.sourceRoot=alias;row.activePath=alias;}
  f.plan.reviewedArtifacts[0].materialization.sourceRoot=alias;
  await assert.rejects(verifySourceAssembly(f.plan),/path_invalid/);
});

test('exact initialize-only success is required before returning staging verification; source and config stay unchanged',async t => {
  const f = await fixture(t), before = await readFile(f.plan.nativeConfiguration.path);
  const result = await verifySourceAssemblyBeforeReady(f.plan);
  assert.deepEqual(result.initialization,{protocolVersion:1,exitCode:0,signal:null,requests:['initialize'],stderrBytes:0});
  const requests = JSON.parse(await readFile(f.audit));
  assert.deepEqual(requests,[{jsonrpc:'2.0',id:1,method:'initialize',params:f.plan.native.initialize}]);
  assert.deepEqual(await readFile(f.plan.nativeConfiguration.path),before);
});

for (const behavior of ['error','exit','stderr','wrong-version','mutate','hang']) test(`initialize ${behavior} cannot return a verified staging result`,async t => {
  const f = await fixture(t,{behavior}); let promoted = false;
  await assert.rejects(async () => {await verifySourceAssemblyBeforeReady(f.plan,{timeoutMs:behavior==='hang'?250:2000});promoted=true;});
  assert.equal(promoted,false);
  assert.deepEqual(JSON.parse(await readFile(f.audit)).map(r=>r.method),['initialize']);
});

test('thin staging CLI binds reviewed plan digest; emit is schema-only and verify requires initialize',async t => {
  const f = await fixture(t), planPath = join(f.root,'plan.json'), bytes = JSON.stringify(f.plan);
  await writeFile(planPath,bytes,{mode:0o600});
  const cli = new URL('../scripts/verify-source-assembly.mjs',import.meta.url).pathname;
  await assert.rejects(execute(process.execPath,[cli,'verify',planPath,'0'.repeat(64)]));
  await assert.rejects(readFile(f.audit),{code:'ENOENT'});
  const emitted = await execute(process.execPath,[cli,'emit',planPath,hash(bytes)]);
  assert.deepEqual(JSON.parse(emitted.stdout),f.assembled.manifest);
  await assert.rejects(readFile(f.audit),{code:'ENOENT'});
  const verified = await execute(process.execPath,[cli,'verify',planPath,hash(bytes)]);
  assert.equal(JSON.parse(verified.stdout).status,'assembly-verified');
  assert.equal(JSON.parse(await readFile(f.audit)).length,1);
});
