// Packaged application, isolated storage, no external model or feedback calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/amplifier-appearance-library';
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 await mkdir(out,{recursive:true});browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await action('session.create');await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep my unfinished ideas.');
 await page.getByRole('button',{name:'Settings',exact:true}).click();await page.locator('[data-settings-section="appearance"]').click();
 await expect(page.getByRole('button',{name:'Preview Aurora',exact:true})).toBeVisible();
 await expect(page.locator('#theme-css')).toBeHidden();
 await page.screenshot({path:out+'/appearance-gallery.png'});
 const before=(await state()).theme.css;
 for(const name of ['Aurora','Atelier','Graphite']){
  await page.getByRole('button',{name:'Preview '+name,exact:true}).click();
  await expect(page.getByRole('button',{name:'Use '+name,exact:true})).toBeVisible();assert.equal((await state()).theme.css,before);
  for(const mode of ['Light','Dark']){await page.getByRole('button',{name:mode,exact:true}).click();await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',mode.toLowerCase());await page.screenshot({path:out+'/'+name.toLowerCase()+'-'+mode.toLowerCase()+'.png'});await page.locator('.a-overlay').evaluate(el=>el.style.visibility='hidden');await page.screenshot({path:out+'/'+name.toLowerCase()+'-'+mode.toLowerCase()+'-app.png'});await page.locator('.a-overlay').evaluate(el=>el.style.visibility='')}
  await page.getByRole('button',{name:'Cancel preview',exact:true}).click();await expect(page.getByRole('button',{name:'Use '+name,exact:true})).toHaveCount(0);assert.equal((await state()).theme.css,before);
 }
 await page.getByRole('button',{name:'Preview Aurora',exact:true}).click();await page.getByRole('button',{name:'Use Aurora',exact:true}).click();
 await expect.poll(async()=>(await state()).theme.name).toBe('Aurora');
 const background=()=>page.locator('#amp-one').evaluate(el=>getComputedStyle(el).backgroundImage);
 await expect.poll(background).not.toBe('none');
 const decoration=page.getByRole('checkbox',{name:'Show decorative theme background'});await decoration.uncheck();await expect.poll(background).toBe('none');await decoration.check();await expect.poll(background).not.toBe('none');
 await page.locator('#theme-import').setInputFiles({name:'Ocean.amplifier.css',mimeType:'text/css',buffer:Buffer.from('#amp-one{--a-accent:#137c8b;background-image:none}')});
 await expect(page.getByRole('button',{name:'Preview Ocean',exact:true})).toBeVisible();assert.equal((await state()).theme.name,'Aurora','Uploading does not change the current theme');
 await page.reload();await expect(page.getByRole('button',{name:'Preview Ocean',exact:true})).toBeVisible();assert.equal((await state()).theme.name,'Aurora');
 await page.locator('#theme-import').setInputFiles({name:'Bad.css',mimeType:'text/css',buffer:Buffer.from('@import "https://example.com/style.css";')});
 await expect(page.getByRole('button',{name:'Preview Bad',exact:true})).toHaveCount(0);assert.equal((await state()).theme.name,'Aurora');
 await page.getByText('Advanced customization',{exact:true}).click();await expect(page.locator('#theme-css')).toBeVisible();await page.locator('#theme-name').fill('My Aurora');await page.getByRole('button',{name:'Save to library',exact:true}).click();await expect(page.getByRole('button',{name:'Preview My Aurora',exact:true})).toBeVisible();
 await page.getByText('Advanced customization',{exact:true}).click();
 for(const width of [760,390,320]){await page.setViewportSize({width,height:900});await expect(page.getByRole('button',{name:'Preview Aurora',exact:true})).toBeVisible();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.locator('.a-settings-content').evaluate(el=>el.scrollTop=0);await page.screenshot({path:out+'/appearance-'+width+'.png'})}
 await page.getByRole('button',{name:'Preview Graphite',exact:true}).click();await page.getByRole('button',{name:'Close settings',exact:true}).click();await expect.poll(async()=>(await state()).view.themePreview).toBe(false);assert.equal((await state()).theme.name,'Aurora');await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveValue('Keep my unfinished ideas.');assert.deepEqual(errors,[]);
 console.log('Appearance library passed: three dual-mode previews, apply/cancel, background toggle, persistent uploads, rejected remote CSS, named save, responsive gallery and preserved draft.');
}finally{await browser?.close();fixture.kill()}
