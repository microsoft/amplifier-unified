import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,realpath,writeFile,readFile,rm,chmod} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomBytes,createHash} from 'node:crypto';
import {serveInstallationBackupControl,connectInstallationBackupControl} from '../src/installation-backup-control.js';
import {createApplicationUpdateCapabilities} from '../src/application-updates.js';
const hash=v=>createHash('sha256').update(v).digest('hex');
async function fixture(t){
 const root=await realpath(await mkdtemp(join(tmpdir(),'backup-control-'))),discoveryFile=join(root,'endpoint.json'),tokenFile=join(root,'token');
 const token=randomBytes(32).toString('hex');await writeFile(tokenFile,token,{mode:0o600});
 const receipts=new Map(),listeners=new Set(),calls=[],archive=Buffer.from('private whole installation backup');
 const coordinator={outlivesDistribution:true,inspect:async()=>({active:null,receipts:[...receipts.values()]}),
  review:async()=>({id:'review',digest:hash('review'),includesCredentials:true,encrypted:false}),
  submit:async(id,args)=>{calls.push(['submit',id,args]);if(!receipts.has(id))receipts.set(id,{commandId:id,status:'accepted',privatePath:'/private/retained'});return receipts.get(id);},
  receipt:async id=>receipts.get(id)??null,reconcile:async id=>receipts.get(id)??null,
  readArchive:async(id,{sha256,offset,maxBytes})=>{calls.push(['read',id]);const chunk=archive.subarray(offset,offset+maxBytes);return {commandId:id,sha256,offset,bytes:archive.length,data:chunk.toString('base64'),encoding:'base64',chunkSha256:hash(chunk)};},
  subscribe:f=>{listeners.add(f);return()=>listeners.delete(f);}};
 const binding={discoveryFile,installationId:'owned',dataScope:'owned'},server=await serveInstallationBackupControl({...binding,tokenFile,coordinator}),client=connectInstallationBackupControl(binding);
 t.after(async()=>{client.close();await server.close();await rm(root,{recursive:true,force:true});});
 return {root,binding,server,client,coordinator,calls,receipts,archive,token,discoveryFile,tokenFile,listeners};
}
test('private control binds installation and scope and never retries a lost or missing command',async t=>{
 const f=await fixture(t);assert.equal((await f.client.review()).includesCredentials,true);
 await f.client.submit('original',{reviewId:'review'});assert.equal((await f.client.receipt('original')).status,'accepted');
 assert.equal(await f.client.receipt('missing'),null);assert.equal(await f.client.reconcile('missing'),null);assert.equal(f.calls.length,1);
 const foreign=connectInstallationBackupControl({...f.binding,installationId:'other'});
 await assert.rejects(foreign.inspect(),/binding_conflict/);foreign.close();
 await chmod(f.tokenFile,0o644);await assert.rejects(f.client.inspect(),/private_file_required/);await chmod(f.tokenFile,0o600);
});
test('control rejects browser origins, unauthorized tokens, paths, and malformed requests',async t=>{
 const f=await fixture(t),endpoint=JSON.parse(await readFile(f.discoveryFile));
 const send=(body,headers={})=>fetch(`http://127.0.0.1:${endpoint.port}/backup`,{method:'POST',headers:{authorization:'Bearer '+f.token,...headers},body:JSON.stringify(body)});
 assert.equal((await send({operation:'inspect'},{origin:'https://untrusted.invalid'})).status,403);
 assert.equal((await send({operation:'inspect'},{authorization:'Bearer wrong'})).status,403);
 for(const body of [{operation:'readFile',args:{path:'/private'}},{operation:'inspect',args:{}},{operation:'submit',commandId:'../escape',args:{}},{operation:'submit',commandId:'x',args:'string'},{operation:'readArchive',commandId:'x',args:{path:'/private'}}])assert.equal((await send(body)).status,400);
 assert.equal(f.calls.length,0);assert.equal((await f.client.inspect()).receipts.length,0);
});
test('user and agent share explicit backup authorization, retained receipts, and bounded downloads',async t=>{
 const f=await fixture(t),authorized=[];
 const supervisor={outlivesDistribution:true,owner:{inspect:async()=>({installed:true})},subscribe:()=>()=>{}};
 const create=()=>createApplicationUpdateCapabilities({supervisor,directory:join(f.root,'facade'),backupCoordinator:f.client,
  authorize:async c=>{if(c.account!=='owned')throw Error('account_denied');},authorizeBackup:async(c,operation,args)=>{if(c.credentialsAllowed!==true)throw Error('credential_export_denied');authorized.push([c.origin,operation,args.includeCredentials]);}});
 let facade=create();t.after(()=>facade.close());
 const invoke=(origin,operation,args={},id='original',credentialsAllowed=true)=>facade.action({version:1,channel:'ahp-root://',topic:'application-updates',operation:'updates.application.backup.'+operation,args,commandId:id},{account:'owned',origin,credentialsAllowed});
 const args={reviewId:'review',reviewDigest:hash('review'),privateContentReviewed:true,credentialsReviewed:true};
 for(const origin of ['ui','agent']){await assert.rejects(invoke(origin,'create',args,'denied',false),/credential_export_denied/);assert.equal((await invoke(origin,'review')).result.includesCredentials,true);}
 assert.equal(f.calls.length,0);
 const accepted=await invoke('ui','create',args);assert.equal(accepted.result.receipt.status,'accepted');assert.equal(accepted.result.receipt.privatePath,undefined);
 const binding={fenceId:'backup-stop',commandId:'original',purpose:'service-stop',instanceId:'first',dataScope:'owned',serviceIdentity:{installationId:'owned',ownerId:'owner',instanceId:'first',dataScope:'owned',releaseDigest:'a'.repeat(64)}};
 const held=await facade.quiescenceParticipant.acquire(binding);assert.ok(held,'submission forwarding finished; independent backup does not keep the child busy');
 await assert.rejects(invoke('ui','create',args,'second'),/intake is closed/);
 await facade.close();
 f.receipts.set('original',{commandId:'original',status:'succeeded',privatePath:'/private/retained',archive:{completeProduct:true,archiveSha256:hash(f.archive),bytes:f.archive.length,manifestDigest:hash('manifest'),outputFile:'/private/archive'}});
 facade=create();const result=(await invoke('agent','receipt',{commandId:'original'})).result.receipt;
 assert.equal(result.status,'succeeded');assert.equal(result.privatePath,undefined);assert.equal(result.archive.outputFile,undefined);assert.equal(f.calls.length,1);
 const resource={uri:result.archive.resourceUri+'&offset=0&maxBytes=16',channel:'ahp-root://',encoding:'base64'},context={account:'owned',origin:'ui',credentialsAllowed:true};
 assert.equal(Buffer.from((await facade.resourceProvider.read(resource,context)).data,'base64').toString(),f.archive.subarray(0,16).toString());
 await assert.rejects(facade.resourceProvider.read(resource,{...context,credentialsAllowed:false}),/credential_export_denied/);
 for(const input of [{...resource,encoding:'utf-8'},{...resource,channel:'ahp-session:/foreign'},{...resource,uri:result.archive.resourceUri+'&maxBytes=262145'},{...resource,uri:resource.uri+'&offset=1'}])await assert.rejects(facade.resourceProvider.read(input,context),/resource_invalid/);
 assert.deepEqual(authorized.filter(x=>x[1].endsWith('review')).map(x=>x[0]),['ui','agent']);
});
