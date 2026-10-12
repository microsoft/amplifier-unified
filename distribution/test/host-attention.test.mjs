import test from 'node:test';
import assert from 'node:assert/strict';
import {projectHostAttention,createHostAttentionReader,attentionSources} from '../src/host-attention.js';

test('passive projections create stable notices without reflecting secrets or statuses as credentials',()=>{
 const provider={setup:{providers:[{id:'account',enabled:true,account:{connected:false,token:'PRIVATE'},credential:{supported:false}},{id:'disabled',enabled:false,credential:{supported:true},credentialsConfigured:false},{id:'custom',credentialsConfigured:false}]}};
 const a=projectHostAttention('provider-catalog',provider);
 assert.equal(a.items.length,1);assert.equal(a.items[0].acknowledge,false);
 assert.equal(JSON.stringify(a).includes('PRIVATE'),false);
 const notice={commandId:'original',status:'unknown',updatedAt:5,error:'PRIVATE'};
 const first=projectHostAttention('application-updates',{applicationUpdates:{operations:[notice]}});
 const second=projectHostAttention('application-updates',{applicationUpdates:{operations:[{...notice,error:'different private message'}]}});assert.deepEqual(first,second);
 assert.notEqual(projectHostAttention('application-updates',{applicationUpdates:{operations:[{...notice,status:'failed'}]}}).items[0].fingerprint,first.items[0].fingerprint);
 assert.equal(projectHostAttention('connectors',{mcp:{servers:[{id:'tool',status:'auth-required'}],pages:{servers:{nextOffset:25}}}}).truncated,true);
 assert.equal(projectHostAttention('diagnostics',{diagnostics:{local:{dropped:2}}}).items.length,1);
 assert.equal(projectHostAttention('feedback',{feedback:{items:[{requestId:'feedback-one',status:'failed',title:'PRIVATE'}],nextCursor:5}}).items[0].requestId,'feedback-one');
});

test('reads only configured bounded host projections with authenticated context; missing sources stay explicit',async()=>{
 const context={clientId:'client',account:'owned'},calls=[];
 const owners=attentionSources.map(topic=>({manifest:{topics:{[topic]:{uri:'amplifier-capability://'+topic}}},read:async(request,actual)=>{assert.equal(actual,context);assert.equal(request.scope,'host');assert.equal(request.clientId,'client');calls.push(topic);if(topic==='diagnostics')throw Error('PRIVATE');return {topic,scope:'host',data:{}};},action:()=>assert.fail('No actions')}));
 const value=await createHostAttentionReader(()=>owners)(context);
 assert.equal(calls.length,6);assert.equal(value.coverage.complete,false);assert.equal(value.items.length,1);assert.equal(JSON.stringify(value).includes('PRIVATE'),false);
 assert.equal(value.items[0].acknowledge,false);
 assert.equal((await createHostAttentionReader(()=>[])(context)).coverage.complete,true);
});
