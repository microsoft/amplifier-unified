// Packaged application and real action dispatch, synthetic media/runtime only.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/voice_visual_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
const output=process.env.AMPLIFIER_SCREENSHOT_DIR||'/tmp/amplifier-composer-primary-action';
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timed out')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}})});
 await mkdir(output,{recursive:true});browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[],calls=[];
 page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')calls.push(request.postDataJSON())});
 await page.addInitScript(()=>{
  class Peer extends EventTarget{constructor(){super();this.connectionState='new'}createDataChannel(){const channel=new EventTarget();channel.readyState='open';channel.close=()=>{};return channel}addTrack(){}async createOffer(){return {type:'offer',sdp:'synthetic-offer'}}async setLocalDescription(value){this.localDescription=value}async setRemoteDescription(){this.connectionState='connected';this.dispatchEvent(new Event('connectionstatechange'))}close(){}}
  window.RTCPeerConnection=Peer;window.delayMicrophone=true;
  navigator.mediaDevices.getUserMedia=async()=>{if(window.denyMicrophone)throw new DOMException('Synthetic denial','NotAllowedError');if(window.delayMicrophone)await new Promise(resolve=>window.releaseMicrophone=resolve);return new MediaStream()};
 });
 await page.goto(url);const composer=page.getByRole('textbox',{name:'Message Amplifier'}),primary=page.locator('[data-part="composer-primary-action"]');await composer.waitFor();
 const action=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const inspect=()=>page.request.get(url+'/fixture').then(response=>response.json());
 const matches=async(label,action,icon)=>{await expect(primary).toHaveCount(1);await expect(primary).toHaveAttribute('aria-label',label);await expect(primary).toHaveAttribute('title',label);await expect(primary).toHaveAttribute('data-action',action);await expect(primary.locator('svg')).toHaveClass(new RegExp('lucide-'+icon));};
 await matches('Start voice call','call.start','audio-lines');await expect(primary).toBeEnabled();await expect(page.locator('.a-call-button')).toHaveCount(0);
 await composer.press('Enter');assert.equal(calls.filter(row=>row.action==='call.start').length,0,'Empty Enter never starts voice');
 await composer.fill(' \n');await matches('Start voice call','call.start','audio-lines');await composer.fill('');
 await primary.click();await matches('End voice call','call.end','x');await expect.poll(()=>page.evaluate(()=>typeof window.releaseMicrophone)).toBe('function');
 await composer.press('Enter');assert.equal(calls.filter(row=>row.action==='call.end').length,0,'Empty Enter never ends voice');
 await primary.click();await matches('Start voice call','call.start','audio-lines');
 await page.evaluate(()=>{window.delayMicrophone=false;window.releaseMicrophone()});
 await primary.click();await expect(page.getByRole('button',{name:'Choose screen source',exact:true})).toBeVisible();await matches('End voice call','call.end','x');
 const sid=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const activity=async(status,workers=[])=>{assert.equal((await page.request.post(url+'/fixture/activity',{data:{sessionId:sid,status,workers}})).status(),200)};
 const ends=calls.filter(row=>row.action==='call.end').length;
 await composer.fill('A typed message during voice');await matches('Send message','conversation.send','arrow-up');await expect(primary).toHaveAttribute('type','submit');
 await primary.click();await expect.poll(async()=>(await inspect()).sent.length).toBe(1);await matches('End voice call','call.end','x');
 assert.equal((await inspect()).sent[0].text,'A typed message during voice');assert.equal(calls.filter(row=>row.action==='call.end').length,ends,'Sending text keeps the call connected');
 await action('view.update',{patch:{draft:'An agent supplied draft'}});await expect(composer).toHaveValue('An agent supplied draft');await matches('Send message','conversation.send','arrow-up');await action('view.update',{patch:{draft:''}});await matches('End voice call','call.end','x');
 await page.getByLabel('Attach files',{exact:true}).setInputFiles({name:'note.txt',mimeType:'text/plain',buffer:Buffer.from('Attachment-only message')});await page.getByRole('button',{name:'Remove note.txt'}).waitFor();await matches('Send message','conversation.send','arrow-up');await expect(primary).toBeEnabled();
 await page.getByRole('button',{name:'Remove note.txt'}).click();await matches('End voice call','call.end','x');
 await activity('working');await matches('End voice call','call.end','x');await primary.click();await matches('Stop response','conversation.stop','x');assert.deepEqual((await inspect()).stopped,[],'Ending voice does not cancel background work');
 await composer.fill('A correction');await matches('Send a correction','conversation.send','arrow-up');await composer.fill('');await matches('Stop response','conversation.stop','x');
 await primary.focus();await page.keyboard.press('Space');await expect.poll(async()=>(await inspect()).stopped).toEqual([sid]);await matches('Start voice call','call.start','audio-lines');
 await activity('idle',[{id:'worker',status:'running',title:'Background worker'}]);await matches('Stop response','conversation.stop','x');await activity('stopping');await expect(primary).toBeDisabled();await activity('idle');await matches('Start voice call','call.start','audio-lines');
 await page.evaluate(()=>window.denyMicrophone=true);await primary.click();await expect(page.getByText(/Microphone access was declined/)).toBeVisible();await matches('Start voice call','call.start','audio-lines');await expect(primary).toBeEnabled();await page.evaluate(()=>window.denyMicrophone=false);
 await page.getByRole('button',{name:'Dismiss error',exact:true}).click();
 await action('view.update',{patch:{navPinned:false,navExpanded:false}});
 for(const width of [1280,390,320]){
  await page.setViewportSize({width,height:900});await composer.fill('');await activity('idle');await matches('Start voice call','call.start','audio-lines');const box=await primary.boundingBox();await page.screenshot({path:output+`/voice-${width}.png`});
  await composer.fill('Ready to send');await matches('Send message','conversation.send','arrow-up');assert.deepEqual(await primary.boundingBox(),box);await page.screenshot({path:output+`/send-${width}.png`});
  await composer.fill('');await activity('working');await matches('Stop response','conversation.stop','x');assert.deepEqual(await primary.boundingBox(),box);await expect(primary).toBeInViewport();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:output+`/cancel-${width}.png`});
 }
 assert.deepEqual(errors,[]);assert.equal(calls.filter(row=>row.action==='conversation.send').length,1);console.log('Composer primary action passed: voice/send/cancel, connecting cancellation, text/attachments during voice, agent drafts, work versus call cancellation, keyboard, permission recovery, stable desktop/mobile geometry. Synthetic media only; no physical audio claim.');
}finally{await browser?.close();fixture.kill('SIGTERM')}
