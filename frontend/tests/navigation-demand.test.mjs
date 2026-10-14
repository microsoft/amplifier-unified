import test from 'node:test';
import assert from 'node:assert/strict';
import {navigationResources,navigationDemandKey,hasNavigationResource} from '../src/shell/navigation-demand.js';

const sidebar=['pinnedNavigation','recentNavigation','workspaceShortcuts'];
test('built-in demand includes only the sidebar and visible browsing page',()=>{
 for(const view of [{},{workSurface:'chat'},{workSurface:'workspace',workWorkspaceTab:'files'},{workSurface:'workspace',workWorkspaceTab:'details'}])
  assert.deepEqual(navigationResources({view}),sidebar);
 for(const [view,resource] of [[{workSurface:'chats'},'sidebarSearch'],[{workSurface:'workspace'},'sidebarWorkspace'],[{workSurface:'workspaces'},'workspaceExplorer']])
  assert.deepEqual(navigationResources({view}),[...sidebar,resource].sort());
});
test('demand scope changes for workspace/tab but not unrelated drafts or progress',()=>{
 const state={view:{workSurface:'workspace',workWorkspaceId:'one'}};
 for(const patch of [{workWorkspaceId:'two'},{workWorkspaceTab:'files'},{workWorkspaceTab:'details'}])
  assert.notEqual(navigationDemandKey(state),navigationDemandKey({...state,view:{...state.view,...patch}}));
 assert.equal(navigationDemandKey(state),navigationDemandKey({...state,revision:99,view:{...state.view,draft:'private'}}));
 assert.equal(navigationDemandKey({view:{}}),navigationDemandKey({view:{workWorkspaceTab:'files'}}));
});
test('missing opted-in resources stay unavailable; legacy and custom full snapshots remain supported',()=>{
 assert.equal(hasNavigationResource({navigationResources:sidebar},'sidebarSearch'),false);
 assert.equal(hasNavigationResource({navigationResources:[]},'workspaceExplorer'),false);
 assert.equal(hasNavigationResource({navigationResources:[...sidebar,'workspaceExplorer']},'workspaceExplorer'),true);
 assert.equal(hasNavigationResource({},'workspaceExplorer'),true);
});
