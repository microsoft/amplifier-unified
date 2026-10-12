import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(process.env.UNIFIED_DISTRIBUTION_ENTRY);
const {createHost}=await import(require.resolve('@amplifier/unified-host'));
const {Store}=await import(require.resolve('@amplifier/unified-host').replace(/index\.js$/, 'store.js'));
const {WebSocket}=require('ws');
import {monitorEventLoopDelay,performance} from 'node:perf_hooks';
import {mkdirSync,writeFileSync,readFileSync,statSync,readdirSync} from 'node:fs';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
assert.equal(process.platform,'linux');
assert.ok(process.env.RELEASE_PERFORMANCE_BASE&&process.env.RELEASE_PERFORMANCE_OUTPUT);
const base=process.env.RELEASE_PERFORMANCE_BASE;mkdirSync(base,{recursive:true});
const out={schema:'audit-host-performance-v1',base,limits:{cpu:2,memory:'4GiB'},provider:'independent deterministic synthetic ACP peer; no accounts',samples:[]};
// Prove effective limits inside the measured process. A user systemd manager can
// accept AllowedCPUs while its delegated cgroup lacks the cpuset controller.
out.cgroup=readFileSync('/proc/self/cgroup','utf8');
const cgroupPath=out.cgroup.split('\n').find(line=>line.startsWith('0::'))?.slice(3);
assert.ok(cgroupPath?.startsWith('/'),'Unified cgroup required');
const control=name=>readFileSync('/sys/fs/cgroup'+cgroupPath+'/'+name,'utf8').trim();
out.enforcedLimits={cpuMax:control('cpu.max'),memoryMax:control('memory.max'),swapMax:control('memory.swap.max')};
const [quota,period]=out.enforcedLimits.cpuMax.split(' ').map(Number);
assert.ok(Number.isFinite(quota)&&quota>0&&period>0&&quota/period<=2,'CPU quota must be enforced');
assert.equal(out.enforcedLimits.memoryMax,String(4*1024**3));assert.equal(out.enforcedLimits.swapMax,'0');
out.allowedCpus=readFileSync('/proc/self/status','utf8').split('\n').find(s=>s.startsWith('Cpus_allowed_list:'));
const expand=value=>value.split(',').flatMap(part=>{const [lo,hi=lo]=part.split('-').map(Number);return Array.from({length:hi-lo+1},(_,i)=>lo+i)}).sort((a,b)=>a-b);
const expectedCpus=expand(process.env.RELEASE_PERFORMANCE_CPUS||'');assert.equal(expectedCpus.length,2);
assert.deepEqual(expand(out.allowedCpus.split(':')[1].trim()),expectedCpus);
const delay=monitorEventLoopDelay({resolution:10});delay.enable();const start=performance.now();
const host=await createHost({stateDirectory:base+'/host',allowedWorkspaceRoots:[base],maxClients:80,engines:[{id:'audit',command:process.execPath,args:[fileURLToPath(new URL('fixtures/release-burst-peer.mjs',import.meta.url))]} ]});
out.exactTextChecks=0;
out.startupMs=performance.now()-start;out.idleRss=process.memoryUsage().rss;const cpu=process.cpuUsage();await new Promise(r=>setTimeout(r,1000));out.idleCpuMicros=process.cpuUsage(cpu);
const peers=[];
async function peer(){const ws=new WebSocket(host.url);await new Promise((r,j)=>{ws.once('open',r);ws.once('error',j)});let id=0;const pending=new Map();const row={ws,events:0,bytes:0};ws.on('message',raw=>{row.bytes+=raw.length;const m=JSON.parse(raw);if(pending.has(m.id)){const p=pending.get(m.id);clearTimeout(p.timer);pending.delete(m.id);m.error?p.j(Error(m.error.message)):p.r(m.result)}else row.events++});row.rpc=(method,params)=>new Promise((r,j)=>{const key=++id,timer=setTimeout(()=>j(Error('RPC timeout '+method)),10000);pending.set(key,{r,j,timer});ws.send(JSON.stringify({jsonrpc:'2.0',id:key,method,params}))});await row.rpc('initialize',{channel:'ahp-root://',clientId:randomUUID(),protocolVersions:['0.9.0'],initialSubscriptions:['ahp-root://']});peers.push(row);return row;}
try{
 const owner=await peer();const sessions=[];
 for(let i=0;i<4;i++){const sid='ahp-session:/'+randomUUID();await owner.rpc('createSession',{channel:sid,provider:'audit',workingDirectories:['file://'+base]});sessions.push(sid)}
 for(let i=0;i<8;i++){const p=await peer();await p.rpc('subscribe',{channel:sessions[i%4].replace('ahp-session:','ahp-chat:'),view:{turns:20}})}
 for(const kind of ['warm','burst','huge'])for(let repeat=0;repeat<3;repeat++){
  delay.reset();const t=performance.now(),before=process.memoryUsage().rss;
  await Promise.all(sessions.map(async sid=>{const commandId=randomUUID();await host.submitTurn(sid,{commandId,text:kind,clientId:'audit',origin:'ui'});const result=await host.waitForTurn(sid,commandId,20000);if(result.status!=='completed')throw Error(JSON.stringify(result));const turns=host.store.turns(sid.replace('ahp-session:','ahp-chat:'),1).turns;assert.equal(turns.length,1);assert.equal(turns[0].responseParts.filter(p=>p.kind==='markdown').map(p=>p.content).join(''),'x'.repeat((kind==='burst'?1000:20)*64));out.exactTextChecks++;}));
  await new Promise(r=>setTimeout(r,25));out.samples.push({kind,repeat,activeSessions:4,viewers:9,elapsedMs:performance.now()-t,eventLoopP95Ms:delay.percentile(95)/1e6,eventLoopMaxMs:delay.max/1e6,rssDelta:process.memoryUsage().rss-before});
 }
 // A stalled client must not prevent healthy readers from receiving replies.
 const slow=await peer();await slow.rpc('subscribe',{channel:sessions[0].replace('ahp-session:','ahp-chat:')});slow.ws._socket.pause();const commandId=randomUUID(),t=performance.now();await host.submitTurn(sessions[0],{commandId,text:'burst',clientId:'audit',origin:'ui'});await host.waitForTurn(sessions[0],commandId,20000);await owner.rpc('listSessions',{channel:'ahp-root://',limit:50});out.slowViewerHealthyClientMs=performance.now()-t;slow.ws.terminate();
 const reconnect=[];for(let i=0;i<50;i++){const t=performance.now(),p=await peer();await p.rpc('listSessions',{channel:'ahp-root://',limit:50});p.ws.terminate();reconnect.push(performance.now()-t)}out.reconnectMs=reconnect;
 out.beforeDetach=host.diagnostics();out.peakRss=process.memoryUsage().rss;for(const p of peers)p.ws.terminate();await new Promise(r=>setTimeout(r,1000));if(global.gc)global.gc();out.afterDetach=host.diagnostics();out.detachedRss=process.memoryUsage().rss;out.totalClientBytes=peers.reduce((n,p)=>n+p.bytes,0);
}catch(e){out.error=e.stack;process.exitCode=1}finally{for(const p of peers)p.ws.terminate();await host.close();delay.disable();}
const store=new Store(base+'/store');const durations=[],ioBefore=readFileSync('/proc/self/io','utf8');
for(let i=0;i<1000;i++){const t=performance.now();store.transaction(()=>store.append('ahp-chat:/synthetic',{type:'chat/delta',content:'x'.repeat(64)}));durations.push(performance.now()-t)}
out.fullDurability={pragma:store.db.prepare('PRAGMA synchronous').get(),transactions:1000,durationsMs:durations,ioBefore,ioAfter:readFileSync('/proc/self/io','utf8')};store.close();
writeFileSync(process.env.RELEASE_PERFORMANCE_OUTPUT,JSON.stringify(out,null,2));console.log(JSON.stringify({startupMs:out.startupMs,samples:out.samples,error:out.error}));

assert.equal(out.error,undefined);assert.equal(out.exactTextChecks,36);
for(const sample of out.samples){assert.ok(sample.elapsedMs<2000,JSON.stringify(sample));assert.ok(sample.eventLoopMaxMs<500,JSON.stringify(sample));}
assert.ok(out.slowViewerHealthyClientMs<5000);assert.equal(Object.values(out.fullDurability.pragma)[0],2);
