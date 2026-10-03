// Independent protocol peer: qualifies composition, not native mutation behavior.
import {createInterface} from 'node:readline';
import {randomUUID} from 'node:crypto';
import {appendFileSync,readFileSync,writeFileSync,existsSync} from 'node:fs';
import {join} from 'node:path';
const root=process.env.CONTEXT_FIXTURE_ROOT,log=join(root,'rpc.jsonl'),store=join(root,'native.json');
const load=()=>existsSync(store)?JSON.parse(readFileSync(store)): {sessions:{},receipts:{}};
const revision='a'.repeat(64),next='b'.repeat(64);
const contextClear={version:1,review:'context.clear.review',apply:'context.clear',receipt:'_amplifier/context/receipt',preservesHistory:true,clearsExplicitGoal:true,activeTask:'pause-required',replayUnknown:false};
const send=value=>process.stdout.write(JSON.stringify({jsonrpc:'2.0',...value})+'\n');
createInterface({input:process.stdin}).on('line',line=>{
 const {id,method,params={}}=JSON.parse(line);if(id===undefined)return;
 appendFileSync(log,JSON.stringify({method,params})+'\n');
 const native={version:1,relocation:{version:1},...(process.env.CONTEXT_FIXTURE_OLDER?{}:{contextClear})};
 if(method==='initialize')return send({id,result:{protocolVersion:1,agentCapabilities:{sessionCapabilities:{resume:{},close:{}},_meta:{'amplifier.dev/native':native}},authMethods:[],_meta:{'amplifier.dev/native':native}}});
 const state=load(),save=()=>writeFileSync(store,JSON.stringify(state));
 if(method==='session/new'){
  if(existsSync(join(root,'cold')))return send({id,error:{code:-32000,message:'No worker admission allowed'}});
  const sessionId=randomUUID();state.sessions[sessionId]={historyCwd:params.cwd,executionDirectory:params.cwd,executionRevision:0};save();return send({id,result:{sessionId,configOptions:[]}});
 }
 if(method==='session/resume'&&existsSync(join(root,'cold')))return send({id,error:{code:-32000,message:'No worker admission allowed'}});
 if(['session/resume','session/close'].includes(method))return send({id,result:{}});
 if(method==='_amplifier/context/receipt'){
  const row=state.receipts[params.commandId];return send({id,result:{receipt:row&&row.sessionId===params.sessionId&&row.cwd===params.cwd?row.receipt:null}});
 }
 if(method==='_amplifier/relocation')return send({id,result:{sessionId:params.sessionId,...state.sessions[params.sessionId],blocked:false}});
 if(method==='_amplifier/native'){
  if(params.operation==='session.relocate'){Object.assign(state.sessions[params.sessionId],{executionDirectory:params.args.target,executionRevision:1});save();return send({id,result:{applied:true,...state.sessions[params.sessionId]}});}
  if(params.operation==='context.clear.review')return send({id,result:{historyRevision:revision,controlRevision:revision,canClear:true,goalPresent:true,goalAction:'clear',task:null}});
  if(params.operation==='context.clear'){
   const result={cleared:true,historyPreserved:true,eventsPreserved:true,goalCleared:true,archiveId:next,previousHistoryRevision:revision,historyRevision:next};
   state.receipts[params.args.commandId]={sessionId:params.sessionId,cwd:state.sessions[params.sessionId].historyCwd,receipt:{version:1,operation:'context.clear',commandId:params.args.commandId,status:'succeeded',createdAt:1,completedAt:2,result}};save();return send({id,result});
  }
  return send({id,result:{}});
 }
 send({id,error:{code:-32601,message:'Fixture method unavailable'}});
});
