import test from 'node:test';
import assert from 'node:assert/strict';
import {request,applyIconTooltips} from '../src/api.js';

test('commands send structured JSON to same origin and return server receipt',async()=>{
 const original=globalThis.fetch;
 try {globalThis.fetch=async(path,options)=>{assert.equal(path,'/api/actions');assert.equal(options.credentials,'same-origin');assert.equal(options.headers['Content-Type'],'application/json');assert.deepEqual(JSON.parse(options.body),{action:'conversation.send',args:{text:'Hello'}});return new Response(JSON.stringify({accepted:true,revision:4}))};assert.deepEqual(await request('/api/actions',{method:'POST',body:{action:'conversation.send',args:{text:'Hello'}}}),{accepted:true,revision:4});}finally{globalThis.fetch=original}
});
test('server validation errors remain visible and do not report success',async()=>{
 const original=globalThis.fetch;try{globalThis.fetch=async()=>new Response(JSON.stringify({accepted:false,error:'Select a conversation first.'}),{status:400});await assert.rejects(request('/api/actions',{method:'POST',body:{}}),/Select a conversation first/);}finally{globalThis.fetch=original}
});
test('unexpected HTML response gives useful connection error',async()=>{
 const original=globalThis.fetch;try{globalThis.fetch=async()=>new Response('<html>proxy failure</html>',{status:502});await assert.rejects(request('/api/state'),/unexpected response \(502\)/);}finally{globalThis.fetch=original}
});
test('icon tooltips mirror accessible labels without replacing richer authored text',()=>{
 const automatic={title:'',dataset:{},classList:{contains:()=>false},querySelector:()=>({}),getAttribute:()=> 'Rename conversation'};
 const authored={title:'Attach files · up to 8 MB',dataset:{},classList:{contains:()=>true},querySelector:()=>null,getAttribute:()=> 'Add attachments'};
 const root={querySelectorAll:()=>[automatic,authored]};
 applyIconTooltips(root);
 assert.equal(automatic.title,'Rename conversation');
 assert.equal(automatic.dataset.iconTitle,'auto');
 assert.equal(authored.title,'Attach files · up to 8 MB');
 automatic.getAttribute=()=> 'Rename selected conversation';
 applyIconTooltips(root);
 assert.equal(automatic.title,'Rename selected conversation');
});
