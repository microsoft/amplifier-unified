import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {syncWorkDetail,useWorkDetail}=await server.ssrLoadModule('/src/work-detail.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());

const node=(i,version='1')=>({id:`step-${i}`,detailVersion:version,result:`summary ${i}`,
 resultDetail:{part:'nodes',id:`step-${i}`,field:'result',windowKey:'retained-window',version}});
const rows=(count=144)=>Array.from({length:count},(_,i)=>node(i));
const snapshot=(items=rows())=>({group:'turn@message',revision:'old',items,before:'oldest',hasEarlier:true});
const chunk=(known,extra={})=>({group:'turn@message',revision:'new',order:Object.keys(known),items:[],...extra});
const tail=(items,extra={})=>({group:'turn@message',revision:'new',items,before:'refreshed-oldest',hasEarlier:true,...extra});
function freeze(value){if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value)}return value}

for(const count of [0,1,100,101,144,200])test(`sync stages ${count} known nodes in at most 100-ID chunks and one latest read`,async()=>{
 const previous=freeze(snapshot(rows(count))),calls=[];
 const result=await syncWorkDetail(previous,async body=>{calls.push(body);return chunk(body.known)},async revision=>{
  assert.equal(calls.length,Math.max(1,Math.ceil(count/100)));assert.equal(revision,'new');calls.push('latest');
  return tail(previous.items.slice(-100));
 });
 const posts=calls.slice(0,-1);assert.equal(calls.at(-1),'latest');
 assert.deepEqual(posts.flatMap(body=>Object.keys(body.known)),previous.items.map(row=>row.id));
 for(const [index,body] of posts.entries()){
  assert.equal(body.paged,true);assert.ok(Object.keys(body.known).length<=100);
  assert.ok(Object.values(body.known).every(version=>version==='1'));
  assert.equal(Object.hasOwn(body,'revision'),index>0);if(index)assert.equal(body.revision,'new');
 }
 assert.deepEqual(result.items.map(row=>row.id),previous.items.map(row=>row.id));
 assert.equal(new Set(result.items.map(row=>row.id)).size,count);
 assert.equal(result.before,'refreshed-oldest');assert.equal(result.revision,'new');
 if(count>100)assert.equal(result.items[0],previous.items[0],'early unchanged reference retained');
 assert.equal(previous.revision,'old');assert.equal(previous.before,'oldest');
});

test('changed full rows replace obsolete refs, removed IDs disappear, overlapping latest rows append once',async()=>{
 const previous=freeze(snapshot()),changed={...node(0,'2'),result:'updated'};
 delete changed.resultDetail;
 const current=[changed,...previous.items.slice(1).filter(row=>row.id!=='step-120'),node(144),node(145)];
 const result=await syncWorkDetail(previous,async({known})=>chunk(known,{
  order:Object.keys(known).filter(id=>id!=='step-120'),items:Object.hasOwn(known,'step-0')?[changed]:[],
 }),async()=>tail(current.slice(-100)));
 assert.deepEqual(result.items,current);assert.equal(result.items.length,145);
 assert.equal(result.items[0],changed);assert.equal(result.items[0].resultDetail,undefined,'old field ref must not survive row replacement');
 assert.equal(result.items[1].resultDetail,previous.items[1].resultDetail);
 assert.deepEqual(result.order,current.map(row=>row.id));assert.equal(previous.items.length,144);
});

test('latest overlap can insert a newly visible row within the retained suffix without reordering older rows',async()=>{
 const previous=freeze(snapshot()),latest=[node(1430),...previous.items.slice(-99),node(144)];
 // Keep every latest response bounded, including a newly observed row before an overlap.
 latest.splice(1,1);
 const result=await syncWorkDetail(previous,async({known})=>chunk(known),async()=>tail(latest));
 assert.deepEqual(result.items.map(row=>row.id),[...previous.items.slice(0,46).map(row=>row.id),...latest.map(row=>row.id)]);
});

const invalidChunks=[
 ['duplicate order',known=>chunk(known,{order:[Object.keys(known)[0],Object.keys(known)[0]]})],
 ['unknown order',known=>chunk(known,{order:['unknown']})],
 ['another chunk ID',known=>chunk(known,{order:['step-100']})],
 ['reordered known IDs',known=>chunk(known,{order:Object.keys(known).reverse()})],
 ['duplicate rows',known=>chunk(known,{items:[node(0,'2'),node(0,'2')]})],
 ['delta outside order',known=>chunk(known,{items:[node(999,'2')]})],
 ['removed delta',known=>chunk(known,{order:Object.keys(known).slice(1),items:[node(0,'2')]})],
 ['missing order',known=>{const result=chunk(known);delete result.order;return result}],
 ['missing items',known=>{const result=chunk(known);delete result.items;return result}],
 ['missing revision',known=>chunk(known,{revision:undefined})],
 ['wrong group',known=>chunk(known,{group:'another-group'})],
 ['missing node version',known=>chunk(known,{items:[{id:'step-0'}]})],
 ['oversized order',known=>chunk(known,{order:Array.from({length:101},(_,i)=>`step-${i}`)})],
 ['oversized items',known=>chunk(known,{items:rows(101)})],
];
for(const [label,invalid] of invalidChunks)test(`reject ${label} without partial commit or another request`,async()=>{
 const previous=freeze(snapshot()),before=JSON.stringify(previous);let reads=0,visible=previous;
 await assert.rejects(async()=>{visible=await syncWorkDetail(previous,async({known})=>{reads++;return invalid(known)},async()=>{reads++;return tail([])})},/Work/);
 assert.equal(reads,1);assert.equal(visible,previous);assert.equal(JSON.stringify(previous),before);
});

for(const stage of ['second-chunk','latest'])for(const fault of ['revision','error'])test(`${stage} ${fault} retains the entire old snapshot and never retries`,async()=>{
 const previous=freeze(snapshot());let reads=0,visible=previous;
 const fail=()=>{if(fault==='error')throw Error('Work read failed');return 'different'};
 await assert.rejects(async()=>{visible=await syncWorkDetail(previous,async({known})=>{
  reads++;return chunk(known,{items:reads===1?[node(0,'2')]:[],revision:stage==='second-chunk'&&reads===2?fail():'new'});
 },async()=>{reads++;return tail(previous.items.slice(-100),{revision:stage==='latest'?fail():'new'})})},/Work/);
 assert.equal(visible,previous);assert.equal(previous.items[0].detailVersion,'1');
 assert.equal(reads,stage==='second-chunk'?2:3);
});

for(const [label,latest] of [
 ['duplicate latest IDs',items=>[items.at(-1),items.at(-1)]],
 ['oversized latest',items=>items.slice(-101)],
 ['wrong overlap order',items=>items.slice(-100).reverse()],
 ['non-suffix overlap',items=>items.slice(0,100)],
 ['changed same-revision overlap',items=>[node(143,'conflict')]],
])test(`reject ${label} after staging chunks without publishing them`,async()=>{
 const previous=freeze(snapshot());let visible=previous;
 await assert.rejects(async()=>{visible=await syncWorkDetail(previous,async({known})=>chunk(known),async()=>tail(latest(previous.items)))},/Work/);
 assert.equal(visible,previous);
});

test('an ID removed by sync cannot be resurrected by latest at the same revision',async()=>{
 const previous=freeze(snapshot());
 await assert.rejects(()=>syncWorkDetail(previous,async({known})=>chunk(known,{order:Object.keys(known).filter(id=>id!=='step-143')}),
  async()=>tail(previous.items.slice(-100))),/Work/);
});

for(const [label,extra] of [
 ['missing revision',{revision:undefined}],['wrong group',{group:'other'}],['missing oldest cursor',{before:undefined}],
])test(`latest ${label} cannot commit staged rows`,async()=>{
 const previous=freeze(snapshot());let visible=previous;
 await assert.rejects(async()=>{visible=await syncWorkDetail(previous,async({known})=>chunk(known),async()=>tail(previous.items.slice(-100),extra))},/Work/);
 assert.equal(visible,previous);
});

test('duplicate saved IDs are rejected before IO rather than silently collapsing into a known object',async()=>{
 const previous=freeze(snapshot([node(0),node(0)]));let reads=0;
 await assert.rejects(()=>syncWorkDetail(previous,async()=>{reads++},async()=>{reads++}),/Work/);assert.equal(reads,0);
});

const originalFetch=globalThis.fetch,originalObserver=globalThis.IntersectionObserver;
let root,requests,view;
function Probe({open=true,automatic=false,revision='old',sessionId='chat'}){
 view=useWorkDetail({sessionId,detailsDeferred:true},{id:'turn@message',windowMessageId:'message',detailRevision:revision},open,automatic);
 return React.createElement('div',{ref:view.element},view.error?React.createElement('p',{role:'alert'},view.error):null);
}
const render=props=>React.createElement(Probe,props);
const complete=async(index,value)=>act(async()=>requests[index].resolve(new Response(JSON.stringify(value))));
const tick=async t=>act(async()=>t.mock.timers.tick(100));
async function loaded(t){
 t.mock.timers.enable({apis:['setTimeout']});
 await act(async()=>{root=create(render())});await tick(t);
 await complete(0,{...snapshot(rows().slice(44)),before:'older'});
 await act(async()=>view.earlier());await complete(1,{...snapshot(rows().slice(0,44)),before:'oldest'});
 assert.equal(view.nodes.length,144);
 await act(async()=>root.update(render({revision:'new'})));await tick(t);
}
test.beforeEach(()=>{
 requests=[];view=null;globalThis.IntersectionObserver=undefined;
 globalThis.fetch=(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,body:options.body?JSON.parse(options.body):null,resolve,reject}));
});
test.afterEach(async()=>{
 await act(async()=>{root?.unmount();for(const request of requests)request.reject(new DOMException('Aborted','AbortError'))});root=null;
 globalThis.fetch=originalFetch;globalThis.IntersectionObserver=originalObserver;
});

test('hook commits all 144 loaded nodes only after two paged syncs and preserved latest GET finish',async t=>{
 await loaded(t);const previous=structuredClone(view.nodes);
 assert.equal(requests.length,3);assert.equal(requests[2].url,'/api/conversation/work-sync');
 assert.equal(requests[2].body.paged,true);assert.equal(Object.keys(requests[2].body.known).length,100);
 assert.equal(requests[2].body.windowMessageId,'message');assert.equal(Object.hasOwn(requests[2].body,'revision'),false);
 await complete(2,chunk(requests[2].body.known,{items:[node(0,'2')]}));
 assert.deepEqual(view.nodes,previous,'first response is not a partial commit');
 assert.equal(Object.keys(requests[3].body.known).length,44);assert.equal(requests[3].body.revision,'new');
 await complete(3,chunk(requests[3].body.known));assert.deepEqual(view.nodes,previous);
 const latestURL=new URL(requests[4].url,'http://fixture.invalid');
 assert.equal(latestURL.pathname,'/api/conversation/detail');
 for(const [key,value] of Object.entries({sessionId:'chat',part:'nodes',group:'turn@message',windowMessageId:'message',preserve:'true',revision:'new'}))assert.equal(latestURL.searchParams.get(key),value);
 assert.equal(latestURL.searchParams.has('before'),false);assert.equal(requests.length,5);
 await complete(4,tail([...rows().slice(-99),node(144)]));
 assert.deepEqual(view.nodes.map(row=>row.id),rows(145).map(row=>row.id));
 assert.equal(view.nodes[0].detailVersion,'2');assert.deepEqual(view.nodes[1].resultDetail,previous[1].resultDetail);
 assert.equal(view.before,'refreshed-oldest');assert.equal(view.busy,false);assert.equal(view.error,'');
 await tick(t);assert.equal(requests.length,5,'no automatic retry or polling after completion');
});

test('hook exposes a late sync error while retaining all previous nodes and cursor, without retry',async t=>{
 await loaded(t);const previous=structuredClone(view.nodes);
 await complete(2,chunk(requests[2].body.known,{items:[node(0,'2')]}));
 await complete(3,chunk(requests[3].body.known));
 await complete(4,tail(rows().slice(-100),{revision:'wrong'}));
 assert.deepEqual(view.nodes,previous);assert.equal(view.before,'oldest');assert.equal(view.busy,false);
 assert.equal(root.root.findAllByProps({role:'alert'}).length,1);assert.match(view.error,/Work/);
 await tick(t);assert.equal(requests.length,5);
});

test('closed and automatic offscreen groups perform no IO; initial and earlier load keep their existing paths',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});let observe;
 globalThis.IntersectionObserver=class{constructor(callback){observe=callback}observe(){}disconnect(){}};
 await act(async()=>{root=create(render({open:false,automatic:true}),{createNodeMock:()=>({})})});
 await tick(t);assert.equal(requests.length,0);
 await act(async()=>root.update(render({open:true,automatic:true})));await tick(t);assert.equal(requests.length,0);
 await act(async()=>observe([{isIntersecting:true}]));await tick(t);assert.equal(requests.length,1);
 const initial=new URL(requests[0].url,'http://fixture.invalid');assert.equal(initial.searchParams.get('part'),'nodes');
 assert.equal(initial.searchParams.has('preserve'),false);assert.equal(requests[0].body,null);
 await complete(0,{...snapshot(rows().slice(-100)),before:'older'});
 await act(async()=>view.earlier());const earlier=new URL(requests[1].url,'http://fixture.invalid');
 assert.equal(earlier.searchParams.get('before'),'older');assert.equal(earlier.searchParams.has('preserve'),false);
 await complete(1,{...snapshot(rows().slice(0,44)),before:null});assert.equal(view.nodes.length,144);
 await act(async()=>observe([{isIntersecting:false}]));
 await act(async()=>root.update(render({automatic:true,revision:'new'})));await tick(t);assert.equal(requests.length,2);
});

test('closing during paged sync aborts the old run and cannot append its late response',async t=>{
 await loaded(t);await complete(2,chunk(requests[2].body.known));
 await act(async()=>root.update(render({open:false,revision:'new'})));
 assert.equal(requests[3].options.signal.aborted,true);
 await complete(3,chunk(requests[3].body.known));await tick(t);
 assert.equal(requests.length,4,'aborted run cannot send latest GET');assert.equal(view.nodes.length,0);assert.equal(view.error,'');
});
