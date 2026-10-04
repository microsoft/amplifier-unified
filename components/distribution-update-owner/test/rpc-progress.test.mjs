import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {setTimeout as delay} from 'node:timers/promises';
import {fetchRpcJson,keepRpcResponseAlive,RPC_PROGRESS_HEADER} from '../dist/rpc-progress.js';
import {SupervisorClient,HostControlClient} from '../dist/index.js';

async function fixture(t, handle) {
  const server = createServer(handle);
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
  return new URL('http://127.0.0.1:'+server.address().port+'/');
}
test('slow healthy responses outlive network silence limit, without accumulating keepalives',async t=>{
  let requests=0;
  const url=await fixture(t,async(req,res)=>{
    requests++;assert.equal(req.headers[RPC_PROGRESS_HEADER],'1');
    const stop=keepRpcResponseAlive(res,10);
    try {await delay(250);res.end('{"ok":true}');} finally {stop();}
  });
  const {value}=await fetchRpcJson(url,{},11,100);
  assert.deepEqual(value,{ok:true});assert.equal(requests,1);
});
test('response silence still times out after headers and is never retried',async t=>{
  let requests=0;
  const url=await fixture(t,(req,res)=>{requests++;res.writeHead(200);res.write('\n');});
  await assert.rejects(fetchRpcJson(url,{},1024,100));
  assert.equal(requests,1);
});
test('caller cancellation still interrupts a responsive RPC without replay',async t=>{
  let requests=0;
  const url=await fixture(t,(req,res)=>{requests++;keepRpcResponseAlive(res,10);});
  const abort=new AbortController();
  const pending=fetchRpcJson(url,{signal:abort.signal},1024,1000);
  await delay(100);abort.abort();
  await assert.rejects(pending);assert.equal(requests,1);
});
test('keepalives never turn a truncated or missing receipt into completion',async t=>{
  const url=await fixture(t,async(req,res)=>{
    const stop=keepRpcResponseAlive(res,10);
    try {await delay(50);res.end('{"ok":');} finally {stop();}
  });
  await assert.rejects(fetchRpcJson(url,{},1024,100));
});
test('final envelope retains its byte bound and UTF-8 bytes',async t=>{
  const payload=JSON.stringify({ok:true,text:'é'.repeat(100)});
  const url=await fixture(t,(req,res)=>res.end('\n'+payload));
  await assert.rejects(fetchRpcJson(url,{},50,1000),/rpc_response_limit/);
  assert.deepEqual((await fetchRpcJson(url,{},1000,1000)).value,JSON.parse(payload));
});
test('fast replies keep their HTTP status and contain no unsolicited heartbeat',async t=>{
  const url=await fixture(t,(req,res)=>{
    const stop=keepRpcResponseAlive(res,1000);
    res.writeHead(400);res.end('{"ok":false}');stop();
  });
  const result=await fetchRpcJson(url,{},1024,1000);
  assert.equal(result.response.status,400);assert.deepEqual(result.value,{ok:false});
});

test('an error after keepalives remains an error at both RPC clients',async t=>{
  let requests=0;
  const url=await fixture(t,async(req,res)=>{
    requests++;
    const stop=keepRpcResponseAlive(res,10);
    try {await delay(50);res.end('{"ok":false,"error":"fixture_refused"}');} finally {stop();}
  });
  const token='a'.repeat(64),supervisor=new SupervisorClient({url:url.href,token});
  const host=new HostControlClient({dataScope:'fixture',connect:()=>({url:url.href,token,dataScope:'fixture'})});
  try {
    await assert.rejects(supervisor.owner.reconcile('held'),/fixture_refused/);
    await assert.rejects(host.inspectQuiescence(),/host_control_unavailable/);
    assert.equal(requests,2);
  } finally {supervisor.close();host.close();}
});
