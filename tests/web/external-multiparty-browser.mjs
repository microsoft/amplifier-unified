// Built client, real host and independent ACP transports; no model/account calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {fixturePython} from './fixture-python.mjs';

const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'/.venv/bin/python'),[root+'/tests/fixtures/external_multiparty_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const {url,workspace}=await new Promise((resolve,reject)=>{
  let output='';const timeout=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timeout);resolve(row)}}catch{}});
  fixture.once('error',reject);fixture.once('exit',code=>reject(Error('Fixture exited '+code)));
 });
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);
 await page.waitForFunction(()=>window.amplifier?.dispatch);
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await action('session.draft',{workspace});
 await page.getByRole('combobox',{name:'Agent for new chat',exact:true}).selectOption('alpha');
 await page.getByRole('button',{name:'Add agent',exact:true}).click();
 await page.getByRole('combobox',{name:'Additional agent 1',exact:true}).selectOption('beta');
 await expect(page.getByText('First message to Alpha. You can choose who replies next.',{exact:true})).toBeVisible();
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await composer.fill('shared result 42');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(page.getByText('Reply: shared result 42',{exact:true})).toBeVisible();
 const sid=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const recipient=page.getByRole('combobox',{name:'Message recipient',exact:true});
 await expect(recipient).toHaveValue('alpha');
 await recipient.selectOption('beta');
 await composer.fill('consider Alpha’s result');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(page.getByText('Reply: consider Alpha’s result',{exact:true})).toBeVisible();
 const observe=()=>page.request.get(url+'/fixture/participants').then(response=>response.json());
 const first=await observe(),room=first.sessions.find(row=>row.id===sid);
 assert.equal(room.externalAgent.participants.length,2);
 const identities=room.externalAgent.participants.map(row=>row.sessionId);
 assert.equal(new Set(identities).size,2);
 assert.deepEqual(room.messages.map(row=>row.agentId||row.recipientAgentId),['alpha','alpha','beta','beta']);
 const betaPrompt=first.peers.beta.find(row=>row.kind==='prompt');
 const context=JSON.parse(betaPrompt.context[0].split('\n',2)[1]);
 assert.equal(context.messages.at(-1).agentId,'alpha');
 assert.equal(context.messages.at(-1).text,'Reply: shared result 42');
 // Reload reuses the same host and remote identities; it never resubmits work.
 await page.reload();await expect(recipient).toHaveValue('beta');
 const reloaded=await observe();assert.deepEqual(reloaded.peers,first.peers);
 assert.deepEqual(reloaded.sessions.find(row=>row.id===sid).externalAgent.participants.map(row=>row.sessionId),identities);
 await recipient.selectOption('alpha');
 await composer.fill('wait-for-stop');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(recipient).toBeDisabled();
 await page.waitForFunction(()=>{const state=window.amplifier.getState();return state.sessions.find(row=>row.id===state.selectedSessionId)?.streaming==='Reply: '});
 await page.getByRole('button',{name:'Stop response',exact:true}).click();
 await expect(recipient).toBeEnabled();
 const stopped=await observe();
 assert.equal(stopped.peers.alpha.filter(row=>row.kind==='cancel').length,1);
 assert.equal(stopped.peers.beta.filter(row=>row.kind==='cancel').length,0);
 assert.equal(stopped.sessions.find(row=>row.id===sid).messages.at(-1).agentId,'alpha');
 await recipient.selectOption('beta');await composer.fill('after Alpha stopped');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(page.getByText('Reply: after Alpha stopped',{exact:true})).toBeVisible();
 const final=await observe();
 assert.equal(final.peers.alpha.filter(row=>row.kind==='prompt').length,2);
 assert.equal(final.peers.beta.filter(row=>row.kind==='prompt').length,2);
 assert.deepEqual(final.nativeStarts,[]);assert.deepEqual(final.nativeSends,[]);assert.deepEqual(errors,[]);
 const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS;
 if(artifacts){await mkdir(artifacts,{recursive:true});await page.screenshot({path:join(artifacts,'multiparty-desktop.png'),animations:'disabled'});}
 await page.setViewportSize({width:390,height:844});await expect(recipient).toBeVisible();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
 if(artifacts)await page.screenshot({path:join(artifacts,'multiparty-mobile.png'),animations:'disabled'});
 console.log(JSON.stringify({passed:true,twoIndependentBindings:true,sharedPublicContext:true,attributedReplies:true,reloadWithoutReplay:true,targetedStop:true,continuedOtherAgent:true,noNativeWorker:true,mobileNoOverflow:true}));
}finally{
 await browser?.close();
 if(fixture.exitCode===null){const exited=new Promise(resolve=>fixture.once('exit',resolve));fixture.kill('SIGTERM');await exited;}
}
