import {createCoordinationCapabilities} from '@amplifier/unified-coordination-capability';
import {launcher} from './owners.js';

// Token streaming does not change coordination state. Selected waits wake only
// for lifecycle, attention, native-worker or explicit owner notifications.
const relevant = new Set(['session/titleChanged','session/inputNeededSet',
 'session/inputNeededRemoved','chat/turnStarted','chat/turnComplete',
 'chat/turnCancelled','chat/turnResume','chat/toolCallConfirmed']);

export async function composeCoordination(config,context,{host,operations,admit,catalog,activeInputProof=false,peerInput=false,peerSteering=false,peerResults=false}){
 const readAttention=async session=>{
  const [questions,task]=await Promise.allSettled([
   operations?operations.readQuestionAttention(session):Promise.reject(Error('Question owner unavailable')),
   host().readTaskState(session),
  ]);
  const questionState=questions.status==='fulfilled'?questions.value:null;
  const taskState=task.status==='fulfilled'&&task.value.available?task.value:null;
  const source=taskState?.task;
  const taskSummary=source?Object.fromEntries(['id','status','revision'].filter(key=>key in source).map(key=>[key,source[key]])):null;
  if(taskSummary&&Array.isArray(source.questionIds))taskSummary.questionIds=source.questionIds.slice(0,64);
  const taskTruncated=Array.isArray(source?.questionIds)&&source.questionIds.length>64;
  return {questionIds:questionState?.questionIds??null,task:taskSummary,
   attentionComplete:questionState?.questionsComplete===true&&!!taskState&&!taskTruncated,
   attentionCoverage:{...questionState?.attentionCoverage,questions:!!questionState,task:!!taskState,taskQuestionsTruncated:taskTruncated},
   omissions:[...(!questionState?['Question owner unavailable']:[]),...(!taskState?['Passive task state unavailable']:[]),...(taskTruncated?['Task question IDs exceed the selected bound']:[])]};
 };
 return createCoordinationCapabilities({
  owner:await launcher('coordination','amplifier_unified_coordination.server',config,context),
  history:{inspect:session=>host().inspectRecallSource(session),read:(session,args)=>host().readRecallSource(session,args)},
  ...(catalog&&activeInputProof?{grants:{
   inspect:async session=>{
    const state=await host().inspectSession(session),indexed=await catalog.get(session);
    if(!indexed||indexed.nativeSessionId!==state.nativeSessionId||indexed.workingDirectory!==state.workingDirectory)throw Error('The root identity is not indexed; refresh the conversation library');
    return {sessionId:session,nativeSessionId:state.nativeSessionId,kind:indexed.kind,parentSessionId:indexed.parentUri,
     workspace:state.executionDirectory,locationRevision:state.nativeLocationRevision??0,interruptionRevision:state.interruptionRevision,
     blocked:!!(state.relocationFence||state.transferFence||indexed.nativeDeleted||indexed.productHidden||indexed.availability!=='available')};
   },
   input:args=>args.active?host().readActiveUserMessage(args.session,args.messageId,args.actorId):host().readUserMessage(args.session,args.messageId),
   review:async args=>{
    try{await host().confirmCapability(args.session,{operation:'coordination.grant',title:'Allow these chats to collaborate?',args:{proposalId:args.proposalId,scope:args.scope,sourceText:args.sourceText.slice(0,12000),sourceTextTruncated:args.sourceText.length>12000}});return {decision:'allow'};}
    catch(error){return error?.data?.decision==='deny'?{decision:'deny'}:{pending:true};}
   },
  }}:{}),
  ...(catalog&&activeInputProof&&peerInput?{delivery:{
   ...(peerResults?{results:{active:(session,inputId,actorId)=>host().readActivePeerInput(session,inputId,actorId)}}:{}),
   inspect:async session=>{const task=await host().readTaskState(session),state=await host().inspectSession(session);return {...state,available:task.available===true,task:task.task,blocked:!!(state.relocationFence||state.transferFence)};},
   submit:(session,input)=>admit('submitPeer',session,input),
   ...(peerSteering?{steering:{submit:(session,input)=>admit('submitPeerSteering',session,input),inspect:(session,id)=>host().inspectPeerSteering(session,id)}}:{}),
  }}:{}),
  listCoordinationSessions:args=>host().listCoordinationSessions(args),
  readCoordinationSession:(session,args)=>host().readCoordinationSession(session,args),
  readCoordinationWorkers:(session,args)=>host().readCoordinationWorkers(session,args),
  readCoordinationAttention:readAttention,
  controlCoordinationWorker:(session,operation,args)=>admit('nativeControlExisting',session,operation,args),
  controlCoordinationSession:async args=>{
   // An authenticated agent has no browser client ID. Give its host command a
   // stable agent-only identity; never borrow an attached human client's ID.
   const clientId=args.origin==='agent'&&!args.clientId?'agent-coordination:'+args.session:args.clientId;
   if(args.operation==='followup')return admit('submitTurn',args.session,{
    commandId:args.commandId,text:args.text,clientId,origin:args.origin});
   const current=await host().inspectSession(args.session);
   if(!current.activeTurnId)return {accepted:false,executed:false,reason:'No active turn to interrupt'};
   return admit('interruptSession',args.session,{commandId:args.commandId,
    turnId:current.activeTurnId,clientId,origin:args.origin});
  },
  observeSession:(session,listener)=>host().observeSession(session,event=>{
   if(relevant.has(event.action.type))return listener();
  }),
  onInvalidate:context.onInvalidate,
 });
}
