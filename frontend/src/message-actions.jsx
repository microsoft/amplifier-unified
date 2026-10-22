import {messageTime} from './message-time';
import {MessageInteractions,QuoteCard,QuotedDocument,AssistantReactions} from './message-interactions';
import {MessageDelivery} from './message-delivery';
import {DetailText,readDetail} from './conversation-detail';
import React,{memo,useEffect,useMemo,useRef,useState} from 'react';
import {Copy,Check,Pencil,GitBranch,ArrowUp,AlertCircle,RotateCcw,LoaderCircle} from 'lucide-react';
import {AttachmentStrip} from './chat-controls';
import {usePendingMessageEdit} from './pending-message-edits';
import {InlineMedia} from './inline-media';
import {ChatReferences} from './chat-reference';
import {PeerMessage,isPeerMessage} from './peer-message';

export function newestMessageIds(messages){
 const latest=new Map();
 for(const message of messages)if(!message.observation&&['user','assistant'].includes(message.role))latest.set(message.role,message.id);
 return new Set(latest.values());
}

export function completedTurnEnds(session){
 const messages=session?.messages||[],ends=new Map();
 const numbered=session?.messageWindow?.mode!=='cursor'||Number.isInteger(session?.sharedHistoryUserTurnOffset);
 let turn=Number(session?.sharedHistoryUserTurnOffset)||0;
 for(let i=0;i<messages.length;i++){
  if(messages[i].role!=='user')continue;
  turn++;
  let end=i+1;while(end<messages.length&&messages[end].role!=='user')end++;
  const last=messages.slice(i+1,end).findLast(m=>m.role==='assistant');if(!last)continue;
  const record=session.execution?.turns?.find(t=>t.inputId===messages[i].inputId&&messages[i].inputId);
  const finished=record?['completed','complete','done'].includes(record.phase||record.status):end<messages.length||!['working','running','starting','stopping','error'].includes(session.status);
  if(finished&&messages[i].id&&!messages[i].displayOnly&&!last.displayOnly)ends.set(last.id,{messageId:messages[i].id,turn:numbered?turn:null});
 }
 return ends;
}

// Controls still observe every current permission, edit and delivery state.
// Only text rendering is isolated, with all of its inputs explicit.
const MessageText=memo(function MessageText({text,reference,role,sessionId,messageId,workspace,act,displayOnly}){
 const writingContext=useMemo(()=>role==='assistant'&&!displayOnly?{sessionId,messageId,act}:undefined,[role,sessionId,messageId,act,displayOnly]);
 const fileContext=useMemo(()=>role==='assistant'?{sessionId,workspace,act}:undefined,[role,sessionId,workspace,act]);
 return <DetailText text={text} reference={reference} markdown userContent={role==='user'} automatic={role==='assistant'} writingContext={writingContext} fileContext={fileContext}/>;
});

export function MessageEntry({message:m,session,state,act,stamp,working,forkTurn,retry,discard,dispatch=act,expandedObservation=false,newest=false,omitMedia=false}){
 const [loadingEdit,setSaving]=useState(false),[localCopied,setLocalCopied]=useState(false),[copying,setCopying]=useState(false),[detailError,setDetailError]=useState(''),edit=state.view?.messageEdit,editing=edit?.sessionId===session.id&&edit?.messageId===m.id;
 const pendingEdit=usePendingMessageEdit(JSON.stringify([state.client?.hostInstanceId,state.client?.id,session.id,m.inputId||m.commandId||m.id]),setDetailError),saving=loadingEdit||pendingEdit.pending;
 const [text,setText]=useState(editing?edit.text:''),pendingText=useRef(null),submitting=useRef(false);
 useEffect(()=>{if(!editing){pendingText.current=null;return}if(pendingText.current===null||edit.text===pendingText.current){setText(edit.text||'');pendingText.current=null}},[editing,edit?.text]);
 const date=m.timestampKnown!==false&&Number.isFinite(m.createdAt)?messageTime(m.createdAt):null;
 const copy=state.view?.messageCopy,copied=copy?.sessionId===session.id&&copy?.messageId===m.id?copy:null;
 const blocked=working||session.configurationBusy||session.workspaceAvailable===false||!!session.historyReadOnlyReason;
 const patch=value=>act('view.update',{patch:{messageEdit:value}});
 const localDelivery=m.localDelivery,delivery=localDelivery||(m.delivery?.status&&m.delivery.status!=='accepted'?m.delivery:null);
 const quoted=m.replyTo?.version===2;
 const editLocked=saving||blocked||quoted||!!delivery&&delivery.status!=='failed';
 const external=m.role==='user'&&!!(m.attribution?.caption||['agent','peer','scheduler'].includes(m.inputOrigin)||m.via==='peer'||m.via==='schedule');
 const attribution=m.attribution?.caption||(m.inputOrigin==='agent'?'Sent by Amplifier':m.inputOrigin==='scheduler'?'Scheduled message':null);
 const label=m.role==='user'?(attribution||(external||m.nativeInputId?'Message':'Your message')):'Amplifier message';
 const submit=async e=>{
  e.preventDefault();if(submitting.current||pendingEdit.isPending()||editLocked||!text.trim())return;
  submitting.current=true;setSaving(true);
  try{setDetailError('');await pendingEdit.run(()=>localDelivery?retry(m,text):dispatch('message.edit',{sessionId:session.id,messageId:m.id,text,mode:edit?.fork?'fork':'current'}))}
  catch(error){setDetailError(error.message)}
  finally{submitting.current=false;setSaving(false)}
 };
 if(m.observation)return null;
 if(isPeerMessage(m))return <PeerMessage message={m} session={session} state={state} act={act} dispatch={dispatch} date={date} label={label} attribution={attribution} newest={newest}/>;
 return <article className={`a-message a-${external?'external-message':m.role==='user'?'user':'assistant'}`} data-message-id={m.id} data-input-id={m.inputId||m.commandId} data-newest={newest||undefined} aria-label={label}>
  <div className="a-message-body">
  {m.replyTo?.version!==2&&<QuoteCard quote={m.replyTo} sessionId={session.id} dispatch={dispatch}/>}<AttachmentStrip items={m.attachments} act={act} sessionId={session.id}/>{detailError&&<p role="alert">{detailError}</p>}
  {editing?<form className="a-message-editor" aria-busy={saving||undefined} onSubmit={submit}>
   <textarea data-private="true" data-view-label="Edit message" autoFocus aria-label="Edit your message" value={text} readOnly={editLocked} aria-busy={saving||undefined} data-action="view.update" onChange={e=>{if(editLocked||submitting.current||pendingEdit.isPending())return;setText(e.target.value);pendingText.current=e.target.value;patch({...edit,text:e.target.value})}} onKeyDown={e=>{if(e.key==='Escape'||e.key==='Enter'&&(e.metaKey||e.ctrlKey)){e.preventDefault();if(editLocked||submitting.current||pendingEdit.isPending())return;if(e.key==='Escape')patch(null);else e.currentTarget.form.requestSubmit()}}}/>
   <p className="a-caption">{localDelivery?'Update this unsent message and try again.':edit?.fork?'Create and open a separate edited chat. This original chat and its pin stay unchanged.':'Continue in this chat, keeping its name, workspace and pin. Later messages leave the active conversation; saved event history and earlier tool effects remain.'}</p>
   {!localDelivery&&<label className="a-inline-checkbox"><input type="checkbox" checked={!!edit?.fork} disabled={editLocked} aria-busy={saving||undefined} data-action="view.update" onChange={e=>{if(editLocked||submitting.current||pendingEdit.isPending())return;patch({...edit,text,fork:e.target.checked})}}/>Create a separate chat (fork)</label>}
   <div className="a-message-edit-actions"><button className="a-soft" type="button" disabled={editLocked} data-action="view.update" onClick={()=>{if(editLocked||submitting.current||pendingEdit.isPending())return;patch(null)}}>Cancel</button><button className="a-primary" type="submit" disabled={editLocked||!text.trim()} data-action="message.edit" data-operation-pending={saving||undefined} aria-busy={saving||undefined}><ArrowUp/>{saving?'Starting…':!localDelivery&&edit?.fork?'Create edited chat':'Save & regenerate'}</button></div>
  </form>:m.replyTo?.version===2?<QuotedDocument document={m.replyTo} sessionId={session.id} dispatch={dispatch}/>:<MessageText text={m.text|| (working?'…':'')} reference={m.textDetail} role={m.role} sessionId={session.id} messageId={m.id} workspace={session.workspace} act={act} displayOnly={m.displayOnly}/>}
  {m.role==='assistant'&&!m.displayOnly&&!m.ephemeral&&!localDelivery&&!editing&&!!m.id&&<ChatReferences key={`${session.id}:${m.id}`} sessionId={session.id} messageId={m.id} revision={session.chatReferenceRevision||''} available={session.linkedChatReferencesAvailable} configurationBusy={!!session.configurationBusy} dispatch={dispatch}/>}
  {m.role==='assistant'&&!m.displayOnly&&!editing&&!omitMedia&&<InlineMedia sessionId={session.id} messageId={m.id} mediaRevision={session.mediaRevision||0} linkedMediaAvailable={session.linkedMediaAvailable} configurationBusy={!!session.configurationBusy} dispatch={dispatch}/>}
  </div>
  <AssistantReactions message={m}/>
  {attribution&&<p className="a-message-attribution">{attribution}</p>}
  {!editing&&<div className="a-message-actions" data-delivery-problem={!!delivery||undefined}>
   <button type="button" className="a-icon" title="Copy as Markdown" aria-label="Copy message as Markdown" data-action="message.copy" disabled={copying} data-operation-pending={copying||undefined} aria-busy={copying||undefined} onClick={async()=>{if(!localDelivery&&!m.displayOnly){await act('message.copy',{sessionId:session.id,messageId:m.id});return}setCopying(true);try{await navigator.clipboard.writeText(m.textDetail?await readDetail(m.textDetail):m.text);setLocalCopied(true)}catch(error){setDetailError(error.message)}finally{setCopying(false)}}}>{copied?.status==='ready'||localCopied?<Check/>:<Copy/>}</button>
   {!localDelivery&&!m.displayOnly&&['user','assistant'].includes(m.role)&&<MessageInteractions message={m} sessionId={session.id} dispatch={dispatch}/>}
   {m.role==='user'&&!external&&!m.displayOnly&&<button type="button" className="a-icon" title={quoted?'Quoted messages retain exact references. Send a correction instead.':session.historyReadOnlyReason|| (session.workspaceAvailable===false?'Workspace folder unavailable':blocked?'Wait for the current work to finish':'Edit message')} aria-label="Edit message" disabled={editLocked} data-operation-pending={saving||undefined} aria-busy={saving||undefined} data-action="view.update" onClick={async()=>{if(editLocked||submitting.current||pendingEdit.isPending())return;setSaving(true);try{patch({sessionId:session.id,messageId:m.id,text:m.textDetail?await readDetail(m.textDetail):m.text,fork:false})}catch(e){setDetailError(e.message)}finally{setSaving(false)}}}><Pencil/></button>}
   {forkTurn&&!m.displayOnly&&<ForkTurn session={session} turn={forkTurn} act={act} working={working}/>}
   <MessageDelivery message={m} session={session} delivery={delivery} localDelivery={localDelivery} dispatch={dispatch} retry={retry} discard={discard}/>

   {copied?.status==='ready'&&<span role="status" className="a-copy-result success">Copied Markdown</span>}
   {copied?.status==='error'&&<span role="alert" className="a-copy-result error"><AlertCircle/>{copied.message||'Could not copy'}</span>}
   {m.via&&!(m.via==='peer'&&attribution)&&<span className="a-message-channel">{m.via==='observation'?'Background follow-up':`via ${m.via}`}</span>}
   {date&&<time className="a-message-date" aria-label={date.full} dateTime={date.iso}>{date.text}</time>}
  </div>}
 </article>;
}
export function ForkTurn({session,turn,act,working}){
 return <button type="button" className="a-icon" title={session.historyReadOnlyReason|| (session.workspaceAvailable===false?'Workspace folder unavailable':working?'Wait for the current work to finish':'Fork a new chat from here')} aria-label={turn.turn===null?'Fork conversation after this turn':`Fork conversation after turn ${turn.turn}`} disabled={working||session.configurationBusy||session.workspaceAvailable===false||!!session.historyReadOnlyReason} data-action="session.fork" onClick={()=>act('session.fork',{id:session.id,afterMessageId:turn.messageId})}><GitBranch/></button>;
}
