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
 </details>;
}

export function UpdateRestart({updates,act}){
 const target=updates.forceRestartTarget?{...updates.forceRestartTarget}:null;
 const key=JSON.stringify(target||null);
 const [request,setRequest]=React.useState(null),[error,setError]=React.useState('');
 const currentKey=React.useRef(null),pending=React.useRef(null);
 const description=React.useId();
 const version=updates.pendingApp?.latest?.replace(/^v/,'');
 const latest=updates.application?.latest?.replace(/^v/,'');
 React.useLayoutEffect(()=>{
  currentKey.current=key;pending.current=null;setRequest(null);setError('');
  return ()=>{currentKey.current=null;pending.current=null};
 },[key]);
 React.useEffect(()=>{
  const result=updates.forceRestartResult;
  if(request?.key===key&&currentKey.current===key&&request?.id&&result?.requestId===request.id&&result.status==='rejected'){
   pending.current=null;setError(result.detail||'The restart was not accepted. Review the current update.');setRequest(null);
  }
 },[request,key,updates.forceRestartResult]);
 const restart=async()=>{
  // Bind the gesture to the rendered target, not a target reread after a click.
  // The ref also fences repeated events before React commits the busy state.
  if(!target||currentKey.current!==key||pending.current)return;
  const attempt={key,status:'pending'};
  pending.current=attempt;setRequest(attempt);setError('');
  const current=()=>currentKey.current===key&&pending.current===attempt;
  const refused=detail=>{pending.current=null;setRequest(null);setError(detail||'The restart was not accepted. Review the current update.')};
  const unknown=()=>{setRequest({...attempt,status:'unknown'});setError('Restart outcome is unknown. Check update status before taking another action.')};
  try{
   const result=await act('updates.forceRestart',{...target,confirm:true},{propagateError:true});
   if(!current())return;
   if(result?.accepted===false){refused(result.error);return}
   if(result?.accepted===true&&typeof result.requestId==='string'&&result.requestId){
    setRequest({...attempt,id:result.requestId,status:'accepted'});
   }else unknown();
  }catch(error){
   if(!current())return;
   if(error?.accepted===false){refused(error.message);return}
   // A lost response is not a refusal. Do not unlock the same target for replay.
   unknown();
  }
 };
 if(!target)return null;
 return <div className="a-update-force-restart">
  <div id={description}>
   <p>{version?`Restart into prepared Amplifier ${version}.`:'Restart into the prepared update.'}</p>
   {latest&&version&&latest!==version&&<p className="a-caption">Amplifier {latest} is available, but this action uses the prepared version above. Wait for the newer release to finish preparing to use it instead.</p>}
   <p>Running chats, workers, tools, and calls will be interrupted. Saved history and drafts will be kept; interrupted work will not be replayed automatically.</p>
   <p className="a-caption">Restarting does not recover held settings or checkout handoffs. You can wait for work to finish instead.</p>
  </div>
  <button type="button" className="a-soft" data-action="updates.forceRestart" aria-describedby={description} disabled={!!request} onClick={restart}>{request?.status==='unknown'?'Restart outcome unknown':request?'Restart requested…':'Interrupt work and restart'}</button>
  {error&&<p role="alert">{error}</p>}
 </div>;
}
