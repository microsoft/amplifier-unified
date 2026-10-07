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
 const {alpha,beta}=await control({op:'heavy',messages:2,otherMessages:2,chars:30,nodes:0});
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:headers}),errors=[],sends=[];
 page.on('pageerror',e=>{errors.push(e.message);console.log('PAGE ERROR',e.message)});page.on('request',r=>{if(r.url().endsWith('/api/actions'))try{const body=r.postDataJSON();if(body.action==='conversation.send')sends.push(body)}catch{}});
 await page.goto(url);const input=page.getByRole('textbox',{name:'Message Amplifier'});await input.waitFor();
 const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const draft=()=>page.evaluate(()=>window.amplifier.getState().view.draft);
 const clear=async()=>{await input.press('Control+a');await input.press('Backspace');await expect.poll(draft).toBe('');};
 const paste=text=>input.evaluate((el,text)=>{const clipboardData=new DataTransfer();clipboardData.setData('text/plain',text);el.dispatchEvent(new ClipboardEvent('paste',{clipboardData,bubbles:true,cancelable:true}))},text);
 await input.pressSequentially('**bold** _emphasis_');await expect(input.locator('strong')).toHaveText('bold');await expect(input.locator('em')).toHaveText('emphasis');await expect.poll(draft).toBe('**bold** *emphasis*');assert.ok((await input.boundingBox()).height<=56,'A single-line draft stays compact');
 await input.press('Control+z');await expect(input.locator('em')).toHaveCount(0);await input.press('Control+Shift+z');await expect(input.locator('em')).toHaveCount(1);
 await input.press('Control+a');await input.press('Delete');await expect.poll(draft).toBe('');await input.pressSequentially('# Heading');await expect(input.locator('h1')).toHaveText('Heading');await input.press('Control+Enter');await input.pressSequentially('* One');await input.press('Control+Enter');await input.pressSequentially('Two');await expect(input.locator('ul li')).toHaveCount(2);
 await input.press('Control+Enter');await input.press('Backspace');await input.pressSequentially('Outside');await expect(input.locator(':scope > p')).toHaveText('Outside');
 await input.press('Shift+Enter');await input.pressSequentially('1. First');await input.press('Control+Enter');await input.pressSequentially('Second');await expect(input.locator('ol li')).toHaveCount(2);assert.equal(sends.length,0);
 const markdown=await draft();await expect.poll(async()=>{const state=await (await fetch(url+'/api/state',{headers:{...headers,'X-Amplifier-Client':await page.evaluate(()=>window.amplifier.getState().client.id)}})).json();return state.view?.draft}).toBe(markdown);
 await action('session.select',{id:beta});await expect.poll(draft).toBe('');await input.pressSequentially('Other **draft**');await action('session.select',{id:alpha});await expect(input.locator('h1')).toHaveText('Heading');await expect.poll(draft).toBe(markdown);
 await page.reload();await input.waitFor();await expect(input.locator('ol li')).toHaveCount(2);await expect.poll(draft).toBe(markdown);
 // External/agent draft changes render without replaying them as user edits.
 await action('view.update',{patch:{draft:'## From an agent\n\nKeep **this**.'},sessionId:alpha});await expect(input.locator('h2')).toHaveText('From an agent');await expect(input.locator('strong')).toHaveText('this');
 await clear();await input.pressSequentially('```js ');await input.pressSequentially('const text = "**literal**";');await input.press('Control+Enter');await input.pressSequentially('// next');await expect(input.locator('pre code')).toContainText('**literal**');await expect(input.locator('strong')).toHaveCount(0);
 await clear();await paste('Hello **from paste**\n\n- A\n- B');await expect(input.locator('strong')).toHaveText('from paste');await expect(input.locator('li')).toHaveCount(2);
 await clear();await paste('<script>alert(1)</script>');await expect(input.locator('script')).toHaveCount(0);await expect(input).toHaveText('<script>alert(1)</script>');
 await clear();await input.pressSequentially('Keep this prompt');await paste('Diagnostics\n'.repeat(1000));await expect(page.locator('.a-attachment')).toContainText('Pasted text');await expect(input).toHaveText('Keep this prompt');
 await page.getByRole('button',{name:'Remove Pasted text.txt',exact:true}).click();
 // Image and Markdown image pastes do not leak remote requests.
 const remote=[];page.on('request',r=>{if(r.url().includes('example.invalid'))remote.push(r.url())});
 await clear();await paste('![diagram](https://example.invalid/image.png)');await expect(input.locator('.a-composer-image-reference')).toHaveText('diagram');assert.deepEqual(remote,[]);
 await clear();await input.evaluate(el=>{const clipboardData=new DataTransfer();const bytes=Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jDioAAAAASUVORK5CYII='),c=>c.charCodeAt(0));clipboardData.items.add(new File([bytes],'clipboard.png',{type:'image/png'}));el.dispatchEvent(new ClipboardEvent('paste',{clipboardData,bubbles:true,cancelable:true}))});
 await expect(page.locator('.a-attachment')).toHaveCount(1);await page.getByRole('button',{name:'Remove clipboard.png',exact:true}).click();
 await clear();await input.pressSequentially('Do not send during composition');await input.evaluate(el=>{el.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode:229,isComposing:true,bubbles:true,cancelable:true}));el.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:''}));});assert.equal(sends.length,0);
 await clear();await input.pressSequentially('Send **formatted**');await input.press('Enter');await expect.poll(()=>sends.length).toBe(1);assert.equal(sends[0].args.text,'Send **formatted**');await expect(input).toHaveText('');await expect(page.locator('.a-user strong').last()).toHaveText('formatted');
 await input.pressSequentially('# A live composer');await input.press('Shift+Enter');await input.pressSequentially('**Bold** and _emphasis_');await input.press('Shift+Enter');await input.pressSequentially('* First item');await input.press('Control+Enter');await input.pressSequentially('Second item');
 await page.screenshot({path:'/tmp/unified-markdown-composer-desktop.png'});
 await page.setViewportSize({width:390,height:844});await expect.poll(async()=>(await input.boundingBox()).width).toBeGreaterThan(300);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.ok((await input.boundingBox()).height<=180);await page.screenshot({path:'/tmp/unified-markdown-composer-mobile.png'});
 assert.deepEqual(errors,[]);console.log('Markdown composer passed: live formatting, undo/redo, headings/lists and exit, draft switch/reload/agent update, literal code, Markdown and large paste, safe HTML, IME guard, Markdown send, desktop/mobile.');
}finally{await browser?.close();fixture.kill()}
