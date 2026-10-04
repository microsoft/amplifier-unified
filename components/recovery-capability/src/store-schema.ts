import {DatabaseSync} from 'node:sqlite';
import {lstatSync} from 'node:fs';
import {join} from 'node:path';

export const storeVersion=1;
export const tableDefinitions={
 jobs:'CREATE TABLE jobs(id TEXT PRIMARY KEY,account TEXT NOT NULL,command TEXT NOT NULL,state TEXT NOT NULL,created INTEGER NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,UNIQUE(account,command))',
 fence:'CREATE TABLE fence(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL)',
 participant_releases:'CREATE TABLE participant_releases(fence_id TEXT PRIMARY KEY,command_id TEXT NOT NULL,signature TEXT NOT NULL,payload TEXT NOT NULL)',
 revision:'CREATE TABLE revision(id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER NOT NULL)',
};
export const indexDefinitions={
 jobs_account_page:'CREATE INDEX jobs_account_page ON jobs(account,created DESC,id DESC)',
 jobs_unsettled:'CREATE INDEX jobs_unsettled ON jobs(state)',
 jobs_fence_command:"CREATE INDEX jobs_fence_command ON jobs(state,json_extract(payload,'$.fenceCommandId'))",
};
export type StoreProfile='fresh'|'unversioned'|'current';
export class RecoveryStoreUnavailable extends Error {
 readonly code='RECOVERY_STORE_UNAVAILABLE';
 constructor(readonly reason:string){super('Recovery state is incomplete or unsupported. Preserve its database and sidecars for reviewed recovery.');}
}
const fail=(reason:string):never=>{throw new RecoveryStoreUnavailable(reason);};
const shape=(sql:string)=>sql.replace(/\bIF\s+NOT\s+EXISTS\b/gi,'').replace(/\s/g,'').replace(/;$/,'').toLowerCase();
function present(path:string){try{const stat=lstatSync(path);if(!stat.isFile())fail('non-regular-store-file');return true;}catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')return false;throw error;}}
/** Metadata and bounded singleton reads only. Never repair or open authority writable here. */
export function inspectStore(directory:string):StoreProfile {
 const path=join(directory,'recovery.sqlite');
 try{
  const main=present(path),sidecars=['-wal','-shm','-journal'].map(suffix=>present(path+suffix));
  if(!main){if(sidecars.some(Boolean))fail('orphan-store-sidecars');return 'fresh';}
  let db:DatabaseSync|undefined;
  try{
   // WAL-aware read-only mode is deliberate: immutable mode could hide committed crash-left WAL.
   db=new DatabaseSync(path,{readOnly:true});
   const version=Number(db.prepare('PRAGMA user_version').get()!.user_version);
   if(version!==0&&version!==storeVersion)fail('unsupported-store-version');
   const rows=db.prepare("SELECT name,type,substr(sql,1,4097) AS sql FROM sqlite_schema WHERE name IN ('jobs','fence','participant_releases','revision') LIMIT 5").all();
   if(rows.length!==4)fail('incomplete-store-schema');
   for(const [name,sql] of Object.entries(tableDefinitions)){
    const row=rows.find(row=>row.name===name);
    if(!row||row.type!=='table'||typeof row.sql!=='string'||shape(row.sql)!==shape(sql))fail('incompatible-store-schema');
   }
   const revision=db.prepare('SELECT id,value,typeof(value) AS value_type FROM revision LIMIT 2').all();
   if(revision.length!==1||revision[0].id!==1||revision[0].value_type!=='integer'||!Number.isSafeInteger(revision[0].value)||Number(revision[0].value)<0)fail('invalid-store-revision');
   const fence=db.prepare('SELECT id,length(CAST(payload AS BLOB)) AS bytes FROM fence LIMIT 2').all();
   if(fence.length>1||fence.some(row=>row.id!==1||!Number.isSafeInteger(row.bytes)||Number(row.bytes)>16384||Number(row.bytes)<2))fail('invalid-store-fence');
   if(fence.length){const payload=db.prepare('SELECT payload FROM fence WHERE id=1').get()!.payload;if(typeof payload!=='string')fail('invalid-store-fence');let parsed:unknown;try{parsed=JSON.parse(payload as string);}catch{fail('invalid-store-fence');}if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))fail('invalid-store-fence');}
   return version===0?'unversioned':'current';
  }finally{db?.close();}
 }catch(error){if(error instanceof RecoveryStoreUnavailable)throw error;throw new RecoveryStoreUnavailable('unreadable-store');}
}
