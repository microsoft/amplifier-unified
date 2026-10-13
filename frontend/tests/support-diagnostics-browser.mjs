import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/session_health_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit'],env:{...process.env,BRIDGE_TIMEOUT_FIXTURE:'1'}});
let browser;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exit '+code)))});
 browser=await chromium.launch({headless:true});const context=await browser.newContext({permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage();await page.goto(ready.url);
 await page.getByRole('button',{name:'View error details',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Error details',exact:true});
 await dialog.getByRole('button',{name:'Copy diagnostics',exact:true}).click();
 await expect(dialog.getByText('Diagnostics copied',{exact:true})).toBeVisible();
 const report=JSON.parse(await page.evaluate(()=>navigator.clipboard.readText()));
 assert.equal(report.diagnosticsSchema,2);assert.equal(report.failure.category,'app_bridge_timeout');
 assert.equal(report.failure.generationId,'bridge-generation');assert.deepEqual(report.failure.inputIds,['input-1']);
 assert.equal(report.failureEvidence[0].timeoutSeconds,60);assert.ok(report.environment.applicationVersion);
 assert.ok(report.client.recentRequests.some(row=>row.requestId&&Number.isFinite(row.serverMs)));
 assert.ok(report.recentRequests.every(row=>!row.route.includes('?')));
 await expect(dialog.getByText('Amplifier timed out waiting for an internal operation.',{exact:true})).toBeVisible();
 await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('denied')}}}));
 await dialog.getByRole('button',{name:'Copy diagnostics',exact:true}).click();
 await dialog.getByText('Copy diagnostic text manually',{exact:true}).click();
 await expect(dialog.getByRole('textbox',{name:'Diagnostic report'})).toHaveValue(/"diagnosticsSchema": 2/);
 console.log('Support diagnostics: correlated bridge evidence, browser/server timings, manual fallback passed');
}finally{await browser?.close();fixture.kill('SIGTERM')}
