import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
// Real host and production assets with synthetic history; no model calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/unified-canvas-usability';
await mkdir(out,{recursive:true});
const fixture=spawn(fixturePython(root+'.venv/bin/python'),[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Canvas usability fixture'});
let browser,failure;const navigationEvidence=[];
try{
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);let output='';fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',data=>{output+=data;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const page=await browser.newPage({viewport:{width:1500,height:850},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await action('session.create',{});
 const panel=page.locator('#workspace-canvas');
 const navigation=async value=>{
  const menu=panel.locator('.a-canvas-more');
  if(!await menu.evaluate(node=>node.open))await menu.locator('summary').click();
  await menu.getByRole('combobox',{name:'Canvas navigation',exact:true}).selectOption(value);
  await expect(panel).toHaveAttribute('data-navigation-preference',value);
  await page.keyboard.press('Escape');await page.locator('#canvas-toggle').focus();
 };
 // Explicit Tabs tests overflow. Auto may legitimately use Compact in a split
 // pane; neither a hidden tab nor a zero-width strip establishes visibility.
 const ids=[];
 for(let i=0;i<8;i++){await action('canvas.show',{kind:'markdown',title:'Document '+i+' with a long descriptive name',content:'# Note '+i});ids.push(await page.evaluate(()=>window.amplifier.getState().canvas.id))}
 await navigation('tabs');await expect(panel).toHaveAttribute('data-navigation','tabs');
 const documentTabs=page.getByRole('tablist',{name:'Canvas views'}).locator('.a-canvas-document-tabs');
 const pagePosition=await page.evaluate(()=>({x:scrollX,y:scrollY}));
 const visibleTab=async()=>{
  await expect(documentTabs).toBeVisible();
  await expect.poll(()=>documentTabs.evaluate(strip=>{
   const active=strip.querySelector('[role="tab"][aria-selected="true"]');if(!active)return false;
   const a=active.getBoundingClientRect(),s=strip.getBoundingClientRect();
   return a.width>0&&a.height>0&&s.width>0&&s.height>0&&a.left>=s.left-1&&a.right<=s.right+1;
  })).toBe(true);
  assert.deepEqual(await page.evaluate(()=>({x:scrollX,y:scrollY})),pagePosition,'Tab reveal must not move the surrounding page');
  const active=documentTabs.getByRole('tab',{selected:true});
  assert.equal(await active.getAttribute('data-artifact-id'),await page.evaluate(()=>window.amplifier.getState().canvas.id));
  navigationEvidence.push({mode:'tabs',id:await active.getAttribute('data-artifact-id'),title:await active.getAttribute('title')});
 };
 await visibleTab();
 await action('canvas.select',{id:ids[0]});await visibleTab();
 await action('canvas.select',{id:ids[7]});await visibleTab();
 const primary=panel.locator('[data-canvas-view="primary"]'),viewer=await primary.elementHandle();
 await expect(primary).toHaveCount(1);
 await navigation('auto');await page.setViewportSize({width:390,height:844});
 await expect(panel).toHaveAttribute('data-navigation','compact');
 const picker=panel.locator('button.a-open-documents');
 await expect(picker).toHaveAccessibleName('Open documents, 8 open, Document 7 with a long descriptive name');
 await expect(picker).toBeVisible();
 const box=await picker.boundingBox();assert.ok(box.width>0&&box.height>0&&box.x>=0&&box.x+box.width<=390);
 await picker.click();
 const documents=page.getByRole('dialog',{name:'Open documents',exact:true});
 await expect(documents.locator('li')).toHaveCount(8);
 await expect(documents.locator('li[aria-current="true"]')).toHaveCount(1);
 const selectedDocument=documents.locator('li[aria-current="true"]');
 await expect(selectedDocument.locator('strong')).toHaveText('Document 7 with a long descriptive name');
 await expect(selectedDocument.locator('small')).toHaveText('markdown · Selected');
 await documents.getByRole('button',{name:'Close open documents',exact:true}).click();
 await expect(picker).toBeFocused();
 await expect(primary).toHaveCount(1);assert.equal(await primary.evaluate((node,old)=>node===old,viewer),true);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.id),ids[7]);
 navigationEvidence.push({mode:'compact',id:ids[7],openDocuments:8,sameViewer:true});
 await viewer.dispose();
 await page.setViewportSize({width:1500,height:850});
 await navigation('tabs');await expect(panel).toHaveAttribute('data-navigation','tabs');await visibleTab();
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 const downloadEvent=page.waitForEvent('download');
 await panel.getByRole('button',{name:'Download canvas source',exact:true}).click();
 assert.equal((await downloadEvent).suggestedFilename(),'Document 7 with a long descriptive name.md');

 // Document tabs keep their friendly title; Details retains the exact file identity.
 const state=await page.evaluate(()=>window.amplifier.getState());
 const workspace=state.workspaces.find(w=>w.id===state.selectedWorkspaceId).path;
 await writeFile(workspace+'/actual-name.md','# File content');
 await action('canvas.show',{kind:'auto',path:workspace+'/actual-name.md',title:'Friendly file title'});
 await expect(documentTabs.getByRole('tab',{selected:true})).toHaveAttribute('title','Friendly file title');
 const filePath=panel.locator('.a-canvas-inspector details.a-canvas-file-path');
 const details=panel.getByRole('button',{name:'Canvas details',exact:true});
 await details.click();await expect(filePath).toBeHidden();
 await details.click();await expect(filePath).toBeVisible();
 await filePath.getByText('File details',{exact:true}).click();
 await expect(filePath.locator('code')).toHaveText([workspace+'/actual-name.md',workspace,'actual-name.md']);
 await expect(filePath.getByRole('button',{name:'Copy full path',exact:true})).toBeEnabled();
 await expect(filePath.getByRole('button',{name:'Copy relative path',exact:true})).toBeEnabled();
 // A sent Markdown attachment opens a preview and retains its download.
 await action('attachment.add',{sessionId:state.selectedSessionId,name:'Review notes.md',base64:Buffer.from('# Attached review\n\nThe original text.').toString('base64')});
 const attachment=await page.evaluate(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId).draftAttachments[0]);
 await action('conversation.send',{sessionId:state.selectedSessionId,text:'Review my notes',attachmentIds:[attachment.id]});
 const sentNote=page.locator('[data-message-id]').filter({hasText:'Review my notes'});
 await sentNote.locator('[data-action="canvas.openAttachment"]').filter({hasText:'Review notes.md'}).click();
 await expect(panel.getByRole('heading',{name:'Attached review'})).toBeVisible();
 await expect(sentNote.getByRole('link',{name:'Download Review notes.md',exact:true})).toHaveAttribute('href',attachment.url);
 await page.reload();await expect(panel.getByRole('heading',{name:'Attached review'})).toBeVisible();
 // Current Graphviz and tall embedded Mermaid rendering, independent of old reports.
 await action('canvas.show',{kind:'dot',title:'Graph check',content:'digraph G { root [label="Root"]; root -> worker; worker -> result; }'});
 await expect.poll(()=>panel.locator('.a-diagram-stage img').evaluateAll(nodes=>nodes.length===1&&nodes[0].complete&&nodes[0].naturalWidth>0)).toBe(true);
 await action('canvas.show',{kind:'markdown',title:'Tall diagram',content:'# Tall diagram\n\n```mermaid\nflowchart TD\n'+Array.from({length:25},(_,i)=>'N'+i+' --> N'+(i+1)).join('\n')+'\n```'});
 const diagram=panel.locator('.a-diagram-stage');
 await expect.poll(()=>diagram.locator('img').evaluateAll(nodes=>nodes.length===1&&nodes[0].complete&&nodes[0].naturalHeight>0)).toBe(true);
 const extent=await diagram.evaluate(el=>{el.scrollTop=el.scrollHeight;return {height:el.scrollHeight,viewport:el.clientHeight,top:el.scrollTop}});
 assert.ok(extent.height>extent.viewport&&extent.top>0,'Tall diagram must be scrollable to its bottom');
 await page.screenshot({path:out+'/canvas-usability.png'});
 assert.deepEqual(errors,[]);
 const result={activeTabs:true,narrowCompact:true,navigationEvidence,filenameDetails:true,titledDownloads:true,markdownAttachment:true,reload:true,graphvizDecoded:true,tallMermaidScrollable:true,browserErrors:0};
 await writeFile(out+'/observations.json',JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Canvas usability fixture',close:[['Canvas usability browser',()=>browser?.close()]],stop,originalError:failure})}
