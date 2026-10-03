import {DatabaseSync} from 'node:sqlite';
import {WorktreeQuiescence} from './quiescence.js';
import {mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
export const canonical=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item);
export const digest=value=>createHash('sha256').update(canonical(value)).digest('hex');
export function gitIdentity(operation,commandId){const hash=createHash('sha1').update(Buffer.from('6ba7b8119dad11d180b400c04fd430c8','hex')).update((operation==='worktree.attach'?'attached-worktree:':'managed-worktree:')+commandId).digest();hash[6]=(hash[6]&15)|80;hash[8]=(hash[8]&63)|128;const hex=hash.subarray(0,16).toString('hex');return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;}
export const now=()=>Date.now()/1000;

/** SQL owns lookup indexes and effect receipts, never native history or Git state. */
export class WorktreeStore {
  constructor(directory,{onMayBeIdle}={}) {
    mkdirSync(directory,{recursive:true,mode:0o700});this.quiescence=new WorktreeQuiescence(directory,onMayBeIdle);try{this.db=new DatabaseSync(join(directory,'worktrees.sqlite'));
    this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,session TEXT NOT NULL,created REAL NOT NULL,value TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS records_session ON records(session,created DESC,id);
      CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,session TEXT NOT NULL,signature TEXT NOT NULL,operation TEXT NOT NULL,phase TEXT NOT NULL,created REAL NOT NULL,source TEXT,target TEXT,value TEXT NOT NULL,result TEXT);
      CREATE INDEX IF NOT EXISTS commands_session ON commands(session,created DESC,id);
      CREATE INDEX IF NOT EXISTS handoffs_session ON commands(session,operation,created DESC,id);
      CREATE INDEX IF NOT EXISTS pending_session ON commands(session,phase);
      CREATE INDEX IF NOT EXISTS command_phase ON commands(phase);
      CREATE INDEX IF NOT EXISTS reserved_source ON commands(phase,source);
      CREATE INDEX IF NOT EXISTS reserved_target ON commands(phase,target);
      CREATE TABLE IF NOT EXISTS topics(session TEXT PRIMARY KEY,revision INTEGER NOT NULL);`);
    // One indexed SQL transition; do not deserialize historical receipt files.
    this.db.prepare(`UPDATE commands SET phase='unknown',value=json_set(value,'$.phase','unknown','$.revision',json_extract(value,'$.revision')+1,'$.detail','The owner restarted before a confirmed boundary. Inspect evidence; no action was replayed.') WHERE phase='pending'`).run();
    this.quiescence.retentionReferences=sessions=>({coverage:'complete',protected:sessions.flatMap(session=>this.db.prepare("SELECT 1 FROM commands WHERE session=? AND phase IN ('pending','unknown') LIMIT 1").get(session)?[{session,reasons:['worktree-unsettled']}]:[]),omissions:[]});
    }catch(error){this.db?.close();this.quiescence.close();throw error;}
  }
  revision(session){return Number(this.db.prepare('SELECT revision FROM topics WHERE session=?').get(session)?.revision??0)}
  touch(session){this.quiescence.assertOpen();this.db.prepare('INSERT INTO topics VALUES(?,1) ON CONFLICT(session) DO UPDATE SET revision=revision+1').run(session)}
  record(session,id){const value=this.db.prepare('SELECT value FROM records WHERE session=? AND id=?').get(session,id);if(!value)throw Error('Checkout not found in this conversation.');return JSON.parse(value.value)}
  saveRecord(session,value){this.quiescence.assertOpen();if(value.sessionId!==session)throw Error('Git record changed conversation scope.');const {checkout,...summary}=value;const manifest={...summary.manifest};delete manifest.untracked;summary.manifest=manifest;this.db.prepare('INSERT INTO records VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value').run(value.id,session,value.createdAt,canonical(summary));this.touch(session)}
  command(session,id){const row=this.db.prepare('SELECT * FROM commands WHERE session=? AND id=?').get(session,id);return row&&{...row,value:JSON.parse(row.value),result:row.result?JSON.parse(row.result):undefined}}
  begin(value,signature){this.quiescence.assertOpen();const old=this.command(value.sessionId,value.id);if(old){if(old.signature!==signature)throw Error('This command identity already has different contents.');return old}
    this.db.prepare('INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,?,NULL)').run(value.id,value.sessionId,signature,value.operation,value.phase,value.createdAt,value.source??null,value.target??null,canonical(value));this.touch(value.sessionId);return null;
  }
  settle(value,result){this.quiescence.assertOpen();this.db.prepare('UPDATE commands SET phase=?,source=?,target=?,value=?,result=? WHERE id=? AND session=?').run(value.phase,value.source??null,value.target??null,canonical(value),result===undefined?null:canonical(result),value.id,value.sessionId);this.touch(value.sessionId)}
  unresolved(session){return this.db.prepare("SELECT value FROM commands WHERE session=? AND phase IN ('pending','unknown') AND operation IN ('worktree.handoff','worktree.reconcile') LIMIT 1").get(session)}
  reserved(path){return this.db.prepare("SELECT 1 FROM commands WHERE phase IN ('pending','unknown') AND (source=? OR target=?) LIMIT 1").get(path,path)}
  page(session,collection='worktrees',{limit=25,cursor}={}) {
    if(!['worktrees','handoffs','commands'].includes(collection)||!Number.isSafeInteger(limit)||limit<1||limit>50)throw Error('Invalid worktree page.');
    let after;try{if(cursor)after=JSON.parse(Buffer.from(cursor,'base64url').toString())}catch{throw Error('Invalid worktree cursor.')}
    if(after&&(after.session!==session||after.collection!==collection||!Number.isFinite(after.created)||typeof after.id!=='string'))throw Error('Worktree cursor belongs to another scope.');
    const table=collection==='worktrees'?'records':'commands';
    const filter=collection==='handoffs'?" AND operation IN ('worktree.handoff','worktree.reconcile')":collection==='commands'?" AND operation NOT IN ('worktree.handoff','worktree.reconcile')":'';
    const args=[session,...(after?[after.created,after.created,after.id]:[]),limit+1];
    const rows=this.db.prepare(`SELECT id,created,value FROM ${table} WHERE session=?${filter}${after?' AND (created<? OR (created=? AND id>?))':''} ORDER BY created DESC,id LIMIT ?`).all(...args);
    const last=rows[Math.min(limit,rows.length)-1];return {items:rows.slice(0,limit).map(row=>JSON.parse(row.value)),...(rows.length>limit?{nextCursor:Buffer.from(JSON.stringify({session,collection,created:last.created,id:last.id})).toString('base64url')}:{})};
  }
  close(){if(this.closed)return;this.quiescence.assertClosable();this.closed=true;try{this.db.close()}finally{this.quiescence.close()}}
}
