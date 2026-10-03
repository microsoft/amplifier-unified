import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,chmod,rm,access} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createGitSourceResolver} from '../src/source-tracking.js';

async function fixture(t){
 const directory=await mkdtemp(join(tmpdir(),'tracking-source-')),git=join(directory,'git-peer'),state=join(directory,'state.json'),log=join(directory,'calls.jsonl');
 await writeFile(git,`#!${process.execPath}
import {readFileSync,appendFileSync} from 'node:fs';
const args=process.argv.slice(2),state=JSON.parse(readFileSync(process.env.TEST_GIT_STATE,'utf8'));
appendFileSync(process.env.TEST_GIT_LOG,JSON.stringify(args)+'\\n');
if(args[0]==='check-ref-format')process.exit(0);
if(args.includes('--get-url')){console.log(state.rewrite??args.at(-1));process.exit(0);}
if(state.hang){setTimeout(()=>{},60000);}else if(state.fail){process.stderr.write('PRIVATE_CREDENTIAL_MUST_NOT_ESCAPE');process.exit(7);}else if(state.large){console.log('a'.repeat(70000));}else{
const ref=args.at(-1);console.log((state.revision??'a'.repeat(40))+'\\t'+ref);
if(state.peeled)console.log(state.peeled+'\\t'+ref+'^{}');
if(state.extra)console.log('c'.repeat(40)+'\\trefs/heads/unrequested');}
`);await chmod(git,0o700);await writeFile(state,'{}');
 t.after(()=>rm(directory,{recursive:true,force:true}));
 const repository='https://source.example.test/owner/project.git',sources=[{repository,ref:'main'}];
 return {repository,sources,git,state,log,set:value=>writeFile(state,JSON.stringify(value)),make:options=>createGitSourceResolver({sources,git,env:{TEST_GIT_STATE:state,TEST_GIT_LOG:log},...options})};
}

test('tracking observations are fresh, deduplicated and independent of publisher revision claims',async t=>{
 const f=await fixture(t),resolve=f.make(),component={...f.sources[0],revision:'f'.repeat(40)},context={signal:new AbortController().signal,fresh:true,reason:'activation'};
 const first=await resolve([component,component],context);assert.deepEqual(first,[{...f.sources[0],revision:'a'.repeat(40),protected:false}]);
 assert.equal((await readFile(f.log,'utf8')).trim().split('\n').filter(line=>line.includes('--exit-code')).length,1);
 await f.set({revision:'b'.repeat(40)});assert.equal((await resolve([component],context))[0].revision,'b'.repeat(40));
});
test('unknown source, explicit protected override and Git URL rewrite cannot authorize an update',async t=>{
 const f=await fixture(t),resolve=f.make();
 await assert.rejects(resolve([{repository:f.repository,ref:'unconfigured'}],{}),/source_not_configured/);
 await assert.rejects(f.make({sources:[{...f.sources[0],protected:true}]})(f.sources,{}),/source_preserved/);
 await assert.rejects(access(f.log));
 await f.set({rewrite:'file:///private/local-override'});await assert.rejects(resolve(f.sources,{}),/source_override_preserved/);
 assert.equal((await readFile(f.log,'utf8')).includes('--exit-code'),false);
});
test('annotated tags use the observed peeled commit and reject unrequested output',async t=>{
 const f=await fixture(t),sources=[{repository:f.repository,ref:'refs/tags/compatible'}],resolve=f.make({sources});
 await f.set({revision:'a'.repeat(40),peeled:'b'.repeat(40)});assert.equal((await resolve(sources,{}))[0].revision,'b'.repeat(40));
 await f.set({extra:true});await assert.rejects(resolve(sources,{}),/source_observation_invalid/);
 assert.throws(()=>f.make({sources:[{repository:f.repository,ref:'*'}]}),/source_ref_invalid/);
 assert.throws(()=>f.make({sources:[...f.sources,...f.sources]}),/source_configuration_duplicate/);
});
test('timeouts, oversized responses and private stderr fail without exposing provider details',async t=>{
 const f=await fixture(t);
 await f.set({fail:true});await assert.rejects(f.make()(f.sources,{}),error=>error.message==='source_observation_unavailable'&&!JSON.stringify(error).includes('PRIVATE_'));
 await f.set({large:true});await assert.rejects(f.make()(f.sources,{}),/source_observation_output_limit/);
 await f.set({hang:true});const start=Date.now();await assert.rejects(f.make({timeoutMs:150})(f.sources,{}),/source_observation_timeout/);assert.ok(Date.now()-start<3000);
 const controller=new AbortController();controller.abort();await assert.rejects(f.make()(f.sources,{signal:controller.signal}),/source_observation_aborted/);
});
