import {constants} from 'node:fs';
import {open,lstat,realpath,mkdir,rename} from 'node:fs/promises';
import {join,isAbsolute,dirname} from 'node:path';
import {createHash,randomUUID} from 'node:crypto';

const STORES=['intake','schedules','operations','questions','observations','transfer','legacy'];
const MAX_BYTES=64*1024*1024,MAX_JSON=32768;
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const canonical=value=>JSON.stringify(sort(value));
function sort(value){return Array.isArray(value)?value.map(sort):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(k=>[k,sort(value[k])])):value;}
const token=value=>typeof value==='string'&&value.length>0&&value.length<=200&&!/[\x00-\x1f]/.test(value);
const digest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
async function syncDirectory(path){const fd=await open(path,constants.O_RDONLY);try{await fd.sync();}finally{await fd.close();}}
async function json(path){
 let fd;try{fd=await open(path,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);}catch(e){if(e.code==='ENOENT')return null;throw e;}
 try{const info=await fd.stat();if(!info.isFile()||info.nlink!==1||info.size>MAX_JSON)throw Error('owner_snapshot_journal_invalid');
  const bytes=Buffer.alloc(info.size+1),{bytesRead}=await fd.read(bytes,0,bytes.length,0);
  if(bytesRead!==info.size)throw Error('owner_snapshot_journal_changed');return JSON.parse(bytes.subarray(0,bytesRead));
 }finally{await fd.close();}
}
async function record(path,value,initial=false){
 const bytes=Buffer.from(canonical(value)+'\n');if(bytes.length>MAX_JSON)throw Error('owner_snapshot_journal_limit');
 const temporary=initial?path:path+'.'+randomUUID(),fd=await open(temporary,'wx',0o600);
 try{await fd.writeFile(bytes);await fd.sync();}finally{await fd.close();}
 if(!initial)await rename(temporary,path);await syncDirectory(dirname(path));
}
function manifestOf(receipt,context,commandId){
 // The Python owner canonicalizes receipts with ensure_ascii=True.
 const expectedSignature=hash(canonical({context,commandId,privateContentReviewed:true}).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0')));
 if(receipt?.commandId!==commandId||receipt.signature!==expectedSignature||receipt.replayed!==false||receipt.activationProvided!==false||receipt.completeProductBackup!==false)throw Error('owner_snapshot_receipt_mismatch');
 if(receipt.status!=='captured')return null;
 const m=receipt.snapshot;
 if(m?.schema!=='amplifier-sqlite-snapshot-set'||m.version!==1||m.activationProvided!==false||!digest(m.manifestSha256)||!Array.isArray(m.stores)||m.stores.length!==STORES.length||new Set(m.stores.map(r=>r.id)).size!==STORES.length)throw Error('owner_snapshot_manifest_invalid');
 let bytes=0;
 for(const r of m.stores){
  if(!STORES.includes(r.id))throw Error('owner_snapshot_store_invalid');
  if(r.status==='missing'&&Object.keys(r).length===2)continue;
  if(r.status!=='captured'||r.file!==r.id+'.sqlite3'||!digest(r.sha256)||!Number.isSafeInteger(r.bytes)||r.bytes<0)throw Error('owner_snapshot_store_invalid');
  bytes+=r.bytes;
 }
 if(bytes>MAX_BYTES)throw Error('owner_snapshot_byte_limit');
 const {manifestSha256,...body}=m;
 if(hash(canonical(body))!==manifestSha256)throw Error('owner_snapshot_manifest_digest_mismatch');
 return m;
}

/** Trusted composition adapter, never a browser path or a new writer authority.
 * The host still owns admission, all participant leases, and release verification.
 * Only the opaque fresh Operations lease grants capture; receipts grant reads.
 */
export function bindHeldOwnerSnapshots({provider,participant,withMaintenance}){
 if(!token(participant?.id)||typeof participant.acquire!=='function'||typeof withMaintenance!=='function'||typeof provider.inspectOwnerSnapshot!=='function'||typeof provider.readOwnerSnapshot!=='function')throw Error('owner_snapshot_adapter_unavailable');
 const held=new Map();
 const wrapped={...participant,async acquire(context){
  // Host bookkeeping (phase, observation time and incrementally acquired owner
  // IDs) is not the participant context. Bind only the public immutable tuple.
  const exact=Object.fromEntries(['fenceId','commandId','purpose','instanceId','dataScope'].map(key=>[key,context[key]]));
  if(exact.purpose==='service-stop')exact.serviceIdentity=structuredClone(context.serviceIdentity);
  const lease=await participant.acquire(exact);if(!lease)return lease;
  const key=canonical(exact),row={context:exact,lease,live:true};
  if(exact.purpose==='recovery'&&typeof lease.captureSnapshot==='function')held.set(key,row);
  return {...lease,release:async(...args)=>{row.live=false;held.delete(key);return lease.release(...args);}};
 }};
 const selected=input=>{
  const rows=[...held.values()].filter(r=>r.live&&r.context.fenceId===input.fenceId&&r.context.commandId===input.commandId);
  if(rows.length!==1)throw Error('owner_snapshot_fresh_held_lease_required');return rows[0];
 };
 const validate=input=>{
  if(!input||Object.keys(input).some(k=>!['fenceId','commandId','snapshotCommandId','directory','privateContentReviewed'].includes(k))||![input.fenceId,input.commandId,input.snapshotCommandId].every(token)||input.privateContentReviewed!==true||typeof input.directory!=='string'||!isAbsolute(input.directory))throw Error('owner_snapshot_review_required');
 };
 const stageHeld=async input=>{
   validate(input);
    const row=selected(input),context=row.context,assertHeld=()=>{if(!row.live||selected(input)!==row)throw Error('owner_snapshot_lease_lost');};
    if(await realpath(input.directory)!==input.directory||!(await lstat(input.directory)).isDirectory())throw Error('owner_snapshot_canonical_directory_required');
    const directory=join(input.directory,hash(input.snapshotCommandId)),path=join(directory,'staging.json');
    const request={ownerId:participant.id,context,snapshotCommandId:input.snapshotCommandId,privateContentReviewed:true},signature=hash(canonical(request));
    const prior=await json(path);
    if(prior){if(prior.signature!==signature)throw Error('owner_snapshot_original_request_differs');return {...prior,replayed:false};}
    await mkdir(directory,{mode:0o700});
    let state={schema:'amplifier-unified-owner-snapshot-stage-v1',...request,signature,status:'unknown',workReplayed:false,completeProductBackup:false};
    await record(path,state,true);assertHeld();
    // A lost earlier native reply is inspected, never dispatched a second time.
    const observed=await provider.inspectOwnerSnapshot(input.snapshotCommandId);
    let receipt=observed?.receipt;
    if(!receipt)receipt=await row.lease.captureSnapshot({commandId:input.snapshotCommandId,privateContentReviewed:true});
    assertHeld();const manifest=manifestOf(receipt,context,input.snapshotCommandId);
    if(!manifest){state={...state,ownerReceipt:receipt};await record(path,state);return state;}
    state={...state,ownerReceipt:receipt};await record(path,state);
    const images=join(directory,'images');await mkdir(images,{mode:0o700});
    const files=[...manifest.stores.filter(r=>r.status==='captured').map(r=>({store:r.id,name:r.file,bytes:r.bytes,sha256:r.sha256})),{store:'manifest',name:'manifest.json',sha256:manifest.manifestSha256}];
    for(const file of files){
     const fd=await open(join(images,file.name),'wx',0o600),total=createHash('sha256');let offset=0,size=file.bytes;
     try{do{
      assertHeld();const page=await provider.readOwnerSnapshot({commandId:input.snapshotCommandId,store:file.store,offset,limit:65536});assertHeld();
      if(page?.encoding!=='base64'||page.offset!==offset||page.sha256!==file.sha256||page.replayed!==false||!Number.isSafeInteger(page.bytes)||page.bytes<0||page.bytes>(file.store==='manifest'?16384:MAX_BYTES)||size!==undefined&&page.bytes!==size||typeof page.data!=='string'||page.data.length>87384)throw Error('owner_snapshot_chunk_invalid');
      size=page.bytes;const raw=Buffer.from(page.data,'base64');
      if(raw.toString('base64')!==page.data||raw.length>65536||offset+raw.length>size||(!raw.length&&offset<size)||page.nextOffset!==(offset+raw.length<size?offset+raw.length:null))throw Error('owner_snapshot_chunk_invalid');
      await fd.writeFile(raw);total.update(raw);offset+=raw.length;
     }while(offset<size);
     if(total.digest('hex')!==file.sha256)throw Error('owner_snapshot_image_digest_mismatch');await fd.sync();
     }finally{await fd.close();}
    }
    assertHeld();await syncDirectory(images);state={...state,status:'sealed',manifestSha256:manifest.manifestSha256};await record(path,state);assertHeld();return state;
 };
 return {participant:wrapped,
  inspect:async commandId=>{if(!token(commandId))throw Error('owner_snapshot_command_required');return provider.inspectOwnerSnapshot(commandId);},
  async stage(input){validate(input);return withMaintenance({fenceId:input.fenceId,commandId:input.commandId},()=>stageHeld(input));},
  /** Internal composition port, called only by the already-held Recovery job.
   * This is deliberately not returned by createDistribution or put on the wire. */
  stageHeld,
  close(){for(const row of held.values())row.live=false;held.clear();},
 };
}
