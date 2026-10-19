import test from 'node:test';
import assert from 'node:assert/strict';
import {startingWorkView,homePatch,currentWorkPage} from '../src/adaptive-work.js';
test('appearance chooses arrival only; explicit starting preference wins',()=>{
 assert.equal(startingWorkView({interfaceDetail:'minimal'}),'focus');
 assert.equal(startingWorkView({interfaceDetail:'standard'}),'brief');
 assert.equal(startingWorkView({interfaceDetail:'detailed'}),'studio');
 assert.equal(startingWorkView({interfaceDetail:'minimal',workStartView:'studio'}),'studio');
 const patch=homePatch({interfaceDetail:'detailed'},{workHomeFilter:'working'});
 assert.equal(patch.navPinned,true);assert.equal(patch.workHomeView,'studio');
 for(const key of ['selectedSessionId','draft','canvas','workHomeScope','workStartView'])assert.equal(Object.hasOwn(patch,key),false);
});
test('late pages cannot answer a new project, search or page',()=>{
 const data={scope:'a',query:'launch',requestedPages:{idle:1}};
 assert.equal(currentWorkPage(data,{workHomeScope:'a',workHomeQuery:'launch',workHomePages:{idle:1}}),true);
 for(const changes of [{workHomeScope:'b'},{workHomeQuery:'offsite'},{workHomePages:{idle:2}}])
  assert.equal(currentWorkPage(data,{workHomeScope:'a',workHomeQuery:'launch',workHomePages:{idle:1},...changes}),false);
});

test('Home preserves an explicitly opened or closed sidebar independently of appearance',()=>{
 const pinned=homePatch({interfaceDetail:'minimal'},{},{navPinned:true,navExpanded:false});
 assert.equal(pinned.navPinned,true);
 assert.equal(pinned.workHomeView,'focus');
 const hidden=homePatch({interfaceDetail:'detailed'},{},{navPinned:false,navExpanded:false});
 assert.equal(hidden.navPinned,false);
 assert.equal(hidden.workHomeView,'studio');
 const drawer=homePatch({interfaceDetail:'standard'},{navExpanded:false},{navExpanded:true,navPinned:false});
 assert.equal(drawer.navExpanded,false);
});

test('returning Home retains choices across chat/project visits and appearance changes',()=>{
 const choices={workHomeView:'studio',workHomeFilter:'idle',workHomeScope:'project-a',workHomeQuery:'launch',workHomePages:{idle:2},navPinned:true};
 for(const workSurface of ['chat','workspace','workspaces','chats']){
  const away={...choices,workSurface,workWorkspaceId:'project-b',selectedSessionId:'other'};
  const back={...away,...homePatch({interfaceDetail:'minimal',workStartView:'focus'},{},away)};
  for(const [key,value] of Object.entries(choices))assert.deepEqual(back[key],value,key);
  assert.equal(back.workSurface,'home');assert.equal(back.selectedSessionId,'other');
 }
 const reset={...choices,...homePatch({interfaceDetail:'minimal'},{workHomeView:'focus',workHomeFilter:'all'},choices)};
 assert.equal(reset.workHomeView,'focus');assert.equal(reset.workHomeFilter,'all');
 assert.equal(reset.workHomeScope,'project-a');assert.equal(reset.workHomeQuery,'launch');
});
