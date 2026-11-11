import React from 'react';

const workKinds=new Set(['conversation','runtime','worker','schedule','update-boundary','smart-tools','voice','feedback']);
const blockerCounts=items=>{
 const chats=new Set(items.map(item=>item.sessionId).filter(Boolean)).size;
 return `${items.length} reason${items.length===1?'':'s'} · ${chats} distinct chat${chats===1?'':'s'}`;
};

export function UpdateBlockers({updates,act}){
 const blockers=updates.blockers||[];
 if(!blockers.length)return null;
 // Classify only the host's observed rows. Counts do not collapse multiple
 // reasons for one chat or infer that omitted configuration holds recovered.
 const groups=[
  ['work','Running, preparing, and background work',blockers.filter(item=>workKinds.has(item.kind))],
  ['configuration','Configuration recovery holds',blockers.filter(item=>item.kind==='configuration')],
  ['other','Other update blockers',blockers.filter(item=>item.kind!=='configuration'&&!workKinds.has(item.kind))],
 ];
 return <details className="a-update-work" data-part="update-blockers">
  <summary>View update blockers ({blockerCounts(blockers)})</summary>
  {groups.filter(([, ,items])=>items.length).map(([kind,title,items])=><section key={kind} aria-label={title}>
   <h4>{title} ({blockerCounts(items)})</h4>
   <ul>{items.map((item,index)=><li key={index}>{item.label}{item.sessionId&&<> — <button type="button" className="a-link" data-action="session.select" onClick={async()=>{const result=await act('session.select',{id:item.sessionId});if(result&&result.accepted!==false)await act('view.update',{patch:{panel:null,workSurface:'chat'}})}}>{item.title||'Open conversation'}</button></>}</li>)}</ul>
  </section>)}
  <p className="a-caption">Only current update blockers are listed. Configuration holds do not necessarily mean a chat is running, and can remain unresolved even when they no longer block an update. Restarting does not recover held settings or checkout handoffs.</p>
  <UpdateRestart updates={updates} act={act}/>
 </details>;
}

export function UpdateRestart({updates,act}){
 const target=updates.forceRestartTarget;
 const key=JSON.stringify(target||null);
 const [confirmation,setConfirmation]=React.useState(null),[submitting,setSubmitting]=React.useState(false),[error,setError]=React.useState('');
 const [request,setRequest]=React.useState(null);
 const button=React.useRef(null),cancelButton=React.useRef(null),restoreFocus=React.useRef(false);
 const current=confirmation?.key===key;
 const latest=updates.application?.latest?.replace(/^v/,'');
 const cancel=()=>{restoreFocus.current=true;setConfirmation(null);setError('')};
 React.useEffect(()=>{
  if(confirmation)cancelButton.current?.focus();
  else if(restoreFocus.current){restoreFocus.current=false;button.current?.focus()}
 },[confirmation]);
 React.useEffect(()=>{
  const result=updates.forceRestartResult;
  if(request&&result?.requestId===request.id&&result.status==='rejected'){
   setSubmitting(false);setError(result.detail);setRequest(null);
  }else if(request&&request.key!==key){setSubmitting(false);setRequest(null)}
 },[request,key,updates.forceRestartResult]);
 const confirm=async()=>{
  if(!current||submitting)return;
  setSubmitting(true);setError('');
  try{
   const result=await act('updates.forceRestart',{...confirmation.target,confirm:true});
   if(result?.accepted===false)throw Error(result.error||'The restart was not accepted. Review the current update.');
   setRequest({id:result?.requestId,key:confirmation.key});
   setConfirmation(null);
  }catch(error){setError(error?.message||'The restart could not be confirmed. Check update status before trying again.');setSubmitting(false)}
 };
 if(!target&&!confirmation)return null;
 return <div className="a-update-force-restart">
  {target&&!confirmation&&<button ref={button} type="button" className="a-soft" disabled={submitting} onClick={()=>{setError('');setConfirmation({key,target:{...target},version:updates.pendingApp?.latest?.replace(/^v/,'')})}}>Force restart…</button>}
  {confirmation&&<div role="alertdialog" aria-label="Confirm update restart" onKeyDown={event=>{if(event.key==='Escape'&&!submitting){event.stopPropagation();cancel()}}}>
   <p><strong>Interrupt active work and restart?</strong></p>
   <p>{confirmation.version?`This restarts into prepared Amplifier ${confirmation.version}.`:'This restarts into the prepared update.'}</p>
   {current&&latest&&confirmation.version&&latest!==confirmation.version&&<p>Amplifier {latest} is available, but this confirmation uses the prepared version above. Cancel and let the newer release finish preparing to use it instead.</p>}
   <p>Running chats, background workers, tools, and calls will be interrupted. Saved conversation history and drafts will be kept. Interrupted work will not be replayed automatically.</p>
   <p>Unresolved checkout handoffs will still require inspection after the restart.</p>
   {!current&&<p role="alert">The staged update or host changed. Cancel and review the current update before confirming again.</p>}
   <div className="a-dialog-actions">
    <button type="button" className="a-primary" data-action="updates.forceRestart" disabled={!current||submitting} onClick={confirm}>{submitting?'Restart requested…':'Interrupt work and restart'}</button>
    <button ref={cancelButton} type="button" className="a-soft" disabled={submitting} onClick={cancel}>Cancel</button>
   </div>
  </div>}
  {error&&<p role="alert">{error}</p>}
 </div>;
}
