import {DatabaseSync} from 'node:sqlite';
import {mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {createHash} from 'node:crypto';

const canonical=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item);
const digest=value=>createHash('sha256').update(canonical(value)).digest('hex');
const text=value=>{if(typeof value!=='string'||!value||value.length>200||/[\x00-\x1f]/.test(value))throw Error('Bounded retention identity required');return value;};
const refused=message=>Object.assign(Error(message),{data:{executed:false}});

/** Trusted composition, never a browser-supplied owner census. Native ownership
 * is separately supplied by the typed host/native retention operation. This
 * coordinator protects product references and journals exact lease releases. */
export function createRetentionProtection({directory,instanceId,dataScope,participants,readItemReceipt}){
 text(instanceId);text(dataScope);
 if(!Array.isArray(participants)||!participants.length||participants.length>64||new Set(participants.map(p=>p.id)).size!==participants.length||typeof readItemReceipt!=='function')throw Error('Exact retention participant census required');
 for(const p of participants)text(p.id);
 mkdirSync(directory,{recursive:true,mode:0o700});
 const lock=new DatabaseSync(join(directory,'owner-lock.sqlite3'));
 try{lock.exec('PRAGMA busy_timeout=0; PRAGMA journal_mode=DELETE; BEGIN EXCLUSIVE');}catch(error){lock.close();throw error;}
 let db;
 try{db=new DatabaseSync(join(directory,'protection.sqlite3'));db.exec('PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS protections(command TEXT PRIMARY KEY,state TEXT NOT NULL,body TEXT NOT NULL); CREATE INDEX IF NOT EXISTS protection_state ON protections(state)');}catch(error){lock.close();throw error;}
 let closed=false,active=0;
 const live=new Map(),byId=new Map(participants.map(p=>[p.id,p]));
 const get=id=>{const value=db.prepare('SELECT body FROM protections WHERE command=?').get(id);return value?JSON.parse(value.body):null;};
 const save=value=>db.prepare('INSERT INTO protections VALUES(?,?,?) ON CONFLICT(command) DO UPDATE SET state=excluded.state,body=excluded.body').run(value.commandId,value.state,canonical(value));
 const view=row=>row?{commandId:row.commandId,session:row.session,state:row.state,owners:row.owners.map(o=>({id:o.id,state:o.state})),...(row.reason?{reason:row.reason}:{})}:null;
 const check=()=>{if(closed)throw Error('Retention protection is closed');};
 const retainUnknown=async row=>{
  row.state='unknown';save(row);
  for(const owner of row.owners){const lease=live.get(row.commandId)?.get(owner.id);if(lease&&owner.state!=='released')try{await lease.release('unknown');}catch{/* An uncertain gate stays held. */}}
  live.delete(row.commandId);
 };
 const releaseInternal=async commandId=>{
  check();const row=get(text(commandId));if(!row)throw Error('Original retention protection receipt required');if(row.state==='released'||row.state==='refused')return view(row);
  const receipt=await readItemReceipt(commandId);
  if(!receipt||receipt.commandId!==commandId||receipt.session!==row.session||!(receipt.status==='hidden'||receipt.status==='refused'&&receipt.executed===false)){
   await retainUnknown(row);throw Error('Retention effect is not conclusively settled; owner intake remains protected');
  }
  if(!row.proof){
   const outcome=row.context.instanceId===instanceId?'unchanged':'ready';
   // Persist the exact proof once; retries after a lost owner acknowledgement
   // reuse these bytes even when a replacement coordinator performs the retry.
   row.proof={verified:true,fenceId:row.context.fenceId,commandId,dataScope:row.context.dataScope,outcome,instanceId,receiptId:'retention:'+digest({commandId,session:row.session,status:receipt.status,executed:receipt.executed??null,result:receipt.result??null})};
   row.state='releasing';save(row);
  }
  const errors=[];
  for(const owner of [...row.owners].reverse()){
   if(owner.state==='released'||owner.state==='pending')continue;
   const participant=byId.get(owner.id),lease=live.get(commandId)?.get(owner.id);
   try{
    if(lease)await lease.release(row.proof.outcome,row.proof);
    else if(typeof participant?.reconcileRelease==='function')await participant.reconcileRelease({...row.context,outcome:row.proof.outcome,proof:row.proof});
    else throw Error('Original owner reconciliation unavailable');
    owner.state='released';save(row);
   }catch{owner.state='unknown';errors.push(owner.id);save(row);}
  }
  live.delete(commandId);row.state=errors.length?'unknown':'released';if(errors.length)row.reason='Unconfirmed owner release: '+errors.join(',');save(row);
  if(errors.length)throw Error(row.reason);return view(row);
 };
 const release=async commandId=>{active++;try{return await releaseInternal(commandId);}finally{active--;}};
 return {
  async acquire(input){
   check();const commandId=text(input.commandId),sessions=[input.session,...(input.descendants??[])];
   if(input.operation!=='hide'||sessions.length>101||new Set(sessions).size!==sessions.length||sessions.some(s=>typeof s!=='string'||!/^ahp-session:\/.{1,480}$/.test(s)))throw refused('Exact bounded retention family required');
   if(get(commandId))throw refused('Original retention protection already exists; inspect its exact receipt');
   if(active||db.prepare("SELECT 1 FROM protections WHERE state NOT IN ('released','refused') LIMIT 1").get())throw refused('Another retention protection has not settled');
   if(participants.some(p=>p.retentionHide?.version!==1||typeof p.acquire!=='function'||typeof p.reconcileRelease!=='function'))throw refused('Configured product owner retention coverage is incomplete');
   const context={fenceId:'retention:'+digest(commandId),commandId,purpose:'retention-hide',instanceId,dataScope};
   const row={commandId,session:input.session,sessions,context,state:'acquiring',owners:participants.map(p=>({id:p.id,state:'pending'}))};
   const leases=new Map();live.set(commandId,leases);save(row);active++;
   try{
    for(const owner of row.owners){
     const participant=byId.get(owner.id);owner.state='acquiring';save(row);
     const lease=await participant.acquire(context);
     if(!lease){owner.state='pending';save(row);throw refused('Product owner has current work: '+owner.id);}
     if(lease.ownerId!==owner.id||lease.fenceId!==context.fenceId)throw Error('Owner returned another retention lease');
     leases.set(owner.id,lease);owner.state='held';save(row);
     if(typeof lease.inspectRetentionReferences!=='function')throw refused('Owner reference coverage is unavailable: '+owner.id);
     const refs=await lease.inspectRetentionReferences({sessions,limit:101});
     if(!refs||refs.coverage!=='complete'||!Array.isArray(refs.protected)||!Array.isArray(refs.omissions)||refs.omissions.length||refs.protected.length)throw refused('Owner retains work or incomplete references: '+owner.id);
    }
    row.state='held';save(row);
    return {ownerIds:row.owners.map(o=>o.id),release:()=>release(commandId)};
   }catch(error){
    // Only this pre-native-effect path can use live admission rollback. A lost
    // acquisition response is uncertain and must be reconciled by exact proof.
    if(row.owners.some(o=>o.state==='acquiring')){row.reason='Owner acquisition acknowledgement is uncertain';await retainUnknown(row);}
    else{
     let uncertain=false;
     for(const owner of [...row.owners].reverse())if(owner.state==='held')try{await leases.get(owner.id).release('unchanged',{kind:'admission-refused'});owner.state='released';save(row);}catch{owner.state='unknown';uncertain=true;save(row);}
     row.state=uncertain?'unknown':'refused';row.reason=String(error.message).slice(0,1000);save(row);live.delete(commandId);
    }
    throw refused(row.reason??String(error.message));
   }finally{active--;}
  },
  receipt(commandId){check();return view(get(text(commandId)));},
  reconcile:release,
  close(){if(active)throw Error('Retention coordination is still active');closed=true;db.close();lock.close();},
 };
}
