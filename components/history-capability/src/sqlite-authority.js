import {DatabaseSync} from 'node:sqlite';
import {lstatSync} from 'node:fs';

const refused=()=>Error('Existing history authority schema is unavailable; original database and sidecars retained');
function file(path){try{const value=lstatSync(path);if(!value.isFile()||value.isSymbolicLink())throw refused();return true;}catch(error){if(error.code==='ENOENT')return false;throw error;}}
export const HISTORY_SCHEMA={workflows:'id:TEXT:0:1 value:TEXT:1:0',commands:'id:TEXT:0:1 workflow:TEXT:1:0 operation:TEXT:1:0 signature:TEXT:1:0 metadata:TEXT:1:0 status:TEXT:1:0 result:TEXT:0:0',revision:'id:INTEGER:0:1 value:INTEGER:1:0'};
export const FENCE_SCHEMA={fence:'id:INTEGER:0:1 value:TEXT:0:0',receipts:'id:TEXT:0:1 value:TEXT:0:0'};

/** Fixed schema metadata only. Inspect before writable open; never repair authority. */
export function inspectAuthority(path,schema,{required=false,singleton}={}){
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
  if(singleton){const rows=db.prepare('SELECT id,value FROM '+singleton+' LIMIT 2').all();if(rows.length!==1||Number(rows[0].id)!==1||!Number.isSafeInteger(Number(rows[0].value))||Number(rows[0].value)<0)throw refused();}
  return {exists:true,version};
 }finally{db.close();}
}
