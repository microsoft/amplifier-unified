import test from 'node:test';
import assert from 'node:assert/strict';
import {createShellHttpFixture} from './shell-http-fixture.mjs';
import {shellScope} from '../src/shell/resources.js';

function fixture(){
 const state={revision:1,sessions:[{id:'saved',title:'Saved chat'}],workspaces:[],view:{},selectedSessionId:null,selectedWorkspaceId:null};
 const shell=createShellHttpFixture(()=>state);
 const request=(path,body)=>({url:()=>`http://fixture${path}`,postDataJSON:()=>body});
 const attach=id=>shell.response(request('/api/clients/attach',{clientId:id}));
 const demand=(id=1)=>({clientId:'one',demandId:id,scope:shellScope(state),resources:['recentNavigation']});
 const register=(body=demand())=>shell.response(request('/api/shell/resources',body));
 return {state,shell,request,attach,demand,register};
}
test('mock bootstrap binds identity and only requested resource coverage',()=>{
 const f=fixture();f.attach('one');
 const demand=f.demand(),params=new URLSearchParams({bundled:'1',clientId:'one',demandId:'1',scope:JSON.stringify(demand.scope),navigationResources:JSON.stringify(demand.resources)});
 const data=f.shell.response(f.request('/api/shell?'+params));
 assert.equal(data.hostInstanceId,f.shell.state().client.hostInstanceId);
 assert.equal(data.shellBootstrapKey,f.shell.state().shellBootstrapKey);
 assert.deepEqual(Object.keys(data.shellResources.instances.chats.resources),['context','recentNavigation']);
 assert.deepEqual(data.shellResources.instances.chats.resources.recentNavigation.value.recentNavigation.items,f.state.sessions);
 assert.equal(data.instanceGenerations.chats,0);
});
test('mock receipts update accepted scope and omit pages until replacement demand',()=>{
 const f=fixture();f.attach('one');f.register();f.state.view.workerDraft='newer';f.state.revision++;
 assert.equal(f.shell.state().shellResources.instances.chats.resources.context.value.view.workerDraft,'newer');
 f.state.selectedSessionId='saved';assert.equal(f.shell.state().shellResources,undefined);
 f.register(f.demand(2));assert.equal(f.shell.state().shellResources.demandId,2);
 f.attach('two');assert.equal(f.shell.state().shellResources,undefined);
 assert.throws(()=>f.register(f.demand(3)),/one/);
});
