import test from 'node:test';import assert from 'node:assert/strict';
import {configurationContext} from '../src/configuration-target.js';
test('explicit workspace is catalog-resolved and authorized, independent of default',async()=>{
 const calls=[],ports={defaultWorkspace:'/default',catalog:{getWorkspace:async({id})=>({id,path:'/selected'})},host:{authorizeWorkspace:async path=>{calls.push(path);return path}}};
 assert.equal((await configurationContext({kind:'workspace',workspaceId:'w'}, {},{clientId:'c'},ports)).workingDirectory,'/selected');
 assert.deepEqual(calls,['/selected']);
 assert.equal((await configurationContext({kind:'global'}, {},{clientId:'c'},ports)).workingDirectory,'/default');
 for(const [target,args,context] of [[{kind:'global'},{scope:'local'},{}],[{kind:'workspace',workspaceId:'w'},{scope:'global'},{}],[{kind:'global'},{workspace:'/chosen'},{}],[{kind:'global'},{},{session:{uri:'ahp-session:/s'}}]])await assert.rejects(configurationContext(target,args,context,ports),e=>e.data.executed===false);
 await assert.rejects(configurationContext({kind:'workspace',workspaceId:'missing'}, {},{}, {...ports,catalog:{getWorkspace:async()=>null}}));
 await assert.rejects(configurationContext({kind:'workspace',workspaceId:'w'}, {},{}, {...ports,host:{authorizeWorkspace:async()=>{throw Error('outside authorized roots')}}}));
});
