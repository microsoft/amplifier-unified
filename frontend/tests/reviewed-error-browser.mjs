// Packaged UI, shared actions and disposable histories; no provider calls.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
 ['-u',fileURLToPath(new URL('../../tests/fixtures/chat_library_server.py',import.meta.url))],
 {env:{...process.env,AMPLIFIER_NAVIGATION_PROOF:'1'},stdio:['ignore','pipe','pipe']});
let logs='',browser,page;
fixture.stderr.on('data',value=>logs+=value);
const ready=new Promise((resolve,reject)=>{
 let output='';fixture.stdout.on('data',value=>{output+=value;const line=output.split('\n').find(value=>value.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});
 fixture.once('error',reject);fixture.once('exit',code=>reject(Error(`Fixture exited ${code}: ${logs}`)));
});
try{
 const port=await ready;
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const api=(path,body)=>page.evaluate(async([path,body])=>{
  const headers={'X-Amplifier-Client':window.amplifier.getState().client.id};
  const response=await fetch(path,body===undefined?{headers}:{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok)throw Error(await response.text());return response.json();
 },[path,body]);
 const info=()=>api('/api/fixture/info');
 const query=()=>page.evaluate(()=>window.amplifier.getShellState().snapshots.chats);
 const recent=page.locator('[data-sidebar-section=recent]'),details=page.locator('.a-navigation-flyout');
 const row=id=>recent.locator('.a-nav-chat[data-session-id="'+id+'"]');
 await page.goto(`http://127.0.0.1:${port}`);await recent.locator('.a-nav-chat').first().waitFor();
 const before=await info(),failed=before.state.sessions.find(row=>row.nativeIdentity==='alpha-197');
 const workspace=state=>state.workspaceExplorer.rows.find(row=>row.path===failed.workspace);
 assert.equal(workspace(before.state).activityCounts.attention,2);
 await row(failed.id).getByRole('button',{name:/Details and actions/}).click();await details.waitFor();
 await expect(details.locator('.a-navigation-detail-status')).toHaveText('Needs attention');
 await details.getByRole('button',{name:'Mark error reviewed',exact:true}).click();
 await expect.poll(async()=>(await info()).state.attention.items.find(row=>row.id==='session:'+failed.id).read).toBe(true);
 await expect(details.locator('.a-navigation-detail-status')).toHaveText('Idle');
 assert.equal((await query()).sidebarNavigation.recent.items.find(item=>item.id===failed.id).activity.kind,'idle');
 const reviewed=await info(),saved=reviewed.state.sessions.find(row=>row.id===failed.id);
 assert.equal(saved.error,failed.error);assert.equal(saved.status,failed.status);assert.deepEqual(saved.messages,failed.messages);
 assert.equal(workspace(reviewed.state).activityCounts.attention,1);
 const agent=await api('/api/fixture/agent',{args:{action:'shell.query',args:{clientId:await page.evaluate(()=>window.amplifier.shellClientId),instanceId:'chats'}}});
 assert.equal(agent.result.sidebarNavigation.recent.items.find(item=>item.id===failed.id).activity.kind,'idle');
 await page.keyboard.press('Escape');await page.reload();await row(failed.id).waitFor();
 await expect.poll(async()=>(await query()).sidebarNavigation.recent.items.find(item=>item.id===failed.id).activity.kind).toBe('idle');
 const approval=before.state.sessions.find(row=>row.nativeIdentity==='alpha-199');
 await row(approval.id).getByRole('button',{name:/Details and actions/}).click();await details.waitFor();
 await expect(details.locator('.a-navigation-detail-status')).toHaveText('Approval requested');
 assert.equal(await details.getByRole('button',{name:'Mark error reviewed',exact:true}).count(),0);
 const final=await info();assert.deepEqual(final.runtimeStarts,[]);assert.deepEqual(final.runtimeSends,[]);assert.deepEqual(errors,[]);
 console.log('PASS: exact review clears chat/workspace markers, survives reload, retains errors and approvals, and starts no work.');
}finally{
 await browser?.close();
 if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
