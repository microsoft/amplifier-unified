import test from 'node:test';
import assert from 'node:assert/strict';
import {recoveryReleaseVerifier} from '../src/quiescence.js';
test('replacement recovery release requires exact completed cleanup metadata and native receipts',async()=>{
 let evidence={fenceId:'fence',commandId:'command',instanceId:'new',originalInstanceId:'old',dataScope:'scope',outcome:'ready',receiptId:'settled',nativeAuthority:'native',nativeLeaseReleased:true,nativeLeaseDisposition:'not-acquired',terminalState:'succeeded',operation:'recovery.workspace.remove',metadataSettlement:true,workspaceArchiveReceiptHash:'a'.repeat(64),nativeResultHash:'b'.repeat(64),nativeCommandId:'original-native'};
 const original=structuredClone(evidence),request={purpose:'recovery',fenceId:'fence',commandId:'command',instanceId:'old',dataScope:'scope',outcome:'ready'};
 const verify=recoveryReleaseVerifier({instanceId:'new',dataScope:'scope',nativeAuthority:'native',recovery:()=>({readReleaseEvidence:()=>evidence})});
 assert.equal((await verify(request)).instanceId,'new');
 for(const patch of [{metadataSettlement:false},{operation:'recovery.snapshot'},{originalInstanceId:'foreign'},{workspaceArchiveReceiptHash:null},{nativeResultHash:null},{nativeCommandId:null},{nativeLeaseDisposition:'released'},{nativeLeaseReleased:false},{terminalState:'refused'},{instanceId:'foreign'}]){
  evidence={...original,...patch};await assert.rejects(verify(request),/exact settled/);
 }
 evidence=original;await assert.rejects(verify({...request,outcome:'unchanged'}),/exact settled/);
});
