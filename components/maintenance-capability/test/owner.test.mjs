import test from 'node:test';import assert from 'node:assert/strict';
import {createMaintenanceCapabilities} from '../dist/index.js';
test('lazy bounded maintenance reads and known-only qualification selection',async()=>{
 let calls=[],authorized=0,result='succeeded';
 const provider=createMaintenanceCapabilities({authorize:async()=>{authorized++;},nativeAdmin:async(op,args)=>{calls.push([op,args]);if(op==='generations.inspect')return {phase:'checked',checkId:'check1',pointer:{current:'old'},items:[]};if(op==='generations.prepare')return {state:result,result:{generation:'new'}};return {state:'succeeded'};}});
 assert.equal(calls.length,0);assert.ok(provider.actionSchemas()['updates.runtime.receipt']);
 await provider.read({topic:'maintenance',scope:'host',uri:provider.manifest.topics.maintenance.uri+'?scope=host',clientId:'one'});
 await provider.read({topic:'maintenance',scope:'host',uri:provider.manifest.topics.maintenance.uri,clientId:'two'});assert.equal(calls.length,1);assert.equal(authorized,2);
 const action={version:1,channel:'ahp-root://',topic:'maintenance',operation:'updates.install',commandId:'install1',args:{}};
 await provider.action(action,{clientId:'one'});assert.equal(calls.at(-1)[0],'generations.promote');assert.equal(calls.at(-1)[1].expectedCurrent,'old');
 result='unknown';calls=[];await provider.action({...action,commandId:'install2'},{clientId:'one'});assert.ok(!calls.some(([op])=>op==='generations.promote'));
 await assert.rejects(provider.action({...action,args:{cwd:'/untrusted'}},{clientId:'one'}),/Unexpected/);
 await provider.close();
});
test('missing host authorization fails before native effects',async()=>{
 let calls=0;const provider=createMaintenanceCapabilities({authorize:async()=>{throw Error('Denied');},nativeAdmin:async()=>{calls++;return {};}});
 await assert.rejects(provider.read({topic:'maintenance',scope:'host',uri:provider.manifest.topics.maintenance.uri,clientId:'one'}),/Denied/);assert.equal(calls,0);
});
test('repair keeps reconstruction and activation separate with exact review and receipt identities',async()=>{
 const calls=[];const context={clientId:'reviewer'};
 const provider=createMaintenanceCapabilities({authorize:async()=>{},nativeAdmin:async(operation,args)=>{calls.push([operation,args]);return operation==='generations.repair'?{state:'unknown',candidate:'retained'}:{state:'succeeded'};}});
 const action=(operation,args,commandId='repair-one')=>provider.action({version:1,channel:'ahp-root://',topic:'maintenance',operation,args,commandId},context);
 const args={generation:'qualified-old',expectedSourceHash:'a'.repeat(64),expectedCurrent:'current'};
 const result=await action('updates.runtime.repair',args);assert.equal(result.result.state,'unknown');
 assert.deepEqual(calls,[['generations.repair',{...args,commandId:'repair-one'}]]);
 await action('updates.runtime.receipt',{commandId:'repair-one'});
 assert.equal(calls.filter(([op])=>op==='generations.repair').length,1);assert.ok(!calls.some(([op])=>op==='generations.promote'));
 await assert.rejects(action('updates.runtime.select',{generation:'new'}),/expectedCurrent/);
 await action('updates.runtime.select',{generation:'new',expectedCurrent:'current'},'reviewed-select');
 assert.deepEqual(calls.at(-1),['generations.promote',{generation:'new',expectedCurrent:'current',commandId:'reviewed-select'}]);
});
test('resident evidence never falls back to administration or session mounting',async()=>{
 let resident=0;const provider=createMaintenanceCapabilities({authorize:async()=>{},nativeAdmin:async()=>{throw Error('Unexpected admin fallback');},inspectResidentRuntime:async(session,args)=>{resident++;assert.equal(session,'ahp-session:/selected');assert.deepEqual(args,{surface:'mounted',limit:2});throw Error('Worker is not resident');}});
 const params={version:1,channel:'ahp-root://',topic:'maintenance',operation:'updates.runtime.worker',args:{sessionId:'ahp-session:/selected',surface:'mounted',limit:2}};
 await assert.rejects(provider.action(params,{clientId:'browser'}),/not resident/);assert.equal(resident,1);
 await assert.rejects(provider.action(params,{clientId:'',origin:'agent',session:'ahp-session:/other'}),/another conversation/);assert.equal(resident,1);
});
