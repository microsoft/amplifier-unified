import {test} from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,mkdir,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';const {createPortabilityCapabilities}=await import(process.env.PORTABILITY_MODULE??'../dist/index.js');
const executable=process.env.PORTABILITY_PYTHON;
test('installed Python owner advertises bounded reads and command receipts through independent transport',{skip:!executable},async()=>{
 const directory=await mkdtemp(join(tmpdir(),'portability-bridge-'));const workspace=join(directory,'work');await mkdir(workspace);const config=join(directory,'config.json');
 const {writeFile}=await import('node:fs/promises');await writeFile(config,JSON.stringify({dataDir:join(directory,'owner'),exchangeDir:join(directory,'exchange'),stageDir:join(workspace,'stages'),workspaceRoots:[workspace]}));
 const denied=async()=>{throw Error('Unused mutation callback must never execute during reads');};
 const owner=createPortabilityCapabilities({owner:{command:executable,cwd:directory,args:['-m','amplifier_unified_portability.server','--config',config]},inspectSession:denied,beginTransfer:denied,commitTransfer:denied,cancelTransfer:denied,adoptTransferredSession:denied,nativeTransfer:denied,exportTransferEvidence:denied,stageTransferEvidence:denied,activateTransferEvidence:denied});
 try{
  const schemas=await owner.actionSchemas();assert.ok(schemas['portability.export'].schema);assert.ok(schemas['portability.reconcile']);
  const page=await owner.read({uri:'amplifier-capability://portability/portability?scope=host',topic:'portability',scope:'host',clientId:'fixture'});assert.deepEqual(page.data.portability.receipts,[]);assert.match(page.data.portability.host.id,/^[0-9a-f]{64}$/);
  await assert.rejects(owner.action({version:1,topic:'portability',channel:'ahp-root://',operation:'portability.stage',commandId:'agent1',args:{path:'/absent',repository:workspace,reviewedCapsuleHash:'0'.repeat(64)}},{clientId:'fixture',origin:'agent'}),/approval/);
  const receipt=await owner.action({version:1,topic:'portability',channel:'ahp-root://',operation:'portability.command',commandId:'read1',args:{commandId:'agent1'}},{clientId:'fixture',origin:'ui'});assert.equal(receipt.result.receipt,null);assert.deepEqual(receipt.invalidate,['portability']);
 }finally{await owner.close();await rm(directory,{recursive:true,force:true});}
});
