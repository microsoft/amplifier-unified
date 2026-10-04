import {DatabaseSync} from 'node:sqlite';
import {lstatSync} from 'node:fs';

const refused=()=>Error('Existing worktree authority schema is unavailable; original database and sidecars retained');
function file(path){try{const value=lstatSync(path);if(!value.isFile()||value.isSymbolicLink())throw refused();return true;}catch(error){if(error.code==='ENOENT')return false;throw error;}}
export const WORKTREE_SCHEMA={records:'id:TEXT:0:1 session:TEXT:1:0 created:REAL:1:0 value:TEXT:1:0',commands:'id:TEXT:0:1 session:TEXT:1:0 signature:TEXT:1:0 operation:TEXT:1:0 phase:TEXT:1:0 created:REAL:1:0 source:TEXT:0:0 target:TEXT:0:0 value:TEXT:1:0 result:TEXT:0:0',topics:'session:TEXT:0:1 revision:INTEGER:1:0'};
export const FENCE_SCHEMA={fence:'id:INTEGER:0:1 value:TEXT:0:0',receipts:'id:TEXT:0:1 value:TEXT:0:0'};
export const WORKERS_SCHEMA={workers:'id:TEXT:0:1 state:TEXT:1:0'};

/** Fixed schema metadata only. Inspect before writable open; never repair authority. */
export function inspectAuthority(path,schema,{required=false}={}){
 const exists=file(path),sidecars=['-wal','-shm','-journal'].map(suffix=>file(path+suffix));
 if(!exists){if(required||sidecars.some(Boolean))throw refused();return {exists:false,version:0};}
 const db=new DatabaseSync(path,{readOnly:true});
 try{
  const version=Number(db.prepare('PRAGMA user_version').get().user_version);
  if(![0,1].includes(version))throw refused();
  for(const [table,definition] of Object.entries(schema)){
   if(!db.prepare("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?").get(table))throw refused();
   const columns=db.prepare('PRAGMA table_info('+table+')').all();
   for(const field of definition.split(' ')){
    const [name,type,notnull,pk]=field.split(':'),column=columns.find(value=>value.name===name);
    if(!column||column.type.toUpperCase()!==type||Number(column.notnull)!==Number(notnull)||Number(column.pk)!==Number(pk))throw refused();
   }
  }
  const laterProfile=Object.keys(schema).includes('commands')&&!!db.prepare("SELECT 1 FROM sqlite_schema WHERE type='index' AND name IN ('managed_command_source','managed_command_target') LIMIT 1").get();
  return {exists:true,version,laterProfile};
 }finally{db.close();}
}
