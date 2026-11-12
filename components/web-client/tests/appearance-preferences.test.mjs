import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {request as apiRequest} from '../src/api.js';
import {hasAppearanceIdentity} from '../src/appearance-composition.js';
import {
 appearancePreferenceMatches,
 applyAppearancePreferenceCommand,
 createAppearancePreferenceClient,
 effectivePreferenceValue,
 readAppearanceStorageRecord,
 reconcileAppearancePreferenceFeedback,
 reconcileAppearancePreferenceValues,
 sparseExecutionVisibilityPatch,
} from '../src/appearance-preferences.js';

const namespace='amplifier.appearance.v2:'+'a'.repeat(32);
const replacementNamespace='amplifier.appearance.v2:'+'b'.repeat(32);
const generation='document-1';
const globalValues={
 appearanceId:'reference',scheme:'system',layout:'balanced',decorations:true,density:'comfortable',
 interfaceDetail:'standard',conversationStatus:false,collapseInterimReplies:false,workStartView:'appearance',
 planPlacement:'composer',executionDetail:'standard',executionVisibility:{},executionMetrics:[],
};
const browserValues={...globalValues,scheme:'dark',layout:'work',executionVisibility:{step:false},executionMetrics:[]};
const copy=value=>structuredClone(value);

// Paired appearance.client.report schema: additional properties are forbidden.
// Namespace is deliberately absent; it only fences browser-local ownership.
const reportRequiredKeys=['generation','revision','storageVersion','activeScope','values','status'];
const reportAllowedKeys=new Set([...reportRequiredKeys,'clientId','commandId','error','renderedCompositionId']);
function validateReportArgs(args){
 assert.deepEqual(Object.keys(args).filter(key=>!reportAllowedKeys.has(key)),[],'Unexpected appearance.client.report keys');
 for(const key of reportRequiredKeys)assert.ok(Object.hasOwn(args,key),`Missing report field: ${key}`);
 assert.ok(['ready','stored','failed'].includes(args.status));
}

function memoryStorage(id=namespace,record){
 const values=new Map(),reads=[],writes=[],deletes=[];
 if(record!==undefined)values.set(id,JSON.stringify(record));
 return {
  getItem(key){reads.push(key);return values.has(key)?values.get(key):null},
  setItem(key,value){writes.push({key,value});values.set(key,value)},
  removeItem(key){deletes.push(key);values.delete(key)},
  setExternal(key,value){values.set(key,JSON.stringify(value))},
  raw(key=id){return values.has(key)?values.get(key):null},
  parsed(key=id){const value=values.has(key)?values.get(key):null;return value===null?null:JSON.parse(value)},
  get writes(){return writes.slice()},
  get reads(){return reads.slice()},
  get deletes(){return deletes.slice()},
 };
}

function makeHarness({
 id=namespace,documentGeneration=generation,activeScope='shared',revision=0,storageVersion='missing',
 localValues=null,sharedValues=globalValues,record,ackTimeoutMs=100,
}={}){
 let server={
  namespace:id,generation:documentGeneration,availability:'connected',activeScope,
  shared:{revision:7,values:copy(sharedValues)},
  local:{revision,storageVersion,values:copy(localValues),reported:false,error:null},
  command:null,
 };
 const storage=memoryStorage(id,record),lockCalls=[],reports=[],actions=[],statuses=[],listeners=new Map();
 const locks={request(name,options,operation){lockCalls.push({name,mode:options?.mode});return Promise.resolve().then(operation)}};
 const eventTarget={
  addEventListener(type,listener){const set=listeners.get(type)||new Set();set.add(listener);listeners.set(type,set)},
  removeEventListener(type,listener){listeners.get(type)?.delete(listener)},
  emit(event){for(const listener of listeners.get('storage')||[])listener(event)},
 };
 let version=0,loseNextStoredAck=false,reportHook=null;
 const seenStorageVersions=new Set(storageVersion==='missing'?[]:[storageVersion]);
 const dispatch=async(action,args={})=>{
  actions.push({action,args:copy(args)});
  if(action==='appearance.preferences.read')return {accepted:true,result:copy(server)};
  if(action!=='appearance.client.report')throw Error(`Unexpected action: ${action}`);
  validateReportArgs(args);
  reports.push(copy(args));
  if(args.generation!==server.generation)
   return {accepted:false,error:'The report belongs to an older document.'};
  if(reportHook){
   const receipt=await reportHook(args,copy(server));
   if(receipt!==undefined)return receipt;
  }
  if(args.status==='ready'){
   // The paired backend's ready path admits changed storage at a newer mirror
   // revision and forbids reusing observed tokens (including rollback ABA).
   const sameRecord=args.storageVersion===server.local.storageVersion&&
    args.activeScope===server.activeScope&&JSON.stringify(args.values)===JSON.stringify(server.local.values);
   if(args.revision<server.local.revision||server.local.reported&&args.revision===server.local.revision&&!sameRecord||
      seenStorageVersions.has(args.storageVersion)&&args.storageVersion!==server.local.storageVersion)
    return {accepted:false,error:'Stale browser storage report'};
   if(server.command?.status==='pending'&&args.revision>server.command.expectedRevision)
    server={...server,command:{...server.command,status:'superseded'}};
   server={...server,activeScope:args.activeScope,local:{...server.local,revision:args.revision,
    storageVersion:args.storageVersion,values:copy(args.values),reported:true,error:null}};
   if(args.storageVersion!=='missing')seenStorageVersions.add(args.storageVersion);
   return {accepted:true,result:{status:'reported'}};
  }
  if(args.status==='stored'&&args.commandId){
   assert.equal(server.command?.status,'pending');
   assert.equal(args.revision,server.command.expectedRevision+1);
   assert.equal(server.local.revision,server.command.expectedRevision);
   assert.equal(server.local.storageVersion,server.command.expectedStorageVersion);
   assert.deepEqual({activeScope:args.activeScope,values:args.values},server.command.after);
   assert.equal(seenStorageVersions.has(args.storageVersion),false);
   server={...server,activeScope:args.activeScope,local:{revision:args.revision,storageVersion:args.storageVersion,
    values:copy(args.values),reported:true,error:null},command:server.command?.id===args.commandId
     ?{...server.command,status:'stored',undoId:`undo:${args.commandId}`}:server.command};
   seenStorageVersions.add(args.storageVersion);
   if(loseNextStoredAck){loseNextStoredAck=false;throw Error('Stored ACK response was lost.');}
   return {accepted:true,result:{status:'stored',undoId:`undo:${args.commandId}`}};
  }
  if(args.status==='failed'&&args.commandId){
   if(server.command?.id===args.commandId)server={...server,command:{...server.command,status:'failed',error:args.error}};
   return {accepted:true,result:{status:'failed',error:args.error}};
  }
  if(args.status==='failed'){
   server={...server,local:{...server.local,error:args.error}};
   return {accepted:true,result:{status:'failed',error:args.error}};
  }
  throw Error(`Unexpected appearance report status: ${args.status}`);
 };
 const createClient=()=>createAppearancePreferenceClient({
  dispatch,storage,locks,eventTarget,createVersion:()=>`browser-version-${++version}`,ackTimeoutMs,
  onStatus:value=>statuses.push(copy(value)),
 });
 const client=createClient();
 return {
  client,createClient,dispatch,storage,locks,lockCalls,reports,actions,statuses,eventTarget,
  get server(){return copy(server)},
  setServer(value){
   if(value.generation!==server.generation||value.namespace!==server.namespace){
    seenStorageVersions.clear();
    if(value.local.storageVersion!=='missing')seenStorageVersions.add(value.local.storageVersion);
   }
   server=copy(value);
  },
  onReport(hook){reportHook=hook},
  loseStoredAck(){loseNextStoredAck=true},
  command(id,after,overrides={}){
   assert.notEqual(server.command?.status,'pending','A pending command blocks the next admission.');
   const command={id,namespace:server.namespace,generation:server.generation,
    expectedRevision:server.local.revision,expectedStorageVersion:server.local.storageVersion,
    after:copy(after),status:'pending',...overrides};
   server={...server,command};
   return copy(command);
  },
 };
}

async function connect(harness){
 await harness.client.observe(harness.server,{connected:true});
 await harness.client.waitUntilReady();
}

async function runCommand(harness,command){
 await harness.client.observe(harness.server,{connected:true});
 return harness.client.waitForCommand(command.id);
}

const flush=()=>new Promise(resolve=>setImmediate(resolve));

test('production identity requires the exact v2 namespace and a nonempty document generation, not an enable flag',()=>{
 assert.equal(hasAppearanceIdentity({namespace,generation}),true);
 assert.equal(hasAppearanceIdentity({namespace,generation,enabled:false}),true,'There is no production experiment gate.');
 for(const invalid of [undefined,null,'appearance-test','amplifier.appearance.v1:'+'a'.repeat(32),
  'amplifier.appearance-experiment-0801:'+'a'.repeat(32),'amplifier.appearance.v2:'+'a'.repeat(31),
  'amplifier.appearance.v2:'+'a'.repeat(33),'amplifier.appearance.v2:'+'A'.repeat(32),
  'amplifier.appearance.v2:'+'g'.repeat(32),namespace+'\n']){
  assert.equal(hasAppearanceIdentity({namespace:invalid,generation}),false,String(invalid));
 }
 for(const invalid of [undefined,null,'',0,1,{},[]]){
  assert.equal(hasAppearanceIdentity({namespace,generation:invalid}),false,String(invalid));
 }
});

test('production readiness reads only its record and leaves legacy and 0.80.1 profiles inert and undeleted',async()=>{
 const h=makeHarness(),retained=[
  'amplifier.appearance','amplifier.appearance.v1:'+'a'.repeat(32),
  'amplifier.appearance-experiment-0801:'+'a'.repeat(32),
 ];
 const old={version:'retained-old-version',activeScope:'client',values:browserValues};
 for(const key of retained)h.storage.setExternal(key,old);
 await connect(h);
 assert.equal(h.storage.reads.every(key=>key===namespace),true);
 assert.equal(h.storage.raw(),null,'Readonly initialization does not create a production browser record.');
 assert.deepEqual(h.reports.map(({generation,activeScope,values,status})=>({generation,activeScope,values,status})),
  [{generation,activeScope:'shared',values:null,status:'ready'}]);
 assert.deepEqual(h.storage.writes,[]);assert.deepEqual(h.storage.deletes,[]);
 for(const key of retained)assert.deepEqual(h.storage.parsed(key),old);
});

test('a stable production namespace retains complete browser values across a host restart and ACKs the new generation without rewriting',async()=>{
 const record={version:'retained-profile-v1',activeScope:'client',values:browserValues};
 const h=makeHarness({documentGeneration:'generation-A',record});
 await connect(h);
 assert.equal(h.server.activeScope,'client');assert.deepEqual(h.server.local.values,browserValues);
 const before=h.storage.raw();
 h.setServer({...h.server,generation:'generation-B',activeScope:'shared',command:null,
  local:{revision:0,storageVersion:'missing',values:null,reported:false,error:null}});
 await connect(h);
 assert.deepEqual(h.reports.map(report=>({generation:report.generation,storageVersion:report.storageVersion,
  activeScope:report.activeScope,values:report.values,status:report.status})),
  ['generation-A','generation-B'].map(generation=>({generation,storageVersion:record.version,
   activeScope:'client',values:browserValues,status:'ready'})));
 assert.equal(h.client.getSnapshot().acknowledged.generation,'generation-B');
 assert.equal(h.client.getSnapshot().namespace,namespace);
 assert.equal(h.server.activeScope,'client');assert.deepEqual(h.server.local.values,browserValues);
 assert.equal(h.storage.raw(),before);assert.deepEqual(h.storage.writes,[]);assert.deepEqual(h.storage.deletes,[]);
});

for(const appearance of [
 {availability:'failed',error:'The production record is malformed.'},
 {namespace,generation,availability:'disconnected'},
 {namespace:'amplifier.appearance-experiment-0801:'+'a'.repeat(32),generation,availability:'connected'},
 {namespace:replacementNamespace.toUpperCase(),generation,availability:'connected'},
 {namespace,generation:'',availability:'connected'},
])test(`unavailable or invalid production identity is inert before storage/report work: ${JSON.stringify(appearance)}`,async()=>{
 for(const warm of [false,true]){
  const h=makeHarness();
  if(warm)await connect(h);
  const before={reads:h.storage.reads.length,writes:h.storage.writes.length,deletes:h.storage.deletes.length,
   actions:h.actions.length,reports:h.reports.length};
  await h.client.observe(appearance);
  assert.equal(h.client.getSnapshot().connected,false);
  assert.deepEqual(await h.client.reportRendered('untrusted-composition'),{rendered:false});
  h.eventTarget.emit({key:namespace});h.client.storageChanged({key:namespace});
  assert.equal(await h.client.reconcile(),false);
  await flush();
  assert.deepEqual({reads:h.storage.reads.length,writes:h.storage.writes.length,deletes:h.storage.deletes.length,
   actions:h.actions.length,reports:h.reports.length},before,'Failure does not read, report, write, delete, or import a fallback.');
  if(warm)assert.equal(h.client.getStatus().status,'disconnected','Missing namespace must still disconnect a live controller.');
 }
});

test('an unavailable snapshot disconnects an in-flight ACK waiter without replaying or undoing an independently stored command',async()=>{
 const h=makeHarness();await connect(h);
 let release,markReported;
 const held=new Promise(resolve=>{release=resolve}),reported=new Promise(resolve=>{markReported=resolve});
 h.onReport(args=>{
  if(args.status!=='stored')return;
  h.setServer({...h.server,activeScope:args.activeScope,command:{...h.server.command,status:'stored',undoId:'held-undo'},
   local:{revision:args.revision,storageVersion:args.storageVersion,values:copy(args.values),reported:true,error:null}});
  markReported();return held;
 });
 const command=h.command('inflight-unavailable',{activeScope:'client',values:browserValues});
 const observing=h.client.observe(h.server,{connected:true});
 const waiting=h.client.waitForCommand(command.id);
 const rejected=assert.rejects(waiting,/disconnected.*No command was replayed/);
 await reported;
 const written=h.storage.raw(),reports=h.reports.length,reads=h.storage.reads.length;
 await h.client.observe({availability:'failed',error:'Malformed production record'});
 await rejected;
 release({accepted:true,result:{status:'stored',undoId:'held-undo'}});
 await observing;
 assert.equal(h.client.getSnapshot().connected,false);
 assert.equal(h.storage.raw(),written);assert.equal(h.storage.writes.length,1);
 assert.deepEqual(h.storage.deletes,[]);assert.equal(h.storage.reads.length,reads);assert.equal(h.reports.length,reports);
 h.onReport(null);await connect(h);
 assert.equal(h.client.getSnapshot().acknowledged.storageVersion,h.storage.parsed().version);
 assert.deepEqual(h.client.getSnapshot().acknowledged.values,browserValues);
 assert.equal(h.server.command.status,'stored');assert.equal(h.storage.writes.length,1);
 assert.equal(h.reports.filter(report=>report.commandId===command.id&&report.status==='stored').length,1);
 assert.equal(h.reports.some(report=>report.status==='failed'),false);
});

// Execute the actual main.jsx callbacks, as the existing state-presentation
// tests do, without installing React or copying the presentation implementation.
const mainSource=readFileSync(new URL('../src/main.jsx',import.meta.url),'utf8');
const callbackStart=mainSource.indexOf(' const readAppearanceSnapshot=useCallback(');
const callbackEnd=mainSource.indexOf(' useEffect(()=>{',mainSource.indexOf(' const updatePresentation=useCallback(',callbackStart));
assert.ok(callbackStart>0&&callbackEnd>callbackStart);
function mainPreferences(harness,dispatch=harness.dispatch,rendered=globalValues){
 return runInNewContext(`${mainSource.slice(callbackStart,callbackEnd)}\n({updatePresentation,updateAppearancePreference,selectAppearanceProfile,readAppearanceSnapshot,readAppearancePreferences})`,{
  useCallback:fn=>fn,clientId:'test-client',dispatch,sparseExecutionVisibilityPatch,appearancePreferenceMatches,effectivePreferenceValue,hasAppearanceIdentity,
  state:{appearance:{namespace,generation,availability:'connected',renderedPreferences:copy(rendered),effective:copy(globalValues)}},appearancePreferenceClient:{current:harness.client},
 });
}

function mainAppearanceRefresh(readAppearancePreferences,latest,setAppearancePreferenceStatus){
 const start=mainSource.indexOf(' useEffect(()=>{\n  const refresh=()=>{');
 const end=mainSource.indexOf(' },[readAppearancePreferences]);',start);
 assert.ok(start>0&&end>start,'Exercise the actual main refresh effect and its rejection handler.');
 const listeners=new Map();
 const cleanup=runInNewContext(mainSource.slice(start,end+' },[readAppearancePreferences]);'.length),{
  useEffect:fn=>fn(),readAppearancePreferences,latest,setAppearancePreferenceStatus,hasAppearanceIdentity,
  window:{addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)},
 });
 return {listeners,cleanup};
}
function heldRead(h){
 let release,started;
 const response=new Promise(resolve=>{release=resolve}),requested=new Promise(resolve=>{started=resolve});
 let hold=true;
 const dispatch=(action,args)=>{
  if(action==='appearance.preferences.read'&&hold){hold=false;started();return response}
  return h.dispatch(action,args);
 };
 return {dispatch,requested,release};
}
function replaceAppearanceOwner(h,replaced){
 h.setServer({...h.server,namespace:replaced==='namespace'?replacementNamespace:namespace,generation:'generation-B',
  activeScope:'shared',command:null,local:{revision:0,storageVersion:'missing',values:null,reported:false,error:null}});
}
const delayedAppearance=(old,outcome)=>outcome==='valid'?old:outcome==='failed'
 ?{...old,availability:'failed',error:'Delayed generation-A record is malformed.'}
 :{availability:'failed',error:'Delayed generation-A identity is unavailable.'};

for(const replaced of ['generation','namespace'])for(const outcome of ['valid','failed','identityless']){
 test(`controller delayed ${outcome} A read cannot poison replacement B ${replaced} readiness or emit an old report`,async()=>{
  const h=makeHarness();await connect(h);
  const old=h.server,held=heldRead(h),statusStart=h.statuses.length,reports=h.reports.length;
  h.client.setDispatch(held.dispatch);
  const pending=h.client.reconcile();await held.requested;
  replaceAppearanceOwner(h,replaced);
  const replacement=h.client.observe(h.server,{connected:true});
  held.release({accepted:true,result:delayedAppearance(old,outcome)});
  assert.equal(await pending,false,'The old read cannot reconcile a replacement owner.');
  await replacement;await h.client.waitUntilReady();
  assert.equal(h.client.getSnapshot().connected,true);
  assert.equal(h.client.getSnapshot().acknowledged.namespace,h.server.namespace);
  assert.equal(h.client.getSnapshot().acknowledged.generation,'generation-B');
  assert.equal(h.client.getStatus().status,'ready');
  assert.equal(h.statuses.slice(statusStart).some(status=>['failed','disconnected'].includes(status.status)),false);
  assert.deepEqual(h.reports.slice(reports).map(({generation,status})=>({generation,status})),
   [{generation:'generation-B',status:'ready'}],'No stale ready/failed report is emitted for A.');
  assert.deepEqual(h.storage.writes,[]);assert.deepEqual(h.storage.deletes,[]);
  const stable=h.client.getSnapshot(),readCount=h.storage.reads.length,reportCount=h.reports.length;
  await flush();await h.client.waitUntilReady();
  assert.deepEqual(h.client.getSnapshot(),stable);assert.equal(h.storage.reads.length,readCount);
  assert.equal(h.reports.length,reportCount);
 });

 test(`main delayed ${outcome} A read is superseded after B ${replaced} is ready without stale refresh failure`,async()=>{
  const h=makeHarness();await connect(h);
  const old=h.server,held=heldRead(h),api=mainPreferences(h,held.dispatch);
  const pending=api.readAppearancePreferences();await held.requested;
  const latest={current:{appearance:old}},refreshStatuses=[];
  const refresh=mainAppearanceRefresh(()=>pending,latest,status=>refreshStatuses.push(status));
  refresh.listeners.get('amplifier-reconnected')();
  const rejected=assert.rejects(pending,error=>{
   assert.equal(error.appearanceSuperseded,true);assert.match(error.message,/document changed/);return true;
  });
  replaceAppearanceOwner(h,replaced);await connect(h);latest.current.appearance=h.server;
  const before={snapshot:h.client.getSnapshot(),status:h.client.getStatus(),server:h.server,
   reports:h.reports.length,reads:h.storage.reads.length,actions:h.actions.length};
  held.release({accepted:true,result:delayedAppearance(old,outcome)});
  await rejected;await flush();await h.client.waitUntilReady();
  assert.deepEqual({snapshot:h.client.getSnapshot(),status:h.client.getStatus(),server:h.server,
   reports:h.reports.length,reads:h.storage.reads.length,actions:h.actions.length},before,
   'B retains its exact ready ACK; A cannot observe, disconnect, report or touch storage.');
  assert.deepEqual(refreshStatuses,[],'The actual refresh catch ignores a superseded read.');
  assert.deepEqual(h.storage.writes,[]);assert.deepEqual(h.storage.deletes,[]);
  refresh.cleanup?.();
 });
}

for(const path of ['controller','main'])test(`a current invalid ${path} read disconnects without storage IO or a failed report`,async()=>{
 const h=makeHarness();await connect(h);
 const held=heldRead(h),before={reads:h.storage.reads.length,reports:h.reports.length};
 h.client.setDispatch(held.dispatch);
 const api=mainPreferences(h,held.dispatch);
 const pending=path==='controller'?h.client.reconcile():api.readAppearancePreferences();
 const failed=path==='main'?assert.rejects(pending,error=>{
  assert.equal(error.appearanceSuperseded,undefined);assert.match(error.message,/Current record is invalid/);return true;
 }):null;
 await held.requested;
 held.release({accepted:true,result:{availability:'failed',error:'Current record is invalid.'}});
 if(failed)await failed;else assert.equal(await pending,false);
 assert.equal(h.client.getSnapshot().connected,false);assert.equal(h.client.getStatus().status,'disconnected');
 assert.equal(h.storage.reads.length,before.reads);assert.equal(h.reports.length,before.reports);
 assert.deepEqual(h.storage.writes,[]);assert.deepEqual(h.storage.deletes,[]);
 await assert.rejects(h.client.waitUntilReady(),/not connected/);
});

function commandFor(record,after,id='change'){
 return {id,namespace,generation,expectedRevision:4,expectedStorageVersion:record.version,after,status:'pending'};
}

test('main rejects failed or foreign production reads before admitting any Global write, selection, shell change or browser IO',async()=>{
 for(const result of [
  {availability:'failed',error:'Malformed production appearance record.'},
  {namespace:'amplifier.appearance-experiment-0801:'+'a'.repeat(32),generation,availability:'connected'},
 ]){
  const h=makeHarness(),calls=[];
  const dispatch=async(action,args)=>{
   calls.push({action,args:copy(args)});
   assert.equal(action,'appearance.preferences.read','A failed read cannot initialize or overwrite either profile.');
   return {accepted:true,result};
  };
  await assert.rejects(mainPreferences(h,dispatch).updateAppearancePreference('scheme','dark',{scope:'shared'}),
   /Malformed production appearance record|unavailable.*Retry the connection/);
  assert.deepEqual(calls,[{action:'appearance.preferences.read',args:{}}]);
  assert.deepEqual(h.storage.reads,[]);assert.deepEqual(h.storage.writes,[]);assert.deepEqual(h.storage.deletes,[]);
  assert.deepEqual(h.reports,[]);
 }
});

test('complete profile normalization keeps implicit defaults and optional accent absence',()=>{
 assert.deepEqual(effectivePreferenceValue('executionVisibility',{}),{step:true,turn:true,conversation:true});
 assert.deepEqual(effectivePreferenceValue('executionMetrics',{}),[]);
 assert.equal(effectivePreferenceValue('accent',{}),undefined);
 assert.equal(Object.hasOwn(globalValues,'accent'),false);
 assert.equal(appearancePreferenceMatches('executionVisibility',{step:false},{step:false,turn:true,conversation:true}),true);
 assert.deepEqual(sparseExecutionVisibilityPatch({step:false,turn:true,conversation:true},{step:true,turn:true,conversation:true}),{step:false});
 assert.deepEqual(sparseExecutionVisibilityPatch({step:true},{step:true}),{});
});

test('accent deletion null and optional absence compare equally without equating explicit colors',()=>{
 for(const left of [null,undefined])for(const right of [null,undefined]){
  assert.equal(appearancePreferenceMatches('accent',left,right),true);
 }
 for(const absent of [null,undefined]){
  assert.equal(appearancePreferenceMatches('accent',absent,'#334455'),false);
  assert.equal(appearancePreferenceMatches('accent','#334455',absent),false);
 }
 assert.equal(appearancePreferenceMatches('accent','#334455','#334455'),true);
 assert.equal(appearancePreferenceMatches('accent','#334455','#445566'),false);
 assert.equal(appearancePreferenceMatches('scheme',null,undefined),false,'Null equivalence is specific to the optional accent.');
});

test('localStorage accepts one atomic profile record and rejects legacy sparse formats',()=>{
 const absent=memoryStorage();
 assert.deepEqual(readAppearanceStorageRecord(absent,namespace),{version:'missing',activeScope:'shared',values:null});
 const valid=memoryStorage(namespace,{version:'record-1',activeScope:'client',values:browserValues});
 assert.deepEqual(readAppearanceStorageRecord(valid,namespace),{version:'record-1',activeScope:'client',values:browserValues});
 for(const legacy of [
  {version:'old',appearanceId:'reference',overrides:{scheme:'dark'}},
  {version:'old',activeScope:'shared',values:null,overrides:{scheme:'dark'}},
  {version:'old',activeScope:'client',values:null},
 ]){
  const storage=memoryStorage(namespace,legacy);
  assert.throws(()=>readAppearanceStorageRecord(storage,namespace),/invalid/);
 }
});

test('storage applies exact admitted records for selection, inactive edits, atomic edit/select and Undo; rejects ABA',()=>{
 const initial={version:'missing',activeScope:'shared',values:null};
 const select={id:'select-client',namespace,generation,expectedRevision:4,expectedStorageVersion:'missing',
  after:{activeScope:'client',values:globalValues},status:'pending'};
 assert.deepEqual(Object.keys(select).sort(),['after','expectedRevision','expectedStorageVersion','generation','id','namespace','status']);
 const first=applyAppearancePreferenceCommand(initial,select,()=> 'record-1');
 assert.deepEqual(first,{version:'record-1',activeScope:'client',values:globalValues});
 const switchGlobal={...select,id:'select-global',expectedStorageVersion:'record-1',
  after:{activeScope:'shared',values:globalValues}};
 const retained=applyAppearancePreferenceCommand(first,switchGlobal,()=> 'record-2');
 assert.deepEqual(retained,{version:'record-2',activeScope:'shared',values:globalValues});
 const editInactive=commandFor(retained,{activeScope:'shared',values:browserValues});
 assert.deepEqual(applyAppearancePreferenceCommand(retained,editInactive,()=> 'record-3'),
  {version:'record-3',activeScope:'shared',values:browserValues});
 const editAndSelect=commandFor(retained,{activeScope:'client',values:browserValues});
 assert.deepEqual(applyAppearancePreferenceCommand(retained,editAndSelect,()=> 'record-4'),
  {version:'record-4',activeScope:'client',values:browserValues});
 assert.deepEqual(applyAppearancePreferenceCommand(first,commandFor(first,{activeScope:'shared',values:null},'undo-first'),()=> 'record-5'),
  {version:'record-5',activeScope:'shared',values:null});
 assert.throws(()=>applyAppearancePreferenceCommand(retained,{...editInactive,expectedStorageVersion:'stale'},()=> 'record-6'),/Another browser tab/);
 assert.throws(()=>applyAppearancePreferenceCommand(retained,commandFor(retained,{activeScope:'client',values:null}),()=> 'record-6'),/invalid/);
 assert.throws(()=>applyAppearancePreferenceCommand(retained,editInactive,()=> 'record-2'),/new browser preference version/);
});

test('client reports absent storage as Global/null and waits for its generation-bound readiness receipt',async()=>{
 const harness=makeHarness();
 await connect(harness);
 assert.deepEqual(harness.reports,[{generation,revision:0,storageVersion:'missing',activeScope:'shared',values:null,status:'ready'}]);
 assert.deepEqual(harness.client.getSnapshot().acknowledged,{namespace,generation,revision:0,storageVersion:'missing',activeScope:'shared',values:null});
 assert.equal(harness.storage.writes.length,0);
 assert.equal(harness.lockCalls.every(item=>item.name===`amplifier.appearance:${namespace}`&&item.mode==='exclusive'),true);
});

test('strict report wire accepts ready, stored, failed and rendered ACKs without local namespace',async()=>{
 const h=makeHarness();
 await connect(h);
 assert.throws(()=>validateReportArgs({...h.reports[0],namespace}),/Unexpected appearance.client.report keys/);
 await h.client.reportRendered('composition-1');
 assert.deepEqual(h.reports.at(-1),{
  generation,revision:0,storageVersion:'missing',activeScope:'shared',values:null,status:'ready',renderedCompositionId:'composition-1',
 });
 const stored=h.command('wire-stored',{activeScope:'client',values:browserValues});
 await runCommand(h,stored);
 h.storage.setExternal(namespace,{version:'newer-tab',activeScope:'client',values:browserValues});
 const failed=h.command('wire-failed',{activeScope:'client',values:globalValues});
 await assert.rejects(runCommand(h,failed),/Another browser tab changed/);
 assert.deepEqual([...new Set(h.reports.map(report=>report.status))].sort(),['failed','ready','stored']);
 assert.equal(h.reports.at(-1).commandId,failed.id);
 assert.equal(h.reports.every(report=>!Object.hasOwn(report,'namespace')),true);
 assert.equal(h.client.getSnapshot().namespace,namespace,'The ownership fence remains local.');
});

test('unavailable storage reports a failed readiness ACK using the strict wire',async()=>{
 const h=makeHarness();
 h.storage.getItem=()=>{throw Error('Browser storage is unavailable')};
 await h.client.observe(h.server,{connected:true});
 await assert.rejects(h.client.waitUntilReady(),/Browser storage is unavailable/);
 assert.deepEqual(h.reports,[{
  generation,revision:0,storageVersion:'missing',activeScope:'shared',values:null,
  status:'failed',error:'Browser storage is unavailable',
 }]);
 assert.equal(h.storage.writes.length,0);
});

test('first This browser selection writes the complete admitted profile and subsequent switches retain it',async()=>{
 const harness=makeHarness();
 await connect(harness);
 const copyCommand=harness.command('copy-global',{activeScope:'client',values:globalValues});
 const copied=await runCommand(harness,copyCommand);
 assert.equal(copied.status,'stored');
 assert.deepEqual(harness.storage.parsed(),{version:'browser-version-1',activeScope:'client',values:globalValues});
 assert.deepEqual(Object.keys(harness.reports.at(-1)).sort(),['activeScope','commandId','generation','revision','status','storageVersion','values']);
 const edited={...globalValues,scheme:'dark',layout:'work',executionVisibility:{step:false}};
 const editCommand=harness.command('edit-local',{activeScope:'client',values:edited});
 await runCommand(harness,editCommand);
 const switched=harness.command('select-global',{activeScope:'shared',values:edited});
 await runCommand(harness,switched);
 assert.deepEqual(harness.storage.parsed(),{version:'browser-version-3',activeScope:'shared',values:edited});
 const writeCount=harness.storage.writes.length;
 harness.setServer({...harness.server,shared:{revision:8,values:{...globalValues,scheme:'light'}},command:null});
 await harness.client.observe(harness.server,{connected:true});
 assert.equal(harness.storage.writes.length,writeCount,'A Global update never writes the browser profile.');
 assert.deepEqual(harness.storage.parsed().values,edited,'The retained browser profile remains independent of Global.');
 const returned=harness.command('select-browser',{activeScope:'client',values:edited});
 await runCommand(harness,returned);
 assert.deepEqual(harness.storage.parsed(),{version:'browser-version-4',activeScope:'client',values:edited});
 harness.client.disconnect();
 const reloaded=harness.createClient();
 await reloaded.observe(harness.server,{connected:true});
 await reloaded.waitUntilReady();
 assert.deepEqual(reloaded.getSnapshot().acknowledged,{namespace,generation,revision:4,storageVersion:'browser-version-4',activeScope:'client',values:edited});
 assert.deepEqual(harness.storage.parsed().values,edited);
});

test('storage notifications reread the actual atomic record instead of trusting a stale event payload',async()=>{
 const harness=makeHarness();
 await connect(harness);
 const external={version:'other-tab-v1',activeScope:'client',values:browserValues};
 harness.storage.setExternal(namespace,external);
 harness.eventTarget.emit({key:namespace,newValue:JSON.stringify({version:'stale-event',activeScope:'shared',values:null})});
 await flush();
 await flush();
 assert.equal(harness.server.local.storageVersion,external.version);
 assert.equal(harness.server.activeScope,'client');
 assert.deepEqual(harness.server.local.values,browserValues);
 assert.equal(harness.reports.at(-1).storageVersion,external.version);
 assert.deepEqual(harness.reports.at(-1).values,browserValues);
 assert.equal(harness.storage.writes.length,0);
});

test('stale storage versions, generations and same-generation foreign namespaces are rejected locally',async()=>{
 const staleStorage=makeHarness();
 await connect(staleStorage);
 staleStorage.storage.setExternal(namespace,{version:'other-tab-v1',activeScope:'shared',values:null});
 const pending=staleStorage.command('stale-storage',{activeScope:'client',values:globalValues});
 await staleStorage.client.observe(staleStorage.server,{connected:true});
 await assert.rejects(staleStorage.client.waitForCommand(pending.id),/Another browser tab changed/);
 assert.equal(staleStorage.storage.writes.length,0);
 assert.deepEqual(staleStorage.storage.parsed(),{version:'other-tab-v1',activeScope:'shared',values:null});
 assert.equal(staleStorage.server.command.status,'failed');

 const staleGeneration=makeHarness({documentGeneration:'document-2'});
 const oldCommand={id:'old-generation',namespace,generation:'document-1',expectedRevision:0,expectedStorageVersion:'missing',
  after:{activeScope:'client',values:globalValues},status:'pending'};
 staleGeneration.setServer({...staleGeneration.server,command:oldCommand});
 await staleGeneration.client.observe(staleGeneration.server,{connected:true});
 await assert.rejects(staleGeneration.client.waitForCommand(oldCommand.id),/older document/);
 assert.equal(staleGeneration.storage.writes.length,0);

 const foreignNamespace=makeHarness();
 await connect(foreignNamespace);
 const foreignCommand=foreignNamespace.command('foreign-namespace',{activeScope:'client',values:globalValues},{namespace:replacementNamespace});
 await foreignNamespace.client.observe(foreignNamespace.server,{connected:true});
 await assert.rejects(foreignNamespace.client.waitForCommand(foreignCommand.id),/older document/);
 assert.equal(foreignNamespace.storage.writes.length,0);
 assert.equal(foreignNamespace.reports.some(report=>report.commandId===foreignCommand.id),false,'Foreign namespace commands never reach the report wire.');
});

test('lost stored acknowledgement is reconciled from the saved receipt without replaying storage',async()=>{
 const harness=makeHarness();
 await connect(harness);
 harness.loseStoredAck();
 const command=harness.command('lost-ack',{activeScope:'client',values:browserValues});
 const receipt=await runCommand(harness,command);
 assert.equal(receipt.status,'stored');
 assert.equal(harness.server.command.status,'stored');
 assert.deepEqual(harness.storage.parsed(),{version:'browser-version-1',activeScope:'client',values:browserValues});
 assert.equal(harness.storage.writes.length,1,'The uncertain command is never replayed.');
 assert.equal(harness.reports.filter(report=>report.commandId===command.id&&report.status==='stored').length,1);
});

for(const initialized of [false,true])test(`rejected shell storage ACK restores confirmed Global with ${initialized?'retained':'absent'} local, converges on reload and releases pending command`,async()=>{
 const record=initialized?{version:'retained-v1',activeScope:'shared',values:browserValues}:undefined;
 const h=makeHarness({localValues:initialized?browserValues:null,storageVersion:record?.version||'missing',record});
 await connect(h);
 const before=h.server,changedGlobal={...globalValues,scheme:'light'};
 h.onReport(args=>{
  if(args.status!=='stored')return;
  // Actual valid race: apply queued the atomic command, storage has written,
  // then Global moves past the shell's prepared preference fence before ACK.
  assert.equal(h.storage.parsed().activeScope,'client');
  h.setServer({...h.server,shared:{revision:before.shared.revision+1,values:changedGlobal}});
  return {accepted:false,error:'Shared appearance preferences changed after preparation.'};
 });
 const dispatch=async(action,args)=>{
  if(action==='shell.inspect')return {accepted:true,result:{revision:23,composition:{instances:[],presentation:{}}}};
  if(action==='shell.changes.prepare')return {accepted:true,result:{id:'stale-preparation'}};
  if(action==='shell.changes.apply'){
   h.command('stale-shell-storage',{activeScope:'client',values:{...(initialized?browserValues:globalValues),layout:'conversation'}});
   return {accepted:true,result:{status:'awaiting-storage',commandId:'stale-shell-storage'}};
  }
  return h.dispatch(action,args);
 };
 await assert.rejects(mainPreferences(h,dispatch).updatePresentation({layout:'conversation'}),/Shared appearance preferences changed/);
 const restored=h.storage.parsed();
 assert.equal(restored.activeScope,'shared');
 assert.deepEqual(restored.values,initialized?browserValues:null);
 assert.notEqual(restored.version,'browser-version-1');
 assert.notEqual(restored.version,before.local.storageVersion,'Rollback never reuses the base token.');
 assert.equal(h.storage.writes.length,2,'One command write and one conditional rollback.');
 assert.equal(h.server.command.status,'failed');
 assert.equal(h.server.activeScope,'shared');assert.deepEqual(h.server.shared.values,changedGlobal);
 assert.equal(h.server.local.storageVersion,restored.version);
 assert.deepEqual(h.server.local.values,restored.values);
 const failed=h.reports.find(report=>report.commandId==='stale-shell-storage'&&report.status==='failed');
 assert.deepEqual({...failed,error:undefined},{
  generation,revision:before.local.revision,storageVersion:before.local.storageVersion,
  activeScope:'shared',values:before.local.values,status:'failed',commandId:'stale-shell-storage',error:undefined,
 },'Failed ACK uses the confirmed server base, not the rolled-back fresh record.');
 for(let attempt=0;attempt<3;attempt++)assert.equal(await h.client.reconcile(),true);
 assert.equal(h.reports.filter(report=>report.commandId==='stale-shell-storage'&&report.status==='stored').length,1,
  'A definitive rejection is never retried as stored.');
 assert.equal(h.storage.writes.length,2);
 h.client.disconnect();
 h.setServer({...h.server,generation:'reloaded-document',activeScope:'shared',command:null,
  local:{revision:0,storageVersion:'missing',values:null,reported:false,error:null}});
 const reloaded=h.createClient();
 await reloaded.observe(h.server,{connected:true});await reloaded.waitUntilReady();
 assert.equal(h.server.activeScope,'shared');assert.deepEqual(h.server.local.values,restored.values);
 assert.deepEqual(h.storage.parsed(),restored);
 h.onReport(null);
 const next=h.command('next-command',{activeScope:'client',values:changedGlobal});
 await reloaded.observe(h.server,{connected:true});
 assert.equal((await reloaded.waitForCommand(next.id)).status,'stored','A later command is usable.');
});

test('rejected ACK never overwrites a newer tab record, and ready reconciliation admits that newer record',async()=>{
 const h=makeHarness();await connect(h);
 const newer={version:'newer-tab-v1',activeScope:'client',values:{...browserValues,scheme:'light'}};
 h.onReport(args=>{
  if(args.status!=='stored')return;
  h.storage.setExternal(namespace,newer);
  return {accepted:false,error:'Prepared preference composition is stale.'};
 });
 const command=h.command('rejected-after-new-tab',{activeScope:'client',values:globalValues});
 await assert.rejects(runCommand(h,command),/Prepared preference composition is stale/);
 assert.deepEqual(h.storage.parsed(),newer);assert.equal(h.storage.writes.length,1);
 assert.equal(h.server.command.status,'failed');
 assert.equal(h.server.local.storageVersion,newer.version);assert.deepEqual(h.server.local.values,newer.values);
 assert.equal(await h.client.reconcile(),true);
 assert.equal(h.reports.filter(report=>report.commandId===command.id&&report.status==='stored').length,1);
});

test('definitive rejection restores the confirmed browser profile without falling back to Global',async()=>{
 const record={version:'active-browser-v1',activeScope:'client',values:browserValues};
 const h=makeHarness({activeScope:'client',localValues:browserValues,storageVersion:record.version,record});
 await connect(h);
 h.onReport(args=>{
  if(args.status==='stored')return {accepted:false,error:'Prepared preference composition is stale.'};
 });
 const command=h.command('rejected-browser-edit',{activeScope:'client',values:{...browserValues,scheme:'light'}});
 await assert.rejects(runCommand(h,command),/Prepared preference composition is stale/);
 assert.equal(h.server.activeScope,'client');assert.deepEqual(h.server.local.values,browserValues);
 assert.equal(h.storage.parsed().activeScope,'client');assert.deepEqual(h.storage.parsed().values,browserValues);
 assert.notEqual(h.storage.parsed().version,record.version);
 assert.equal(h.server.command.status,'failed');assert.equal(h.storage.writes.length,2);
});

test('known rejection restores storage even when settlement reads are unavailable, then converges without another stored ACK',async()=>{
 const h=makeHarness();await connect(h);
 let unavailable=false;
 h.client.setDispatch((action,args)=>{
  if(action==='appearance.preferences.read'&&unavailable)throw Error('Settlement read unavailable');
  return h.dispatch(action,args);
 });
 h.onReport(args=>{
  if(args.status==='stored'){unavailable=true;return {accepted:false,error:'Prepared preference composition is stale.'}}
 });
 const command=h.command('rejected-with-unavailable-read',{activeScope:'client',values:browserValues});
 await assert.rejects(runCommand(h,command),/Prepared preference composition is stale/);
 assert.equal(h.server.command.status,'pending','Unconfirmed settlement is retained, not invented.');
 assert.equal(h.storage.parsed().activeScope,'shared');assert.equal(h.storage.parsed().values,null);
 assert.equal(h.storage.writes.length,2);
 unavailable=false;
 assert.equal(await h.client.reconcile(),true);
 assert.equal(h.server.command.status,'failed');assert.equal(h.server.activeScope,'shared');
 assert.equal(h.server.local.storageVersion,h.storage.parsed().version);
 assert.equal(h.reports.filter(report=>report.commandId===command.id&&report.status==='stored').length,1);
 assert.equal(h.storage.writes.length,2);
});

// Time out the stored report while its original admission is still in flight.
// Reads stay unavailable until each test explicitly permits network recovery.
async function timedOutStoredReport(h,id,admission=promise=>promise){
 await connect(h);
 let release;
 const response=new Promise(resolve=>{release=resolve});
 h.onReport(args=>{if(args.status==='stored')return admission(response)});
 let unavailable=false;
 h.client.setDispatch((action,args)=>{
  if(action==='appearance.preferences.read'&&unavailable)throw Error('Settlement read unavailable');
  if(action==='appearance.client.report'&&args.status==='stored')unavailable=true;
  return h.dispatch(action,args);
 });
 const command=h.command(id,{activeScope:'client',values:browserValues});
 await assert.rejects(runCommand(h,command),/activation was not confirmed/);
 assert.equal(h.storage.parsed().activeScope,'client');assert.equal(h.storage.writes.length,1);
 assert.equal(h.server.command.status,'pending');
 return {command,release,allowReads(){unavailable=false}};
}

for(const admission of ['receipt','failed-result','http-200','http-409'])test(`late negative stored ACK (${admission}) rolls back without reads, then settles failed/ready and reloads Global`,async t=>{
 const h=makeHarness({ackTimeoutMs:10});
 const originalFetch=globalThis.fetch;
 t.after(()=>{globalThis.fetch=originalFetch});
 const held=await timedOutStoredReport(h,'late-negative',promise=>{
  if(admission==='receipt'||admission==='failed-result')return promise;
  globalThis.fetch=async()=>{await promise;return new Response(JSON.stringify({accepted:false,error:'Late admission refused.'}),{
   status:admission==='http-200'?200:409,headers:{'content-type':'application/json'},
  })};
  return apiRequest('/api/actions',{method:'POST',body:{action:'appearance.client.report'}});
 });
 held.release(admission==='failed-result'?{accepted:true,result:{status:'failed',error:'Late admission refused.'}}
  :{accepted:false,error:'Late admission refused.'});
 const locksBefore=h.lockCalls.length;
 await flush();
 const restored=h.storage.parsed();
 assert.deepEqual(restored,{version:'browser-version-2',activeScope:'shared',values:null},'The late result itself schedules rollback.');
 assert.deepEqual(h.lockCalls.slice(locksBefore),[{name:`amplifier.appearance:${namespace}`,mode:'exclusive'}]);
 assert.equal(await h.client.reconcile(),false,'Reads are still unavailable, not fabricated.');
 assert.equal(h.storage.writes.length,2);
 assert.equal(h.server.command.status,'pending');
 held.allowReads();
 assert.equal(await h.client.reconcile(),true);
 assert.equal(h.server.command.status,'failed');assert.equal(h.server.activeScope,'shared');
 assert.deepEqual(h.reports.filter(report=>report.commandId===held.command.id).map(report=>report.status),['stored','failed']);
 assert.equal(h.reports.at(-1).status,'ready');assert.equal(h.server.local.storageVersion,restored.version);
 await assert.rejects(h.client.waitForCommand(held.command.id),/Late admission refused/);
 h.client.disconnect();
 h.setServer({...h.server,generation:'reload-after-late-rejection',activeScope:'shared',command:null,
  local:{revision:0,storageVersion:'missing',values:null,reported:false,error:null}});
 const reloaded=h.createClient();
 await reloaded.observe(h.server,{connected:true});await reloaded.waitUntilReady();
 assert.equal(h.server.activeScope,'shared');assert.equal(h.server.local.values,null);
 assert.deepEqual(h.storage.parsed(),restored);assert.equal(h.storage.writes.length,2);
});

test('known late negative ACK bounds a never-settling proof read, rolls back and reloads the confirmed profile',async t=>{
 const h=makeHarness({ackTimeoutMs:10});
 const held=await timedOutStoredReport(h,'late-negative-stalled-read');
 t.mock.timers.enable({apis:['setTimeout']});
 let reads=0;
 h.client.setDispatch((action,args)=>{
  if(action==='appearance.preferences.read'){reads++;return new Promise(()=>{})}
  return h.dispatch(action,args);
 });
 held.release({accepted:false,error:'Late admission refused.'});await flush();
 assert.equal(reads,1);assert.equal(h.storage.writes.length,1,'Stored proof gets the bounded first opportunity.');
 t.mock.timers.tick(9);await flush();assert.equal(h.storage.writes.length,1);
 t.mock.timers.tick(1);await flush();
 const restored={version:'browser-version-2',activeScope:'shared',values:null};
 assert.deepEqual(h.storage.parsed(),restored,'A never-settling read cannot retain a known rejected write.');
 assert.equal(h.server.command.status,'pending','Timeout does not fabricate server settlement.');
 h.client.setDispatch(h.dispatch);
 assert.equal(await h.client.reconcile(),true);
 assert.equal(h.server.command.status,'failed');assert.equal(h.server.local.storageVersion,restored.version);
 assert.deepEqual(h.reports.filter(report=>report.commandId===held.command.id).map(report=>report.status),['stored','failed']);
 h.client.disconnect();
 h.setServer({...h.server,generation:'reload-after-stalled-proof',command:null,
  local:{revision:0,storageVersion:'missing',values:null,reported:false,error:null}});
 const reloaded=h.createClient();
 await reloaded.observe(h.server,{connected:true});await reloaded.waitUntilReady();
 assert.equal(h.server.activeScope,'shared');assert.equal(h.server.local.values,null);
 assert.deepEqual(h.storage.parsed(),restored);assert.equal(h.storage.writes.length,2);
});

for(const changed of ['none','namespace','generation'])test(`proof returned after its bounded read cannot mutate settled recovery with ${changed} identity change`,async t=>{
 const h=makeHarness({ackTimeoutMs:10});
 const held=await timedOutStoredReport(h,'late-proof');
 const written=h.storage.parsed(),old=h.server;
 const proof={...old,activeScope:written.activeScope,
  command:{...old.command,status:'stored',undoId:'late-proof-undo'},
  local:{revision:1,storageVersion:written.version,values:written.values,reported:true,error:null}};
 let releaseProof;
 const response=new Promise(resolve=>{releaseProof=resolve});
 t.mock.timers.enable({apis:['setTimeout']});
 h.client.setDispatch((action,args)=>action==='appearance.preferences.read'?response:h.dispatch(action,args));
 held.release({accepted:false,error:'Late admission refused.'});await flush();
 t.mock.timers.tick(10);await flush();
 assert.equal(h.storage.parsed().activeScope,'shared','Known refusal recovers at the proof deadline.');
 h.client.setDispatch(h.dispatch);
 if(changed==='none')assert.equal(await h.client.reconcile(),true);
 else{
  h.setServer({...h.server,namespace:changed==='namespace'?replacementNamespace:namespace,
   generation:changed==='generation'?'replacement-generation':generation,command:null,
   local:{revision:0,storageVersion:'missing',values:null,reported:false,error:null}});
  await h.client.observe(h.server,{connected:true});await h.client.waitUntilReady();
 }
 const before={server:h.server,snapshot:h.client.getSnapshot(),status:h.client.getStatus(),
  reports:h.reports.length,writes:h.storage.writes.length,record:h.storage.parsed()};
 releaseProof({accepted:true,result:proof});await flush();await flush();
 assert.deepEqual({server:h.server,snapshot:h.client.getSnapshot(),status:h.client.getStatus(),
  reports:h.reports.length,writes:h.storage.writes.length,record:h.storage.parsed()},before,
  'A timed-out read has no late continuation that can revive the old command or owner.');
});

test('a stalled proof read with an unknown ACK outcome never implies refusal or rollback',async t=>{
 const h=makeHarness({ackTimeoutMs:10});
 await timedOutStoredReport(h,'unknown-stalled-proof');
 const saved=h.storage.parsed();
 t.mock.timers.enable({apis:['setTimeout']});
 h.client.setDispatch((action,args)=>action==='appearance.preferences.read'?new Promise(()=>{}):h.dispatch(action,args));
 void h.client.reconcile();await flush();t.mock.timers.tick(10);await flush();
 assert.deepEqual(h.storage.parsed(),saved);assert.equal(h.storage.writes.length,1);
 assert.equal(h.server.command.status,'pending');assert.equal(h.reports.some(report=>report.status==='failed'),false);
});

for(const proof of ['retry-response','stored-command-read'])test(`older refused ACK cannot undo a stored retry proven by ${proof}`,async()=>{
 const h=makeHarness({ackTimeoutMs:100});
 // A temporary backend guard refuses the first ACK without settling the pending
 // command. Its negative receipt stays in flight while reads are unavailable.
 const held=await timedOutStoredReport(h,'competing-acks');
 held.allowReads();h.onReport(null);
 let releaseRetry,markStored;
 const retryResponse=new Promise(resolve=>{releaseRetry=resolve});
 const retryStored=new Promise(resolve=>{markStored=resolve});
 h.client.setDispatch(async(action,args)=>{
  const receipt=await h.dispatch(action,args);
  if(action==='appearance.client.report'&&args.status==='stored'){
   markStored();await retryResponse;
  }
  return receipt;
 });
 const recovery=h.client.reconcile();
 await retryStored;
 const saved=h.storage.parsed();
 assert.equal(h.server.command.status,'stored','The backend stores the permitted retry before releasing its response.');
 held.release({accepted:false,error:'Earlier temporary guard refused admission.'});
 await flush(); // The older refusal queues recovery behind the held retry.
 if(proof==='retry-response')releaseRetry();
 assert.equal(await recovery,proof==='retry-response');
 // Drain the queued refusal; in the read case it must discover the saved stored
 // command before attempting rollback, even though the retry response timed out.
 assert.equal(await h.client.reconcile(),true);
 releaseRetry();await flush();
 assert.equal((await h.client.waitForCommand(held.command.id)).status,'stored');
 assert.deepEqual(h.storage.parsed(),saved,'No older individual refusal may undo a proven stored command.');
 assert.equal(h.storage.writes.length,1,'Only the original storage write occurs.');
 assert.equal(h.server.command.status,'stored');assert.equal(h.server.activeScope,'client');
 assert.deepEqual(h.server.local.values,browserValues);
 assert.equal(h.server.local.storageVersion,saved.version);
 assert.deepEqual(h.reports.filter(report=>report.commandId===held.command.id).map(report=>report.status),['stored','stored']);
 assert.equal(h.client.getStatus().status,'ready');
});

test('late negative ACK from a timed-out acknowledgement retry uses the same fenced recovery',async()=>{
 const h=makeHarness({ackTimeoutMs:10});await connect(h);
 let release,unavailable=false,attempts=0;
 const response=new Promise(resolve=>{release=resolve});
 h.client.setDispatch((action,args)=>{
  if(action==='appearance.preferences.read'&&unavailable)throw Error('Settlement read unavailable');
  return h.dispatch(action,args);
 });
 h.onReport(args=>{
  if(args.status!=='stored')return;
  if(++attempts===1)throw Error('Initial admission outcome unknown.');
  unavailable=true;return response;
 });
 const command=h.command('late-retry-refusal',{activeScope:'client',values:browserValues});
 await assert.rejects(runCommand(h,command),/activation was not confirmed/);
 assert.equal(attempts,2);assert.equal(h.storage.writes.length,1);
 release({accepted:false,error:'Late retry refused.'});await flush();
 assert.deepEqual(h.storage.parsed(),{version:'browser-version-2',activeScope:'shared',values:null});
 unavailable=false;assert.equal(await h.client.reconcile(),true);
 assert.equal(h.server.command.status,'failed');assert.equal(h.server.activeScope,'shared');
 assert.equal(attempts,2);assert.equal(h.storage.writes.length,2);
});

for(const outcome of ['lost','transport-error','unreadable'])test(`timed out ${outcome} stored admission never implies rejection or rolls back`,async()=>{
 const h=makeHarness({ackTimeoutMs:10});
 const held=await timedOutStoredReport(h,'late-unknown',async promise=>{
  const receipt=await promise;
  if(outcome==='transport-error')throw Error('Transport failed without an admission.');
  return receipt;
 });
 const saved=h.storage.parsed();
 if(outcome!=='lost')held.release({});
 await flush();await h.client.reconcile();
 assert.deepEqual(h.storage.parsed(),saved);assert.equal(h.storage.writes.length,1);
 assert.equal(h.server.command.status,'pending');
 assert.equal(h.reports.some(report=>report.status==='failed'),false);
});

for(const changed of ['version','values','activeScope'])test(`late rejection preserves a tab record with newer ${changed}`,async()=>{
 const h=makeHarness({ackTimeoutMs:10});
 const held=await timedOutStoredReport(h,'late-newer-tab');
 const newer={...h.storage.parsed(),[changed]:changed==='version'?'newer-tab-version':changed==='values'?{...browserValues,scheme:'light'}:'shared'};
 h.storage.setExternal(namespace,newer);
 held.release({accepted:false,error:'Late admission refused.'});
 await flush();await h.client.reconcile();
 assert.deepEqual(h.storage.parsed(),newer);assert.equal(h.storage.writes.length,1);
 held.allowReads();assert.equal(await h.client.reconcile(),true);
 assert.deepEqual(h.storage.parsed(),newer);assert.equal(h.server.local.storageVersion,newer.version);
 assert.equal(h.server.activeScope,newer.activeScope);assert.deepEqual(h.server.local.values,newer.values);
 assert.equal(h.server.command.status,'failed');
});

for(const changed of ['namespace','generation'])test(`late rejection from old ${changed} cannot mutate the replacement identity`,async()=>{
 const h=makeHarness({ackTimeoutMs:10});
 const held=await timedOutStoredReport(h,'late-old-owner');
 const saved=h.storage.parsed(),nextNamespace=changed==='namespace'?replacementNamespace:namespace;
 h.storage.setExternal(nextNamespace,saved);
 h.setServer({...h.server,namespace:nextNamespace,generation:changed==='generation'?'replacement-generation':generation,
  activeScope:'shared',command:null,local:{revision:0,storageVersion:'missing',values:null,reported:false,error:null}});
 held.allowReads();await h.client.observe(h.server,{connected:true});
 const before=h.server,reports=h.reports.length,status=h.client.getStatus();
 held.release({accepted:false,error:'Late old admission refused.'});
 await flush();
 assert.deepEqual(h.server,before);assert.equal(h.reports.length,reports);assert.deepEqual(h.client.getStatus(),status);
 assert.deepEqual(h.storage.parsed(nextNamespace),saved);assert.equal(h.storage.writes.length,1);
});

test('unknown pending stored ACK can retry the acknowledgement without replaying or rolling back storage',async()=>{
 const h=makeHarness();await connect(h);
 let unknown=true;
 h.onReport(args=>{
  if(args.status==='stored'&&unknown){unknown=false;throw Error('Connection lost before the stored ACK outcome was known.')}
 });
 const command=h.command('unknown-pending-ack',{activeScope:'client',values:browserValues});
 assert.equal((await runCommand(h,command)).status,'stored');
 assert.equal(h.server.command.status,'stored');assert.equal(h.storage.writes.length,1);
 assert.equal(h.reports.filter(report=>report.commandId===command.id&&report.status==='stored').length,2);
 assert.equal(h.reports.some(report=>report.status==='failed'),false);
});

test('an initially unknown stored ACK that is definitively rejected on reconciliation rolls back instead of looping',async()=>{
 const h=makeHarness();await connect(h);
 let attempts=0;
 h.onReport(args=>{
  if(args.status!=='stored')return;
  if(++attempts===1)throw Error('ACK outcome unknown');
  return {accepted:false,error:'Prepared preference composition is stale.'};
 });
 const command=h.command('unknown-then-rejected',{activeScope:'client',values:browserValues});
 await assert.rejects(runCommand(h,command),/Prepared preference composition is stale/);
 assert.equal(h.server.command.status,'failed');assert.equal(h.server.activeScope,'shared');
 assert.equal(h.storage.parsed().activeScope,'shared');assert.equal(h.storage.parsed().values,null);
 assert.equal(h.storage.writes.length,2);
 assert.equal(await h.client.reconcile(),true);assert.equal(attempts,2);
});

for(const status of [200,409])test(`actual HTTP adapter preserves accepted:false rejection (${status}) for conditional preference recovery`,async t=>{
 const originalFetch=globalThis.fetch;
 t.after(()=>{globalThis.fetch=originalFetch});
 const h=makeHarness();await connect(h);
 globalThis.fetch=async()=>new Response(JSON.stringify({accepted:false,error:'Prepared preference composition is stale.'}),{
  status,headers:{'content-type':'application/json'},
 });
 h.onReport(args=>{
  if(args.status==='stored')return apiRequest('/api/actions',{method:'POST',body:{action:'appearance.client.report',args}});
 });
 const command=h.command('http-rejected-ack',{activeScope:'client',values:browserValues});
 await assert.rejects(runCommand(h,command),/Prepared preference composition is stale/);
 assert.equal(h.server.command.status,'failed');assert.equal(h.server.activeScope,'shared');
 assert.equal(h.storage.parsed().activeScope,'shared');assert.equal(h.storage.parsed().values,null);
 assert.equal(h.storage.writes.length,2);assert.equal(await h.client.reconcile(),true);
 assert.equal(h.reports.filter(report=>report.status==='stored').length,1);
});

test('HTTP status alone without an explicit negative receipt remains an unknown ACK outcome',async t=>{
 const originalFetch=globalThis.fetch;
 t.after(()=>{globalThis.fetch=originalFetch});
 const h=makeHarness();await connect(h);
 globalThis.fetch=async()=>new Response(JSON.stringify({error:'Upstream response unavailable'}),{status:502});
 let unknown=true;
 h.onReport(args=>{
  if(args.status==='stored'&&unknown){
   unknown=false;
   return apiRequest('/api/actions',{method:'POST',body:{action:'appearance.client.report',args}});
  }
 });
 const command=h.command('http-unknown-ack',{activeScope:'client',values:browserValues});
 assert.equal((await runCommand(h,command)).status,'stored');
 assert.equal(h.storage.writes.length,1);assert.equal(h.reports.filter(report=>report.status==='stored').length,2);
 assert.equal(h.reports.some(report=>report.status==='failed'),false);
});

for(const replaced of ['namespace','generation'])test(`rejection recovery rechecks ${replaced} ownership after acquiring the browser lock`,async()=>{
 const h=makeHarness();await connect(h);
 let recovering=false,changed=false;
 h.onReport(args=>{
  if(args.status==='stored'){recovering=true;return {accepted:false,error:'Prepared preference composition is stale.'}}
 });
 const request=h.locks.request.bind(h.locks);
 const nextNamespace=replaced==='namespace'?replacementNamespace:namespace;
 const newer={version:'new-document-record',activeScope:'shared',values:browserValues};
 h.locks.request=(name,options,operation)=>{
  if(recovering&&!changed){
   changed=true;
   h.storage.setExternal(nextNamespace,newer);
   h.setServer({...h.server,namespace:nextNamespace,generation:replaced==='generation'?'replacement-generation':generation,
    activeScope:'shared',command:null,local:{revision:0,storageVersion:'missing',values:null,reported:false,error:null}});
   void h.client.observe(h.server,{connected:true});
  }
  return request(name,options,operation);
 };
 const command=h.command('rejected-old-owner',{activeScope:'client',values:globalValues});
 await assert.rejects(runCommand(h,command),/Prepared preference composition is stale|document changed/);
 await h.client.reconcile();
 assert.equal(changed,true);assert.equal(h.storage.writes.length,1,'No old-owner rollback is allowed.');
 assert.deepEqual(h.storage.parsed(nextNamespace),newer);
 assert.equal(h.reports.some(report=>report.commandId===command.id&&report.status==='failed'),false);
});

test('lost failed ACK after rejection is settled from its saved failed receipt without another stored ACK',async()=>{
 const h=makeHarness();await connect(h);
 h.onReport(args=>{
  if(args.status==='stored')return {accepted:false,error:'Prepared preference composition is stale.'};
  if(args.status==='failed'&&args.commandId){
   h.setServer({...h.server,command:{...h.server.command,status:'failed',error:args.error}});
   throw Error('Failed ACK response was lost.');
  }
 });
 const command=h.command('rejected-lost-failed-ack',{activeScope:'client',values:browserValues});
 await assert.rejects(runCommand(h,command),/Prepared preference composition is stale/);
 assert.equal(h.server.command.status,'failed');assert.equal(h.storage.parsed().activeScope,'shared');
 assert.equal(await h.client.reconcile(),true);
 assert.equal(h.server.activeScope,'shared');assert.equal(h.storage.writes.length,2);
 assert.equal(h.reports.filter(report=>report.commandId===command.id&&report.status==='stored').length,1);
 assert.equal(h.reports.filter(report=>report.commandId===command.id&&report.status==='failed').length,1);
});

for(const initialized of [false,true])test(`main presentation atomically edits/selects ${initialized?'retained':'first-copy'} browser profile through shell prepare/apply and exact ACK`,async()=>{
 const retained={...browserValues,density:'compact',accent:'#334455'};
 const initialRecord=initialized?{version:'retained-v1',activeScope:'shared',values:retained}:undefined;
 const h=makeHarness({localValues:initialized?retained:null,storageVersion:initialized?'retained-v1':'missing',record:initialRecord});
 await connect(h);
 const before=h.server,calls=[],committed={instances:[{id:'chats',package:'builtin.chats',slot:'navigation'}],presentation:{layout:'balanced'}};
 const dispatch=async(action,args)=>{
  calls.push({action,args:copy(args)});
  if(action==='shell.inspect')return {accepted:true,result:{revision:23,composition:copy(committed),effectiveComposition:{instances:[],presentation:globalValues}}};
  if(action==='shell.changes.prepare'){
   assert.equal(h.server.activeScope,'shared');assert.equal(h.storage.writes.length,0);
   return {accepted:true,result:{id:'atomic-preparation'}};
  }
  if(action==='shell.changes.apply'){
   assert.deepEqual(h.server,before,'Preparation must not select or edit either profile.');
   const base=initialized?retained:globalValues;
   h.command('atomic-storage',{activeScope:'client',values:{...base,
    executionVisibility:{...base.executionVisibility,step:false},executionMetrics:[]}});
   return {accepted:true,result:{status:'awaiting-storage',commandId:'atomic-storage'}};
  }
  return h.dispatch(action,args);
 };
 const {updatePresentation}=mainPreferences(h,dispatch,{...globalValues,executionVisibility:{step:true,turn:true,conversation:true}});
 const result=await updatePresentation({executionVisibility:{step:false,turn:true,conversation:true},executionMetrics:[]});
 assert.deepEqual(calls.slice(0,3),[
  {action:'shell.inspect',args:{clientId:'test-client'}},
  {action:'shell.changes.prepare',args:{clientId:'test-client',expectedRevision:23,
   composition:{...committed,presentation:{executionVisibility:{step:false},executionMetrics:[]}}}},
  {action:'shell.changes.apply',args:{clientId:'test-client',expectedRevision:23,changeId:'atomic-preparation'}},
 ]);
 assert.equal(calls.some(row=>['appearance.preferences.select','appearance.preferences.update'].includes(row.action)),false);
 assert.equal(result.commandId,'atomic-storage');assert.equal(result.undoId,'undo:atomic-storage');assert.equal(result.status,'stored');
 assert.equal(h.server.activeScope,'client');assert.equal(h.server.command.status,'stored');
 assert.deepEqual(h.server.shared,before.shared,'Global is never edited by a client-targeted control.');
 assert.equal(h.storage.writes.length,1,'Source and values are stored together once.');
 assert.deepEqual(h.storage.parsed().values,h.server.local.values);
 assert.equal(h.server.local.values.scheme,initialized?retained.scheme:globalValues.scheme);
 assert.equal(h.server.local.values.density,initialized?'compact':'comfortable');
 assert.equal(Object.hasOwn(h.server.local.values,'accent'),initialized);
});

for(const failure of ['rejected','thrown'])test(`main presentation ${failure} apply leaves Global/source/retained local unchanged`,async()=>{
 const record={version:'retained-v1',activeScope:'shared',values:browserValues};
 const h=makeHarness({localValues:browserValues,storageVersion:record.version,record});await connect(h);
 const before=h.server,calls=[];
 const dispatch=async(action,args)=>{
  calls.push(action);
  if(action==='shell.inspect')return {accepted:true,result:{revision:9,composition:{instances:[],presentation:{}}}};
  if(action==='shell.changes.prepare')return {accepted:true,result:{id:'failed-preparation'}};
  if(action==='shell.changes.apply'){
   if(failure==='thrown')throw Error('Fixture refused apply');
   return {accepted:false,error:'Fixture refused apply'};
  }
  return h.dispatch(action,args);
 };
 await assert.rejects(mainPreferences(h,dispatch).updatePresentation({layout:'work'}),/Fixture refused apply/);
 assert.deepEqual(calls,['shell.inspect','shell.changes.prepare','shell.changes.apply']);
 assert.deepEqual(h.server,before);assert.deepEqual(h.storage.parsed(),record);assert.equal(h.storage.writes.length,0);
});

test('main explicit inactive browser-profile edit uses update without selecting it',async()=>{
 const record={version:'retained-v1',activeScope:'shared',values:browserValues};
 const h=makeHarness({localValues:browserValues,storageVersion:record.version,record});await connect(h);
 const calls=[],dispatch=async(action,args)=>{
  calls.push({action,args:copy(args)});
  if(action==='appearance.preferences.update'){
   h.command('inactive-edit',{activeScope:'shared',values:{...browserValues,scheme:'light'}});
   return {accepted:true,result:{status:'awaiting-storage',commandId:'inactive-edit'}};
  }
  return h.dispatch(action,args);
 };
 const result=await mainPreferences(h,dispatch).updateAppearancePreference('scheme','light',{
  scope:'client',namespace,generation,expectedRevision:0,expectedStorageVersion:record.version,
 });
 assert.deepEqual(calls.find(row=>row.action==='appearance.preferences.update').args,{
  scope:'client',generation,expectedRevision:0,expectedStorageVersion:record.version,patch:{scheme:'light'},
 });
 assert.equal(calls.some(row=>row.action==='appearance.preferences.select'||row.action.startsWith('shell.')),false);
 assert.equal(result.status,'stored');assert.equal(h.server.activeScope,'shared');
 assert.equal(h.server.local.values.scheme,'light');assert.deepEqual(h.server.shared.values,globalValues);
});

for(const scope of ['shared','client'])test(`main accent reset sends only null to the captured ${scope} profile and stores the admitted absence`,async()=>{
 const shared={...globalValues,appearanceId:'saved:ocean.css',accent:'#112233',density:'compact'};
 const local={...browserValues,appearanceId:'saved:forest.css',accent:'#445566'};
 const record={version:'accent-v1',activeScope:'shared',values:local};
 const h=makeHarness({sharedValues:shared,localValues:local,storageVersion:record.version,record});
 await connect(h);
 const before=h.server,calls=[],withoutAccent={...(scope==='shared'?shared:local)};
 delete withoutAccent.accent;
 const dispatch=async(action,args)=>{
  calls.push({action,args:copy(args)});
  if(action==='appearance.preferences.update'){
   if(scope==='shared'){
    h.setServer({...h.server,shared:{revision:8,values:withoutAccent}});
    return {accepted:true,result:{status:'stored',revision:8,shared:{revision:8,values:withoutAccent}}};
   }
   h.command('accent-reset',{activeScope:'shared',values:withoutAccent});
   return {accepted:true,result:{status:'awaiting-storage',commandId:'accent-reset'}};
  }
  return h.dispatch(action,args);
 };
 const result=await mainPreferences(h,dispatch).updateAppearancePreference('accent',null,{
  scope,namespace,generation,expectedRevision:scope==='shared'?7:0,
  ...(scope==='client'?{expectedStorageVersion:record.version}:{}),
 });
 assert.deepEqual(calls.find(row=>row.action==='appearance.preferences.update').args,{
  scope,generation,expectedRevision:scope==='shared'?7:0,patch:{accent:null},
  ...(scope==='client'?{expectedStorageVersion:record.version}:{}),
 });
 assert.equal(calls.filter(row=>row.action==='appearance.preferences.update').length,1);
 assert.equal(calls.some(row=>row.action==='appearance.preferences.select'||row.action.startsWith('shell.')),false);
 assert.equal(result.status,'stored');assert.equal(h.server.activeScope,'shared');
 assert.deepEqual((scope==='shared'?h.server.shared:h.server.local).values,withoutAccent);
 assert.equal(Object.hasOwn((scope==='shared'?h.server.shared:h.server.local).values,'accent'),false,'The host-admitted profile has no persisted null override.');
 if(scope==='shared'){
  assert.deepEqual(h.server.local,before.local);assert.deepEqual(h.storage.parsed(),record);
  assert.equal(h.storage.writes.length,0);
 }else{
  assert.deepEqual(h.server.shared,before.shared);assert.deepEqual(h.storage.parsed().values,withoutAccent);
  assert.equal(h.storage.writes.length,1);
 }
});

test('definite browser storage failure leaves source, Global and retained local values unchanged',async()=>{
 const record={version:'retained-v1',activeScope:'shared',values:browserValues};
 const h=makeHarness({localValues:browserValues,storageVersion:record.version,record});await connect(h);
 const before=h.server;
 h.storage.setItem=()=>{throw Error('Storage refused write')};
 const command=h.command('failed-atomic-storage',{activeScope:'client',values:{...browserValues,layout:'conversation'}});
 await assert.rejects(runCommand(h,command),/Storage refused write|latest stored value/);
 assert.equal(h.server.command.status,'failed');assert.equal(h.server.activeScope,'shared');
 assert.deepEqual(h.server.local,before.local);assert.deepEqual(h.server.shared,before.shared);
 assert.deepEqual(h.storage.parsed(),record);assert.equal(h.storage.writes.length,0);
});

test('quota-failed Global switch reconfirms only the unchanged browser record after a fresh read and ready ACK, while the UI command stays failed',async()=>{
 const record={version:'confirmed-browser-v1',activeScope:'client',values:browserValues};
 const h=makeHarness({activeScope:'client',localValues:browserValues,storageVersion:record.version,record});
 await connect(h);
 const before=h.server,acknowledged=h.client.getSnapshot().acknowledged,raw=h.storage.raw();
 const trace=[],calls=[],attempts=[];
 let failed=false,readHeld=false,releaseRead,markRead,releaseReady,markReady;
 const readGate=new Promise(resolve=>{releaseRead=resolve}),readStarted=new Promise(resolve=>{markRead=resolve});
 const readyGate=new Promise(resolve=>{releaseReady=resolve}),readyStarted=new Promise(resolve=>{markReady=resolve});
 const getItem=h.storage.getItem.bind(h.storage);
 h.storage.getItem=key=>{trace.push('storage-read');return getItem(key)};
 h.storage.setItem=(key,value)=>{
  attempts.push({key,value:JSON.parse(value)});trace.push('quota-throw');
  throw new DOMException('Injected storage quota exceeded.','QuotaExceededError');
 };
 h.onReport(async(args,server)=>{
  if(args.status==='failed'&&args.commandId==='quota-select-global'){
   // Unlike the shared fake's command-only failure, the actual host also mirrors
   // storageError. A later ready report clears that error, not the failed command.
   h.setServer({...server,local:{...server.local,error:args.error},
    command:{...server.command,status:'failed',error:args.error}});
   failed=true;trace.push('failed-ACK');
   return {accepted:true,result:{status:'failed',error:args.error}};
  }
  if(args.status==='ready'&&failed){
   assert.deepEqual(args,{
    generation,revision:acknowledged.revision,storageVersion:record.version,
    activeScope:'client',values:browserValues,status:'ready',
   },'Only the freshly read, unchanged browser record can be reconfirmed.');
   assert.match(server.local.error,/Injected storage quota exceeded/);
   trace.push('ready-pending');markReady();await readyGate;
   h.setServer({...server,activeScope:args.activeScope,local:{...server.local,
    revision:args.revision,storageVersion:args.storageVersion,values:copy(args.values),reported:true,error:null}});
   trace.push('ready-ACK');
   return {accepted:true,result:{status:'reported'}};
  }
 });
 const dispatch=async(action,args={})=>{
  calls.push({action,args:copy(args)});
  if(action==='appearance.preferences.select'){
   assert.deepEqual(copy(args),{generation,activeScope:'shared',expectedRevision:acknowledged.revision,
    expectedStorageVersion:record.version});
   h.command('quota-select-global',{activeScope:'shared',values:browserValues});
   return {accepted:true,result:{status:'awaiting-storage',commandId:'quota-select-global'}};
  }
  if(action==='appearance.preferences.read'&&failed&&!readHeld){
   readHeld=true;trace.push('fresh-read-pending');markRead();await readGate;
   trace.push('fresh-read');
  }
  return h.dispatch(action,args);
 };
 h.client.setDispatch(dispatch);
 const outcome=mainPreferences(h,dispatch,browserValues).selectAppearanceProfile('shared');
 const rejected=assert.rejects(outcome,/Injected storage quota exceeded.*no command was replayed/);
 await readStarted;
 assert.equal(h.client.getSnapshot().acknowledged,null,'Failure clears the old ACK before reconfirmation.');
 assert.equal(h.client.getStatus().status,'failed');
 assert.equal(h.server.command.status,'failed');assert.match(h.server.local.error,/Injected storage quota exceeded/);
 assert.equal(h.server.activeScope,'client');assert.equal(h.storage.raw(),raw);
 releaseRead();await readyStarted;
 assert.equal(h.client.getSnapshot().acknowledged,null,'A fresh read and an in-flight ready report are not an ACK.');
 assert.equal(h.client.getStatus().status,'failed');assert.notEqual(h.server.local.error,null);
 assert.deepEqual(trace.slice(trace.indexOf('failed-ACK')),[
  'failed-ACK','fresh-read-pending','fresh-read','storage-read','ready-pending',
 ]);
 releaseReady();await rejected;
 await h.client.waitUntilReady();
 assert.deepEqual(h.client.getSnapshot().acknowledged,acknowledged,'Ready restores only the old exact record, never the attempted Global record.');
 assert.deepEqual(h.server.local,before.local,'Ready clears storageError without changing the confirmed mirror.');
 assert.deepEqual(h.server.shared,before.shared);assert.equal(h.server.activeScope,'client');
 assert.equal(h.server.command.status,'failed');assert.equal(h.client.getStatus().status,'ready');
 await assert.rejects(h.client.waitForCommand('quota-select-global'),/Injected storage quota exceeded/);
 assert.equal(await h.client.reconcile(),true);await h.client.observe(h.server,{connected:true});
 assert.deepEqual(h.reports.map(report=>({status:report.status,activeScope:report.activeScope,
  storageVersion:report.storageVersion,commandId:report.commandId})),[
  {status:'ready',activeScope:'client',storageVersion:record.version,commandId:undefined},
  {status:'failed',activeScope:'client',storageVersion:record.version,commandId:'quota-select-global'},
  {status:'ready',activeScope:'client',storageVersion:record.version,commandId:undefined},
 ]);
 assert.deepEqual(attempts,[{key:namespace,value:{version:'browser-version-1',activeScope:'shared',values:browserValues}}]);
 assert.equal(calls.filter(row=>row.action==='appearance.preferences.select').length,1,'The failed UI gesture is not replayed.');
 assert.equal(calls.every(row=>['appearance.preferences.read','appearance.preferences.select','appearance.client.report'].includes(row.action)),true);
 assert.equal(h.storage.raw(),raw);assert.deepEqual(h.storage.writes,[]);assert.deepEqual(h.storage.deletes,[]);
 assert.equal(trace.at(-1),'storage-read');
 assert.equal(trace.filter(event=>event==='ready-ACK').length,1);
});

test('main presentation empty patches return unchanged before storage readiness, inspection or selection',async()=>{
 const h=makeHarness(),calls=[],api=mainPreferences(h,async action=>{calls.push(action);throw Error('Unexpected action')},
  {...globalValues,executionVisibility:{step:true,turn:true,conversation:true}});
 for(const patch of [{},{executionVisibility:{}},{executionVisibility:{step:true,turn:true}}]){
  assert.equal((await api.updatePresentation(patch)).status,'unchanged');
 }
 assert.deepEqual(calls,[]);assert.equal(h.storage.writes.length,0);
});

test('selected Balanced landing callback is a no-op on Global with a different retained browser profile',async()=>{
 const retained={...browserValues,interfaceDetail:'detailed',executionDetail:'minimal'};
 const record={version:'retained-v1',activeScope:'shared',values:retained};
 const h=makeHarness({localValues:retained,storageVersion:record.version,record});await connect(h);
 const before=h.server,calls=[],api=mainPreferences(h,async action=>{calls.push(action);throw Error('Unexpected action')});
 const landing=readFileSync(new URL('../src/work-landing.jsx',import.meta.url),'utf8');
 const callback=landing.match(/onClick=\{(\(\)=>nav\.setPresentation\(\{interfaceDetail:key,executionDetail:key\}\))\}/)?.[1];
 assert.ok(callback,'Exercise the real landing detail-button callback.');
 const selectedBalanced=runInNewContext(`(${callback})`,{nav:{setPresentation:api.updatePresentation},key:'standard'});
 assert.equal((await selectedBalanced()).status,'unchanged');
 assert.deepEqual(calls,[]);assert.deepEqual(h.server,before);
 assert.deepEqual(h.storage.parsed(),record);assert.equal(h.storage.writes.length,0);
});

test('Balanced landing gesture preserves both explicit fields when only one differs on Global',async()=>{
 const shared={...globalValues,interfaceDetail:'detailed',executionDetail:'standard'};
 const retained={...browserValues,interfaceDetail:'minimal',executionDetail:'minimal',density:'compact'};
 const record={version:'retained-v1',activeScope:'shared',values:retained};
 const h=makeHarness({sharedValues:shared,localValues:retained,storageVersion:record.version,record});await connect(h);
 const patches=[],before=h.server;
 const dispatch=async(action,args)=>{
  if(action==='shell.inspect')return {accepted:true,result:{revision:3,composition:{instances:[],presentation:{}}}};
  if(action==='shell.changes.prepare'){patches.push(copy(args.composition.presentation));return {accepted:true,result:{id:'balanced-gesture'}}}
  if(action==='shell.changes.apply'){
   h.command('balanced-storage',{activeScope:'client',values:{...retained,...patches.at(-1)}});
   return {accepted:true,result:{status:'awaiting-storage',commandId:'balanced-storage'}};
  }
  return h.dispatch(action,args);
 };
 const api=mainPreferences(h,dispatch,shared);
 const landing=readFileSync(new URL('../src/work-landing.jsx',import.meta.url),'utf8');
 const callback=landing.match(/onClick=\{(\(\)=>nav\.setPresentation\(\{interfaceDetail:key,executionDetail:key\}\))\}/)?.[1];
 assert.ok(callback,'Exercise the real Balanced callback, not a hand-written single-field gesture.');
 const balanced=runInNewContext(`(${callback})`,{nav:{setPresentation:api.updatePresentation},key:'standard'});
 assert.equal((await balanced()).status,'stored');
 assert.deepEqual(patches,[{interfaceDetail:'standard',executionDetail:'standard'}]);
 assert.equal(h.server.activeScope,'client');assert.deepEqual(h.server.shared,before.shared);
 assert.deepEqual(h.server.local.values,{...retained,interfaceDetail:'standard',executionDetail:'standard'});
 assert.deepEqual(h.storage.parsed().values,h.server.local.values);assert.equal(h.storage.writes.length,1);
});

test('main presentation filters all unchanged scalars, arrays and visibility maps against rendered defaults',async()=>{
 const h=makeHarness(),calls=[],api=mainPreferences(h,async action=>{calls.push(action);throw Error('Unexpected action')});
 for(const patch of [copy(globalValues),{executionMetrics:[],executionVisibility:{conversation:true,step:true,turn:true}},
  {interfaceDetail:'standard',executionDetail:'standard',workStartView:'appearance'}]){
  assert.equal((await api.updatePresentation(patch)).status,'unchanged');
 }
 assert.deepEqual(calls,[]);assert.equal(h.storage.writes.length,0);
});

test('main presentation keeps explicit unchanged fields alongside a changed array',async()=>{
 const h=makeHarness();await connect(h);
 const patches=[],dispatch=async(action,args)=>{
  if(action==='shell.inspect')return {accepted:true,result:{revision:3,composition:{instances:[],presentation:{}}}};
  if(action==='shell.changes.prepare'){patches.push(copy(args.composition.presentation));return {accepted:true,result:{id:'metrics-change'}}}
  if(action==='shell.changes.apply')return {accepted:true,result:{status:'unchanged'}};
  return h.dispatch(action,args);
 };
 const rendered={...globalValues,executionMetrics:['calls','costUsd']};
 await mainPreferences(h,dispatch,rendered).updatePresentation({interfaceDetail:'standard',executionMetrics:['calls','costUsd']});
 assert.deepEqual(patches,[]);
 await mainPreferences(h,dispatch,rendered).updatePresentation({interfaceDetail:'standard',executionMetrics:['calls']});
 assert.deepEqual(patches,[{interfaceDetail:'standard',executionMetrics:['calls']}]);
});

test('main presentation fresh visibility reversal compares current rendered values, not stale effective or prior gestures',async()=>{
 const h=makeHarness();await connect(h);
 const patches=[];
 const dispatch=async(action,args)=>{
  if(action==='shell.inspect')return {accepted:true,result:{revision:3,composition:{instances:[],presentation:{}}}};
  if(action==='shell.changes.prepare'){patches.push(copy(args.composition.presentation));return {accepted:true,result:{id:'visibility-change'}}}
  if(action==='shell.changes.apply')return {accepted:true,result:{status:'unchanged'}};
  return h.dispatch(action,args);
 };
 await mainPreferences(h,dispatch,{...globalValues,executionVisibility:{step:true,turn:true,conversation:true}})
  .updatePresentation({executionVisibility:{step:false,turn:true,conversation:true}});
 await mainPreferences(h,dispatch,{...globalValues,executionVisibility:{step:false,turn:false,conversation:true}})
  .updatePresentation({executionVisibility:{step:true,turn:false,conversation:true}});
 assert.deepEqual(patches,[{executionVisibility:{step:false}},{executionVisibility:{step:true}}]);
});

test('stored overlays reconcile against their own profile revision, not the other profile',()=>{
 const overlay={scheme:{value:'dark',scope:'client',status:'stored',confirmation:{namespace,generation,revision:2}}};
 const appearance={namespace,generation,activeScope:'shared',
  local:{revision:1,values:browserValues},shared:{revision:20,values:globalValues}};
 assert.equal(reconcileAppearancePreferenceValues(overlay,appearance),overlay);
 assert.deepEqual(reconcileAppearancePreferenceValues(overlay,{...appearance,local:{revision:2,values:browserValues}}),{});
 const shared={scheme:{value:'dark',scope:'shared',status:'stored',confirmation:{namespace,generation,revision:3}}};
 assert.equal(reconcileAppearancePreferenceValues(shared,{...appearance,shared:{revision:2,values:globalValues}}),shared);
 assert.deepEqual(reconcileAppearancePreferenceValues(shared,{...appearance,shared:{revision:3,values:globalValues}}),{});
 assert.equal(reconcileAppearancePreferenceValues({scheme:{...overlay.scheme,status:'failed'}},appearance).scheme.status,'failed');
});

for(const scope of ['shared','client'])test(`meaningful failure feedback expires only after a newer decision in its captured ${scope} profile`,()=>{
 const profile=scope==='shared'?'shared':'local',other=scope==='shared'?'local':'shared';
 const feedback={accent:{status:'failed',scope,message:'Accent change was not confirmed.',confirmation:{namespace,generation,revision:5}}};
 const appearance={namespace,generation,activeScope:scope,local:{revision:4,values:browserValues},shared:{revision:4,values:globalValues}};
 assert.equal(reconcileAppearancePreferenceFeedback(feedback,appearance),feedback);
 const equal={...appearance,[profile]:{...appearance[profile],revision:5}};
 assert.equal(reconcileAppearancePreferenceFeedback(feedback,equal),feedback,'The captured revision retains its unresolved failure.');
 const otherAdvanced={...equal,[other]:{...appearance[other],revision:90}};
 assert.equal(reconcileAppearancePreferenceFeedback(feedback,otherAdvanced),feedback,'The other profile does not own this failure.');
 assert.deepEqual(reconcileAppearancePreferenceFeedback(feedback,{...otherAdvanced,[profile]:{...equal[profile],revision:6}}),{});
 assert.equal(feedback.accent.message,'Accent change was not confirmed.','Reconciliation does not mutate its input.');
 const pending={accent:{...feedback.accent,status:'pending'}};
 assert.equal(reconcileAppearancePreferenceFeedback(pending,{...appearance,[profile]:{revision:99}}),pending,'An in-flight operation retains its owner until settlement.');
 for(const replaced of [{namespace:replacementNamespace},{generation:'other-document'}]){
  assert.deepEqual(reconcileAppearancePreferenceFeedback(feedback,{...equal,...replaced}),{});
 }
});

for(const scope of ['shared','client'])test(`a failed accent overlay cannot survive a different appearance in its captured ${scope} profile`,()=>{
 const profile=scope==='shared'?'shared':'local',other=scope==='shared'?'local':'shared';
 const entry={operation:'accent',value:'#abcdef',scope,status:'failed',appearanceId:'saved:ocean.css',
  target:{namespace,generation,expectedRevision:4}};
 const values={accent:entry,scheme:{operation:'scheme',value:'dark',scope,status:'failed'}};
 const appearance={namespace,generation,local:{revision:4,values:{...browserValues,appearanceId:'saved:ocean.css'}},
  shared:{revision:4,values:{...globalValues,appearanceId:'saved:ocean.css'}}};
 assert.equal(reconcileAppearancePreferenceValues(values,appearance),values);
 assert.equal(reconcileAppearancePreferenceValues(values,{...appearance,[other]:{revision:99,values:{appearanceId:'saved:forest.css'}}}),values);
 const changed={...appearance,[profile]:{revision:5,values:{appearanceId:'saved:forest.css'}}};
 assert.deepEqual(reconcileAppearancePreferenceValues(values,changed),{scheme:values.scheme});
 assert.equal(values.accent,entry,'The failed intent remains immutable.');
 assert.deepEqual(reconcileAppearancePreferenceValues(values,{...appearance,[profile]:{
  ...appearance[profile],revision:5,
 }}),{scheme:values.scheme},'A newer decision in the same profile also supersedes the failed accent.');
 const pending={accent:{...entry,status:'pending'}};
 assert.equal(reconcileAppearancePreferenceValues(pending,appearance),pending,'Unchanged state does not settle an in-flight write.');
 const reset={accent:{...entry,value:null,status:'stored',confirmation:{namespace,generation}}};
 assert.deepEqual(reconcileAppearancePreferenceValues(reset,appearance),{},'An absent authoritative accent confirms a null reset even without a revision.');
});
