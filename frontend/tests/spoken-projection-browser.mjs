// Installed real receiving data; no model/browser state injection or media ACK.
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const url=process.env.PROJECTION_URL,sid=process.env.PROJECTION_SESSION,checks={},errors=[];
const browser=await chromium.launch({headless:true,executablePath:'/usr/bin/chromium',
 args:['--no-zygote','--single-process','--disable-gpu']});
const context=await browser.newContext({viewport:{width:1280,height:900},
 extraHTTPHeaders:{Authorization:`Bearer ${process.env.PROJECTION_TOKEN}`}});
const page=await context.newPage();
page.on('pageerror',error=>errors.push(error.message));
const check=(name,value)=>{checks[name]=!!value;assert.ok(value,name)};
try{
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:900});
  await page.goto(url);
  await page.waitForSelector('#amp-one',{timeout:30000});
  await page.waitForFunction(id=>window.amplifier?.getState()?.sessions?.some(s=>s.id===id),sid);
  const initial=await page.evaluate(id=>window.amplifier.getState().sessions.find(s=>s.id===id),sid);
  check(`relay_rows_${width}`,initial.messages.some(m=>m.presentation==='backend-relay'));
  check(`public_mixed_rows_${width}`,initial.messages.some(m=>m.role==='assistant'&&m.text==='PUBLIC-OFFLINE-ANSWER'));
  check(`no_normal_private_bubble_${width}`,initial.messages.filter(m=>m.presentation==='backend-relay').every(m=>m.text===''));
  const privateId=initial.messages.find(m=>m.presentation==='backend-relay'&&!m.writtenFallback?.expanded).id;
  const collapsed=page.locator(`[data-message-id="${privateId}"] details`);
  check(`collapsed_unknown_${width}`,await collapsed.count()===1);
  await collapsed.locator('summary').focus();
  await page.keyboard.press('Enter');
  await collapsed.locator('.a-detail-text').getByText('PUBLIC-OFFLINE-ANSWER',{exact:true}).waitFor({timeout:15000});
  check(`keyboard_reveal_original_${width}`,await collapsed.getAttribute('open')!==null);
  const expanded=page.locator('[data-presentation="backend-relay"] details[open]');
  check(`negative_fallback_visible_${width}`,await expanded.count()>=2);
  await page.reload();
  await page.waitForSelector('#amp-one');
  await page.waitForSelector('[data-presentation="backend-relay"]');
  check(`reload_retains_collapsed_${width}`,await page.locator('[data-presentation="backend-relay"] details:not([open])').count()>0);
  check(`viewport_no_overflow_${width}`,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  const api=await page.evaluate(async id=>(await fetch('/api/state?sessionId='+id)).json(),sid);
  const row=api.sessions.find(s=>s.id===sid);
  check(`explicit_api_projects_${width}`,row.messages.some(m=>m.presentation==='backend-relay'&&m.text===''));
 }
 check('no_page_errors',errors.length===0);
 console.log(JSON.stringify({status:'PASS',checks}));
}finally{
 await writeFile(process.env.PROJECTION_OUTPUT,JSON.stringify({checks,errors},null,2));
 await browser.close();
}