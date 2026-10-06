import test from 'node:test';
import assert from 'node:assert/strict';
import {createMaintenanceCapabilities} from '../dist/index.js';
const metadata={version:1,metadataOnly:true,previewRequired:true,receipt:true,paged:true,workReplayed:false};
const action=(operation,args={},commandId='one')=>({version:1,channel:'ahp-root://',topic:'maintenance',operation:'maintenance.bundleReferences.'+operation,args,commandId});
test('saved bundle repair is negotiated, bounded, authorized and receipt-only on recovery',async()=>{
 const calls=[];
 const options={nativeAdmin:async(...args)=>{calls.push(args);return{receipt:{state:'unknown'}}},authorize:async context=>{assert.equal(context.clientId,'reviewer')}};
 const absent=createMaintenanceCapabilities(options);
 assert.equal(absent.manifest.actions['maintenance.bundleReferences.preview'],undefined);
 await assert.rejects(absent.action(action('preview'),{clientId:'reviewer'}),/Unknown/);
 const owner=createMaintenanceCapabilities({...options,bundleReferences:metadata});
 await owner.action(action('preview'),{clientId:'reviewer'});
 await owner.action(action('apply',{previewHash:'a'.repeat(64)}),{clientId:'reviewer'});
 const recovered=await owner.action(action('receipt',{commandId:'one'}),{clientId:'reviewer'});
 assert.equal(recovered.result.receipt.state,'unknown');
 assert.deepEqual(calls.map(([op,args])=>[op,args]),[
  ['maintenance.bundleReferences.preview',{}],
  ['maintenance.bundleReferences.apply',{previewHash:'a'.repeat(64),commandId:'bundle-references:one'}],
  ['maintenance.receipt',{commandId:'bundle-references:one'}],
 ]);
 await assert.rejects(owner.action(action('apply',{previewHash:'a'.repeat(64),cwd:'/untrusted'}),{clientId:'reviewer'}),/Unexpected/);
 await assert.rejects(owner.action(action('preview'),{clientId:'reviewer',origin:'agent'}),/account-level/);
 assert.equal(calls.length,3);
});
