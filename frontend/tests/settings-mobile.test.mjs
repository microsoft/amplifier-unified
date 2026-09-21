import {test} from 'node:test';
import assert from 'node:assert/strict';
import {settingsPatch,settingsSections} from '../src/settings-navigation.js';
import {settingsTrail,settingsBaseNavigation,mergeSettingsNavigation,settingsIndexPatch} from '../src/settings-mobile.js';
test('mobile navigation preserves current editor drafts and resets only navigation flags',()=>{
 const view={providerEditor:{id:'one',model:'latest-draft',detailOpen:true,order:{ids:['two','one']}},settingsFilters:{providers:'latest-filter'}};
 const patch=mergeSettingsNavigation(view,settingsBaseNavigation('providers'));
 assert.deepEqual(patch.providerEditor,{id:'one',model:'latest-draft',detailOpen:false,order:{ids:['two','one']},orderOpen:false});
 assert.equal('settingsFilters' in patch,false);
 assert.deepEqual(settingsTrail(settingsIndexPatch).map(row=>row.key),['index']);
});
test('every settings destination has an index and a section parent',()=>{
 for(const section of settingsSections)for(const [page]of section.pages){
  const trail=settingsTrail(settingsBaseNavigation(page));assert.deepEqual(trail.map(row=>row.key),['index',page]);
 }
});
test('nested routing navigation excludes editable model/profile contents',()=>{
 const view={...settingsPatch('routing'),routingEditor:{role:'coding',candidate:2,candidateOpen:true,detailOpen:true,matrix:{secretFixture:'never snapshot'}}};
 const trail=settingsTrail(view);assert.deepEqual(trail.map(row=>row.key),['index','routing','routing/role/coding','routing/choice/coding']);
 assert.equal(JSON.stringify(trail).includes('never snapshot'),false);
 const parent=mergeSettingsNavigation(view,trail.at(-2).navigation);assert.equal(parent.routingEditor.candidateOpen,false);assert.equal(parent.routingEditor.matrix.secretFixture,'never snapshot');
});
test('catalog source setup and module details have meaningful parents',()=>{
 const tool=settingsTrail({...settingsPatch('smart-tools'),smartToolsEditor:{page:'source',returnPage:'catalog',repository:'private-repo'}});assert.deepEqual(tool.map(row=>row.key),['index','smart-tools','tools/catalog','tools/source']);assert.equal(JSON.stringify(tool).includes('private-repo'),false);
 const modules=settingsTrail({...settingsPatch('loaded-modules'),moduleEditor:{detailOpen:true,selectedKey:'tools:0',text:JSON.stringify({tools:[{module:'tool-example',id:'Example'}]})}});assert.equal(modules.at(-1).title,'Example');
});
