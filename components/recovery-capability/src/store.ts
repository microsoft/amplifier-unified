import {DatabaseSync} from 'node:sqlite';
import {mkdirSync,chmodSync} from 'node:fs';
import {join} from 'node:path';
import type {Job,Json} from './types.js';
export class Store {
 readonly db:DatabaseSync;
 constructor(directory:string){
  mkdirSync(directory,{recursive:true,mode:0o700});
  const path=join(directory,'recovery.sqlite');this.db=new DatabaseSync(path);chmodSync(path,0o600);
  this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
   CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,account TEXT NOT NULL,command TEXT NOT NULL,state TEXT NOT NULL,created INTEGER NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,UNIQUE(account,command));
   CREATE INDEX IF NOT EXISTS jobs_account_page ON jobs(account,created DESC,id DESC);
   CREATE INDEX IF NOT EXISTS jobs_unsettled ON jobs(state);
   CREATE INDEX IF NOT EXISTS jobs_fence_command ON jobs(state,json_extract(payload,'$.fenceCommandId'));
   CREATE TABLE IF NOT EXISTS fence(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS revision(id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER NOT NULL);
   INSERT OR IGNORE INTO revision VALUES(1,0);`);
  // A restarted owner has no authority to replay commands or assume a former lease survived.
  const interrupted=this.db.prepare("UPDATE jobs SET state='unknown',revision=revision+1,payload=json_set(payload,'$.state','unknown','$.revision',revision+1,'$.reason','owner-restarted-no-replay') WHERE state IN ('queued','quiescing','running','releasing')").run();if(interrupted.changes)this.bump();
 }
 revision(){return Number(this.db.prepare('SELECT value FROM revision WHERE id=1').get()!.value);}
 get(id:string):Job|undefined{const row=this.db.prepare('SELECT payload FROM jobs WHERE id=?').get(id);return row?JSON.parse(String(row.payload)):undefined;}
 command(account:string,command:string):Job|undefined{const row=this.db.prepare('SELECT payload FROM jobs WHERE account=? AND command=?').get(account,command);return row?JSON.parse(String(row.payload)):undefined;}
 insert(job:Job){this.db.prepare('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)').run(job.id,job.accountId,job.commandId,job.state,job.createdAt,job.revision,JSON.stringify(job));this.bump();}
 save(job:Job){job.updatedAt=Date.now();job.revision++;this.db.prepare('UPDATE jobs SET state=?,revision=?,payload=? WHERE id=?').run(job.state,job.revision,JSON.stringify(job),job.id);this.bump();}
 private bump(){this.db.exec('UPDATE revision SET value=value+1 WHERE id=1');}
 unsettled(except?:string){return this.db.prepare("SELECT id,state FROM jobs WHERE state IN ('queued','quiescing','running','releasing','unknown') AND id!=? LIMIT 1").get(except??'');}
 page(account:string,limit:number,before?:{created:number;id:string}){
  return before?this.db.prepare('SELECT id,command,state,created,revision FROM jobs WHERE account=? AND (created<? OR (created=? AND id<?)) ORDER BY created DESC,id DESC LIMIT ?').all(account,before.created,before.created,before.id,limit):this.db.prepare('SELECT id,command,state,created,revision FROM jobs WHERE account=? ORDER BY created DESC,id DESC LIMIT ?').all(account,limit);
 }
 fence():Json|undefined{const row=this.db.prepare('SELECT payload FROM fence WHERE id=1').get();return row?JSON.parse(String(row.payload)):undefined;}
 setFence(value:Json){this.db.prepare('INSERT INTO fence VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload').run(JSON.stringify(value));}
 clearFence(){this.db.exec('DELETE FROM fence WHERE id=1');}
 close(){this.db.close();}
}
