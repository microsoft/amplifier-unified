import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/voice_visual_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timed out')),20000);fixture.once('exit',()=>reject(Error('Fixture exited')));fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}}})});
 browser=await chromium.launch({headless:true});const page=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(()=>{
  class Peer extends EventTarget{constructor(){super();this.connectionState='new'}createDataChannel(){const channel=new EventTarget();channel.readyState='open';channel.close=()=>{};return channel}addTrack(){}async createOffer(){return {type:'offer',sdp:'synthetic-offer'}}async setLocalDescription(value){this.localDescription=value}async setRemoteDescription(){this.connectionState='connected';this.dispatchEvent(new Event('connectionstatechange'))}close(){}}
  window.RTCPeerConnection=Peer;navigator.mediaDevices.getUserMedia=async()=>new MediaStream();
 });
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const activity=(sessionId,status)=>page.evaluate(async data=>{const response=await fetch('/fixture/activity',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});if(!response.ok)throw Error('Fixture activity failed')},{sessionId,status});
 const inspect=()=>page.evaluate(async()=>await(await fetch('/fixture')).json());
 await action('session.create',{});
 const original=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await page.getByRole('button',{name:'Start voice call',exact:true}).click();
 await page.getByRole('button',{name:'End call',exact:true}).waitFor();
 await activity(original,'working');
 await page.getByRole('button',{name:'Mute microphone',exact:true}).click();
 await expect(page.getByText('Microphone muted. Replies and work continue.',{exact:true})).toBeVisible();
 assert.deepEqual((await inspect()).stopped,[]);
 await page.getByRole('button',{name:'Stop work',exact:true}).waitFor();
 await action('session.create',{});
 const other=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);assert.notEqual(other,original);
 await page.getByRole('button',{name:'Stop work',exact:true}).click();
 await expect.poll(async()=>(await inspect()).stopped).toEqual([original]);
 await expect(page.getByRole('button',{name:'End call',exact:true})).toBeVisible();
 await activity(original,'working');
 await page.getByRole('button',{name:'End call',exact:true}).click();
 await expect(page.getByRole('button',{name:'End call',exact:true})).toHaveCount(0);
 assert.deepEqual((await inspect()).stopped,[original]);
 assert.deepEqual(errors,[]);
 console.log('Voice work controls browser passed: mute preserves work, stop targets originating chat after navigation and keeps audio connected, end-call does not stop work. Synthetic signaling; no microphone/provider audio claim.');
}finally{if(browser)await browser.close();fixture.kill();}
