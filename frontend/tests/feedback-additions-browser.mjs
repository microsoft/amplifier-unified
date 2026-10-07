// Narrow current-source component fixture. No SDK, GitHub, account or user files.
// Run only in the manager's retained DTU after service routing is integrated.
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../',import.meta.url));
const directory=await mkdtemp(root+'tests/.feedback-additions-');
const name=directory.split('/').at(-1);
let server,browser;
try{
 await writeFile(directory+'/index.html','<div id="fixture"></div><script type="module" src="./main.jsx"></script>');
 await writeFile(directory+'/main.jsx',String.raw`
import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {FeedbackFiles} from '../../src/feedback-followup.jsx';
const FID='original-feedback',OTHER='other-feedback';
const fresh=()=>({clients:{a:{files:{},saved:{},receipts:[]},b:{files:{},saved:{},receipts:[]}},calls:[],stageIds:{},mode:'success'});
let storage=JSON.parse(localStorage.getItem('file-fixture')||'null')||fresh();
window.fixture=storage;
function App(){
 const [client,setClient]=useState('a'),[feedbackId,setReport]=useState(FID),[,refresh]=useState(0);
 function commit(){localStorage.setItem('file-fixture',JSON.stringify(storage));refresh(n=>n+1)}
 const record=storage.clients[client];
 async function act(action,args){
  storage.calls.push({action,args:structuredClone(args),client});
  if(action==='feedback.attachment.add'){
   if(!storage.stageIds[args.requestId]){
    const bytes=Uint8Array.from(atob(args.base64),c=>c.charCodeAt(0));
    const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');
    const id=crypto.randomUUID().replaceAll('-','');
    record.files[feedbackId]=[...(record.files[feedbackId]||[]),{id,name:args.name,mime:'text/plain',size:bytes.length,sha256:hash,url:'/fixture-file/'+id}];
    storage.stageIds[args.requestId]=true;
   }
  }else if(action==='feedback.attachment.remove'){
   record.files[feedbackId]=(record.files[feedbackId]||[]).filter(row=>row.id!==args.id);
  }else if(action==='feedback.attachments.review'){
   record.receipts.unshift({requestId:args.requestId,feedbackId,action,status:'completed',message:'Reviewed; nothing uploaded.',
    review:{url:'https://github.com/microsoft/amplifier-unified/issues/'+(feedbackId===FID?42:43),issueId:feedbackId===FID?42:43,
     repository:{id:101,full_name:'microsoft/amplifier-unified',private:true},account:{id:7,login:'fixture-owner'},
     manifest:(record.files[feedbackId]||[]).map(({id,name,mime,size,sha256})=>({id,name,mime,size,sha256})).sort((a,b)=>a.id.localeCompare(b.id)),
     disclosure:'Private preflight is not an atomic visibility guarantee. Files remain in repository history; local removal does not delete uploaded history.'}});
  }else if(action==='feedback.attachments.add'){
   const existing=record.receipts.find(row=>row.requestId===args.requestId);
   if(!existing){
    const review=record.receipts.find(row=>row.requestId===args.reviewRequestId);review.consumedBy=args.requestId;
    const partial=storage.mode==='unknown';
    record.receipts.unshift({requestId:args.requestId,feedbackId,action,status:partial?'partial':'submitted',
     filesStored:true,commentStatus:partial?'unknown':'succeeded',message:partial?'Files stored; comment delivery unknown. No replay.':'Files linked; original report preserved.',
     phases:[{phaseId:args.requestId+':ref',status:'succeeded'},{phaseId:args.requestId+':comment',status:partial?'unknown':'succeeded'}],
     attachments:review.review.manifest.map(row=>({...row,url:'https://github.com/microsoft/amplifier-unified/blob/'+('c'.repeat(40))+'/'+row.id+'/'+row.name})),
     ...(partial?{}:{commentUrl:review.review.url+'#issuecomment-123'})});
    if(!partial)record.files[feedbackId]=[];
   }
  }else if(action==='feedback.attachments.reconcile'){
   record.receipts.unshift({requestId:args.requestId,feedbackId,action,status:'completed',message:'Read only; no uploads repeated.'});
  }else throw Error('Unexpected fixture action');
  commit();return {accepted:true};
 }
 async function save(value){record.saved[feedbackId]=structuredClone(value);commit()}
 return <><label>Fixture client<select value={client} onChange={e=>setClient(e.target.value)}><option value="a">a</option><option value="b">b</option></select></label>
  <label>Fixture report<select value={feedbackId} onChange={e=>setReport(e.target.value)}><option value={FID}>original</option><option value={OTHER}>other</option></select></label>
  <FeedbackFiles key={client+feedbackId} feedbackId={feedbackId} state={{feedback:{attachmentDrafts:record.files,additions:record.receipts},view:{feedbackDraft:{attachments:[]}}}}
   act={act} saved={record.saved[feedbackId]} save={save}/></>;
}
createRoot(document.getElementById('fixture')).render(<App/>);
`);
 server=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false},
  optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await server.listen();browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(server.resolvedUrls.local[0]+'tests/'+name+'/index.html');
 const add=page.getByRole('button',{name:'Add reviewed files',exact:true});
 await page.getByLabel('Follow-up files',{exact:true}).setInputFiles({name:'reviewed.txt',mimeType:'text/plain',buffer:Buffer.from('fixture bytes')});
 await page.getByText(/SHA256 [a-f0-9]{64}/).waitFor();
 assert.equal(await add.isEnabled(),false);
 await page.getByRole('button',{name:'Review private file destination',exact:true}).click();
 await page.getByText('GitHub account fixture-owner (ID 7)',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.fixture.calls.filter(row=>row.action==='feedback.attachments.add').length),0);
 assert.equal(await add.isEnabled(),false); // review alone cannot consent
 await page.getByRole('checkbox').check();assert.equal(await add.isEnabled(),true);
 // Changing the set invalidates review; exact manifest/SHA must be shown again.
 await page.getByLabel('Follow-up files',{exact:true}).setInputFiles({name:'second.txt',mimeType:'text/plain',buffer:Buffer.from('second fixture')});
 await page.waitForFunction(()=>window.fixture.clients.a.files['original-feedback']?.length===2);
 assert.equal(await add.isEnabled(),false);
 await page.getByRole('button',{name:'Review private file destination',exact:true}).click();
 await page.getByText('GitHub account fixture-owner (ID 7)',{exact:true}).waitFor();
 await page.getByRole('checkbox').check();
 await page.getByLabel('File addition comment (optional)',{exact:true}).fill('A retained ordinary-file draft');
 await page.getByLabel('File addition comment (optional)',{exact:true}).blur();
 await page.waitForFunction(()=>window.fixture.clients.a.saved['original-feedback']?.comment==='A retained ordinary-file draft');
 await page.getByLabel('Fixture client',{exact:true}).selectOption('b');
 assert.equal(await page.getByText(/SHA256 [a-f0-9]{64}/).count(),0);
 await page.getByLabel('File addition comment (optional)',{exact:true}).fill('Other client draft');
 await page.getByLabel('File addition comment (optional)',{exact:true}).blur();
 await page.waitForFunction(()=>window.fixture.clients.b.saved['original-feedback']?.comment==='Other client draft');
 await page.getByLabel('Fixture client',{exact:true}).selectOption('a');
 assert.equal(await page.getByLabel('File addition comment (optional)',{exact:true}).inputValue(),'A retained ordinary-file draft');
 await page.getByLabel('Fixture report',{exact:true}).selectOption('other-feedback');
 assert.equal(await page.getByText(/SHA256 [a-f0-9]{64}/).count(),0);
 await page.getByLabel('Fixture report',{exact:true}).selectOption('original-feedback');
 await page.reload();
 assert.equal(await page.getByLabel('File addition comment (optional)',{exact:true}).inputValue(),'A retained ordinary-file draft');
 assert.equal(await add.isEnabled(),false); // reload does not recover transient consent
 await page.getByRole('checkbox').check();
 await page.evaluate(()=>window.fixture.mode='unknown');
 await add.click();
 await page.getByText('Files stored; comment delivery unknown. No replay.',{exact:true}).waitFor();
 assert.equal(await page.getByLabel('Follow-up files',{exact:true}).isDisabled(),true);
 assert.equal(await page.getByLabel('File addition comment (optional)',{exact:true}).isDisabled(),true);
 assert.equal(await page.getByRole('button',{name:'Add reviewed files',exact:true}).count(),0);
 const payload=await page.evaluate(()=>window.fixture.clients.a.saved['original-feedback'].pending);
 assert.equal(payload.confirmedFiles.length,2);assert.equal(payload.comment,'A retained ordinary-file draft');
 await page.reload();
 await page.getByRole('button',{name:'Check file delivery (read only)',exact:true}).click();
 assert.equal(await page.evaluate(()=>window.fixture.calls.filter(row=>row.action==='feedback.attachments.add').length),1);
 assert.equal(await page.evaluate(()=>window.fixture.calls.filter(row=>row.action==='feedback.attachments.reconcile').length),1);
 assert.equal(await page.getByRole('link',{name:/Stored /}).count(),2);
 await page.getByLabel('Fixture client',{exact:true}).selectOption('b');
 assert.equal(await page.getByLabel('File addition comment (optional)',{exact:true}).inputValue(),'Other client draft');
 // Independent successful addition exposes the canonical comment/file links.
 await page.evaluate(()=>window.fixture.mode='success');
 await page.getByLabel('Follow-up files',{exact:true}).setInputFiles({name:'success.txt',mimeType:'text/plain',buffer:Buffer.from('success fixture')});
 await page.getByRole('button',{name:'Review private file destination',exact:true}).click();
 await page.getByText('GitHub account fixture-owner (ID 7)',{exact:true}).waitFor();
 await page.getByRole('checkbox').check();await add.click();
 await page.getByRole('link',{name:'View file addition comment',exact:true}).waitFor();
 assert.equal(await page.getByRole('link',{name:'View file addition comment',exact:true}).getAttribute('href'),'https://github.com/microsoft/amplifier-unified/issues/42#issuecomment-123');
 await page.setViewportSize({width:390,height:844});
 assert.deepEqual(errors,[]);
 console.log('Current-source file fixture: review is not consent; exact SHA/set guards; client/report/reload preservation; frozen partial delivery with read-only check; canonical success links. Host/GitHub integration remains a separate check.');
}finally{
 await browser?.close();await server?.close();await rm(directory,{recursive:true,force:true});
}