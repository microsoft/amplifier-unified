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
