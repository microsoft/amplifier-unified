import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,access} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {createWorktreeCapability} from '../src/index.js';
import {WorktreeStore} from '../src/store.js';
import {createGitWorker} from '../src/git-worker.js';
const python=process.env.WORKTREE_PYTHON||fileURLToPath(new URL('../.venv/bin/python',import.meta.url));
const session='ahp-session:/owned-test';
async function setup(options={}) {
  const root=await mkdtemp(join(tmpdir(),'unified-worktree-')),source=join(root,'source'),directory=join(root,'state');await mkdir(source);
  const git=(...args)=>execFileSync('git',['-c','core.hooksPath=/dev/null','-C',source,...args],{encoding:'utf8'}).trim();
  git('init','-q');git('config','user.name','Owned fixture');git('config','user.email','fixture@example.invalid');await writeFile(join(source,'tracked.txt'),'original\n');git('add','.');git('commit','-qm','fixture');
  const ctx={session,historyHome:source,workingDirectory:source,executionDirectory:source,executionRevision:0},effects=[],changes=[];
  let guarded=false;
  const owner=createWorktreeCapability({directory,python,inspectSession:async sid=>{assert.equal(sid,session);return {...ctx}},directoryInUse:async path=>({inUse:path===ctx.executionDirectory,sessions:[],pending:false}),withDirectoryGuard:async(path,fn)=>{assert.notEqual(path,ctx.executionDirectory);guarded=true;try{return await fn()}finally{guarded=false}},relocateSession:async(sid,args)=>{effects.push(args);assert.equal(sid,session);assert.equal(args.expectedExecutionRevision,ctx.executionRevision);ctx.executionDirectory=args.target;ctx.executionRevision++;return {applied:true,executionDirectory:args.target,executionRevision:ctx.executionRevision,receipt:{id:args.commandId}}},onChanged:(topic,scope)=>changes.push([topic,scope]),...options,...(options.wrapGit?{gitWorker:options.wrapGit(createGitWorker({directory:join(directory,'git'),python}))}:{})});
  const action=async(operation,args={},commandId=crypto.randomUUID(),caller={clientId:'fixture',origin:'ui'})=>(await owner.action({channel:session,topic:'worktrees',operation,version:1,args:{sessionId:session,...args},commandId},caller)).result;
  return {root,source,directory,ctx,owner,action,git,effects,changes,get guarded(){return guarded},async close(){await owner.close();await rm(root,{recursive:true,force:true})}};
}
test('installed public Git wheel, scoped receipts, dirty carry, handoff, safe cleanup',async()=>{
  const f=await setup();try{
    await writeFile(join(f.source,'tracked.txt'),'staged\n');f.git('add','.');await writeFile(join(f.source,'tracked.txt'),'unstaged\n');await writeFile(join(f.source,'private.txt'),'untracked\n');
    const inspected=await f.action('worktree.inspect'),before=f.git('status','--porcelain');
    const checkout=await f.action('worktree.create',{sourceRevision:inspected.repository.sourceRevision,mode:'carry_dirty'},'create');
    assert.equal(checkout.status,'ready');assert.equal(await readFile(join(checkout.path,'tracked.txt'),'utf8'),'unstaged\n');assert.equal(await readFile(join(checkout.path,'private.txt'),'utf8'),'untracked\n');assert.equal(f.git('status','--porcelain'),before);
    const duplicate=await f.action('worktree.create',{sourceRevision:inspected.repository.sourceRevision,mode:'carry_dirty'},'create');assert.equal(duplicate.id,checkout.id);assert.equal(duplicate.duplicate,true);
    await assert.rejects(f.action('worktree.create',{sourceRevision:'changed'},'create'),/different contents/);
    const pending=await f.action('worktree.handoff',{id:checkout.id,expectedExecutionRevision:0},'handoff');assert.equal(pending.phase,'pending');await f.owner.idle();
    assert.equal(f.ctx.executionDirectory,checkout.path);assert.equal(f.ctx.historyHome,f.source);assert.equal(f.effects.length,1);
    const state=await f.owner.read({uri:'amplifier-capability://worktrees',scope:session});assert.equal(state.data.worktrees[session].worktreeHandoffs[0].phase,'applied');assert.equal(state.data.worktrees[session].configurationBusy,false);
    await assert.rejects(f.action('worktree.remove',{id:checkout.id,expectedRevision:checkout.revision}),/still uses/);
    await f.action('worktree.handoff',{id:null,expectedExecutionRevision:1});await f.owner.idle();
    await assert.rejects(f.action('worktree.remove',{id:checkout.id,expectedRevision:checkout.revision},'dirty-remove'),/changed or ignored/);
    assert.equal(await readFile(join(f.source,'tracked.txt'),'utf8'),'unstaged\n');
    const manifest=JSON.parse((await f.owner.resourceProvider.read({channel:'ahp-root://',uri:checkout.manifestUri})).data);assert.equal(manifest.manifest.untracked[0].path,'private.txt');
    const clean=await f.action('worktree.create',{sourceRevision:inspected.repository.sourceRevision,mode:'clean'},'clean');
    const removed=await f.action('worktree.remove',{id:clean.id,expectedRevision:clean.revision},'remove');assert.equal(removed.status,'removed');await assert.rejects(access(clean.path));await access(f.source);
  }finally{await f.close()}
});
test('unknown host relocation stays fenced and duplicate commands do not execute again',async()=>{
  let attempted=0;const f=await setup({relocateSession:async()=>{attempted++;throw Object.assign(Error('transport lost after admission'),{receipt:{id:'host-receipt'}})}});try{
    const inspected=await f.action('worktree.inspect'),checkout=await f.action('worktree.create',{sourceRevision:inspected.repository.sourceRevision});
    const first=await f.action('worktree.handoff',{id:checkout.id,expectedExecutionRevision:0},'move');await f.owner.idle();
    const same=await f.action('worktree.handoff',{id:checkout.id,expectedExecutionRevision:0},'move');assert.equal(same.id,first.id);assert.equal(same.phase,'unknown');assert.equal(attempted,1);
    await assert.rejects(f.action('worktree.handoff',{id:null,expectedExecutionRevision:0},'another'),/unresolved/);
    await assert.rejects(f.action('worktree.remove',{id:checkout.id,expectedRevision:checkout.revision}),/unresolved/);
    await assert.rejects(f.action('worktree.reconcile',{id:same.id,expectedRevision:same.revision,destination:'source',evidence:'I inspected the location.'},'bad-agent',{origin:'agent'}),/actual finding/);
    assert.equal(attempted,1);
  }finally{await f.close()}
});
test('known refusal is rejected and history home is never moved',async()=>{
  const f=await setup({relocateSession:async()=>({applied:false,reason:'Session is busy',executed:false})});try{
    const pending=await f.action('worktree.handoff',{id:null,expectedExecutionRevision:0});await f.owner.idle();const result=f.owner.store.command(session,pending.id).value;
    assert.equal(result.phase,'rejected');assert.equal(f.ctx.executionDirectory,f.source);assert.equal(f.ctx.historyHome,f.source);
  }finally{await f.close()}
});
test('explicit UI reconciliation uses a new command and records the old unknown receipt',async()=>{
  let count=0,actual=[];const f=await setup({relocateSession:async(_session,args)=>{actual.push(args);if(++count===1)throw Object.assign(Error('lost'),{receipt:{id:'native-unknown'}});return {applied:true,executionDirectory:args.target,executionRevision:1,receipt:{id:args.commandId}}}});try{
    const pending=await f.action('worktree.handoff',{id:null,expectedExecutionRevision:0},'initial');await f.owner.idle();const unknown=f.owner.store.command(session,pending.id).value;
    const replacement=await f.action('worktree.reconcile',{id:unknown.id,expectedRevision:unknown.revision,destination:'source',evidence:'Verified the retained source writer was released.'},'resolve');await f.owner.idle();
    assert.notEqual(replacement.id,unknown.id);assert.equal(actual[1].reconciles,'native-unknown');assert.equal(f.owner.store.command(session,unknown.id).value.phase,'reconciled');assert.equal(count,2);
  }finally{await f.close()}
});
test('scope and action schemas reject changed identity before starting any worker',async()=>{
  const f=await setup();try{
    await assert.rejects(f.owner.action({channel:session,topic:'worktrees',version:1,operation:'worktree.list',args:{sessionId:'ahp-session:/other'}},{origin:'ui'}),/scope/);
    await assert.rejects(f.action('worktree.create',{sourceRevision:'x',mode:'unsafe'}),/Invalid mode/);
    await assert.rejects(f.action('worktree.list',{limit:100000}),/Invalid limit/);
    await assert.rejects(f.action('worktree.status',{id:'not-owned'}),/not found/);
  }finally{await f.close()}
});
test('lost Git acknowledgement recovers only its exact durable record and evidence resolution never reruns it',async()=>{
  let effects=0;const f=await setup({wrapGit:git=>({close:()=>git.close(),async request(method,args){const result=await git.request(method,args);if(method==='create'){effects++;throw Error('Lost acknowledgement after durable Git effect.')}return result}})});try{
    const inspection=await f.action('worktree.inspect');let receipt;
    await assert.rejects(f.action('worktree.create',{sourceRevision:inspection.repository.sourceRevision},'lost-create'),error=>{receipt=error.receipt;return true});
    assert.equal(receipt.phase,'unknown');assert.equal(f.owner.store.page(session).items.length,0);
    const duplicate=await f.action('worktree.create',{sourceRevision:inspection.repository.sourceRevision},'lost-create');assert.equal(duplicate.phase,'unknown');assert.equal(effects,1);
    const recovered=await f.action('worktree.status',{id:receipt.id});assert.equal(recovered.status,'ready');assert.equal(recovered.id,receipt.gitRecordId);assert.equal(f.owner.store.page(session).items.length,1);
    await assert.rejects(f.action('worktree.resolve',{id:receipt.id,expectedRevision:receipt.revision,resolution:'completed',evidence:'Saw durable checkout'},'bad-provenance',{origin:'agent'}),/actual finding/);
    const resolved=await f.action('worktree.resolve',{id:receipt.id,expectedRevision:receipt.revision,resolution:'completed',evidence:'Inspected the saved checkout and original source.'},'finding');assert.equal(resolved.resolved.phase,'applied');assert.equal(effects,1);assert.equal(await readFile(join(f.source,'tracked.txt'),'utf8'),'original\n');
  }finally{await f.close()}
});
test('25,000 cold records stay indexed; pages and restart touch no record directories',async()=>{
  const root=await mkdtemp(join(tmpdir(),'worktree-index-'));let store=new WorktreeStore(root);try{
    store.db.exec('BEGIN');for(let i=0;i<25000;i++)store.saveRecord(i<75?session:'ahp-session:/cold-'+i,{id:String(i),sessionId:i<75?session:'ahp-session:/cold-'+i,createdAt:i,path:'/cold/'+i,manifest:{}});store.db.exec('COMMIT');
    const page=store.page(session),second=store.page(session,'worktrees',{cursor:page.nextCursor});assert.equal(page.items.length,25);assert.equal(second.items.length,25);assert.equal(new Set([...page.items,...second.items].map(r=>r.id)).size,50);
    const plan=store.db.prepare('EXPLAIN QUERY PLAN SELECT value FROM records WHERE session=? ORDER BY created DESC,id LIMIT 26').all(session);assert.ok(plan.some(row=>row.detail.includes('records_session')));
    assert.throws(()=>store.page('ahp-session:/another','worktrees',{cursor:page.nextCursor}),/another scope/);
    store.begin({id:'pending',sessionId:session,operation:'worktree.handoff',phase:'pending',revision:1,createdAt:0,source:'/old',target:'/new'},'signature');store.close();store=new WorktreeStore(root);assert.equal(store.command(session,'pending').value.phase,'unknown');assert.ok(store.reserved('/new'));assert.equal(store.page(session).items.length,25);
  }finally{store.close();await rm(root,{recursive:true,force:true})}
});
