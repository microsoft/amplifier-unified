import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','inherit']});
let browser,release;
try{
 const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row.port)}}catch{}})});
 const url=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const response=await fetch(url+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(response.status,200);return response.json()};
 const {alpha}=await control({op:'heavy',active:false,messages:160,otherMessages:1,chars:500,nodes:0});
 await control({op:'patch',sessions:{[alpha]:{completion:{id:'startup-completion',at:Date.now()/1000}}}});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:headers}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const gate=new Promise(resolve=>{release=resolve});
 await page.route('**/api/shell?*',async route=>{await gate;await route.continue()});
 await page.goto(url,{waitUntil:'domcontentloaded'});
 await expect(page.locator('.boot')).toBeVisible();
 // State arrives while the conversation DOM is deliberately absent. Effects
 // must attach when the shell is ready even if that state never changes.
 await page.waitForFunction(()=>window.amplifier?.getState()?.selectedSessionId);
 const read=()=>page.evaluate(id=>window.amplifier.getState().attention.items.find(item=>item.id==='completion:'+id)?.read,alpha);
 assert.equal(await read(),false);
 release();await page.unrouteAll({behavior:'wait'});
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
 await expect(page.locator('[data-message-id]')).toHaveCount(60);
 await expect.poll(read).toBe(true);
 const pane=page.getByRole('log',{name:'Conversation messages'});
 await expect.poll(()=>pane.evaluate(el=>el.scrollTop)).toBeGreaterThan(120);
 await pane.evaluate(el=>{el.scrollTop=80});
 await expect(page.locator('[data-message-id]')).toHaveCount(120);
 assert.deepEqual(errors,[]);
 console.log('Delayed appearance startup attaches completion acknowledgement and automatic earlier-history scrolling.');
}finally{release?.();await browser?.close();fixture.kill()}
