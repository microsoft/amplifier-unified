import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createClientMigration} from '../src/client-migration.js';
test('only exact selected workspace identities are exported and duplicate legacy identities remain unresolved',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'workspace-migration-')),database=join(directory,'old.sqlite');let migration;
 try{
  const db=new DatabaseSync(database);db.exec('CREATE TABLE client_views(id TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE state(id INTEGER PRIMARY KEY,value TEXT NOT NULL)');
  const record={selectedWorkspaceId:'selected',view:{workWorkspaceId:'duplicate',draft:'unsent'}};
  db.prepare('INSERT INTO client_views VALUES (?,?)').run('tab',JSON.stringify(record));
  db.prepare('INSERT INTO state VALUES (1,?)').run(JSON.stringify({workspaces:[{id:'selected',path:'/owned/work'},{id:'other',path:'/private/other'},{id:'duplicate',path:'/one'},{id:'duplicate',path:'/two'}]}));db.close();
  const before=await readFile(database),seen=[];
  migration=createClientMigration({database,account:'owner',resolveWorkspace:async path=>{seen.push(path);return {id:'workspace:resolved',path};}});
  const meta=await migration.metadata({clientId:'new',metadata:{'amplifier.dev/legacyClient':{id:'tab'}}});
  const result=JSON.parse((await migration.resourceProvider.read({uri:meta['amplifier.dev/clientMigration'].uri},{clientId:'new'})).data);
  assert.deepEqual(seen,['/owned/work']);assert.deepEqual(result.workspaceMap,{selected:{id:'workspace:resolved',path:'/owned/work'}});assert.deepEqual(result.record,record);assert.deepEqual(await readFile(database),before);
 }finally{migration?.close();await rm(directory,{recursive:true,force:true});}
});
test('legacy migration reads exactly one named client, binds its grant and preserves source bytes',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'client-migration-')),database=join(directory,'old.sqlite');let migration;
 try{
  const db=new DatabaseSync(database);db.exec('CREATE TABLE client_views(id TEXT PRIMARY KEY,value TEXT NOT NULL)');
  const original={selectedSessionId:'native-a',drafts:{'native-a':'unsent','ambiguous':'also unsent'},attachments:{'native-a':[{id:'old-image'}]},view:{draft:'selected draft'},deviceCommands:[{type:'never-replay'}]};
  const add=db.prepare('INSERT INTO client_views VALUES (?,?)');add.run('old-tab',JSON.stringify(original));add.run('other-tab','broken JSON must never be read');db.close();
  const before=await readFile(database);let lookups=0;
  migration=createClientMigration({database,account:'owner',resolveNative:async params=>{lookups++;assert.deepEqual(params.identities,['native-a','ambiguous']);return {'native-a':{status:'matched',uri:'ahp-session:/resolved'},ambiguous:{status:'ambiguous'}};}});
  assert.equal(await migration.metadata({clientId:'new',metadata:{}}),undefined);
  const metadata=await migration.metadata({clientId:'new',metadata:{'amplifier.dev/legacyClient':{id:'old-tab'}}});assert.equal(lookups,0);
  const uri=metadata['amplifier.dev/clientMigration'].uri;
  await assert.rejects(migration.resourceProvider.read({uri},{clientId:'unrelated'}),/unavailable/);
  const value=JSON.parse((await migration.resourceProvider.read({uri},{clientId:'new'})).data);
  assert.deepEqual(value.record,original);assert.deepEqual(value.sessionMap,{'native-a':'ahp-session:/resolved'});assert.equal(value.sourceRetained,true);assert.equal(lookups,1);
  assert.deepEqual(await readFile(database),before);
 }finally{migration?.close();await rm(directory,{recursive:true,force:true});}
});
test('oversize legacy state is preserved without transferring or parsing its body',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'client-migration-large-')),database=join(directory,'old.sqlite');let migration;
 try{
  const db=new DatabaseSync(database);db.exec('CREATE TABLE client_views(id TEXT PRIMARY KEY,value TEXT NOT NULL)');db.prepare('INSERT INTO client_views VALUES (?,?)').run('large','x'.repeat(1024*1024));db.close();
  migration=createClientMigration({database,account:'owner'});
  assert.deepEqual((await migration.metadata({clientId:'new',metadata:{'amplifier.dev/legacyClient':{id:'large'}}}))['amplifier.dev/clientMigration'],{version:1,account:'owner',status:'oversize',sourceRetained:true});
 }finally{migration?.close();await rm(directory,{recursive:true,force:true});}
});
