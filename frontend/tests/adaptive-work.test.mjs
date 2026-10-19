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
