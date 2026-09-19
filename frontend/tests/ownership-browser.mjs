import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/ownership_ui_server.py',import.meta.url))],{stdio:'inherit'});
let browser;
try{
 for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8967/api/health')).ok)break}catch{}await new Promise(resolve=>setTimeout(resolve,100))}
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto('http://127.0.0.1:8967/');await page.waitForSelector('#amp-one');
 const draft=page.getByRole('textbox',{name:'Message Amplifier'});
 await draft.fill('Keep this draft');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 const takeover=page.getByRole('button',{name:'Continue here',exact:true});
 await takeover.waitFor();
 assert.equal(await page.locator('.a-alert').count(),1);
 assert.equal(await draft.inputValue(),'Keep this draft');
 assert.equal(await page.getByText('Amplifier needs a little help.',{exact:true}).count(),0);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().attention.items.filter(i=>i.title==='Conversation needs attention').length),0);
 await page.screenshot({path:'/tmp/amplifier-ownership-notice.png'});
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'settings',settingsSection:'conversation'}}));
 await page.locator('[role=dialog]').waitFor();
 assert.equal(await page.getByText('Conversation needs attention',{exact:false}).count(),0);
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:null}}));
 await takeover.click();
 await page.waitForFunction(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId).ownership.status==='available');
 assert.equal(await page.locator('.a-alert').count(),0);
 assert.equal(await draft.inputValue(),'Keep this draft');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.getByText('Continued successfully.',{exact:true}).waitFor();
 assert.deepEqual(errors,[]);
 console.log('Ownership browser flow passed: one notice, no Settings duplicates, draft retained, explicit takeover resumes.');
}finally{await browser?.close();fixture.kill('SIGTERM')}
