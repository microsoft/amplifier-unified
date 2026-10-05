import test from 'node:test';
import assert from 'node:assert/strict';
import {composeOriginalAttachments} from '../src/original-attachments.js';
class LegacyHost {}
class CurrentHost {verifyOriginalForkAttachment(){} readPublicOriginalRows(){} reconcileOriginalForkAttachments(){}}
test('older Host/Resources packages retain the existing resolver and receive no original grant ports',async()=>{
 for(const Host of [LegacyHost,CurrentHost]){
  let seen;const resources={resolvePromptAttachment:async(...args)=>{seen=args;return 'ordinary';}},ports=composeOriginalAttachments(Host,()=>undefined),ctx={session:'ahp-session:/legacy'},attachment={uri:'amplifier-attachment://original/body'},options={mode:'inline'};
  assert.deepEqual(ports.hostOptions(resources),{});if(Host===LegacyHost)assert.deepEqual(ports.resourceOptions,{});
  assert.equal(await ports.resolvePromptAttachment(resources,ctx,attachment,options),'ordinary');assert.deepEqual(seen,[ctx,attachment,options]);
 }
});
test('unrelated URI schemes keep the existing resolver without original grant lookup',async()=>{
 const ports=composeOriginalAttachments(CurrentHost,()=>undefined);let calls=0;
 const resources={retainForkAttachments(){},resolveOriginalAttachment(){assert.fail('unrelated reference queried as an original');},resolvePromptAttachment:async(_ctx,attachment)=>{calls++;return attachment.uri;}};
 for(const uri of ['file:///selected.txt','https://example.invalid/body','amplifier-resource://selected/body'])assert.equal(await ports.resolvePromptAttachment(resources,{session:'ahp-session:/scope'},{uri}),uri);
 assert.equal(calls,3);
});
test('qualified target URI is transient; unknown or mismatched grant cannot materialize',async()=>{
 const ports=composeOriginalAttachments(CurrentHost,()=>undefined),context={session:'ahp-session:/target'},attachment={uri:'amplifier-attachment://original/body',label:'literal label'};
 let grant=null,seen;const resources={retainForkAttachments(){},resolveOriginalAttachment:async()=>grant,resolvePromptAttachment:async(_ctx,a)=>{seen=a;return [];}};
 await ports.resolvePromptAttachment(resources,context,attachment);assert.equal(seen,attachment);
 grant={version:2,status:'completed',replayed:false,targetHostSession:context.session,originalReference:{owner:'resources',id:attachment.uri},accessUri:'amplifier-attachment://target/body'};
 await ports.resolvePromptAttachment(resources,context,attachment);assert.equal(seen.uri,grant.accessUri);assert.equal(attachment.uri,'amplifier-attachment://original/body');
 for(const patch of [{status:'unknown'},{targetHostSession:'ahp-session:/foreign'},{replayed:true},{originalReference:{owner:'resources',id:'foreign'}},{accessUri:undefined}]){
  const saved=grant;grant={...grant,...patch};seen=undefined;await assert.rejects(ports.resolvePromptAttachment(resources,context,attachment),e=>e.data?.executed===false);assert.equal(seen,undefined);grant=saved;
 }
});
