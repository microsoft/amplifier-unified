import test from 'node:test';import assert from 'node:assert/strict';import {DatabaseSync} from 'node:sqlite';import {mkdtemp,mkdir,readFile,writeFile,rm,symlink} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';import {createHash} from 'node:crypto';
import {createClientMigration} from '../src/client-migration.js';
import {spawnSync} from 'node:child_process';
const call=(id,patch={})=>({kind:'llm',id,sessionId:'old',rootSessionId:'old',revision:2,phase:'completed',producerId:'old-worker',usage:{inputTokens:10,outputTokens:2,cacheWriteTokens:3},...patch});
async function fixture(t,session,overlay){const root=await mkdtemp(join(tmpdir(),'legacy-usage-')),database=join(root,'old.db'),db=new DatabaseSync(database);db.exec('CREATE TABLE state(id INTEGER PRIMARY KEY,value TEXT);CREATE TABLE state_records(kind TEXT,id TEXT,value TEXT,PRIMARY KEY(kind,id));CREATE TABLE state_resources(id TEXT PRIMARY KEY,value TEXT)');db.prepare('INSERT INTO state VALUES(1,?)').run(JSON.stringify({sessions:session}));for(const [id,value]of overlay??[])db.prepare('INSERT INTO state_records VALUES(?,?,?)').run('session',id,JSON.stringify(value));const reader=()=>createClientMigration({database,account:'owned',engineId:'native'});t.after(async()=>{db.close();await rm(root,{recursive:true,force:true});});return {root,database,db,reader};}
const session=execution=>({id:'old',runtimeSessionId:'native-old',workspace:'/owned',execution});
const scope={engineId:'native',nativeSessionId:'native-old',workingDirectory:'/owned'};
test('current scoped record wins; edited-away, child and unknown calls retain identity without private bodies',async t=>{
 const f=await fixture(t,[session({nodes:[call('stale')]})],[['old',session({nodes:[call('kept',{rawRequest:'never export',arguments:{private:'secret'}}),{kind:'worker',rootSessionId:'old',sessionId:'child'},call('child-call',{sessionId:'child'}),call('foreign',{rootSessionId:'elsewhere'}),call('unbound',{sessionId:'stranger'}),call('interrupted',{phase:'running',usage:{}})],retiredUsageNodes:[call('edited')]})]]);
 const before=await readFile(f.database),migration=f.reader();try{const rows=await migration.usage(scope);assert.deepEqual(rows.map(r=>r.id),['kept','child-call','interrupted','edited']);assert.equal(rows[0].sessionId,'native-old');assert.equal(rows[1].sessionId,'child');assert.ok(rows.every(r=>r.rootSessionId==='native-old'));assert.equal(rows[2].phase,'outcome_unknown');assert.ok(!JSON.stringify(rows).includes('secret'));assert.deepEqual(await migration.usage({...scope,engineId:'other'}),[]);assert.deepEqual(await readFile(f.database),before);}finally{migration.close();}
});
test('deletions and changed native aliases cannot revive checkpoint accounting',async t=>{for(const value of [null,{...session({nodes:[call('other')]}),runtimeSessionId:'different-native'}]){const f=await fixture(t,[session({nodes:[call('stale')]})],[['old',value]]),migration=f.reader();try{assert.deepEqual(await migration.usage(scope),[]);}finally{migration.close();}}});
test('ambiguous and mismatched workspace mappings refuse instead of guessing',async t=>{for(const values of [[session({}),{...session({}),id:'alias'}],[{...session({}),workspace:'/elsewhere'}]]){const f=await fixture(t,values),migration=f.reader();try{await assert.rejects(migration.usage(scope),/ambiguous|workspace differs/);}finally{migration.close();}}});
test('cold immutable execution data is hash-checked, read once by exact reference and never follows a symlink',async t=>{
 const raw=JSON.stringify({nodes:[call('cold')],retiredUsageNodes:[call('retired')]}),id=createHash('sha256').update(raw).digest('hex');const saved=session(undefined);delete saved.execution;saved._coldFields={execution:{$resource:id}};
 const f=await fixture(t,[saved]);f.db.prepare('INSERT INTO state_resources VALUES(?,?)').run(id,JSON.stringify({$blob:id}));await mkdir(join(f.root,'artifacts'));const file=join(f.root,'artifacts',id+'.json');await writeFile(file,raw);const migration=f.reader();try{assert.equal((await migration.usage(scope)).length,2);await writeFile(file,raw+' ');await assert.rejects(migration.usage(scope),/changed/);await rm(file);await writeFile(join(f.root,'outside'),raw);await symlink(join(f.root,'outside'),file);await assert.rejects(migration.usage(scope),/redirected/);}finally{migration.close();}
});
test('current main serializers retain cold edited-away usage through the migration reader',{skip:!process.env.AMPLIFIER_LEGACY_SERIALIZER_ROOT||!process.env.AMPLIFIER_CAPACITY_PYTHON},async t=>{
 const root=await mkdtemp(join(tmpdir(),'main-serialized-usage-')),database=join(root,'main.db');t.after(()=>rm(root,{recursive:true,force:true}));
 const result=spawnSync(process.env.AMPLIFIER_CAPACITY_PYTHON,['-I','-B','-c',String.raw`
import json,sqlite3,sys
sys.path.insert(0,sys.argv[1])
from amplifier_web.state_records import initialize,checkpoint,load
from amplifier_web.resource_files import put
from amplifier_web.capacity import usage_snapshot
db=sqlite3.connect(':memory:');db.execute('CREATE TABLE state(id INTEGER PRIMARY KEY,value TEXT)');db.execute('CREATE TABLE state_resources(id TEXT PRIMARY KEY,value TEXT)');initialize(db)
call={'kind':'llm','id':'unicode-call','sessionId':'old','rootSessionId':'old','phase':'completed','revision':2,'provider':'provider','model':'déjà vu','usage':{'inputTokens':100,'outputTokens':5,'cacheReadTokens':90,'cacheWriteTokens':20,'costUsd':0.001}}
retired={**call,'id':'retired','usage':{'inputTokens':10,'outputTokens':5}}
session={'id':'old','runtimeSessionId':'native-old','workspace':'/owned','execution':{'nodes':[call],'retiredUsageNodes':[retired]}}
expected=usage_snapshot(session)['metrics']['grossTotalTokens']['value'];reference=put(db,session.pop('execution'));session['_coldFields']={'execution':reference}
checkpoint(db,{'revision':3,'sessions':[session]});assert load(db)['sessions'][0]==session;db.commit()
output=sqlite3.connect(sys.argv[2]);db.backup(output);output.close();db.close();print(expected)
`,process.env.AMPLIFIER_LEGACY_SERIALIZER_ROOT,database],{encoding:'utf8'});assert.equal(result.status,0,result.stderr);
 const migration=createClientMigration({database,account:'owned',engineId:'native'});try{const rows=await migration.usage(scope);assert.equal(rows.length,2);assert.equal(rows[0].model,'déjà vu');assert.equal(rows.reduce((sum,row)=>sum+row.usage.inputTokens+row.usage.outputTokens+(row.usage.cacheWriteTokens??0),0),Number(result.stdout.trim()));}finally{migration.close();}
});
