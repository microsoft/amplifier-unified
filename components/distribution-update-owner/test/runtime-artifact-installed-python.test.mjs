import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,writeFile,readdir,stat,rm,symlink,access} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import * as tar from 'tar';
import {SignedReleaseAdapter,releaseDigest} from '../dist/releases.js';
import {publisher} from './release-fixtures.mjs';
const execute=promisify(execFile),hash=b=>createHash('sha256').update(b).digest('hex');
const owned=process.env.RUNTIME_ARTIFACT_FIXTURE;
test('actual standard installed Python role stays in its artifact and uses the common signed adapter',{skip:!owned},async t=>{
 const root=join(owned,'python-artifact'),files=[];
 async function walk(dir,prefix=''){for(const name of (await readdir(dir)).sort()){const path=prefix?prefix+'/'+name:name,f=join(dir,name),info=await stat(f);if(info.isDirectory())await walk(f,path);else{const data=await readFile(f);files.push({path,sha256:hash(data),bytes:data.length,mode:info.mode&0o777});}}}
 await walk(root);const pub=await publisher();t.after(()=>pub.close());
 const path=join(owned,'python-artifact.tgz');
 // Produce exactly the existing package/ archive convention, without copying
 // another runtime: tar from the artifact root's exact file list.
 try { await access(path); } catch { await tar.c({file:path,cwd:root,gzip:true,prefix:'package'},files.map(f=>f.path)); }const bytes=await readFile(path);
 const release={identity:{id:'fixture-v1',version:'1.0.0',revision:'a'.repeat(40),digest:''},artifact:{url:pub.origin+'/fixture-v1.tgz',sha256:hash(bytes),bytes:bytes.length},entrypoint:'bin/python3.13',platform:'linux',arch:'arm64',files,components:[{name:'cpython',version:'3.13.12',root:'',repository:'https://github.com/astral-sh/python-build-standalone.git',ref:'main',revision:'b'.repeat(40)},{name:'amplifier-publishing',version:'0.1.1',root:'lib/python3.13/site-packages/amplifier_publishing',repository:'https://github.com/microsoft/amplifier-publishing.git',ref:'main',revision:'6f1bfa6fbf32ff334cff3adeeffdf58360595bd4',metadata:{kind:'python-dist-info',path:'lib/python3.13/site-packages/amplifier_publishing-0.1.1.dist-info/METADATA'}}],profile:{schema:'runtime-artifact-v1',kind:'executable',interface:{name:'publishing-cli-fixture',version:'1'},args:['-I','-B','-m','amplifier_publishing.service','--help'],writableRoots:['state']}};
 release.identity.digest=releaseDigest(release);pub.publish([{release,bytes}],1);
 let observations=0;
 const adapter=new SignedReleaseAdapter({directory:join(owned,process.env.RUNTIME_ARTIFACT_STORE??'candidates'),channelUrl:pub.origin+'/channel.json',trustedKeys:pub.keys,accessScope:'fixture',allowedArtifactOrigins:[pub.origin],allowLoopbackHttp:true,resolveSources:async components=>{observations++;return components.map(c=>({...c,protected:false}));},writableRoots:{state:join(owned,'state')}});
 await adapter.check({fresh:true,commandId:'check',signal:new AbortController().signal});const target=await adapter.prepare(release.identity,{commandId:'prepare',signal:new AbortController().signal});
 const launch=await adapter.resolveLaunch(target);assert.ok(launch.command.startsWith(join(owned,process.env.RUNTIME_ARTIFACT_STORE??'candidates')+'/'));assert.match((await execute(launch.command,launch.args,{cwd:launch.cwd,env:launch.env,timeout:10000})).stdout,/Run an explicitly configured private service/);
 const probe='import json,sys,pathlib,amplifier_publishing,amplifier_publishing.service; root=pathlib.Path(sys.executable).parent.parent; paths=[p for p in sys.path if p]; assert all(pathlib.Path(p).is_relative_to(root) for p in paths); assert pathlib.Path(amplifier_publishing.__file__).is_relative_to(root); print(json.dumps({"executable":sys.executable,"prefix":sys.prefix,"module":amplifier_publishing.__file__,"sysPath":paths}))';
 const origins=JSON.parse((await execute(launch.command,['-I','-B','-c',probe],{cwd:launch.cwd,env:launch.env,timeout:10000})).stdout);assert.equal(origins.prefix,launch.cwd);assert.equal(observations,1);
 const installed=await adapter.installed(target);await writeFile(join(installed.root,'lib/python3.13/site-packages/amplifier_publishing/__init__.py'),'tamper');await assert.rejects(adapter.resolveLaunch(target),/local_source_changes/);
 const oldCommand=await readFile(join(root,'bin/python3.13'));await rm(join(installed.root,'bin/python3.13'));await symlink('/usr/bin/python3',join(installed.root,'bin/python3.13'));await assert.rejects(adapter.resolveLaunch(target),/local_source_changes/);
 for(const file of files)assert.equal(hash(await readFile(join(root,file.path))),file.sha256);
 await writeFile(join(owned,process.env.RUNTIME_ARTIFACT_RECEIPT??'QUALIFICATION.json'),JSON.stringify({fixtureOnly:true,identity:target.identity,origins,sourceResolverCalls:observations,actualInstalledModuleHelp:true,tamperRefused:true,outsideInterpreterRefused:true,donorAndSourceArtifactPreserved:true,files:files.length,bytes:files.reduce((n,f)=>n+f.bytes,0),limits:['Fixture source observations are synthetic; no upstream latest or provider acceptance.','Signature uses ephemeral test key only.','Not Native ACP, Host lifecycle or production release qualification.']},null,2)+'\n');
 assert.ok(oldCommand.length>0);
});
