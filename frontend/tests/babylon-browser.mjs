import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const fixture=spawn(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/chat_ui_server.py',import.meta.url))],{stdio:'inherit'});
for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
const browser=await chromium.launch({headless:true,args:['--enable-unsafe-swiftshader']}),page=await browser.newPage({viewport:{width:1400,height:950}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const content=`<style>html,body{margin:0;height:100%;overflow:hidden}canvas{width:100%;height:85%;touch-action:none}button{padding:8px;margin:8px}</style><h2>Orbit studio</h2><button id="color">Change planet color</button><span id="status">Blue planet</span><canvas id="scene" aria-label="Interactive 3D planet"></canvas><script>
if(!BABYLON.Engine.IsSupported)throw Error('WebGL is unavailable');
const canvas=document.getElementById('scene'),engine=new BABYLON.Engine(canvas,true),scene=new BABYLON.Scene(engine);
scene.clearColor=new BABYLON.Color4(.05,.07,.15,1);
const camera=new BABYLON.ArcRotateCamera('camera',Math.PI/3,Math.PI/3,5,BABYLON.Vector3.Zero(),scene);camera.attachControl(canvas,true);
new BABYLON.HemisphericLight('light',new BABYLON.Vector3(0,1,0),scene);
const planet=BABYLON.MeshBuilder.CreateSphere('planet',{diameter:2,segments:32},scene),material=new BABYLON.StandardMaterial('planet-material',scene);material.diffuseColor=new BABYLON.Color3(.2,.5,1);planet.material=material;
window.scene=scene;window.engine=engine;window.framesRendered=0;
engine.runRenderLoop(()=>{scene.render();window.framesRendered++;});addEventListener('resize',()=>engine.resize());
document.getElementById('color').onclick=()=>{material.diffuseColor=new BABYLON.Color3(1,.3,.4);document.getElementById('status').textContent='Rose planet'};
</script>`;
try{
 await page.goto('http://127.0.0.1:8958/');await page.waitForSelector('#amp-one');
 await page.evaluate(content=>window.amplifier.dispatch('canvas.show',{kind:'babylon',title:'Orbit studio',content}),content);
 const frame=page.frameLocator('.a-canvas-html');await frame.getByRole('heading',{name:'Orbit studio'}).waitFor();
 await frame.locator('#scene').evaluate(async()=>{for(let i=0;i<100&&!(window.framesRendered>=5);i++)await new Promise(r=>setTimeout(r,100));if(!(window.framesRendered>=5))throw Error('No rendered frames')});
 assert.equal(await frame.locator('#scene').evaluate(()=>window.scene.meshes.length),1);
 const angle=await frame.locator('#scene').evaluate(()=>scene.activeCamera.alpha);const box=await frame.locator('#scene').boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2+80,box.y+box.height/2,{steps:8});await page.mouse.up();assert.notEqual(await frame.locator('#scene').evaluate(()=>scene.activeCamera.alpha),angle);
 await page.waitForFunction(()=>window.amplifier.getState().canvas.document?.controls?.some(c=>c.label==='Change planet color'));
 await page.evaluate(()=>{const c=window.amplifier.getState().canvas;return window.amplifier.dispatch('canvas.interact',{id:c.id,controlId:c.document.controls.find(c=>c.label==='Change planet color').id,event:'click'})});await frame.getByText('Rose planet',{exact:true}).waitFor();
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'Download canvas source'}).click();const result=await download;assert.equal(result.suggestedFilename(),'canvas-3d.html');const exported=await readFile(await result.path(),'utf8');assert.ok(exported.includes('data-amplifier-library="babylon"')&&exported.includes('Orbit studio'));
 await page.screenshot({path:'/tmp/amplifier-babylon-scene.png'});await page.reload();await page.frameLocator('.a-canvas-html').getByText('Blue planet',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Close canvas panel'}).click();
 const toggle=page.getByRole('button',{name:'Pin navigation open',exact:true});await toggle.hover();await toggle.click();await page.waitForFunction(()=>window.amplifier.getState().view.navPinned===true);assert.equal(await page.getByRole('button',{name:'Unpin navigation',exact:true}).count(),1);await page.mouse.move(700,30);assert.equal(await page.locator('.a-nav-slot').evaluate(el=>el.classList.contains('is-pinned')),true);await page.getByRole('button',{name:'Unpin navigation',exact:true}).click();await page.waitForFunction(()=>window.amplifier.getState().view.navPinned===false);
 assert.deepEqual(errors,[]);console.log('Babylon renders WebGL frames, supports camera interaction and agent-operated DOM controls, exports its bundled runtime, persists across reload; single sidebar icon pins and unpins.');
}catch(e){console.log('Errors',errors,await page.evaluate(()=>window.amplifier?.getState()?.canvas?.renderReports));throw e}finally{await browser.close();fixture.kill()}
