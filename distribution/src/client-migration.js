import {DatabaseSync} from 'node:sqlite';
import {randomUUID} from 'node:crypto';
import {isAbsolute} from 'node:path';

/** Read one explicitly named legacy client. This never constructs legacy app state. */
export function createClientMigration({database,account,engineId='amplifier',resolveNative,resolveWorkspace}){
 if(!isAbsolute(database)||!account)throw Error('Client migration requires an absolute database and explicit owning account');
 const db=new DatabaseSync(database,{readOnly:true}),grants=new Map(),maximum=1024*1024;
 const present=db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='client_views'").get();
 const size=present?db.prepare('SELECT length(CAST(value AS BLOB)) AS bytes FROM client_views WHERE id=?'):undefined;
 const read=present?db.prepare('SELECT value FROM client_views WHERE id=? AND length(CAST(value AS BLOB))<=?'):undefined;
 const hasState=db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='state'").get();
 const workspaces=hasState?db.prepare("SELECT json_extract(value,'$.workspaces') AS workspaces FROM state WHERE id=1 AND length(CAST(value AS BLOB))<=? AND json_valid(value)"):undefined;
 const hasRecords=db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='state_records'").get();
 const workspaceRecord=hasRecords?db.prepare("SELECT length(CAST(value AS BLOB)) AS bytes, CASE WHEN length(CAST(value AS BLOB))<=? THEN value END AS value FROM state_records WHERE kind='global' AND id='workspaces'"):undefined;
 const readWorkspaces=()=>{
  // Incremental global records are authoritative, including deletion. Never
  // resurrect a stale checkpoint when the newer record cannot be read.
  const row=workspaceRecord?.get(maximum);
  if(!row)return JSON.parse(workspaces?.get(maximum)?.workspaces||'[]');
  if(row.bytes>maximum)throw Error('Saved workspace selection exceeds migration capacity; the original was preserved');
  let entry;try{entry=JSON.parse(row.value);}catch{throw Error('Saved workspace selection is malformed; the original was preserved');}
  if(!entry||typeof entry!=='object'||Array.isArray(entry)||Object.keys(entry).length!==2||!Object.hasOwn(entry,'value')||typeof entry.present!=='boolean'||(entry.present&&(!Array.isArray(entry.value)||entry.value.length>10000)))throw Error('Saved workspace selection is malformed; the original was preserved');
  return entry.present?entry.value:[];
 };
 const prune=()=>{for(const [token,grant]of grants)if(grant.expires<Date.now())grants.delete(token);};
 return {
  async metadata({clientId,metadata}){
   const identity=metadata?.['amplifier.dev/legacyClient']?.id;
   if(typeof identity!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(identity))return;
   const row=size?.get(identity);if(!row)return;
   if(row.bytes>maximum-65536)return {'amplifier.dev/clientMigration':{version:1,account,status:'oversize',sourceRetained:true}};
   prune();if(grants.size>=128)return;
   const token=randomUUID();grants.set(token,{identity,clientId,expires:Date.now()+300000});
   return {'amplifier.dev/clientMigration':{version:1,account,uri:'amplifier-migration://client/'+token}};
  },
  resourceProvider:{scheme:'amplifier-migration',async read(params,context){
   prune();const uri=new URL(params.uri),grant=grants.get(uri.pathname.slice(1));
   if(uri.host!=='client'||uri.search||uri.hash||!grant||grant.clientId!==context.clientId)throw Error('Client migration grant is unavailable');
   const row=read?.get(grant.identity,maximum-65536);if(!row)throw Error('Original client state changed or exceeds migration capacity; it was preserved');
   const record=JSON.parse(row.value),identities=[...new Set([record.selectedSessionId,...Object.keys(record.drafts??{}),...Object.keys(record.attachments??{})].filter(id=>typeof id==='string'&&id&&id!=='new'&&!id.startsWith('ahp-session:')))];
   // Excess identities remain in the exact original for later explicit recovery.
   const resolved=resolveNative?await resolveNative({engineId,identities:identities.slice(0,500)}):{},sessionMap={};
   for(const [id,result]of Object.entries(resolved))if(result.status==='matched')sessionMap[id]=result.uri;
   const workspaceMap={};
   if(resolveWorkspace){
    const selected=[...new Set([record.selectedWorkspaceId,record.view?.workWorkspaceId].filter(id=>typeof id==='string'&&id))];
    const rows=selected.length?readWorkspaces():[];
    if(Array.isArray(rows)&&rows.length<=10000)for(const id of selected){
     const matches=rows.filter(row=>row?.id===id&&typeof row.path==='string');
     if(matches.length===1){const result=await resolveWorkspace(matches[0].path);if(result?.id&&result.path)workspaceMap[id]={id:result.id,path:result.path};}
    }
   }
   const data=JSON.stringify({version:1,record,sessionMap,workspaceMap,sourceRetained:true});
   if(Buffer.byteLength(data)>maximum)throw Error('Migration output exceeds capacity; the original was preserved');
   return {uri:params.uri,encoding:'utf-8',contentType:'application/json',data};
  }},
  close(){grants.clear();db.close();}
 };
}
