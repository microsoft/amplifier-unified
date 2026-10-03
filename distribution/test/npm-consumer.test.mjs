import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,readdir,rm,symlink} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {prepareNpmConsumer,installNpmConsumer} from './npm-consumer.mjs';
const execute=promisify(execFile);
async function fixture(t){
 const parent=await mkdtemp(join(tmpdir(),'npm-consumer-regression-'));t.after(()=>rm(parent,{recursive:true,force:true}));
 const owned=join(parent,'owned'),consumer=join(owned,'consumer');await mkdir(consumer,{recursive:true});
 const manifest=JSON.stringify({name:'parent-sentinel',private:true,workspaces:['owned/*']});
 await writeFile(join(parent,'package.json'),manifest);await writeFile(join(parent,'package-lock.json'),'parent-lock-sentinel');
 const packageRoot=join(owned,'fixture/package');await mkdir(packageRoot,{recursive:true});
 await writeFile(join(packageRoot,'package.json'),JSON.stringify({name:'isolation-fixture',version:'1.0.0',type:'module'}));
 await writeFile(join(packageRoot,'index.js'),'export const isolated=true;');
 const archive=join(owned,'fixture.tgz');await execute('tar',['-czf',archive,'-C',join(owned,'fixture'),'package']);
 return {parent,owned,consumer,archive,manifest};
}
async function parentIntact(f){assert.equal(await readFile(join(f.parent,'package.json'),'utf8'),f.manifest);assert.equal(await readFile(join(f.parent,'package-lock.json'),'utf8'),'parent-lock-sentinel');assert.deepEqual((await readdir(f.parent)).sort(),['owned','package-lock.json','package.json']);}

test('actual npm local install stays owned under parent workspace and hostile global/prefix configuration',async t=>{
 const f=await fixture(t),target=await prepareNpmConsumer(f.consumer,f.owned);
 await installNpmConsumer(target,[f.archive],{env:{...process.env,npm_config_prefix:f.parent,npm_config_global:'true',npm_config_workspaces:'true',npm_config_cache:join(f.owned,'cache'),npm_config_offline:'true'}});
 assert.equal(JSON.parse(await readFile(join(target.directory,'node_modules/isolation-fixture/package.json'),'utf8')).version,'1.0.0');await parentIntact(f);
});

test('missing manifest refuses before npm writes even inside an existing parent package',async t=>{
 const f=await fixture(t),target=await prepareNpmConsumer(f.consumer,f.owned);await rm(join(target.directory,'package.json'));
 await assert.rejects(installNpmConsumer(target,[f.archive]),/ENOENT/);await parentIntact(f);assert.deepEqual(await readdir(f.consumer),[]);
});

test('existing parent project and symlinked roots/modules cannot be claimed as consumers',async t=>{
 const f=await fixture(t);await assert.rejects(prepareNpmConsumer(f.parent,f.owned),/outside/);
 await assert.rejects(prepareNpmConsumer(f.parent,f.parent),/EEXIST/);
 const link=join(f.owned,'link');await symlink(f.parent,link);await assert.rejects(prepareNpmConsumer(link,f.owned),/outside|symlink/);
 const target=await prepareNpmConsumer(f.consumer,f.owned);await symlink(f.parent,join(target.directory,'node_modules'));await assert.rejects(installNpmConsumer(target,[f.archive]),/symlink/);await parentIntact(f);
});

test('npm resolved-root mismatch fails before invoking install',async t=>{
 const f=await fixture(t),target=await prepareNpmConsumer(f.consumer,f.owned),bin=join(f.owned,'bin');await mkdir(bin);
 const probe=join(bin,'npm');await writeFile(probe,'#!/bin/sh\nif [ "$1" = install ]; then touch "'+join(f.parent,'forbidden-install')+'"; fi\nprintf "%s\\n" "'+f.parent+'"\n',{mode:0o755});
 await assert.rejects(installNpmConsumer(target,[f.archive],{env:{...process.env,PATH:bin+':'+process.env.PATH}}),/resolved outside/);await parentIntact(f);
});

test('workflow CLI creates its manifest and runs actual npm only in the explicit owned consumer',async t=>{
 const f=await fixture(t);
 await execute(process.execPath,[new URL('./npm-consumer.mjs',import.meta.url).pathname,f.consumer,f.owned,f.archive],{cwd:f.parent,env:{...process.env,npm_config_global:'true',npm_config_prefix:f.parent,npm_config_workspaces:'true',npm_config_cache:join(f.owned,'cache'),npm_config_offline:'true'}});
 assert.equal(JSON.parse(await readFile(join(f.consumer,'node_modules/isolation-fixture/package.json'),'utf8')).name,'isolation-fixture');await parentIntact(f);
});
