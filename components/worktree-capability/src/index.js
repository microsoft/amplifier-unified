import {join,resolve} from 'node:path';
import {hostname} from 'node:os';
import {actionSchemas,validateArgs} from './actions.js';
import {WorktreeStore,digest,gitIdentity,now} from './store.js';
import {createGitWorker} from './git-worker.js';
const reads=new Set(['worktree.inspect','worktree.list','worktree.status']);
const handoffs=new Set(['worktree.handoff','worktree.reconcile']);
const errorDetail=error=>String(error?.message||error).slice(0,2000);
const bounded=(value,name,max=8192)=>{if(typeof value!=='string'||!value||value.length>max||value.includes('\0'))throw Error('Invalid '+name);return value};
const recordURI=(session,id,offset=0)=>{const uri=new URL(`amplifier-worktree://records/${id}`);uri.searchParams.set('session',session);if(offset)uri.searchParams.set('offset',offset);return uri.href};

/** Unified policy over public Git and host seams. No native runtime or global state imports. */
export function createWorktreeCapability({directory,python,inspectSession,relocateSession,directoryInUse,withDirectoryGuard,readUserMessage,onChanged=()=>{},onMayBeIdle=()=>{},executionHost={id:hostname(),label:hostname(),scope:'local'},gitWorker}={}) {
  const store=new WorktreeStore(directory,{onMayBeIdle}),git=gitWorker||createGitWorker({python,directory:join(directory,'git'),executionHost,onMayBeIdle});
  store.quiescence.coverageGaps=()=>git.quiescenceCoverage===1?[]:['custom-git-worker-lifetime-unverified'];
  const jobs=new Set(),inFlight=new Set();let serial=Promise.resolve(),closed=false,queued=0;
  const manifest={version:1,topics:{worktrees:{uri:'amplifier-capability://worktrees',version:1,watch:true}},actions:Object.fromEntries(Object.keys(actionSchemas).map(operation=>[operation,{topic:'worktrees',operation,method:'x-amplifier/capabilityAction'}]))};
  const changed=scope=>{onChanged('worktrees',scope)};
  async function context(session,caller={}) {
    bounded(session,'conversation');if(!session.startsWith('ahp-session:/'))throw Error('A selected conversation is required.');
    const supplied=typeof caller.session==='string'?caller.session:caller.session?.uri;
    if(supplied&&supplied!==session)throw Error('Worktree caller changed conversation scope.');
    const value=await inspectSession(session,{clientId:caller.clientId});if(value.session!==session)throw Error('Session authority changed.');
    const historyHome=value.historyHome||value.workingDirectory;
    const executionDirectory=value.executionDirectory||value.workingDirectory;
    bounded(historyHome,'history home');bounded(executionDirectory,'execution directory');
    return {...value,session,historyHome,executionDirectory,executionRevision:value.executionRevision??0};
  }
  function checkHost(value){if(value.executionHost&&(value.executionHost.id!==executionHost.id||value.executionHost.scope!=='local'))throw Error('This checkout belongs to another execution host.');}
  const decorate=(session,record)=>({...record,manifestUri:recordURI(session,record.id)});
  async function snapshot(scope,query={}) {
    const ctx=await context(scope),page=store.page(scope,'worktrees',query),handoffPage=store.page(scope,'handoffs'),commands=store.page(scope,'commands');
    const value={worktrees:page.items.map(row=>decorate(scope,row)),worktreeHandoffs:handoffPage.items,worktreeCommands:commands.items,worktreePage:{nextCursor:page.nextCursor,handoffsNextCursor:handoffPage.nextCursor,commandsNextCursor:commands.nextCursor},historyHome:ctx.historyHome,workingDirectory:ctx.executionDirectory,executionRevision:ctx.executionRevision,executionHost,configurationBusy:Boolean(ctx.configurationBusy||store.unresolved(scope))};
    const data={worktrees:{[scope]:value}};if(Buffer.byteLength(JSON.stringify(data))>768*1024)throw Error('Worktree metadata exceeds its page bound; request a smaller page.');
    return {topic:'worktrees',scope,revision:store.revision(scope)+ctx.executionRevision,data};
  }
  async function read(request){const uri=new URL(request.uri);return snapshot(request.scope,{limit:uri.searchParams.has('limit')?Number(uri.searchParams.get('limit')):25,cursor:uri.searchParams.get('cursor')||undefined})}
  const row=(scope,id)=>{const record=store.record(scope,id);checkHost(record);return record};
  async function finding(ctx,caller,args,previous){if(caller.origin==='ui')return;const source=readUserMessage&&args.sourceMessageId?await readUserMessage(ctx.session,args.sourceMessageId):null;const time=typeof source?.createdAt==='string'?Date.parse(source.createdAt)/1000:source?.createdAt;if(!source||source.role!=='user'||!['ui','user','voice'].includes(source.inputOrigin)||source.questionId||source.scheduledRunId||!Number.isFinite(time)||time<previous.createdAt)throw Error('Cite the user’s actual finding after this operation.');}
  async function inspect(ctx) {
    const page=store.page(ctx.session),receiptPage=store.page(ctx.session,'handoffs');
    return {currentHost:executionHost,executionHost,historyHome:ctx.historyHome,executionDirectory:ctx.executionDirectory,executionRevision:ctx.executionRevision,settingsScope:ctx.historyHome,items:page.items.map(record=>decorate(ctx.session,record)),nextCursor:page.nextCursor,handoffs:receiptPage.items,handoffsNextCursor:receiptPage.nextCursor,repository:await git.request('inspect',{path:ctx.executionDirectory})};
  }
  function settle(receipt,phase,detail,result) {const updated={...receipt,phase,revision:receipt.revision+1,updatedAt:now(),detail};store.settle(updated,result);changed(receipt.sessionId);return updated}
  async function finish(receipt,previous,caller) {
    let hostAttempted=false;
    try {
      const [target,home]=await Promise.all([git.request('inspect',{path:receipt.target}),git.request('inspect',{path:receipt.historyHome})]);
      if(target.repository!==home.repository)throw Object.assign(Error('The target no longer belongs to the original repository.'),{executed:false});
      hostAttempted=true;const result=await relocateSession(receipt.sessionId,{commandId:receipt.id,target:receipt.target,expectedExecutionRevision:receipt.executionRevision,...(previous?{reconciles:typeof previous.hostReceipt==='string'?previous.hostReceipt:previous.hostReceipt?.id||previous.id,evidence:receipt.evidence}:{} )});
      if(result.applied!==true) {
        if(result.executed===false){settle(receipt,'rejected',result.reason||'The host refused this move before execution.',result);return;}
        throw Object.assign(Error('The host did not confirm the execution directory.'),{receipt:result.receipt});
      }
      if(resolve(result.executionDirectory)!==resolve(receipt.target))throw Error('The host confirmed a different execution directory; inspect its receipt.');
      const applied=settle({...receipt,hostReceipt:result.receipt,executionRevision:result.executionRevision},'applied','Execution directory changed. Original history and settings remain in place; no input was replayed.',result);
      if(previous)settle({...previous,reconciliation:applied.id},'reconciled','Resolved by a new explicit evidence-based handoff.',result);
    } catch(error) {const hostReceipt=error.receipt||error.data?.receipt,rejected=!hostAttempted||error.executed===false;settle({...receipt,...(hostReceipt?{hostReceipt}:{})},rejected?'rejected':'unknown',rejected?errorDetail(error):'A confirmed boundary was not reached. Inspect and reconcile; no effect was replayed. '+errorDetail(error));}
  }
  async function mutate(operation,args,ctx,caller,commandId) {
    const identity=digest([ctx.session,commandId]),signature=digest([operation,args,caller.origin||'ui']);
    const existing=store.command(ctx.session,identity);if(existing){if(existing.signature!==signature)throw Error('Command identity already has different contents.');return existing.result===undefined?{...existing.value,duplicate:true}:{...existing.result,duplicate:true,receipt:existing.value};}
    let previous,target,record;
    if(operation==='worktree.resolve') {
      previous=store.command(ctx.session,args.id)?.value;
      if(!previous||previous.phase!=='unknown'||previous.revision!==args.expectedRevision||!['worktree.create','worktree.attach','worktree.remove'].includes(previous.operation))throw Error('Inspect the current unknown Git receipt before recording a finding.');
      await finding(ctx,caller,args,previous);
      if(args.resolution==='completed'){
        if(!previous.gitRecordId)throw Error('Original Git identity is unavailable; completion cannot be proved.');
        record=await git.request('get',{id:previous.gitRecordId});
        if(record.sessionId!==ctx.session||record.status!==(previous.operation==='worktree.remove'?'removed':'ready'))throw Error('Durable Git evidence does not prove completion.');
      }
    } else if(operation==='worktree.reconcile') {
      previous=store.command(ctx.session,args.id)?.value;
      if(!previous||previous.phase!=='unknown'||previous.revision!==args.expectedRevision||!handoffs.has(previous.operation))throw Error('Inspect the current unknown handoff before reconciliation.');
      await finding(ctx,caller,args,previous);
      target=previous[args.destination];
    } else if(operation==='worktree.handoff') {
      if(ctx.configurationBusy||store.unresolved(ctx.session))throw Error('A configuration change or handoff remains unresolved.');
      if(ctx.executionRevision!==args.expectedExecutionRevision)throw Error('Execution revision changed; inspect this conversation again.');
      if(args.id!==null){record=row(ctx.session,args.id);if(record.status!=='ready')throw Error('The checkout is not ready.');}
      target=record?.path||ctx.historyHome;
    } else if(operation==='worktree.remove') {
      record=row(ctx.session,args.id);if(record.revision!==args.expectedRevision)throw Error('Checkout revision changed; inspect it again.');
      if(store.reserved(record.path))throw Error('An unresolved operation still reserves this checkout.');
      if(!withDirectoryGuard||!directoryInUse)throw Error('Host directory authority is unavailable; cleanup was not admitted.');
      if((await directoryInUse(record.path)).inUse)throw Error('A session or unresolved handoff still uses this directory.');
      target=record.path;
    }
    if(operation==='worktree.create')target=join(directory,'git','checkouts',gitIdentity(operation,identity));else if(operation==='worktree.attach')target=resolve(args.path);
    if(handoffs.has(operation)&&!relocateSession)throw Error('This host cannot safely relocate a session.');
    const receipt={id:identity,sessionId:ctx.session,operation,phase:'pending',revision:1,createdAt:now(),executionHost,source:ctx.executionDirectory,target:target||null,historyHome:ctx.historyHome,executionRevision:ctx.executionRevision,inputsReplayed:false,origin:caller.origin||'ui',...(['worktree.create','worktree.attach','worktree.remove'].includes(operation)?{gitRecordId:operation==='worktree.remove'?args.id:gitIdentity(operation,identity)}:{}),...(previous?{reconciles:previous.id,evidence:args.evidence,sourceMessageId:args.sourceMessageId}:{}),detail:handoffs.has(operation)?'Saving and releasing the native writer before changing execution directory.':'The operation was admitted; its result has not been confirmed.'};
    store.begin(receipt,signature);changed(ctx.session);
    if(operation==='worktree.resolve'){if(record)store.saveRecord(ctx.session,record);const resolved=settle({...previous,resolutionEvidence:args.evidence,reconciliation:receipt.id},args.resolution==='completed'?'applied':'abandoned',args.resolution==='completed'?'Completion verified from original durable Git evidence; no effect was replayed.':'User finding recorded. Original files and evidence were preserved; no effect was replayed.',record);const current=settle(receipt,'applied','Receipt finding recorded without Git effects.',{resolved});return {...current,resolved};}
    if(handoffs.has(operation)){const job=store.quiescence.effect(()=>finish(receipt,previous,caller)).finally(()=>jobs.delete(job));jobs.add(job);return receipt;}
    try {
      let result;
      if(operation==='worktree.create')result=await git.request('create',{source:ctx.executionDirectory,commandId:identity,sessionId:ctx.session,sourceRevision:args.sourceRevision,mode:args.mode||'clean',ref:args.ref||'HEAD',branch:args.branch});
      else if(operation==='worktree.attach')result=await git.request('attach',{path:args.path,source:ctx.historyHome,commandId:identity,sessionId:ctx.session});
      else result=await withDirectoryGuard(record.path,()=>git.request('remove',{id:args.id,expectedRevision:args.expectedRevision,commandId:identity}));
      store.saveRecord(ctx.session,result);const value=decorate(ctx.session,result),saved=settle(receipt,'applied','The Git operation completed. Source history and manifests were preserved.',value);return {...value,receipt:saved};
    } catch(error) {
      if(error.record){store.saveRecord(ctx.session,error.record);receipt.target=error.record.path;}
      const saved=settle(receipt,error.executed===false?'rejected':'unknown',(error.executed===false?'No Git effect was admitted. ':'Outcome is unconfirmed. Inspect existing evidence; no action was replayed. ')+errorDetail(error));
      throw Object.assign(error,{receipt:saved});
    }
  }
  const quiescenceAccess={'worktree.list':'read'};
  const action=(request,caller={})=>{const pending=quiescenceAccess[request.operation]?performAction(request,caller):store.quiescence.effect(()=>performAction(request,caller));inFlight.add(pending);return pending.finally(()=>inFlight.delete(pending));};
  async function performAction(request,caller={}) {
    if(closed)throw Error('Worktree owner is closed.');
    if(request.version!==1||request.topic!=='worktrees'||!manifest.actions[request.operation])throw Error('Unsupported worktree capability.');
    const args=request.args||{};validateArgs(request.operation,args);if(args.sessionId&&args.sessionId!==request.channel)throw Error('Worktree action cannot change conversation scope.');
    const ctx=await context(request.channel,caller);let result;
    if(reads.has(request.operation)) {
      if(request.operation==='worktree.inspect')result=await inspect(ctx);
      else if(request.operation==='worktree.list')result={collection:args.collection||'worktrees',...store.page(ctx.session,args.collection||'worktrees',args)};
      else {const command=store.command(ctx.session,args.id)?.value;const id=command?.gitRecordId||args.id;if(!command)row(ctx.session,id);result=decorate(ctx.session,await git.request('status',{id}));if(result.sessionId!==ctx.session)throw Error('Git record changed conversation scope.');if(command){store.saveRecord(ctx.session,result);changed(ctx.session);}}
    } else {
      bounded(request.commandId,'durable command identity',512);if(queued>=32)throw Error('Worktree mutation capacity reached; request was not admitted.');queued++;
      const pending=serial.then(async()=>mutate(request.operation,args,await context(request.channel,caller),caller,request.commandId));serial=pending.catch(()=>{});
      try{result=await pending}finally{queued--}
    }
    return {accepted:true,result,...(request.operation!=='worktree.list'?{updates:[await snapshot(ctx.session)]}:{})};
  }
  const resourceProvider={scheme:'amplifier-worktree',async read(params){if(params.channel&&params.channel!=='ahp-root://')throw Error('Resources use the root channel.');const uri=new URL(params.uri);if(uri.hostname!=='records'||!/^\/[a-f0-9-]{36}$/.test(uri.pathname))throw Error('Unknown worktree resource.');const scope=uri.searchParams.get('session');await context(scope);const id=uri.pathname.slice(1);row(scope,id);const offset=Number(uri.searchParams.get('offset')||0);if(!Number.isSafeInteger(offset)||offset<0)throw Error('Invalid manifest cursor.');const value=await git.request('get',{id,offset,limit:100});return {encoding:'utf-8',contentType:'application/json',data:JSON.stringify({...value,...(value.manifest?.nextOffset?{nextPage:recordURI(scope,id,value.manifest.nextOffset)}:{})})}}};
  return {manifest,actionSchemas,read,action,resourceProvider,store,quiescenceAccess,quiescenceParticipant:ownerId=>store.quiescence.participant(ownerId),inspectQuiescence:()=>store.quiescence.inspect(),async idle(){await serial;await Promise.all([...jobs])},async close(){closed=true;await Promise.allSettled([...inFlight]);await serial;await Promise.all([...jobs]);await git.close();store.close()}};
}
