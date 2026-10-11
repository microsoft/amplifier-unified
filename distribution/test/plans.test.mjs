import test from 'node:test';
import assert from 'node:assert/strict';
import {planReadAction,readPlans} from '../src/plans.js';
test('agent plan inspection uses the exact same bounded Host reader as the UI',async()=>{
 const args={ids:['ahp-session:/one','ahp-session:/two'],includeItems:true},calls=[];
 const host={async sessionPlans(input){calls.push(input);return {items:[],readOnly:true};}};
 assert.equal(planReadAction.schema.properties.ids.maxItems,50);
 assert.deepEqual(await readPlans(host,args),{accepted:true,result:{items:[],readOnly:true}});
 assert.deepEqual(calls,[args]);
 await assert.rejects(readPlans({sessionPlans:async()=>{throw Error('Identity unavailable')}},args),/Identity unavailable/);
});
