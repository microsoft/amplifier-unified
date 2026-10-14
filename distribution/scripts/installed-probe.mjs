import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {once} from 'node:events';
import {randomUUID} from 'node:crypto';
import {createDistribution} from '@amplifier/unified';
import {webDirectory} from '@amplifier/unified-client-web';
import {WebSocket} from 'ws';

const workspace=join(process.cwd(),'workspace');await mkdir(workspace);
const app=await createDistribution({account:'installed-fixture',stateDirectory:join(process.cwd(),'state'),webDirectory,defaultWorkspace:workspace,allowedWorkspaceRoots:[workspace],engines:[{id:'fixture',label:'Independent ACP',command:process.execPath,args:[join(process.cwd(),'acp.mjs')]}]});
let socket;const pending=new Map();let next=0;
try{
 const response=await fetch(app.url);assert.equal(response.status,200);const html=await response.text();assert.match(html,/connection.js/);
 const asset=html.match(/(?:src|href)="([^" ]+\.js)"/g)?.map(item=>item.slice(item.indexOf('"')+1,-1)).find(path=>path.includes('/assets/'));
 assert.ok(asset,'Packaged client references its built asset');assert.equal((await fetch(new URL(asset,app.url))).status,200);
 socket=new WebSocket(app.url.replace(/^http/,'ws')+'/ahp',{origin:app.url});await once(socket,'open');
 socket.on('message',raw=>{const message=JSON.parse(raw),entry=pending.get(message.id);if(entry){pending.delete(message.id);clearTimeout(entry.timer);message.error?entry.reject(Error(message.error.message)):entry.resolve(message.result);}});
 const request=(method,params)=>new Promise((resolve,reject)=>{const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(Error(method+' timed out'));},10000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));});
 const init=await request('initialize',{channel:'ahp-root://',clientId:'installed-browser',protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});assert.equal(init.protocolVersion,'0.9.0');
 const session='ahp-session:/'+randomUUID();await request('createSession',{channel:session,provider:'fixture',workingDirectories:[pathToFileURL(workspace).href]});
 const commandId=randomUUID();await app.host.submitTurn(session,{commandId,clientId:'installed-browser',text:'Independent installed graph'});
 const settled=await app.host.waitForTurn(session,commandId,10000);assert.equal(settled.status,'completed');assert.match(settled.text,/Independent fixture/);
 const canvas=await request('x-amplifier/capabilityAction',{channel:session,topic:'canvas',operation:'canvas.show',version:1,args:{kind:'text',content:'Packaged resource'},commandId:randomUUID()});
 const body=await request('resourceRead',{channel:'ahp-root://',uri:canvas.result.artifact.bodyUri,encoding:'utf-8'});assert.equal(JSON.parse(body.data).content,'Packaged resource');
 process.stdout.write(JSON.stringify({installedConsumer:true,staticClient:true,ahpInitialize:true,acpTurn:true,scopedResource:true,node:process.version})+'\n');
}finally{
 for(const {timer}of pending.values())clearTimeout(timer);
 socket?.terminate();await app.close();
}
