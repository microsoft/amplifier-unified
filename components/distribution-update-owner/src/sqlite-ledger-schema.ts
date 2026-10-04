import {DatabaseSync} from 'node:sqlite';
import {lstatSync} from 'node:fs';

export type LedgerKind='updates'|'service'|'manual_ingress';
type Column=[string,string,number,number];
const schemas:Record<string,Column[]>={
 owner:[['id','INTEGER',0,1],['pid','INTEGER',1,0],['token','TEXT',1,0]],
 state:[['id','INTEGER',0,1],['value','TEXT',1,0]],
 operations:[['id','TEXT',0,1],['fingerprint','TEXT',1,0],['value','TEXT',1,0]],
 events:[['id','INTEGER',0,1],['value','TEXT',1,0]],
 release_notes:[['id','INTEGER',0,1],['value','TEXT',1,0],['revision','TEXT',1,0],['warning','TEXT',0,0]],
 notice_reviews:[['digest','TEXT',0,1],['receipt_id','TEXT',1,0],['reviewed_at','INTEGER',1,0]],
 preference_revision:[['id','INTEGER',0,1],['value','TEXT',1,0]],
 preference_reset_reviews:[['id','TEXT',0,1],['value','TEXT',1,0]],
 preference_reset_commands:[['id','TEXT',0,1],['fingerprint','TEXT',1,0],['value','TEXT',1,0]],
 binding:[['id','INTEGER',0,1],['value','TEXT',0,0]],
 commands:[['id','TEXT',0,1],['fingerprint','TEXT',0,0],['value','TEXT',0,0]],
 held:[['id','INTEGER',0,1],['body','TEXT',1,0]],
 releases:[['id','TEXT',0,1],['signature','TEXT',1,0]],
};
const core=['owner','state','operations','events'];
const notes=['release_notes','notice_reviews'];
const preferences=['preference_revision','preference_reset_reviews','preference_reset_commands'];
const expected:Record<LedgerKind,string[]>={updates:[...core,...notes,...preferences],service:['owner','binding','commands'],manual_ingress:['held','releases']};
function fail(kind:LedgerKind):never{throw Error(kind+'_ledger_unconfirmed');}
const exists=(path:string)=>{try{return lstatSync(path);}catch(e){if((e as NodeJS.ErrnoException).code==='ENOENT')return null;throw e;}};
/** Main absence alone is never fresh if journal or namespace authority survives. */
export function preflightLedger(path:string,kind:LedgerKind,binding:unknown,namespaceEvidence=false):boolean {
 const main=exists(path),evidence=['-wal','-shm','-journal'].map(suffix=>exists(path+suffix));
 if(evidence.some(entry=>entry&&(!entry.isFile()||entry.isSymbolicLink())))fail(kind);
 const sidecars=evidence.some(entry=>entry!==null);
 if(!main){if(sidecars||namespaceEvidence)fail(kind);return true;}
 if(!main.isFile()||main.isSymbolicLink())fail(kind);
 // Do not set journal_mode or open writable before this bounded probe. A real
 // read-only connection observes committed WAL; immutable mode would ignore it.
 const db=new DatabaseSync(path,{readOnly:true});
 try{validateLedger(db,kind,binding,false);}finally{db.close();}
 return false;
}
function singleton(db:DatabaseSync,table:string,column:string,kind:LedgerKind,max=1048576):string {
 const row=db.prepare(`SELECT CASE WHEN length(CAST(${column} AS BLOB))<=? THEN ${column} END AS value FROM ${table} WHERE id=1`).get(max);
 if(!row||typeof row.value!=='string'||!row.value.length)fail(kind);
 return row.value as string;
}
/** Recheck under the existing writer lease, without reading command/event rows. */
export function validateLedger(db:DatabaseSync,kind:LedgerKind,binding:unknown,allowEmpty:boolean):void {
 const version=Number(db.prepare('PRAGMA user_version').get()!.user_version);
 const names=db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 10").all().map(row=>String(row.name));
 if(allowEmpty&&version===0&&!names.length)return;
 if(version!==0&&version!==1)fail(kind);
 let required=expected[kind];
 if(kind==='updates'&&version===0){
  const hasNotes=notes.some(t=>names.includes(t)),hasPreferences=preferences.some(t=>names.includes(t));
  if(hasPreferences&&!hasNotes)fail(kind);
  required=[...core,...(hasNotes?notes:[]),...(hasPreferences?preferences:[])];
 }
 if(names.length!==required.length||required.some(t=>!names.includes(t)))fail(kind);
 for(const table of required){
  let columns=schemas[table];
  if(kind==='service'&&table==='owner')columns=[['id','INTEGER',0,1],['pid','INTEGER',0,0],['token','TEXT',0,0]];
  const actual=db.prepare('SELECT name,type,"notnull",pk FROM pragma_table_info(?) LIMIT ?').all(table,columns.length+1).map(c=>[c.name,String(c.type).toUpperCase(),Number(c.notnull),Number(c.pk)]);
  if(JSON.stringify(actual)!==JSON.stringify(columns))fail(kind);
  if(table==='held'||table==='preference_revision'){
   const sql=db.prepare("SELECT CASE WHEN length(sql)<=8192 THEN sql END AS sql FROM sqlite_master WHERE type='table' AND name=?").get(table)?.sql;
   if(typeof sql!=='string'||!/CHECK\s*\(\s*id\s*=\s*1\s*\)/i.test(sql))fail(kind);
  }
 }
 if(kind==='updates'){
  let state:any;try{state=JSON.parse(singleton(db,'state','value',kind));}catch{fail(kind);}
  if(state?.schema!==1)throw Error('unsupported_state_schema');
  if(state.dataScope!==(binding as any)?.dataScope)throw Error('owner_scope_conflict');
  if(required.includes('preference_revision'))singleton(db,'preference_revision','value',kind,256);
 }else if(kind==='service'){
  if(singleton(db,'binding','value',kind,4096)!==JSON.stringify(binding))throw Error('service_owner_binding_conflict');
 }else{
  const row=db.prepare('SELECT CASE WHEN length(CAST(body AS BLOB))<=65536 THEN body END AS value FROM held WHERE id=1').get();
  if(row){let held:any;try{held=JSON.parse(String(row.value));}catch{fail(kind);}
   if(!held||!['held','unknown'].includes(held.phase)||['fenceId','commandId','purpose','instanceId','dataScope'].some(k=>typeof held[k]!=='string'||!held[k]))fail(kind);
  }
 }
}
/** Profile 1 binds the complete current schema after successful initialization. */
export function markLedger(db:DatabaseSync){db.exec('PRAGMA user_version=1');}
