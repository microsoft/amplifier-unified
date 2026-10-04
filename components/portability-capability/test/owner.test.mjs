import {test} from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,mkdir,rm,realpath} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';const {createPortabilityCapabilities}=await import(process.env.PORTABILITY_MODULE??'../dist/index.js');
const executable=process.env.PORTABILITY_PYTHON;

test('root selected action authenticates explicit AHP scope and never treats native IDs as channels',{skip:!executable},async()=>{
 const directory=await realpath(await mkdtemp(join(tmpdir(),'portability-selected-')));const workspace=join(directory,'work');await mkdir(workspace);const config=join(directory,'config.json');
 const {writeFile}=await import('node:fs/promises');await writeFile(config,JSON.stringify({dataDir:join(directory,'owner'),exchangeDir:join(directory,'exchange'),stageDir:join(workspace,'stages'),workspaceRoots:[workspace]}));
 const inspected=[];const uri='ahp-session:///selected';const denied=async()=>{throw Error('No mutation during inspection');};
 const owner=createPortabilityCapabilities({owner:{command:executable,cwd:directory,args:['-m','amplifier_unified_portability.server','--config',config]},inspectSession:async(session,context)=>{inspected.push({session,context});return {uri:session,nativeSessionId:'native-selected',executionDirectory:workspace,executionRevision:7};},beginTransfer:denied,commitTransfer:denied,cancelTransfer:denied,adoptTransferredSession:denied,nativeTransfer:denied,exportTransferEvidence:denied,stageTransferEvidence:denied,activateTransferEvidence:denied});
 const action={version:1,topic:'portability',channel:'ahp-root://',operation:'portability.inspect',commandId:'inspect-selected',args:{sessionId:uri}};
 try{
  const value=await owner.action(action,{clientId:'authenticated',origin:'ui'});assert.deepEqual(value.result.receipts,[]);assert.equal(inspected[0].context.clientId,'authenticated');assert.equal(inspected[0].session,uri);
  const recovered=await owner.action({...action,operation:'portability.command',args:{commandId:'absent',sessionId:uri}},{clientId:'authenticated'});assert.equal(recovered.result.receipt,null);
  await assert.rejects(owner.action({...action,args:{sessionId:'native-uuid'}},{clientId:'authenticated'}),/scope/);
  await assert.rejects(owner.action(action,{clientId:'authenticated',origin:'agent',session:'ahp-session:///another'}),/mismatch/);
 }finally{await owner.close();await rm(directory,{recursive:true,force:true});}
});


for(const missing of ['commands','bindings'])test(`damaged ${missing} refuses subprocess startup without host callbacks`,{skip:!executable},async()=>{
 const {execFile}=await import('node:child_process');const {promisify}=await import('node:util');const {writeFile,readFile}=await import('node:fs/promises');
 const directory=await realpath(await mkdtemp(join(tmpdir(),'portability-startup-')));const workspace=join(directory,'work');const data=join(directory,'owner');await mkdir(workspace);await mkdir(data);
 const config=join(directory,'config.json');await writeFile(config,JSON.stringify({dataDir:data,exchangeDir:join(directory,'exchange'),stageDir:join(workspace,'stages'),workspaceRoots:[workspace]}));
 const sql=missing==='commands'?'CREATE TABLE bindings(transfer TEXT PRIMARY KEY,uri TEXT,native TEXT,cwd TEXT,engine TEXT)':'CREATE TABLE commands(scope TEXT,id TEXT,signature TEXT,body TEXT,PRIMARY KEY(scope,id))';
 await promisify(execFile)(executable,['-c','import sqlite3,sys; db=sqlite3.connect(sys.argv[1]);db.execute(sys.argv[2]);db.commit();db.close()',join(data,'owner.sqlite3'),sql]);const before=await readFile(join(data,'owner.sqlite3'));let callbacks=0;
 const {OwnerConnection}=await import(process.env.PORTABILITY_MODULE??'../dist/index.js');const owner=new OwnerConnection({command:executable,cwd:directory,args:['-m','amplifier_unified_portability.server','--config',config]},async()=>{callbacks++;throw Error('Unexpected callback');},()=>{});
 try{await assert.rejects(owner.request('snapshot',{session:'host'}),/exited|failed/);assert.equal(callbacks,0);assert.deepEqual(await readFile(join(data,'owner.sqlite3')),before);}finally{await owner.close();await rm(directory,{recursive:true,force:true});}
});
