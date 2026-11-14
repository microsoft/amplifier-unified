import React,{useEffect,useRef,useState} from 'react';
import {X,Copy,RefreshCw} from 'lucide-react';
import './conversation-controls.css';
import {readItems} from './attention';
import {isTopLevelChat} from './chat-navigation';
import {sessionIdentity} from './navigation-presentation';
import {ConversationUsage} from './conversation-usage.jsx';
import {settingsPatch} from './settings-navigation';
import {openProblemReport} from './feedback';

function recoveryUnsafe(session){
 return ['working','running','starting','stopping'].includes(session.status)||session.configurationBusy||(session.workers||[]).some(worker=>['queued','starting','running','working','stopping'].includes(worker.status));
}

export function ConversationName({session,act,details=true}){
 const [name,setName]=useState(session.title||''),[saving,setSaving]=useState(false),[error,setError]=useState('');
 const dirty=useRef(false),inFlight=useRef(false);
 const automatic=session.autoName??(session.titleSource!=='manual'&&session.nativeNameSource!=='manual');
 const naming=session.naming?.status==='working';
 const [autoChoice,setAutoChoice]=useState(automatic);
 useEffect(()=>setAutoChoice(automatic),[automatic]);
 useEffect(()=>{if(!dirty.current)setName(session.title||'')},[session.title]);
 async function save(event){
  event.preventDefault();if(inFlight.current||!name.trim())return;
  inFlight.current=true;setSaving(true);setError('');
  try{
   const result=await act('session.rename',{id:session.id,title:name.trim()});
   if(!result||result.accepted===false)throw Error('Could not save the name. Your edit is kept.');
   dirty.current=false;setName(name.trim());
  }catch(error){setError(error.message)}finally{inFlight.current=false;setSaving(false)}
 }
 async function namingAction(args){
  if(inFlight.current)return;inFlight.current=true;setSaving(true);setError('');
  if('automatic' in args)setAutoChoice(args.automatic);
  try{const result=await act('session.naming',{id:session.id,...args});if(!result||result.accepted===false)throw Error('Could not update chat naming.');}
  catch(error){setAutoChoice(automatic);setError(error.message)}finally{inFlight.current=false;setSaving(false)}
 }
 return <><form onSubmit={save} aria-busy={saving||naming}><label htmlFor="session-title">Chat name</label><div className="a-chat-name-row"><input id="session-title" value={name} disabled={saving} required maxLength={200} onChange={event=>{dirty.current=true;setName(event.target.value);setError('')}}/><label className="a-chat-name-auto"><input type="checkbox" aria-label="Automatic chat naming" data-action="session.naming" checked={autoChoice} disabled={saving} onChange={event=>namingAction({automatic:event.target.checked})}/>Auto</label><button type="button" className="a-icon" aria-label="Regenerate chat name" title="Generate a name from this conversation" data-action="session.naming" disabled={saving||naming||name!==session.title} onClick={()=>namingAction({regenerate:true})}><RefreshCw className={naming?'a-name-spinning':undefined}/></button></div><div className="a-dialog-actions"><button type="submit" className="a-soft" data-action="session.rename" disabled={saving||!name.trim()||name===session.title}>{saving?'Saving…':'Save name'}</button></div>{naming&&<p role="status">Generating chat name…</p>}{(error||session.naming?.error)&&<p role="alert" className="a-danger">{error||session.naming.error}</p>}</form>{details&&<ConversationDetails key={session.id} session={session} act={act}/>}</>;

}

export function ConversationFailure({session,failure=session.failure,moduleFailures=session.moduleFailures||[]}){
 if(!failure&&!session.error&&!moduleFailures.length)return null;
 const unknown=!failure||failure.category==='unknown';
 const at=failure?.recordedAt??session.errorAt;
 const date=Number.isFinite(at)&&at>0?new Date(at*1000):null;
 const kind=failure?.errorType;
 return <section aria-label="Recorded error details">
  <p><strong>{moduleFailures.length?'Some parts of this chat could not load':unknown?'Recorded problem':failure.summary}</strong></p>
  <p>{moduleFailures.length?'Review the affected components below.':unknown?'The cause is not available in the saved details. Nothing is automatically sent again.':failure.guidance}</p>
  {date&&Number.isFinite(date.getTime())&&<p>Recorded <time dateTime={date.toISOString()}>{date.toLocaleString()}</time></p>}
  {moduleFailures.length>0&&<ul>{moduleFailures.map((row,index)=><li key={index}><strong>{row.module}</strong>: {row.guidance}</li>)}</ul>}
  <details><summary>Technical details</summary>
   {kind&&!['str','dict','Error'].includes(kind)&&<p>Error type: <code>{kind}</code></p>}
   {failure?.generationId&&<p>Turn ID: <code>{failure.generationId}</code></p>}
   {failure?.inputId&&<p>Input ID: <code>{failure.inputId}</code></p>}
   {failure?.exceptionChain?.length>0&&<pre className="a-error-message">{JSON.stringify(failure.exceptionChain,null,2)}</pre>}
   {failure?.countFailure&&<dl aria-label="Conversation size diagnostic">{failure.countFailure.httpStatus&&<><dt>Service status</dt><dd>{failure.countFailure.httpStatus}</dd></>}{failure.countFailure.attempts&&<><dt>Counting attempts</dt><dd>{failure.countFailure.attempts}</dd></>}{failure.countFailure.requestId&&<><dt>Request ID</dt><dd><code>{failure.countFailure.requestId}</code></dd></>}</dl>}
   {session.error?<><p>Recorded message</p><pre className="a-error-message">{session.error}</pre></>:<p>No additional message was recorded.</p>}
  </details>
 </section>;
}

export function ConversationDetails({session,act,focused=false,open}){
 const [report,setReport]=useState(session.health),[copyReport,setCopyReport]=useState(null),[busy,setBusy]=useState(''),[error,setError]=useState(''),[copied,setCopied]=useState('');
 const inFlight=useRef(false);
 const reportIdentity=JSON.stringify([session.id,session.status,session.failure?.generationId,session.failure?.recordedAt,session.failure?.category,session.failure?.errorType,session.error]);
 const activeIdentity=useRef(reportIdentity);activeIdentity.current=reportIdentity;
 useEffect(()=>{setReport(session.health)},[session.health,reportIdentity]);
 // Equivalent server snapshots must not erase the outcome of a local copy.
 // A different conversation or failure invalidates its captured report.
 useEffect(()=>{setCopied('');setError('');setCopyReport(null)},[reportIdentity]);
 async function inspect(){
  if(inFlight.current)return;inFlight.current=true;setBusy('inspect');setError('');
  try{const result=await act('session.inspect',{id:session.id});if(activeIdentity.current!==reportIdentity)return;if(!result||result.accepted===false)throw Error('Could not inspect this conversation.');if(result.result?.stale)throw Error('The conversation changed while reading its diagnostics. Try copying again.');setReport(result.result);return result.result}catch(error){if(activeIdentity.current===reportIdentity)setError(error.message)}finally{inFlight.current=false;setBusy('')}
 }
 useEffect(()=>{if(!focused)inspect()},[session.id,focused]);
 async function recover(){
  if(inFlight.current||recoveryUnsafe(session)||!recoverable)return;inFlight.current=true;setBusy('recover');setError('');
  try{const result=await act('session.recover',{id:session.id});if(!result||result.accepted===false)throw Error('Could not create the recovery copy.')}catch(error){setError(error.message)}finally{inFlight.current=false;setBusy('')}
 }
 async function copy(value,label){try{await navigator.clipboard.writeText(value);if(activeIdentity.current===reportIdentity)setCopied(label)}catch{if(activeIdentity.current===reportIdentity)setError(label==='Session ID copied'?'Clipboard unavailable. You can select the session ID above.':'Clipboard unavailable. You can select the diagnostic text below.')}}
 async function copyDiagnostics(){
  if(inFlight.current)return;
  setCopied('');setCopyReport(null);
  const pending=inspect().then(current=>{
   if(!current||activeIdentity.current!==reportIdentity)return;
   const captured={...current,client:{userAgent:(navigator.userAgent||'').slice(0,512),language:navigator.language,online:navigator.onLine,visibility:globalThis.document?.visibilityState}};
   setCopyReport(captured);return captured;
  });
  if(navigator.clipboard?.write&&typeof ClipboardItem!=='undefined'){
   // Start the clipboard operation in the click gesture, including on Safari;
   // its promised content waits for the fresh diagnostic snapshot.
   const content=pending.then(current=>{if(!current)throw Error('Diagnostics unavailable');return new Blob([JSON.stringify(current,null,2)],{type:'text/plain'})});
   content.catch(()=>{});
   try{await navigator.clipboard.write([new ClipboardItem({'text/plain':content})]);if(activeIdentity.current===reportIdentity)setCopied('Diagnostics copied')}
   catch{if(activeIdentity.current===reportIdentity)setError(current=>current||'Clipboard unavailable. You can select the diagnostic text below.')}
  }else{const current=await pending;if(current)await copy(JSON.stringify(current,null,2),'Diagnostics copied')}
 }
 const currentReport=report?.status&&report.status!==session.status?undefined:report;
 const identity=sessionIdentity(session),failure=currentReport?.failure||session.failure;
 const moduleFailures=currentReport?.moduleFailures||session.moduleFailures||[];
 const working=recoveryUnsafe(session);
 const recoverable=['context_limit','context_compaction','invalid_image'].includes(failure?.category);
 return <div className="a-conversation-details" aria-busy={!!busy}>
  {focused&&!failure&&!session.error&&!moduleFailures.length&&<p>No current error is recorded for this chat.</p>}
  {focused&&<ConversationFailure session={session} failure={failure} moduleFailures={moduleFailures}/>}
  <p><strong>Session ID</strong><span className="a-session-identity"><code>{identity}</code><button type="button" className="a-icon" aria-label="Copy session ID" title="Copy session ID" onClick={()=>copy(identity,'Session ID copied')}><Copy/></button></span></p>
  {!focused&&<>
   {identity!==session.id&&<p>App ID: <code style={{overflowWrap:'anywhere'}}>{session.id}</code></p>}
   <p style={{overflowWrap:'anywhere'}}>{session.workspace}<br/>Bundle: {session.bundle} · Status: {session.status}</p>
   <ConversationFailure session={session} failure={failure} moduleFailures={moduleFailures}/>
  </>}
  <p className="a-caption">Local diagnostics can contain chat titles, workspace paths and recorded error messages. Review them before sharing; copying does not upload them.</p>
  {recoverable&&<p>If this history can no longer be used, create an independent copy with readable text. Old tool calls and image payloads stay in the original; reattach images if needed. Safety stops remain in effect. Nothing runs until you send a new message.</p>}
  <div className="a-dialog-actions">
   <button type="button" className="a-soft" disabled={!!busy} onClick={copyDiagnostics}>{busy==='inspect'?'Preparing diagnostics…':'Copy diagnostics'}</button>
   {focused&&<button type="button" className="a-soft" onClick={()=>open('chat-help')}>Help &amp; diagnostics</button>}
   {recoverable&&<button type="button" className="a-soft" data-action="session.recover" disabled={!!busy||working} onClick={recover}>{busy==='recover'?'Creating recovery copy…':'Create recovery copy'}</button>}
  </div>
  {working&&recoverable&&<p>Wait for the current work to stop before creating a copy.</p>}
  {copied&&<p role="status">{copied}</p>}{error&&<p role="alert" className="a-danger">{error}</p>}
  {error&&copyReport&&<details><summary>Copy diagnostic text manually</summary><textarea aria-label="Diagnostic report" readOnly rows={12} value={JSON.stringify(copyReport,null,2)} onFocus={event=>event.target.select()}/></details>}
  {!focused&&<ConversationUsage key={session.id} sessionId={session.id} act={act}/>}
 </div>;

}

export function ConversationError({state,session,act}){
 const [recovering,setRecovering]=useState(false),[error,setError]=useState('');
 const inFlight=useRef(false);
 useEffect(()=>setError(''),[session?.id,session?.failure?.recordedAt]);
 if(!session?.failure&&!session?.error)return session?.recovery?<p className="a-hint">Recovery copy · Readable history retained. Old tool and image payloads remain in the original conversation. No work was replayed.</p>:null;
 const item=state.attention?.items?.find(row=>row.id==='session:'+session.id);
 const failure=session.failure;
 if(item?.read)return null;
 const recoverable=['context_limit','context_compaction','invalid_image'].includes(failure?.category),working=recoveryUnsafe(session);
 async function recover(){
  if(inFlight.current||working||!recoverable)return;
  inFlight.current=true;setRecovering(true);setError('');
  try{const response=await act('session.recover',{id:session.id});if(response?.accepted!==true)throw Error('Could not create the recovery copy.')}
  catch(error){setError(error.message)}finally{inFlight.current=false;setRecovering(false)}
 }
 const exceptional=['context_limit','context_compaction','invalid_image','computer_capture_stop','authentication','provider_selection','bundle_configuration','runtime_dependency','persistence'].includes(failure?.category);
 const requestBound=!!(failure?.generationId||failure?.inputId||failure?.inputIds?.length);
 const inputId=failure?.inputId||failure?.inputIds?.[0];
 const failureContext=inputId&&failure?.generationId?{sessionId:session.id,inputId,generationId:failure.generationId}:null;
 async function report(){
  if(inFlight.current||state.view?.feedbackDraft?.pending)return;
  inFlight.current=true;setError('');
  try{await openProblemReport(state,act,failureContext)}catch(error){setError(error.message)}finally{inFlight.current=false}
 }
 return <div className="a-alert" role="alert" data-part="conversation-trouble"><span><strong>{failure?.summary||'Something needs attention.'}</strong> {exceptional?failure.guidance:requestBound?'You can send a new instruction when you’re ready.':'Your saved messages are kept. Review the details below.'}</span>
  {recoverable&&<button type="button" className="a-link" data-action="session.recover" disabled={recovering||working} onClick={recover}>{recovering?'Creating recovery copy…':'Create recovery copy'}</button>}
  {failure?.category==='authentication'&&<button type="button" className="a-link" data-action="view.update" onClick={()=>act('view.update',{patch:settingsPatch('ai-connections')})}>AI connections</button>}
  {failure?.category==='provider_selection'&&<button type="button" className="a-link" data-action="view.update" onClick={()=>act('view.update',{patch:{panel:'chat-models'}})}>Model &amp; tools</button>}
  <details><summary>Details &amp; reporting</summary>
   {recoverable&&<p>A recovery copy keeps readable text, not old tool calls or image payloads. Safety stops remain; nothing is replayed.</p>}
   <button type="button" className="a-link" data-action="view.update" onClick={()=>act('view.update',{patch:{panel:'chat-error'}})}>Technical details</button>
   <button type="button" className="a-link" data-action="view.update" disabled={!!state.view?.feedbackDraft?.pending} onClick={report}>Report a problem</button>
  </details>
  {error&&<p role="alert">{error}</p>}
  {item&&<button type="button" aria-label="Dismiss conversation error" data-action="attention.read" onClick={()=>readItems(act,[item])}><X/></button>}
 </div>;
}

export function ConversationSelect({state,session,choices,onSelect}){
 const [held,setHeld]=useState(null);
 const scope=state.selectedWorkspaceId;
 const rows=held?.scope===scope?held.items:choices.items;
 const hold=()=>setHeld(previous=>previous?.scope===scope?previous:{scope,items:choices.items});
 if(!rows.length)return null;
 return <select className="a-session-select" aria-label="Select conversation" data-action="session.select" value={isTopLevelChat(session)?session?.id||'':''}
  onFocus={hold} onPointerDown={hold} onBlur={()=>setHeld(null)} onKeyDown={event=>{if(event.key==='Escape')setHeld(null)}}
  onChange={event=>{const id=event.target.value;setHeld(null);onSelect(id)}}>
  {(!session||!isTopLevelChat(session))&&<option value="" disabled>{session?'Viewing subagent history':'New chat'}</option>}
  {rows.map(row=><option key={row.id} value={row.id}>{row.title}{state.attention?.sessions?.[row.id]?' · Needs attention':['working','running','starting','stopping'].includes(row.status)?' · '+(row.status==='starting'?'Preparing':row.status==='stopping'?'Stopping':'Working'):''}</option>)}
  {choices.total>rows.length&&<option disabled>Find all chats in the workspace sidebar</option>}
 </select>;
}
