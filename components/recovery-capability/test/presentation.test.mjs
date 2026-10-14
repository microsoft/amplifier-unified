import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,realpath,rm,writeFile,readFile} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';import {randomUUID} from 'node:crypto';import {DatabaseSync} from 'node:sqlite';
const {createRecoveryCapabilities,createPresentationCapabilities}=await import(process.env.RECOVERY_PACKAGE_MODULE??'../dist/index.js');
const hostModule=process.env.RECOVERY_PRESENTATION_HOST_MODULE;
const {createHost}=hostModule?await import(hostModule):{};
const context={actorId:'owner',clientId:'owner',origin:'ui'};
const wait=async fn=>{for(let i=0;i<1000;i++){const value=await fn();if(value)return value;await new Promise(r=>setTimeout(r,5));}throw Error('Recovery job did not settle');};
async function fixture(count=3,presentationOnly=false){
 const directory=await realpath(await mkdtemp(join(tmpdir(),'recovery-presentation-'))),rows=new Map(),checkpoints=new Map(),receipts=new Map();
 for(let i=0;i<count;i++){const uri='ahp-session:/'+String(i).padStart(4,'0');rows.set(uri,{uri,engineId:'fixture',nativeSessionId:'native-'+i,workingDirectory:directory,title:'Conversation '+i,kind:'root',createdAt:'2026-01-01T00:00:00Z',modifiedAt:'2026-01-01T00:00:00Z',workspaceAvailability:'present'});}
 const canonicalPath=join(directory,'canonical-transcript.jsonl'),canonicalBytes='retained input and unknown command receipt\n';await writeFile(canonicalPath,canonicalBytes);
 let host,owner,dispatches=0,applyCalls=0,rebuildCalls=0,failAt=0,loseReply=false,loseRebuildReply=false,mutateReceipt,mutatePage,mutateRebuild,hold;const authorizations=[];
 const catalog={get:async s=>structuredClone(rows.get(s)),list:async()=>({items:[...rows.values()].filter(r=>!r.productHidden),nextCursor:null}),upsert:async r=>rows.set(r.uri,r),status:async()=>({phase:'complete',issues:0}),sessionVisibilityProjectionStatus:async({source})=>({source,revision:checkpoints.get(source)??0}),sessionVisibilityProjectionReceipt:async({commandId})=>receipts.get(commandId)??null,projectSessionVisibility:async r=>{
  dispatches++;if(hold)await hold;if(dispatches===failAt)throw Error('Unsent second projection');assert.equal(r.afterRevision,checkpoints.get(r.source)??0);
  for(const item of r.items)rows.get(item.uri).productHidden=item.hidden;const receipt={source:r.source,commandId:r.commandId,revision:r.throughRevision,projected:r.items.length};checkpoints.set(r.source,r.throughRevision);receipts.set(r.commandId,receipt);return receipt;
 }};
 const presentation={presentationReset:{version:1,preservesCanonical:true,preservesAuthority:true},
  prepareConversationPresentation:(...args)=>host.prepareConversationPresentation(...args),
  readConversationPresentationReview:async(...args)=>{const page=await host.readConversationPresentationReview(...args);return mutatePage?mutatePage(page):page;},
  applyConversationPresentation:async(input,actor)=>{assert.deepEqual(Object.keys(input).sort(),['commandId','reviewHash','reviewId']);applyCalls++;const receipt=await host.applyConversationPresentation(input,actor);if(loseReply)throw Error('Lost committed host reply');return mutateReceipt?mutateReceipt(receipt):receipt;},
  conversationPresentationReceipt:(...args)=>host.conversationPresentationReceipt(...args),reconcileConversationPresentation:(...args)=>host.reconcileConversationPresentation(...args),inspectConversationPresentation:()=>host.inspectConversationPresentation(),
  rebuildConversationPresentation:async(...args)=>{rebuildCalls++;const r=await host.rebuildConversationPresentation(...args);if(loseRebuildReply)throw Error('Lost rebuild reply');return mutateRebuild?mutateRebuild(r):r;},
  reconcileConversationPresentationRebuild:(...args)=>host.reconcileConversationPresentationRebuild(...args),
 };
 const forbidden=async()=>{assert.fail('Visibility must not acquire or release global maintenance');};
 const options={directory:join(directory,'recovery'),nativeAuthority:'unused-native',nativeAdmin:async()=>{throw Error('Presentation must never call native maintenance');},resolveSession:async()=>{throw Error('Presentation must not resolve native history');},authorize:async(ctx,operation,args)=>{authorizations.push({operation,args});return {accountId:ctx.clientId==='other'?'other-account':'account'};},conversationPresentation:presentation,quiescence:{admitQuiescence:forbidden,inspectQuiescence:forbidden,quiescenceReceipt:forbidden,releaseQuiescence:forbidden,withQuiescenceMaintenance:forbidden}};
 const config={stateDirectory:join(directory,'host'),allowedWorkspaceRoots:[directory],catalog,engines:[{id:'fixture',command:'/must-not-start-presentation-worker'}],
  capabilities:{get manifest(){return owner.manifest;},get quiescenceAccess(){return owner.quiescenceAccess;},action:(...a)=>owner.action(...a),read:(...a)=>owner.read(...a)},
  conversationPresentation:{reconstructMetadata:async({readMappings})=>{let cursor;do{const p=readMappings(cursor);for(const r of p.items)rows.set(r.uri,structuredClone(r));cursor=p.nextCursor;}while(cursor);}},
 };
 owner=makeOwner();host=await createHost(config);
 function makeOwner(){return presentationOnly?createPresentationCapabilities({directory:options.directory,conversationPresentation:presentation,authorize:options.authorize}):createRecoveryCapabilities(options);}
 const call=(operation,args={},commandId=randomUUID(),ctx=context)=>host.invokeCapability({version:1,topic:'recovery',channel:'ahp-root://',operation,args,commandId},ctx);
 const settled=id=>wait(async()=>{const r=(await call('recovery.job',{jobId:id})).result;return ['prepared','succeeded','refused','unknown'].includes(r.state)?r:false;});
 const prepare=async(ids=[...rows.keys()],extra={})=>settled((await call('recovery.presentation.prepare',{operation:'reset',sessions:ids,reviewed:true,...extra})).result.id);
 const apply=async p=>settled((await call('recovery.presentation.apply',{preparedJobId:p.id,previewHash:p.previewHash})).result.id);
 const rebuild=async()=>settled((await call('recovery.presentation.rebuild',{expectedRevision:host.inspectConversationPresentation().revision,reviewed:true})).result.id);
 return {directory,rows,options,presentation,authorizations,get host(){return host;},get owner(){return owner;},call,settled,prepare,apply,rebuild,stats:()=>({applyCalls,dispatches,rebuildCalls}),failAt:v=>failAt=v,loseReply:v=>loseReply=v,loseRebuildReply:v=>loseRebuildReply=v,hold:v=>hold=v,mutateReceipt:fn=>mutateReceipt=fn,mutatePage:fn=>mutatePage=fn,mutateRebuild:fn=>mutateRebuild=fn,
  restart:async()=>{await owner.close();await host.close();owner=makeOwner();host=await createHost(config);},close:async()=>{try{await owner.close();assert.equal(host.diagnostics().activeAgents,0);assert.equal(await readFile(canonicalPath,'utf8'),canonicalBytes);}finally{await host.close();await rm(directory,{recursive:true,force:true});}}};
}

test('presentation requires a complete independent negotiated port, without reference census',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'recovery-no-presentation-'));let owner;
 try{const options={directory,nativeAuthority:'fixture',nativeAdmin:async()=>({}),authorize:async()=>({accountId:'account'}),resolveSession:async()=>({}),quiescence:{}};owner=createRecoveryCapabilities(options);assert.equal('recovery.presentation.prepare' in owner.actionSchemas(),false);assert.equal(owner.quiescenceParticipant.inspectPresentationReferences,undefined);assert.equal(owner.quiescenceParticipant.presentationReset,undefined);await owner.close();owner=undefined;assert.throws(()=>createRecoveryCapabilities({...options,conversationPresentation:{presentationReset:{version:1}}}),/Complete negotiated/);}finally{await owner?.close();await rm(directory,{recursive:true,force:true});}
});

test('real host/recovery: 500 explicit rows, 50-row pages, account binding and no global/native maintenance',{skip:!hostModule},async()=>{
 const f=await fixture(501);try{
  const sessions=[...f.rows.keys()];await assert.rejects(f.prepare(sessions),/bounded array/);await assert.rejects(f.prepare([sessions[0],sessions[0]]),/duplicate/);
  await assert.rejects(f.call('recovery.presentation.prepare',{operation:'reset',sessions:sessions.slice(0,2),reviewed:true},'cross-agent',{...context,origin:'agent',session:sessions[0]}),/own conversation/);
  await assert.rejects(f.prepare([sessions[0]],{expectedRevision:0}),/not permitted/);
  const prepared=await f.prepare(sessions.slice(0,500));assert.equal(prepared.state,'prepared');assert.equal(prepared.sessions.length,500);
  let cursor,seen=[];do{const page=(await f.call('recovery.preview',{jobId:prepared.id,limit:50,...(cursor?{cursor}:{})})).result;assert.equal(page.reviewHash,prepared.previewHash);assert.ok(page.items.length<=50);assert.equal(page.count,500);seen.push(...page.items.map(r=>r.session));cursor=page.nextCursor;}while(cursor);assert.equal(new Set(seen).size,500);
  await assert.rejects(f.call('recovery.preview',{jobId:prepared.id},'foreign',{...context,clientId:'other',actorId:'other'}),/unavailable/);
  f.mutatePage(p=>({...p,privateBeforeImage:{secret:'never-retain'}}));await assert.rejects(f.call('recovery.preview',{jobId:prepared.id}),/Invalid bounded/);f.mutatePage(undefined);
  const done=await f.apply(prepared);assert.equal(done.state,'succeeded');assert.equal(done.result.receipt.effect.count,500);assert.equal(done.result.receipt.projection.status,'ready');assert.equal(done.intakeFence,undefined);assert.equal(f.host.inspectQuiescence().intakeClosed,false);
  assert.ok(f.authorizations.some(x=>x.operation==='recovery.presentation.apply'&&x.args.sessions?.length===500&&x.args.operation==='reset'));
  const db=new DatabaseSync(join(f.directory,'recovery/recovery.sqlite'));const payload=String(db.prepare('SELECT payload FROM jobs WHERE id=?').get(prepared.id).payload);db.close();assert.ok(!payload.includes('Conversation '));assert.ok(!payload.includes('privateBeforeImage'));assert.ok(!payload.includes('workingDirectory'));
 }finally{await f.close();}
});

test('disjoint reset and undo use per-row identity/revision while preserving mixed prior visibility',{skip:!hostModule},async()=>{
 const f=await fixture(4);try{
  const ids=[...f.rows.keys()];const a=await f.prepare([ids[0]]),b=await f.prepare([ids[1]]);const first=await f.apply(a);assert.equal(first.state,'succeeded');assert.equal((await f.apply(b)).state,'succeeded');
  const undo=await f.prepare([ids[0]],{operation:'restore',resetJobId:first.id});assert.equal((await f.apply(undo)).state,'succeeded');assert.equal(f.rows.get(ids[0]).productHidden,false);assert.equal(f.rows.get(ids[1]).productHidden,true);
  const mixed=await f.apply(await f.prepare(ids.slice(0,3)));assert.equal(mixed.state,'succeeded');assert.equal((await f.apply(await f.prepare(ids.slice(0,3),{operation:'restore',resetJobId:mixed.id}))).state,'succeeded');assert.equal(f.rows.get(ids[0]).productHidden,false);assert.equal(f.rows.get(ids[1]).productHidden,true);
  await assert.rejects(f.prepare([ids[3]],{operation:'restore',resetJobId:first.id}),/selection required/);
 }finally{await f.close();}
});

test('same-row change after review refuses without another visibility effect',{skip:!hostModule},async()=>{
 const f=await fixture();try{const p=await f.prepare();assert.equal((await f.apply(await f.prepare())).state,'succeeded');const before=f.stats().dispatches,result=await f.apply(p);assert.equal(result.state,'refused');assert.equal(result.reason,'presentation-selection-refused');assert.equal(f.stats().dispatches,before);assert.equal(f.host.inspectQuiescence().intakeClosed,false);}finally{await f.close();}
});

test('lost committed reply survives host/owner restart; unknown job never blocks a disjoint reset',{skip:!hostModule},async()=>{
 const f=await fixture();try{
  const ids=[...f.rows.keys()],p=await f.prepare([ids[0]]);f.loseReply(true);const args={preparedJobId:p.id,previewHash:p.previewHash},result=await f.settled((await f.call('recovery.presentation.apply',args,'only-once')).result.id);assert.equal(result.state,'unknown');assert.equal(result.intakeFence,undefined);assert.equal(f.host.inspectQuiescence().intakeClosed,false);
  f.loseReply(false);assert.equal((await f.apply(await f.prepare([ids[1]]))).state,'succeeded');await f.restart();
  const repeated=await f.owner.action({version:1,topic:'recovery',channel:'ahp-root://',operation:'recovery.presentation.apply',commandId:'only-once',args},context);assert.equal(repeated.result.id,result.id);assert.equal(repeated.result.state,'unknown');
  const recovered=(await f.call('recovery.reconcile',{jobId:result.id})).result;assert.equal(recovered.state,'succeeded');assert.equal(f.stats().applyCalls,2);assert.equal(f.stats().dispatches,2);
 }finally{await f.close();}
});

test('confirmed markers survive undispatched projection; only explicit new rebuild repairs discovery',{skip:!hostModule},async()=>{
 const f=await fixture(105);try{
  f.failAt(2);const result=await f.apply(await f.prepare());assert.equal(result.state,'succeeded');assert.equal(result.result.receipt.projection.status,'unknown');assert.equal(result.reason,'presentation-saved-projection-pending');
  const before=f.stats();const r=(await f.call('recovery.reconcile',{jobId:result.id})).result;assert.equal(r.state,'succeeded');assert.equal(r.result.receipt.projection.status,'unknown');assert.deepEqual(f.stats(),before);assert.equal(f.host.inspectQuiescence().intakeClosed,false);
  await f.restart();f.failAt(0);const repaired=await f.rebuild();assert.equal(repaired.state,'succeeded');assert.equal(repaired.result.coverage,'derived-conversation-presentation');assert.equal(f.stats().applyCalls,1);assert.equal(f.stats().rebuildCalls,1);assert.equal(f.host.inspectConversationPresentation().ready,true);
  const after=(await f.call('recovery.reconcile',{jobId:result.id})).result;assert.equal(after.result.receipt.projection.status,'ready');assert.ok([...f.rows.values()].every(r=>r.productHidden));
 }finally{await f.close();}
});

test('lost rebuild reply reconciles its original receipt without resetting or rebuilding again',{skip:!hostModule},async()=>{
 const f=await fixture();try{
  assert.equal((await f.apply(await f.prepare())).state,'succeeded');f.loseRebuildReply(true);const args={expectedRevision:f.host.inspectConversationPresentation().revision,reviewed:true},r=await f.settled((await f.call('recovery.presentation.rebuild',args,'repair-once')).result.id);assert.equal(r.state,'unknown');await f.restart();
  assert.equal((await f.owner.action({version:1,topic:'recovery',channel:'ahp-root://',operation:'recovery.presentation.rebuild',commandId:'repair-once',args},context)).result.id,r.id);assert.equal((await f.call('recovery.reconcile',{jobId:r.id})).result.state,'succeeded');assert.equal(f.stats().rebuildCalls,1);assert.equal(f.stats().applyCalls,1);
  await assert.rejects(f.call('recovery.presentation.rebuild',args,'agent-rebuild',{...context,origin:'agent',session:[...f.rows.keys()][0]}),/account-level/);
 }finally{await f.close();}
});

test('malformed effect/projection receipts cannot claim success or retain private data',{skip:!hostModule},async()=>{
 for(const mutate of [r=>({...r,effect:{...r.effect,status:'unknown',revision:undefined}}),r=>({...r,commandId:'wrong'}),r=>({...r,effect:{...r.effect,count:999}}),r=>({...r,effect:{...r.effect,privateData:'secret'}}),r=>({...r,projection:{...r.projection,revision:-1}}),r=>({...r,effect:{...r.effect,revision:r.effect.previousRevision+2}})]){
  const f=await fixture();try{f.mutateReceipt(mutate);const result=await f.apply(await f.prepare());assert.equal(result.state,'unknown');assert.equal(result.intakeFence,undefined);assert.equal(f.host.inspectQuiescence().intakeClosed,false);assert.ok(!JSON.stringify(result).includes('privateData'));assert.equal((await f.call('recovery.reconcile',{jobId:result.id})).result.state,'succeeded');assert.equal(f.stats().applyCalls,1);}finally{await f.close();}
 }
});

test('typed refusal cannot be forged by a malformed post-effect rebuild receipt',{skip:!hostModule},async()=>{
 const f=await fixture();try{f.mutateRebuild(r=>({...r,privateBeforeImage:'secret',data:{executed:false},code:-32015}));const result=await f.rebuild();assert.equal(result.state,'unknown');assert.ok(!JSON.stringify(result).includes('secret'));assert.equal((await f.call('recovery.reconcile',{jobId:result.id})).result.state,'succeeded');assert.equal(f.stats().rebuildCalls,1);}finally{await f.close();}
});

test('unknown visibility does not veto real maintenance; active visibility joins shutdown',{skip:!hostModule},async()=>{
 const f=await fixture();let unblock;
 try{
  f.hold(new Promise(r=>unblock=r));const p=await f.prepare(),id=(await f.call('recovery.presentation.apply',{preparedJobId:p.id,previewHash:p.previewHash})).result.id;await wait(()=>f.stats().dispatches);
  const held={fenceId:'maintenance',commandId:'maintenance',purpose:'recovery',instanceId:'fixture',dataScope:'fixture'};assert.equal(await f.owner.quiescenceParticipant.acquire(held),null);
  f.loseReply(true);unblock();assert.equal((await f.settled(id)).state,'unknown');const lease=await f.owner.quiescenceParticipant.acquire(held);assert.ok(lease);await lease.release('unchanged',{kind:'admission-refused'});
  assert.equal(f.host.inspectQuiescence().intakeClosed,false);
 }finally{unblock?.();await f.close();}
});

test('unknown native jobs remain globally protected while disjoint presentation review stays independent',{skip:!hostModule},async()=>{
 const f=await fixture();try{
  const db=new DatabaseSync(join(f.directory,'recovery/recovery.sqlite')),id=randomUUID(),job={id,accountId:'account',commandId:id,state:'unknown',createdAt:1,revision:0,operation:'recovery.snapshot',sessions:[],fenceCommandId:'native'};
  db.prepare('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)').run(id,'account',id,'unknown',1,0,JSON.stringify(job));db.close();assert.equal((await f.prepare()).state,'prepared');
  const held={fenceId:'maintenance',commandId:'maintenance',purpose:'recovery',instanceId:'fixture',dataScope:'fixture'};assert.equal(await f.owner.quiescenceParticipant.acquire(held),null);
  const r=await f.call('recovery.prepare',{sessions:['ahp-session:/0000'],parts:['session-state'],privateContentReviewed:true});assert.equal(r.accepted,false);
 }finally{await f.close();}
});

test('visibility close joins an in-flight call without replay; stale rebuild revision is a proven refusal',{skip:!hostModule},async()=>{
 const f=await fixture();let unblock;
 try{
  const refused=await f.settled((await f.call('recovery.presentation.rebuild',{expectedRevision:999,reviewed:true})).result.id);assert.equal(refused.state,'refused');assert.equal(refused.reason,'presentation-host-preflight-refused');assert.equal(f.stats().dispatches,0);
  f.hold(new Promise(r=>unblock=r));const p=await f.prepare();await f.call('recovery.presentation.apply',{preparedJobId:p.id,previewHash:p.previewHash});await wait(()=>f.stats().dispatches);
  let closed=false;const closing=f.owner.close().then(()=>closed=true);await new Promise(r=>setTimeout(r,15));assert.equal(closed,false);unblock();await closing;assert.equal(f.stats().applyCalls,1);
 }finally{unblock?.();await f.close();}
});


test('presentation-only factory refuses partial native composition and requires a complete host port',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'recovery-generic-config-'));
 try{
  const options={directory,authorize:async()=>({accountId:'account'})};
  assert.throws(()=>createPresentationCapabilities(options),/complete host port/);
  assert.throws(()=>createPresentationCapabilities({...options,conversationPresentation:{presentationReset:{version:1}}}),/Complete negotiated/);
  for(const key of ['nativeAuthority','nativeAdmin','resolveSession','quiescence','nativeMaintenance','appResetOwners','restoreDestinationChoices','leaseSeconds'])assert.throws(()=>createPresentationCapabilities({...options,[key]:undefined}),/cannot configure native/);
  assert.throws(()=>createRecoveryCapabilities(options),/requires its configured authority/);
 }finally{await rm(directory,{recursive:true,force:true});}
});

test('generic ACP host composes visibility only, survives lost reply, and restores without native admin or quiescence', {skip:!hostModule}, async()=>{
 const f=await fixture(3,true);try{
  assert.deepEqual(Object.keys(f.owner.actionSchemas()).sort(),['recovery.command','recovery.job','recovery.list','recovery.presentation.apply','recovery.presentation.inspect','recovery.presentation.prepare','recovery.presentation.rebuild','recovery.preview','recovery.reconcile']);
  const list=(await f.call('recovery.list')).result;assert.equal(list.coverage,'presentation-only');assert.deepEqual(list.capabilities.archiveParts,[]);assert.equal(list.capabilities.restoreAvailable,false);assert.equal(list.capabilities.appReset,undefined);assert.equal(list.capabilities.cacheInventory,undefined);
  await assert.rejects(f.owner.action({version:1,topic:'recovery',channel:'ahp-root://',operation:'recovery.prepare',args:{},commandId:'no-native'},context),/Unadvertised/);
  await assert.rejects(f.owner.resourceRead({channel:'ahp-root://',uri:'amplifier-recovery://archive/missing'},context),/Native recovery is not configured/);
  const p=await f.prepare(),args={preparedJobId:p.id,previewHash:p.previewHash};f.loseReply(true);
  const pending=await f.settled((await f.call('recovery.presentation.apply',args,'generic-once')).result.id);assert.equal(pending.state,'unknown');
  await f.restart();const command=(await f.call('recovery.command',{commandId:'generic-once'})).result;assert.equal(command.id,pending.id);
  const original=(await f.call('recovery.reconcile',{jobId:command.id})).result;assert.equal(original.state,'succeeded');assert.equal(f.stats().applyCalls,1);
  f.loseReply(false);const restored=await f.apply(await f.prepare([...f.rows.keys()],{operation:'restore',resetJobId:original.id}));assert.equal(restored.state,'succeeded');assert.ok([...f.rows.values()].every(row=>row.productHidden===false));assert.equal((await f.rebuild()).state,'succeeded');
  assert.equal(f.host.inspectQuiescence().intakeClosed,false);
  // Reusing a durable store must not reconcile native work through absent ports.
  const db=new DatabaseSync(join(f.directory,'recovery/recovery.sqlite')),nativeId=randomUUID();
  db.prepare('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)').run(nativeId,'account',nativeId,'unknown',1,0,JSON.stringify({id:nativeId,accountId:'account',commandId:nativeId,state:'unknown',createdAt:1,revision:0,operation:'recovery.appReset.apply',sessions:[],appResetCommands:{native:'retained-command'}}));db.close();
  await assert.rejects(f.call('recovery.reconcile',{jobId:nativeId}),/Native recovery is not configured/);
  assert.equal((await f.call('recovery.job',{jobId:nativeId})).result.state,'unknown');
 }finally{await f.close();}
});
