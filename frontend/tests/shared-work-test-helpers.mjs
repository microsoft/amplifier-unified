// Small test-only guards; no product dispatch, runtime or browser is created here.
import assert from 'node:assert/strict';

export const unknownCleanupValue=/^(?:unknown|not recorded)(?:\s*[/\u00b7\u2014-]\s*(?:unknown|not recorded))?\.?$/i;
export const passiveWorkReads=new Set(['workspace.work.list','workspace.work.read','workspace.work.history','workspace.work.source',
 'workspace.work.wait','workspace.work.event','workspace.work.coverage']);
const forbiddenPassiveActions=new Set(['session.select','session.warm','conversation.send','worker.spawn','task.start','coordination.create']);

export function assertPassiveActions(calls) {
 for(const call of calls) {
  assert.ok(!forbiddenPassiveActions.has(call.action),`Passive Work dispatched ${call.action} from ${call.client}`);
  if(call.action.startsWith('workspace.work.'))assert.ok(passiveWorkReads.has(call.action),`Unexpected Work mutation ${call.action}`);
 }
}

// EventEmitter does not await request listeners. Catch locally and fail later in
// the awaited browser try block, so its finally always owns ordinary cleanup.
export function createWorkObserver({maxCalls=1000,maxErrors=16}={}) {
 assert.ok(Number.isInteger(maxCalls)&&maxCalls>0);
 assert.ok(Number.isInteger(maxErrors)&&maxErrors>0);
 const calls=[],errors=[];let errorCount=0;
 const recordError=(client,kind,message)=>{
  errorCount++;
  if(errors.length<maxErrors)errors.push({client,kind,message:String(message).slice(0,500)});
 };
 return {
  calls,errors,
  get errorCount(){return errorCount},
  request(client,request) {
   try {
    if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
    const body=request.postDataJSON();
    assert.ok(body&&typeof body==='object'&&!Array.isArray(body),'Action body must be an object');
    assert.ok(typeof body.action==='string'&&body.action.length>0&&body.action.trim()===body.action,'Action name must be a nonempty string');
    assert.ok(body.args===undefined||body.args&&typeof body.args==='object'&&!Array.isArray(body.args),'Action arguments must be an object');
    assert.ok(JSON.stringify(body).length<=65536,'Action body exceeds observation bound');
    assert.ok(calls.length<maxCalls,'Action observation capacity exceeded');
    calls.push({client,action:body.action,args:body.args??{}});
    if(body.action==='session.warm')recordError(client,'forbidden-warm','Work browser requested session.warm');
   } catch(error) {recordError(client,'request-observation',error.message)}
  },
  pageError(client,error) {recordError(client,'page-error',error.message)},
  assertHealthy() {
   assert.equal(errorCount,0,`Browser observation errors (${errorCount}; retained ${errors.length}): ${JSON.stringify(errors)}`);
  },
 };
}

export async function bounded(promise,timeoutMs,label) {
 let timer;
 try {return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label+' timed out')),timeoutMs)})])}
 finally {clearTimeout(timer)}
}

// Observe a returned handler promise immediately, without dictating whether the
// product callback is sync or async and without an unhandled rejection window.
export const observeCompletion=value=>Promise.resolve(value).then(
 value=>({status:'fulfilled',value}),reason=>({status:'rejected',reason}));
export async function requireCompletion(completion) {
 const result=await completion;
 if(result.status==='rejected')throw result.reason;
 return result.value;
}
export function deferredRead() {
 let resolve;
 const promise=new Promise(done=>{resolve=done});
 return {promise,resolve};
}
