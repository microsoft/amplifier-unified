// Real navigation projections/actions; disposable history and synthetic runtime.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/chat_library_server.py',import.meta.url))],{env:{...process.env,AMPLIFIER_NAVIGATION_PROOF:'1'},stdio:['ignore','pipe','pipe']});
let logs='',vite,browser,page;
fixture.stderr.on('data',s=>logs+=s);
const ready=new Promise((resolve,reject)=>{let output='';fixture.stdout.on('data',s=>{output+=s;const line=output.split('\n').find(s=>s.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});fixture.once('error',reject);fixture.once('exit',code=>reject(Error(`Fixture exited ${code}: ${logs}`)))});
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/amplifier-navigation';
try{
 await mkdir(out,{recursive:true});const port=await ready,target=`http://127.0.0.1:${port}`;
 if(process.env.AMPLIFIER_TEST_DEV){vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,configure(proxy){proxy.on('proxyReq',r=>r.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen()}
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const api=(path,body)=>page.evaluate(async([path,body])=>{const headers={'X-Amplifier-Client':window.amplifier.getState().client.id};const r=await fetch(path,body===undefined?{headers}:{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw Error(await r.text());return r.json()},[path,body]);
 const info=()=>api('/api/fixture/info');
 const patch=async(instanceId,values)=>api('/api/fixture/agent',{args:{action:'shell.view.update',args:{clientId:await page.evaluate(()=>window.amplifier.shellClientId),instanceId,patch:values}}});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const recent=page.locator('[data-sidebar-section=recent]'),pinned=page.locator('[data-sidebar-section=pinned]'),workspaces=page.locator('[data-sidebar-section=workspaces]');
 const chatrow=id=>recent.locator('.a-nav-chat[data-session-id="'+id+'"]'),details=page.locator('.a-navigation-flyout');
 const chatSearch=recent.getByRole('searchbox',{name:'Filter conversations',exact:true});
 const snapshot=()=>page.evaluate(()=>window.amplifier.getShellState().snapshots.chats);
 const toggle=section=>section.locator('.a-sidebar-section-heading>button').first();
 await page.goto(vite?.resolvedUrls.local[0]||target);await recent.locator('.a-nav-chat').first().waitFor();
 const initial=await info(),selected=initial.initialSession;
 assert.deepEqual(await page.locator('[data-sidebar-section]').evaluateAll(rows=>rows.map(row=>row.dataset.sidebarSection)),['pinned','workspaces','recent']);
 assert.equal(await page.getByRole('group',{name:'Chat view'}).count(),0);
 assert.equal(await page.locator('.a-workspace-explorer').count(),1);
 assert.equal(await recent.locator('.a-nav-chat').count(),40);
 assert.equal((await snapshot()).sidebarNavigation.recent.total,206);
 // Search/hover are passive and use the native identity, with stable row geometry.
 await chatSearch.fill('alpha-201');await expect(recent.locator('.a-nav-chat')).toHaveCount(1);
 const geometry=()=>chatrow(selected).evaluate(el=>({height:el.getBoundingClientRect().height,width:el.getBoundingClientRect().width}));
 const before=await geometry();await chatrow(selected).hover();await details.waitFor();
 assert.deepEqual(await geometry(),before);assert.equal(before.height,60);
 assert.deepEqual(await details.locator('code').allTextContents(),[initial.paths.one,'alpha-201']);
 await page.context().grantPermissions(['clipboard-read','clipboard-write']);
 await details.getByRole('button',{name:'Copy session id',exact:true}).click();
 assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'alpha-201');
 await page.screenshot({path:out+'/chat-flyout.png'});await page.keyboard.press('Escape');
 const more=chatrow(selected).getByRole('button',{name:/Details and actions/});
 await more.focus();await page.keyboard.press('Enter');await details.waitFor();await page.keyboard.press('Escape');await expect(more).toBeFocused();
 await chatSearch.fill(initial.quietSession);await chatrow(initial.quietSession).waitFor();
 await chatrow(initial.quietSession).getByRole('button',{name:/Details and actions/}).click();await details.waitFor();
 assert.deepEqual(await details.locator('code').allTextContents(),[initial.paths.one,initial.quietSession]);await page.keyboard.press('Escape');
 await chatSearch.fill('');await expect(recent.locator('.a-nav-chat')).toHaveCount(40);
 await recent.getByRole('button',{name:'Show more conversations'}).click();
 await expect.poll(async()=>(await snapshot()).sidebarNavigation.recent.index).toBe(1);
 assert.equal(await recent.locator('.a-nav-chat').count(),40);assert.equal((await info()).state.selectedSessionId,selected);
 await recent.locator('summary').click();await recent.getByRole('button',{name:'2 need attention',exact:true}).click();
 await expect(recent.locator('.a-nav-chat')).toHaveCount(2);
 const agentPage=await api('/api/fixture/agent',{args:{action:'shell.query',args:{clientId:await page.evaluate(()=>window.amplifier.shellClientId),instanceId:'chats'}}});
 assert.equal(agentPage.result.sidebarNavigation.recent.total,2);
 await patch('chats',{navRecentView:{navStatusFilter:'unread'}});
 await expect(recent.locator('.a-nav-chat')).toHaveCount(1);
 const unreadId=await recent.locator('.a-nav-chat').getAttribute('data-session-id');
 await recent.locator('.a-nav-chat').hover();await details.waitFor();assert.ok((await info()).state.attention.sessions[unreadId]);
 await page.keyboard.press('Escape');await patch('chats',{navRecentView:{navFilter:'',navStatusFilter:'all'}});
 await recent.locator('summary').click();

 // One explorer retains duplicate path labels and drills into a workspace's chats.
 const search=workspaces.getByRole('searchbox',{name:'Filter workspaces',exact:true});
 await search.fill('*/playground');await expect(page.locator('.a-workspace-row')).toHaveCount(2);
 assert.equal(new Set(await page.locator('.a-workspace-result-path').allTextContents()).size,2);
 await page.locator('.a-workspace-row').first().getByRole('button',{name:/Details and actions/}).click();await details.waitFor();
 await page.screenshot({path:out+'/workspace-flyout.png'});await page.keyboard.press('Escape');
 await page.getByRole('button',{name:'Open chats in '+initial.paths.two,exact:true}).click();
 const workspaceChats=workspaces.locator('.a-workspace-chat-view');
 await expect(workspaceChats.locator('.a-nav-chat')).toHaveCount(3);
 assert.equal(await page.locator('.a-workspace-explorer').count(),0);
 const beta=(await snapshot()).sidebarNavigation.workspace.items[0];
 await workspaceChats.getByRole('searchbox',{name:'Filter conversations'}).fill('Beta 001');
 await expect(workspaceChats.locator('.a-nav-chat')).toHaveCount(1);
 assert.equal((await snapshot()).sidebarNavigation.recent.total,206);
 await workspaces.getByRole('button',{name:'All workspaces',exact:true}).click();await search.waitFor();
 await search.fill('');await workspaces.getByRole('button',{name:'Browse folders',exact:true}).click();
 await page.locator('.a-workspace-location').waitFor();const browsed=(await info()).state.selectedSessionId;
 await page.getByRole('button',{name:'Go to parent workspace folder',exact:true}).click();
 assert.equal((await info()).state.selectedSessionId,browsed);await page.screenshot({path:out+'/workspace-folders.png'});
 await page.reload();await page.locator('.a-workspace-explorer').waitFor();assert.equal(await workspaces.getByRole('button',{name:'Browse folders',exact:true}).getAttribute('aria-pressed'),'true');

 // Pins stay the same across browsing, filters and saved legacy view preferences.
 await action('session.select',{id:selected});
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await composer.fill('Keep this unsent draft while moving pins');
 const pinIds=[selected,initial.quietSession,beta.id];
 for(const id of pinIds)await action('session.pin',{id,pinned:true});
 const order=()=>pinned.locator('[data-order-id]').evaluateAll(rows=>rows.map(row=>row.dataset.orderId));
 await expect.poll(order).toEqual(pinIds);
 const pinnedContents=()=>pinned.locator('.a-nav-chat').evaluateAll(rows=>rows.map(row=>row.textContent));
 const contents=await pinnedContents();
 await patch('chats',{navSimple:false,navChatScope:'all',navRecentView:{navFilter:'does not match'}});
 await expect(recent.locator('.a-nav-chat')).toHaveCount(0);assert.deepEqual(await pinnedContents(),contents);
 await patch('chats',{navSimple:true,navChatScope:'workspace',navRecentView:{navFilter:''}});
 assert.deepEqual(await pinnedContents(),contents);
 const third=pinned.locator('[data-order-id="'+beta.id+'"]'),first=pinned.locator('[data-order-id="'+selected+'"]');
 const drag=async()=>{
  const handle=third.getByRole('button',{name:/Reorder/});await handle.scrollIntoViewIfNeeded();
  const a=await handle.boundingBox(),b=await first.boundingBox();
  await page.mouse.move(a.x+a.width/2,a.y+a.height/2);await page.mouse.down();await page.mouse.move(a.x+a.width/2,b.y+b.height/2,{steps:10});
 };
 await drag();const floating=page.locator('.a-reorder-floating');
 await expect(floating).toBeVisible();assert.ok((await floating.innerText()).includes(beta.title));
 await expect(floating.locator('.a-nav-chat-workspace')).toHaveText(beta.workspaceLabel);await expect(floating.locator('.a-navigation-age')).toBeVisible();
 await expect(pinned.locator('.a-order-insertion')).toBeVisible();await expect(pinned.locator('.a-order-placeholder')).toHaveCount(1);
 const floatBox=await floating.boundingBox();assert.ok(floatBox.width>150&&floatBox.x>=0);
 await page.screenshot({path:out+'/pin-drag.png'});await page.keyboard.press('Escape');await page.mouse.up();
 await expect.poll(order).toEqual(pinIds);assert.deepEqual((await info()).state.pinnedSessionIds,pinIds);
 await drag();await page.mouse.up();await expect.poll(order).toEqual([beta.id,selected,initial.quietSession]);
 await expect.poll(async()=>(await info()).state.pinnedSessionIds).toEqual([beta.id,selected,initial.quietSession]);
 const handle=pinned.getByRole('button',{name:'Reorder '+beta.title,exact:true});
 await expect(handle).not.toHaveAttribute('aria-disabled','true');await handle.focus();await page.keyboard.press('Alt+ArrowDown');await expect.poll(order).toEqual([selected,beta.id,initial.quietSession]);await expect(handle).toBeFocused();
 await expect(handle).not.toHaveAttribute('aria-disabled','true');await page.keyboard.press('ArrowUp');await expect.poll(order).toEqual([beta.id,selected,initial.quietSession]);
 await expect(handle).not.toHaveAttribute('aria-disabled','true');
 await drag();await page.mouse.move(1000,20,{steps:8});await page.mouse.up();await expect.poll(order).toEqual([beta.id,selected,initial.quietSession]);
 await drag();await action('session.pin',{id:initial.quietSession,pinned:false});await expect(floating).toHaveCount(0);await page.mouse.up();
 await expect.poll(order).toEqual([beta.id,selected]);await action('session.pin',{id:initial.quietSession,pinned:true});await expect.poll(order).toEqual([beta.id,selected,initial.quietSession]);
 // A pinned chat also present in workspace drill-in gets one editor at the clicked row.
 await patch('chats',{navWorkspaceList:false,navFilter:''});
 const selectedPin=pinned.locator('[data-session-id="'+selected+'"]');
 await selectedPin.getByRole('button',{name:/Details and actions/}).click();await details.waitFor();
 await details.getByRole('button',{name:/^Rename /}).click();await expect(details).toHaveCount(1);
 await expect(details.getByRole('textbox',{name:/New name for/})).toBeVisible();
 await details.getByRole('button',{name:'Cancel conversation rename'}).click();await page.keyboard.press('Escape');
 await patch('chats',{navWorkspaceList:true});
 assert.equal((await info()).state.selectedSessionId,selected);await expect(composer).toHaveValue('Keep this unsent draft while moving pins');
 await toggle(workspaces).click();await toggle(recent).click();await page.screenshot({path:out+'/sidebar-pins.png'});
 await toggle(pinned).click();await page.screenshot({path:out+'/sidebar-collapsed.png'});
 await page.reload();await expect(toggle(pinned)).toHaveAttribute('aria-expanded','false');await expect(toggle(workspaces)).toHaveAttribute('aria-expanded','false');await expect(toggle(recent)).toHaveAttribute('aria-expanded','false');
 await toggle(pinned).click();await expect.poll(order).toEqual([beta.id,selected,initial.quietSession]);await expect(composer).toHaveValue('Keep this unsent draft while moving pins');

 for(const scheme of ['light','dark']){
  await action('view.update',{patch:{scheme}});
  for(const width of [390,320]){
   await page.setViewportSize({width,height:844});
   await expect(page.locator('.a-nav-slot')).toHaveAttribute('role','dialog');
   if(!(await page.locator('.a-nav-slot').isVisible()))await page.getByRole('button',{name:'Open navigation',exact:true}).click();
   await pinned.locator('.a-nav-chat').first().getByRole('button',{name:/Details and actions/}).click();await details.waitFor();
   const rect=await details.boundingBox();assert.ok(rect.x>=0&&rect.x+rect.width<=width+1&&rect.y+rect.height<=844+1);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:out+'/navigation-'+scheme+'-'+width+'.png'});await page.keyboard.press('Escape');
  }
 }
 await page.setViewportSize({width:1280,height:900});await action('view.update',{patch:{navPinned:true,navExpanded:true}});
 await patch('workspaces',{navWorkspaceMode:'recent',navWorkspaceFilter:''});await patch('chats',{navSectionsCollapsed:[],navWorkspaceList:true});
 await toggle(pinned).scrollIntoViewIfNeeded();await page.screenshot({path:out+'/sidebar-overview-dark.png'});
 assert.deepEqual((await info()).runtimeStarts,[]);assert.deepEqual((await info()).runtimeSends,[]);assert.deepEqual(errors,[]);
 console.log('Navigation browser passed: three saved sections, isolated native-history views, 40-item pages, rich drag preview/drop/cancel, keyboard order/focus, stable details, agent parity, persisted drafts/pins, desktop and 320/390 light/dark layouts, no model work.');

}catch(error){await page?.screenshot({path:out+'/failure.png'}).catch(()=>{});if(logs)console.error(logs);throw error}
finally{await browser?.close();await vite?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}}
