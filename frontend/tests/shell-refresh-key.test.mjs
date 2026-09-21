import test from 'node:test';
import assert from 'node:assert/strict';
import {shellRefreshKey} from '../src/shell/refresh-key.js';

const state={revision:1,shellDataKey:'navigation-1',shellChangeToken:'host:0',selectedSessionId:'one',selectedWorkspaceId:'work',
 sessions:[{id:'one',title:'First',status:'idle',bundle:'work'}],canvas:{id:'canvas',kind:'text',title:'Notes',open:true},runtime:{available:true}};

test('unrelated publications do not query shell again',()=>{
 assert.equal(shellRefreshKey(state),shellRefreshKey({...state,revision:2,diagnostics:{sampleCount:42}}));
 assert.equal(shellRefreshKey(state),shellRefreshKey({...state,sessions:[{...state.sessions[0],streaming:'Delta'}]}));
});

test('navigation, module changes, client selection and visible summaries invalidate shell',()=>{
 for(const patch of [{shellDataKey:'navigation-2'},{shellChangeToken:'host:1'},{shellChangeToken:'new-host:0'},
  {selectedSessionId:null},{selectedWorkspaceId:'other'},{runtime:{available:false}},
  {canvas:{...state.canvas,open:false}},{sessions:[{...state.sessions[0],naming:{status:'working'}}]}]){
  assert.notEqual(shellRefreshKey(state),shellRefreshKey({...state,...patch}));
 }
});

test('older hosts keep revision-based refresh',()=>{
 const legacy={...state};delete legacy.shellDataKey;
 assert.notEqual(shellRefreshKey(legacy),shellRefreshKey({...legacy,revision:2}));
 assert.equal(shellRefreshKey(null),null);
});
