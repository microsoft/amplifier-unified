import {DatabaseSync} from 'node:sqlite';
import {randomUUID} from 'node:crypto';
import {isAbsolute} from 'node:path';

/** Read one explicitly named legacy client. This never constructs legacy app state. */
export function createClientMigration({database,account,engineId='amplifier',resolveNative}){
 if(!isAbsolute(database)||!account)throw Error('Client migration requires an absolute database and explicit owning account');
 const db=new DatabaseSync(database,{readOnly:true}),grants=new Map(),maximum=1024*1024;
 const present=db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='client_views'").get();
 const size=present?db.prepare('SELECT length(CAST(value AS BLOB)) AS bytes FROM client_views WHERE id=?'):undefined;
 const read=present?db.prepare('SELECT value FROM client_views WHERE id=? AND length(CAST(value AS BLOB))<=?'):undefined;
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
   const data=JSON.stringify({version:1,record,sessionMap,sourceRetained:true});
   if(Buffer.byteLength(data)>maximum)throw Error('Migration output exceeds capacity; the original was preserved');
   return {uri:params.uri,encoding:'utf-8',contentType:'application/json',data};
  }},
  close(){grants.clear();db.close();}
 };
}
