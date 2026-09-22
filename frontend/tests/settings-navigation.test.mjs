import {test} from 'node:test';
import assert from 'node:assert/strict';
import {settingsSections,settingsLocation,settingsPatch,settingsUnread} from '../src/settings-navigation.js';

test('every destination round-trips through the existing public view action',()=>{
 const seen=new Set();
 for(const section of settingsSections)for(const [page] of section.pages){
  assert.ok(!seen.has(page));seen.add(page);
  const patch=settingsPatch(page);
  assert.deepEqual(Object.keys(patch).sort(),['panel','settingsExpanded','settingsSection']);
  assert.equal(settingsLocation(patch).page,page);
  assert.equal(settingsLocation(patch).section.id,section.id);
 }
 assert.equal(seen.size,28);
 assert.ok(seen.has('desktop'));assert.ok(seen.has('publishing'));
 assert.throws(()=>settingsPatch('not-a-page'),/Unknown settings page/);
});
test('saved and agent navigation from the old hierarchy stays meaningful',()=>{
 assert.equal(settingsLocation({settingsSection:'maintenance',settingsExpanded:['notifications']}).section.id,'notifications');
 assert.equal(settingsLocation({settingsSection:'setup',settingsExpanded:['loaded-modules']}).section.id,'bundles');
 assert.equal(settingsLocation({settingsSection:'capabilities',settingsExpanded:['registries']}).section.id,'advanced');
 assert.equal(settingsLocation({panel:'appearance',settingsExpanded:['providers']}).page,'appearance');
 assert.equal(settingsLocation({settingsExpanded:['unknown','routing']}).page,'routing');
 assert.equal(settingsLocation({settingsSection:'maintenance',settingsExpanded:[]}).page,'updates');
 assert.equal(settingsLocation({settingsSection:'capabilities',settingsExpanded:[]}).page,'app-bundles');
 assert.equal(settingsLocation({settingsExpanded:[]}).page,'overview');
});
test('unread items follow their destination and are counted exactly once',()=>{
 const state={attention:{pages:{providers:2,routing:1,notifications:4,registries:3,'smart-tools':5}}};
 const counts=Object.fromEntries(settingsSections.map(s=>[s.id,settingsUnread(state,s)]));
 assert.equal(counts.models,3);assert.equal(counts.advanced,3);assert.equal(counts.notifications,4);assert.equal(counts['smart-tools'],5);
 assert.equal(Object.values(counts).reduce((a,b)=>a+b,0),15);
});
