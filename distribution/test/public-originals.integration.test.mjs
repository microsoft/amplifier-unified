import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile,rename,chmod} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {randomUUID,createHash} from 'node:crypto';
import {once} from 'node:events';
import {WebSocket} from 'ws';
import {createDistribution} from '../src/index.js';
const hash=b=>createHash('sha256').update(b).digest('hex');
class Peer{
 constructor(ws){this.ws=ws;this.id=0;this.pending=new Map();ws.on('message',raw=>{const x=JSON.parse(raw),p=this.pending.get(x.id);if(p){this.pending.delete(x.id);clearTimeout(p.timer);x.error?p.reject(Object.assign(Error(x.error.message),x.error)):p.resolve(x.result);}});}
 static async open(url){const ws=new WebSocket(url.replace(/^http/,'ws')+'/ahp',{origin:url});await once(ws,'open');const p=new Peer(ws);await p.request('initialize',{channel:'ahp-root://',clientId:'owned-originals',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});return p;}
 request(method,params){const id=++this.id;return new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Request timed out: '+method)),120000);this.pending.set(id,{resolve,reject,timer});this.ws.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});}
 close(){this.ws.terminate();for(const p of this.pending.values()){clearTimeout(p.timer);p.reject(Error('Closed'));}this.pending.clear();}
}
test('actual createDistribution retains originals, scopes real fork bytes and recovers partial grants without replay',
 {skip:!process.env.UNIFIED_ORIGINALS_FIXTURE_BUILDER,timeout:240000},async()=>{
 const builder=process.env.UNIFIED_ORIGINALS_FIXTURE_BUILDER;assert.equal(hash(await readFile(builder)),process.env.UNIFIED_ORIGINALS_FIXTURE_BUILDER_SHA256);
 const {prepareNativeFixture}=await import(pathToFileURL(builder));
 const root=resolve(process.env.UNIFIED_ORIGINALS_RUN_DIRECTORY);assert.ok(root.includes('/native-host-resources-composition-20261004/runs/'));
 await mkdir(root,{recursive:false});const fixture=await prepareNativeFixture(root),web=join(root,'web');await mkdir(web);await writeFile(join(web,'index.html'),'<!doctype html><title>Owned qualification</title>');
 const config={account:'owned-originals',stateDirectory:join(root,'distribution-state'),webDirectory:web,defaultWorkspace:fixture.workingDirectory,allowedWorkspaceRoots:[fixture.workingDirectory],engines:[fixture.engine]};
 let app,peer;const evidence={status:'RUNNING',root,checks:[]};
 const open=async()=>{app=await createDistribution(config);peer=await Peer.open(app.url);assert.ok(app.host.config.publicOriginalResources);};
 const close=async()=>{peer?.close();peer=undefined;await app?.close();app=undefined;};
 const audit=async()=>{try{return (await readFile(fixture.auditFile,'utf8')).split('\n').filter(Boolean);}catch(e){if(e.code==='ENOENT')return [];throw e;}};
 const rows=async s=>{let cursor,revision,all=[];do{const p=await app.host.readPublicOriginalRows(s,{...(cursor?{cursor}:{}),...(revision?{expectedRevision:revision}:{})});assert.deepEqual(p.diagnostics,[]);revision=p.revision;all.push(...p.rows);if(!p.nextCursor)assert.equal(p.complete,true);cursor=p.nextCursor;}while(cursor);return all;};
 const submit=async(s,text,attachments)=>{const commandId=randomUUID();await app.host.submitTurn(s,{commandId,clientId:'owned-originals',text,attachments});const result=await app.host.waitForTurn(s,commandId,120000);assert.equal(result.status,'completed',JSON.stringify(result));return commandId;};
 const fork=async s=>{const commandId=randomUUID(),current=await app.host.inspectSessionHistory(s),result=await app.host.forkSession(s,{commandId,expectedHistoryRevision:current.historyRevision});assert.ok(result.uri);return {session:result.uri,commandId};};
 const upload=async(s,text,name)=>{const bytes=Buffer.from(text),action=(operation,args)=>app.resources.action({version:1,topic:'attachments',channel:s,operation,args});const made=(await action('attachments.create',{requestId:randomUUID(),name,contentType:'text/plain',size:bytes.length,sha256:hash(bytes)})).result.attachment;for(let offset=0;offset<bytes.length;offset+=262144)await app.resources.resourceProviders[1].write({uri:made.uploadUri+'&offset='+offset,encoding:'base64',data:bytes.subarray(offset,offset+262144).toString('base64'),mode:'append'});const descriptor=(await action('attachments.commit',{id:made.id,requestId:randomUUID()})).result.attachment;return {bytes,descriptor,attachment:{type:'resource',label:'Literal '+name,contentType:'text/plain',uri:descriptor.resourceUri}};};
 try{
  await open();const A='ahp-session:/'+randomUUID();await peer.request('createSession',{channel:A,provider:fixture.engine.id,workingDirectories:[pathToFileURL(fixture.workingDirectory).href]});
  const files=[await upload(A,'Exact alpha target bytes\n','alpha.txt'),await upload(A,'Exact beta target bytes\n','beta.txt')],literal='  Literal @mention text\n',input=await submit(A,literal,files.map(f=>f.attachment));
  const aRows=await rows(A),a=aRows.find(r=>r.publicMessage.inputId===input);assert.ok(a);assert.deepEqual(a.publicMessage.blocks,[{type:'text',text:literal},...files.map(f=>({type:'attachment',reference:{owner:'resources',id:f.attachment.uri},name:f.attachment.label,mimeType:'text/plain'}))]);
  const beforeCold=await audit();await close();await open();assert.deepEqual(await rows(A),aRows);assert.deepEqual(await audit(),beforeCold);evidence.checks.push('normal factory original text/cards survive passive cold restart');
  const B=await fork(A),b=(await rows(B.session)).find(r=>r.publicMessage.inputId===input);assert.deepEqual(b.publicMessage,a.publicMessage);assert.deepEqual(b.copySource.source,a.locator);
  const beforeGrantRead=await audit();assert.equal(await app.resources.resolveOriginalAttachment(B.session,{owner:'resources',id:files[0].attachment.uri}),null);assert.deepEqual(await audit(),beforeGrantRead);
  assert.equal((await app.host.reconcileOriginalForkAttachments(A,B.commandId)).grantCoverage,'complete');
  const bodyDirectory=join(config.stateDirectory,'resources/attachments');for(const f of files){const p=join(bodyDirectory,f.descriptor.id+'.blob');await rename(p,p+'.source-preserved');}
  const C=await fork(B.session),c=(await rows(C.session)).find(r=>r.publicMessage.inputId===input);assert.deepEqual(c.publicMessage,a.publicMessage);assert.deepEqual(c.copySource.source,b.locator);assert.equal((await app.host.reconcileOriginalForkAttachments(B.session,C.commandId)).grantCoverage,'complete');
  for(const target of [B.session,C.session])for(const f of files){const g=await app.resources.resolveOriginalAttachment(target,{owner:'resources',id:f.attachment.uri});assert.equal(g.status,'completed');assert.equal(new URL(g.accessUri).searchParams.get('session'),target);assert.notEqual(g.accessUri,f.attachment.uri);assert.equal(hash(await readFile(join(bodyDirectory,new URL(g.accessUri).hostname+'.blob'))),hash(f.bytes));await assert.rejects(app.resources.resolvePromptAttachment({session:target},{uri:f.attachment.uri}));await assert.rejects(app.resources.resolvePromptAttachment({session:A},{uri:g.accessUri}));}
  for(const f of files){const p=join(bodyDirectory,f.descriptor.id+'.blob');await rename(p+'.source-preserved',p);}evidence.checks.push('real Native A→B→C immediate lineage; owner grants copy B bytes while A unavailable; exact scope/body hashes');
  const projected=await app.host.readSessionContext(C.session);assert.ok(projected.messages.some(m=>m.text===literal));
  const newInput=await submit(C.session,'Resolve unchanged original cards',files.map(f=>f.attachment));assert.deepEqual((await rows(C.session)).find(r=>r.publicMessage.inputId===newInput).publicMessage.blocks.slice(1),a.publicMessage.blocks.slice(1));assert.deepEqual((await rows(C.session)).find(r=>r.locator.messageId===c.locator.messageId),c);
  const providerLines=await audit();assert.equal(providerLines.length,2);for(const f of files)assert.ok(providerLines.at(-1).includes(f.bytes.toString().trim()));assert.ok(!providerLines.at(-1).includes('amplifier_public_message'));evidence.checks.push('factory resolves target access for actual provider prompt, retains opaque original reference and historical row');
  const D=await fork(A),d=(await rows(D.session)).find(r=>r.publicMessage.inputId===input);await app.resources.retainForkAttachments({version:2,commandId:D.commandId,target:{row:d.locator,blockIndex:1}});
  // Deny new body creation in this owned fixture directory; no private SQL or owner hooks.
  await chmod(bodyDirectory,0o500);let partial;try{partial=await app.host.reconcileOriginalForkAttachments(A,D.commandId);}finally{await chmod(bodyDirectory,0o700);}assert.equal(partial.qualified,1);assert.equal(partial.unresolved,1);assert.equal(partial.grantCoverage,'partial');
  const reference={owner:'resources',id:files[1].attachment.uri},unknown=await app.resources.resolveOriginalAttachment(D.session,reference);assert.equal(unknown.status,'unknown');assert.equal(unknown.accessUri,undefined);
  const count=await audit(),dRows=await rows(D.session),receipt=await app.host.inspectSessionLifecycleReceipt(A,D.commandId);await close();await open();assert.deepEqual(await rows(D.session),dRows);assert.deepEqual(await audit(),count);assert.deepEqual(await app.resources.resolveOriginalAttachment(D.session,reference),unknown);assert.deepEqual(await app.host.inspectSessionLifecycleReceipt(A,D.commandId),receipt);
  // Unknown grant cannot fall back to the original source URI at the root resolver.
  const unresolved={...files[1].attachment},refusedInput=randomUUID();await assert.rejects(app.host.submitTurn(D.session,{commandId:refusedInput,clientId:'owned-originals',text:'Must refuse unresolved grant',attachments:[unresolved]}));await assert.rejects(app.host.waitForTurn(D.session,refusedInput),/No admitted turn/);assert.equal(unresolved.uri,reference.id);assert.deepEqual(await audit(),count);
  assert.equal((await app.host.reconcileOriginalForkAttachments(A,D.commandId)).grantCoverage,'complete');assert.deepEqual(await audit(),count);assert.deepEqual(await rows(D.session),dRows);assert.deepEqual(await app.host.inspectSessionLifecycleReceipt(A,D.commandId),receipt);evidence.checks.push('filesystem copy failure remains unknown across factory restart; root refuses unresolved access; explicit continuation does not refork/replay');
  evidence.status='PASS_ACTUAL_CREATE_DISTRIBUTION';evidence.providerCompletions=providerLines.length;evidence.nativeForks=3;evidence.limits=['Current-primary exact rows only; exact archived grant recovery unqualified.','Deterministic offline provider; no default Work/delegation/model/browser/account/preview acceptance.','Native generation-unknown is separate from retained-grant unknown.'];
 }catch(error){evidence.status='FAILED';evidence.error={message:error.message,stack:error.stack};throw error;}
 finally{await writeFile(join(root,'EVIDENCE.json'),JSON.stringify(evidence,null,2)+'\n');await close();}
});
