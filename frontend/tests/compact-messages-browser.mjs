import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(root+'.venv/bin/python',['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.port){clearTimeout(timer);resolve(row.port)}}catch{}})});
 const url=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const r=await fetch(url+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(r.status,200);return r.json()};
 const {alpha}=await control({op:'heavy',active:false,messages:4,otherMessages:2,chars:50,nodes:0});
 const now=Date.now()/1000;
 const messages=[{id:'u0',role:'user',text:'Earlier question',via:'chat',createdAt:now-100},
 {id:'a0',role:'assistant',text:'Earlier response.',via:'chat',createdAt:now-95},
 {id:'u1',role:'user',text:'Ok, do it.',via:'call',createdAt:now-90},
 {id:'observation',role:'user',text:'External observation: INTERNAL_JSON_MUST_NOT_APPEAR',observation:{source:'amplifier-delegate'},createdAt:now-70},
 {id:'a1',role:'assistant',text:'The work is complete. The compact message layout keeps the conversation easy to read.',via:'chat',createdAt:now-30}];
 const turns=[{id:'t1',anchorMessageId:'u1',startedAt:now-85,endedAt:now-75,phase:'completed'},
 {id:'t2',anchorMessageId:'observation',startedAt:now-65,endedAt:now-55,phase:'completed'},
 {id:'t3',anchorMessageId:'observation',startedAt:now-50,endedAt:now-40,phase:'completed'}];
 const nodes=turns.map((turn,i)=>({id:'step'+i,turnId:turn.id,anchorMessageId:turn.anchorMessageId,kind:'tool',label:'Synthetic step '+i,startedAt:turn.startedAt,endedAt:turn.endedAt,status:'completed',input:'{}',output:'Result '+i}));
 await control({op:'patch',sessions:{[alpha]:{messages,execution:{turns,nodes}}}});
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:headers,colorScheme:'light'});
 const page=await context.newPage(),errors=[],details=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('part=nodes'))details.push(r.url())});
 await page.goto(url);const input=page.getByRole('textbox',{name:'Message Amplifier'});await input.waitFor();
 const user=page.locator('[data-message-id="u1"]'),body=user.locator('.a-message-body'),footer=user.locator('.a-message-actions');
 await expect(footer).toHaveCSS('opacity','1');await expect(page.locator('[data-message-id="a1"] .a-message-actions')).toHaveCSS('opacity','1');
 assert.equal(await page.locator('.a-msg-meta').count(),0);assert.equal(await page.getByText(/INTERNAL_JSON/).count(),0);
 const bounds=await body.boundingBox(),actions=await footer.boundingBox();assert.ok(bounds.height<70,JSON.stringify(bounds));assert.ok(actions.y>=bounds.y+bounds.height);
 await expect(footer.getByText('via call',{exact:true})).toBeVisible();
 const group=page.locator('[data-part="execution-group"]');await expect(group).toHaveCount(1);await expect(group.locator(':scope > button')).toContainText('Worked for 30s');
 assert.equal(details.length,0);await group.locator(':scope > button').click();await expect(group.getByText('Synthetic step 2',{exact:true})).toBeVisible();
 assert.equal(await group.locator('.a-execution-turn-line').count(),1);await group.locator(':scope > button').click();
 await user.getByRole('button',{name:'Edit message',exact:true}).click();await page.getByRole('textbox',{name:'Edit your message'}).fill('Updated message');await page.getByRole('button',{name:'Cancel',exact:true}).click();
 await input.fill('Please inspect this report.');
 const pasted='Diagnostic report 📋\r\n'.repeat(700);
 await input.evaluate((element,text)=>{const data=new DataTransfer();data.setData('text/plain',text);element.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}))},pasted);
 const attachment=page.locator('[data-part="composer"] .a-attachment');await expect(attachment).toHaveCount(1);await expect(attachment).toContainText('Pasted text');await expect(input).toHaveValue('Please inspect this report.');
 const href=await attachment.locator('a').getAttribute('href');assert.equal(await (await fetch(url+href,{headers})).text(),pasted);
 await page.reload();await expect(page.locator('[data-part="composer"] .a-attachment')).toContainText('Pasted text');
 await page.screenshot({path:'/tmp/unified-compact-messages-desktop.png'});
 await page.setViewportSize({width:390,height:844});await expect(page.locator('[data-message-id="u1"] .a-message-actions')).toHaveCSS('opacity','1');
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:'/tmp/unified-compact-messages-mobile.png'});
 await page.getByRole('button',{name:'Remove Pasted text.txt',exact:true}).click();await expect(page.locator('[data-part="composer"] .a-attachment')).toHaveCount(0);
 assert.deepEqual(errors,[]);console.log('Compact messages passed: external footers, newest actions, no observations, grouped lazy work, editing, exact large paste, reload and mobile.');
}finally{await browser?.close();fixture.kill()}
