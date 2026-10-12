import {DatabaseSync} from 'node:sqlite';
import {constants,lstatSync,openSync,closeSync,realpathSync} from 'node:fs';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';

/** A process lifetime lease, separate from durable operation receipts. A dead
 * controller can be replaced, but its operations remain unknown until inspected.
 * A PID is used only to refuse another writer, never to adopt or signal one. */
export function acquireInstallationBackupLease(stateDirectory,{installationId,dataScope}){
 const path=join(stateDirectory,'controller.sqlite3'),lease=randomUUID();
 const dir=lstatSync(stateDirectory);
 if(!dir.isDirectory()||dir.mode&0o077||dir.uid!==process.getuid()||realpathSync(stateDirectory)!==stateDirectory)throw Error('backup_private_directory_required');
 try{const fd=openSync(path,constants.O_CREAT|constants.O_EXCL|constants.O_WRONLY,0o600);closeSync(fd);}catch(e){if(e.code!=='EEXIST')throw e;}
 const stat=lstatSync(path);
 if(!stat.isFile()||stat.nlink!==1||stat.mode&0o077||stat.uid!==process.getuid())throw Error('backup_private_record_required');
 const db=new DatabaseSync(path),binding=JSON.stringify({installationId,dataScope});let begun=false,closed=false;
 try{
  db.exec('PRAGMA busy_timeout=5000; PRAGMA synchronous=FULL; BEGIN IMMEDIATE');begun=true;
  db.exec('CREATE TABLE IF NOT EXISTS controller(id INTEGER PRIMARY KEY CHECK(id=1),binding TEXT NOT NULL,pid INTEGER,lease TEXT)');
  const old=db.prepare('SELECT * FROM controller WHERE id=1').get();
  if(old&&old.binding!==binding)throw Error('backup_controller_binding_conflict');
  if(old?.pid!==null&&old?.pid!==undefined){
   if(!Number.isSafeInteger(old.pid)||old.pid<1||typeof old.lease!=='string')throw Error('backup_controller_lease_invalid');
   let alive=true;
   try{process.kill(old.pid,0);}catch(e){if(e.code==='ESRCH')alive=false;else if(e.code!=='EPERM')throw e;}
   if(alive)throw Error('backup_controller_already_running');
  }
  db.prepare('INSERT OR REPLACE INTO controller VALUES(1,?,?,?)').run(binding,process.pid,lease);
  db.exec('COMMIT');begun=false;
 }catch(e){if(begun)db.exec('ROLLBACK');db.close();throw e;}
 return {close(){if(closed)return;db.prepare('UPDATE controller SET pid=NULL,lease=NULL WHERE id=1 AND lease=?').run(lease);db.close();closed=true;}};
}
