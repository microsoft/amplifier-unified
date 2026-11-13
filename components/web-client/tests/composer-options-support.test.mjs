import test from 'node:test';
import assert from 'node:assert/strict';
import {composerOptionsPanel,showComposerOptionWith} from './composer-options-support.mjs';

function fixture(){
 const seen=[];let open=false,clicks=0;
 const summary={click:async()=>{open=!open;clicks++}};
 const advanced={getAttribute:async name=>{assert.equal(name,'open');return open?'':null},locator:selector=>{assert.equal(selector,'summary');return summary}};
 const dialog={locator:selector=>selector==='details'?{filter:()=>advanced}:{section:selector}};
 const page={getByRole:(role,options)=>{seen.push({role,options});return dialog},locator:()=>({})};
 const expect=target=>({
  toHaveCount:async count=>{assert.equal(target,dialog);assert.equal(count,1)},
  toBeVisible:async()=>{if(target.section?.includes('bundle'))assert.equal(open,true)},
 });
 return {page,expect,seen,dialog,get clicks(){return clicks}};
}
test('one canonical panel identity and idempotent Advanced disclosure',async()=>{
 const f=fixture();assert.equal(composerOptionsPanel(f.page),f.dialog);
 for(const section of ['model','provider','effort','bundle','bundle','model'])await showComposerOptionWith(f.expect,f.page,section);
 assert.equal(f.clicks,1,'Repeated bundle access must not toggle it closed; ordinary controls never toggle it');
 assert.ok(f.seen.every(row=>row.role==='dialog'&&row.options.name==='Model for this chat'&&row.options.exact===true));
});
test('unknown sections fail without opening or toggling a panel',async()=>{
 const f=fixture();await assert.rejects(showComposerOptionWith(f.expect,f.page,'legacy-page'),/Unknown composer option/);
 assert.deepEqual(f.seen,[]);assert.equal(f.clicks,0);
});
