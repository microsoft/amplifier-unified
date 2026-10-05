import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,symlink,chmod,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import * as tar from 'tar';
import {SignedReleaseAdapter,releaseDigest,verifyReleaseTree,readSignedChannel} from '../dist/index.js';
import {publisher,artifact as nodeArtifact} from './release-fixtures.mjs';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {resolveArtifactRoles} from '../../../distribution/src/artifact-roles.mjs';
const hash=b=>createHash('sha256').update(b).digest('hex');
const context={commandId:'runtime-fixture',signal:new AbortController().signal};
async function fixture(t,kind='executable'){
 const base=await realpath(await mkdtemp(join(tmpdir(),'artifact-role-')));const pub=await publisher();
 t.after(async()=>{await pub.close();await rm(base,{recursive:true,force:true});});
 const root=join(base,'package'),state=join(base,'state');await mkdir(root);await mkdir(state);
 const data=kind==='static'?{'index.html':'fixture web'}:{'bin/role':'inert fixture executable','lib/app-1.0.dist-info/METADATA':'Metadata-Version: 2.1\nName: app\nVersion: 1.0\n\nfixture'};
 const files=[];
 for(const [path,body] of Object.entries(data)){await mkdir(join(root,path,'..'),{recursive:true});const mode=path==='bin/role'?0o755:0o644;await writeFile(join(root,path),body,{mode});files.push({path,sha256:hash(body),bytes:Buffer.byteLength(body),mode});}
 const archive=join(base,'input.tgz');await tar.c({file:archive,cwd:base,gzip:true},['package']);const bytes=await readFile(archive);
 const release={identity:{id:'fixture-v1',version:'1.0.0',revision:'a'.repeat(40),digest:''},artifact:{url:pub.origin+'/fixture-v1.tgz',sha256:hash(bytes),bytes:bytes.length},entrypoint:kind==='static'?'index.html':'bin/role',platform:process.platform,arch:process.arch,files,components:[{name:kind==='static'?'web':'app',version:'1.0',root:'',repository:'https://example.com/runtime.git',ref:'main',revision:'a'.repeat(40),...(kind==='static'?{}:{metadata:{kind:'python-dist-info',path:'lib/app-1.0.dist-info/METADATA'}})}],profile:{schema:'runtime-artifact-v1',kind,interface:{name:kind==='static'?'web-assets':'test-role',version:'1'},args:kind==='static'?[]:['-I','-B','-m','app'],writableRoots:kind==='static'?[]:['state']}};
 release.identity.digest=releaseDigest(release);pub.publish([{release,bytes}],1);
 const options={directory:join(base,'store'),channelUrl:pub.origin+'/channel.json',trustedKeys:pub.keys,accessScope:'fixture',allowedArtifactOrigins:[pub.origin],allowLoopbackHttp:true,resolveSources:async components=>components.map(c=>({...c,protected:false})),writableRoots:kind==='static'?{}:{state}};
 const adapter=new SignedReleaseAdapter(options);await adapter.check({...context,fresh:true});const target=await adapter.prepare(release.identity,context);const installed=await adapter.installed(target);
 return {base,pub,release,adapter,target,state,root:installed.root,options};
}
test('same signed adapter prepares role identity, standard Python metadata and private root names',async t=>{
 const f=await fixture(t);const role=await f.adapter.resolveRole(f.target,{name:'test-role',version:'1'},{state:f.state});
 assert.deepEqual(role.identity,f.release.identity);assert.equal(role.launch.command,join(f.root,'bin/role'));assert.deepEqual(role.launch.args,['-I','-B','-m','app']);assert.equal(role.launch.env.AMPLIFIER_RUNTIME_ROOT_STATE,f.state);
 assert.deepEqual(await f.adapter.resolveLaunch(f.target),role.launch);assert.ok(!JSON.stringify(f.release).includes(f.state));
 await assert.rejects(f.adapter.resolveRole(f.target,{name:'acp',version:'1'},{state:f.state}),/interface_mismatch/);
 await assert.rejects(f.adapter.resolveRole(f.target,role.interface,{}),/writable_roots_invalid/);
 await assert.rejects(f.adapter.resolveRole(f.target,role.interface,{state:f.root}),/writable_roots_invalid/);
 await assert.rejects(f.adapter.resolveRole(f.target,role.interface,{state:f.base}),/writable_roots_invalid/);
});
test('static Web identity resolves separately and cannot become executable',async t=>{
 const f=await fixture(t,'static');const role=await f.adapter.resolveRole(f.target,{name:'web-assets',version:'1'});assert.equal(role.launch,null);assert.equal(role.entrypoint,join(f.root,'index.html'));await assert.rejects(f.adapter.resolveLaunch(f.target),/static_artifact_not_executable/);
});
test('installed file tamper, undeclared package and outside symlink refuse role resolution',async t=>{
 const f=await fixture(t);const path=join(f.root,'bin/role');await writeFile(path,'tamper');await assert.rejects(f.adapter.resolveLaunch(f.target),/local_source_changes/);
 await writeFile(path,'inert fixture executable');await chmod(path,0o755);assert.equal(await verifyReleaseTree(f.root,f.release),true);
 await rm(path);await symlink(process.execPath,path);assert.equal(await verifyReleaseTree(f.root,f.release),false);await assert.rejects(f.adapter.resolveLaunch(f.target),/local_source_changes/);
});
test('profile/interface changes require a new signed digest and escape is refused at admission',async t=>{
 const f=await fixture(t);const changed=structuredClone(f.release);changed.profile.interface.version='2';assert.throws(()=>readSignedChannel(f.pub.envelope([changed],null),f.pub.keys),/digest_mismatch/);
 changed.identity.digest=releaseDigest(changed);assert.equal(readSignedChannel(f.pub.envelope([changed],null),f.pub.keys).channel.releases[0].profile.interface.version,'2');
 changed.entrypoint='../outside';assert.throws(()=>readSignedChannel(f.pub.envelope([changed],null),f.pub.keys),/invalid_release_path/);
 const bad=structuredClone(f.release);bad.profile.writableRoots=['/private/state'];assert.throws(()=>releaseDigest(bad),/artifact_profile_invalid/);
});

test('distribution role composition preserves independent identities and private roots',async t=>{
 const app=await fixture(t),web=await fixture(t,'static');
 const roles=await resolveArtifactRoles([{name:'native',adapter:app.adapter,target:app.target,interface:app.release.profile.interface,writableRoots:{state:app.state}},{name:'web',adapter:web.adapter,target:web.target,interface:web.release.profile.interface}]);
 assert.equal(roles.native.launch.command,join(app.root,'bin/role'));assert.equal(roles.web.launch,null);assert.notEqual(roles.native.identity.digest,roles.web.identity.digest);
 await assert.rejects(resolveArtifactRoles([{name:'native',adapter:app.adapter,target:app.target,interface:{name:'acp',version:'2'},writableRoots:{state:app.state}}]),/interface_mismatch/);
});

test('profile rejects undeclared installed metadata and loader overrides',async t=>{
 const f=await fixture(t);f.options.launchEnv={PYTHONPATH:'/outside'};await assert.rejects(f.adapter.resolveLaunch(f.target),/runtime_override_invalid/);
 const wrong=structuredClone(f.release);wrong.components[0].name='another';assert.equal(await verifyReleaseTree(f.root,wrong),false);
 const path=join(f.root,'lib/app-1.0.dist-info/METADATA');await writeFile(path,'Name: another\nVersion: 1.0\n');assert.equal(await verifyReleaseTree(f.root,f.release),false);
});

test('existing signed Node entrypoint actually runs without adopting the artifact profile',async t=>{
 const base=await realpath(await mkdtemp(join(tmpdir(),'node-role-'))),pub=await publisher();t.after(async()=>{await pub.close();await rm(base,{recursive:true,force:true});});
 const item=await nodeArtifact(base,1,pub.origin,'console.log("installed-node-fixture")');pub.publish([item],1);
 const adapter=new SignedReleaseAdapter({directory:join(base,'candidates'),channelUrl:pub.origin+'/channel.json',trustedKeys:pub.keys,accessScope:'fixture',allowedArtifactOrigins:[pub.origin],allowLoopbackHttp:true,resolveSources:async components=>components.map(c=>({...c,protected:false}))});
 await adapter.check({...context,fresh:true});const target=await adapter.prepare(item.release.identity,context),launch=await adapter.resolveLaunch(target);
 assert.equal((await promisify(execFile)(launch.command,launch.args,{cwd:launch.cwd,env:launch.env,timeout:10000})).stdout.trim(),'installed-node-fixture');
 await assert.rejects(adapter.resolveRole(target,{name:'ahp',version:'1'}),/interface_mismatch/);
});
