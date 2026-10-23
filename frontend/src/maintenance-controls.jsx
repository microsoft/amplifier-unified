import React from 'react';
import {useModalFocus} from './responsive-navigation';
import './maintenance-controls.css';

const phases={
 open:['Work can run','Pause all holds new work immediately, then waits for running work to reach a safe stopping point.'],
 pausing:['Pausing work','New work is held. Running tools and calls may still be finishing before work can pause safely.'],
 paused:['Work is paused','New work is held. Resume restores only work saved by this pause that is still eligible.'],
 resuming:['Resuming paused work','Checking saved work against current stops, permissions and limits.'],
 stopping:['Stopping work','New work is held. Waiting for owned execution to confirm that it stopped.'],
 uncertain:['Work needs attention','New work is held. Some work could not be confirmed safe to pause or stop. Inspect its status before continuing.'],
};
const rootLabels={waiting:'Waiting for a safe point',suspended:'Paused',unsupported:'Cannot pause safely',uncertain:'Needs inspection',stopped:'Stopped',quiescent:'Idle',resumed:'Resumed',blocked:'Kept stopped',stopping:'Stopping','resume-requested':'Resume requested',resuming:'Resuming'};
const identity=value=>JSON.stringify([value?.epoch,value?.revision]);
const failure=result=>{if(result?.accepted!==true)throw Error(result?.error||'The request was not accepted.');return result};

// Both ordinary Activity and Updates consume this one host-owned projection.
// Local state describes only a pending user interaction, never pause readiness.
export function MaintenanceControls({state,act,active=true}){
 const status=state.hostMaintenance,latest=React.useRef(status);latest.current=status;
 const [request,setRequest]=React.useState(null),[error,setError]=React.useState(''),[confirmation,setConfirmation]=React.useState(null);
 const [previewing,setPreviewing]=React.useState(false),[expanded,setExpanded]=React.useState(false),[offset,setOffset]=React.useState(0),[page,setPage]=React.useState(null),[pageError,setPageError]=React.useState('');
 const pending=React.useRef(false),previewToken=React.useRef(0),dialog=React.useRef(null),mounted=React.useRef(true),stopButton=React.useRef(null),restoreFocus=React.useRef(false);
 React.useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;previewToken.current++}},[]);
 React.useEffect(()=>{if(!active){previewToken.current++;setConfirmation(null);setPreviewing(false)}},[active]);
 function cancel(){restoreFocus.current=true;setConfirmation(null)}
 useModalFocus(dialog,!!confirmation,()=>{if(!pending.current)cancel()});
 React.useLayoutEffect(()=>{if(!confirmation&&restoreFocus.current){restoreFocus.current=false;stopButton.current?.focus()}},[confirmation]);
 const key=identity(status),currentConfirmation=confirmation?.key===key;
 React.useEffect(()=>{
  if(!active||!expanded||!status)return;
  let current=true;setPage(null);setPageError('');
  Promise.resolve(act('maintenance.status',{offset,limit:25})).then(result=>{
   const value=failure(result)?.result;
   if(current&&identity(latest.current)===key&&identity(value)===key)setPage(value);
  }).catch(e=>{if(current)setPageError(e.message||'Could not inspect paused work. Close and reopen these details to try again.')});
  return()=>{current=false};
 },[active,expanded,offset,key,act]);
 async function submit(operation){
  if(pending.current)return;
  pending.current=true;setRequest({...operation,phase:'sending'});setError('');
  try{
   failure(await act(operation.action,operation.args,{id:operation.id}));
   if(mounted.current){setRequest(null);setConfirmation(null)}
  }catch(e){
   if(mounted.current){
    setRequest(e.status>=400&&e.status<500?null:{...operation,phase:'uncertain'});
    setConfirmation(null);setError(e.message||'The request result could not be confirmed.');
   }
  }finally{pending.current=false}
 }
 function command(action,args={}){
  if(request||pending.current)return;
  return submit({action,args,id:crypto.randomUUID()});
 }
 async function preview(){
  if(request||pending.current||previewing)return;
  const token=++previewToken.current,captured=identity(latest.current);setPreviewing(true);setError('');
  try{
   const result=failure(await act('maintenance.stopPreview',{}))?.result;
   if(!mounted.current||token!==previewToken.current)return;
   if(identity(latest.current)!==captured)throw Error('Work changed while preparing confirmation. Review its status and choose Stop now again.');
   if(!result?.hostInstanceId||!result?.targetDigest)throw Error('The host could not identify the work to stop.');
   setConfirmation({key:captured,target:{hostInstanceId:result.hostInstanceId,targetDigest:result.targetDigest},count:result.ownedRoots});
  }catch(e){if(mounted.current&&token===previewToken.current)setError(e.message||'Could not prepare the Stop confirmation.')}
  finally{if(mounted.current&&token===previewToken.current)setPreviewing(false)}
 }
 if(!status)return null;
 const counts=status.counts||{},attention=(counts.unsupported||0)+(counts.uncertain||0);
 const [title,detail]=status.phase==='uncertain'&&!status.held?['Some resumed work needs inspection','New work can run, but some resumed work was not confirmed. Inspect it or choose Pause all to capture current work.']:status.phase==='paused'&&!status.readyForRestart?['Work is held','New work is held. Some running work has not confirmed a safe pause. Inspect its status below.']:status.readyForRestart&&!counts.suspended&&counts.stopped>0?['Work has stopped','New work remains held. Stopped execution will not resume automatically.']:phases[status.phase]||['Work status unavailable','Inspect the saved status before starting or stopping work.'];
 const busy=!!request||previewing;
 const detailsCurrent=page&&identity(page)===key;
 return <section className="a-maintenance-controls" aria-label="Pause and stop work" data-part="host-maintenance">
  <h3>Pause and stop work</h3>
  <div role="status" aria-live="polite"><strong>{title}</strong><p>{detail}</p>
   {status.readyForRestart&&<p>All tracked work is paused, stopped or idle. Work stays paused after a restart until you choose Resume.</p>}
   {!!attention&&<p>{attention} {attention===1?'chat needs':'chats need'} inspection before work can resume.</p>}
   {!!counts.blocked&&<p>{counts.blocked} {counts.blocked===1?'chat will':'chats will'} stay stopped because its authority or limits changed.</p>}
   {status.held&&!status.inventoryComplete&&<p>The host could not confirm all running work. It is not ready for a safe restart.</p>}
   {!!counts.waiting&&<p>{counts.waiting} {counts.waiting===1?'chat is':'chats are'} still reaching a safe point. Waiting will not automatically stop it.</p>}
   {status.held&&!!status.auxiliary?.items?.length&&<ul>{status.auxiliary.items.map((row,index)=><li key={row.kind+'-'+index}><strong>{row.label}</strong><p className="a-caption">{row.detail}</p></li>)}</ul>}
   {status.auxiliary?.scope==='host-observed'&&<p className="a-caption">This status covers work observed by this host. It does not confirm that work already accepted by an external service has stopped.</p>}
  </div>
  <div className="a-dialog-actions">
   {status.held?<button type="button" className="a-primary" data-action="maintenance.resume" disabled={busy||status.canResume!==true||!status.epoch} onClick={()=>command('maintenance.resume',{epoch:status.epoch})}>Resume paused work</button>
    :<button type="button" className="a-soft" data-action="maintenance.pause" disabled={busy||status.canPause!==true} onClick={()=>command('maintenance.pause')}>Pause all</button>}
   <button ref={stopButton} type="button" className="a-soft a-danger" data-action="maintenance.stopPreview" disabled={busy} onClick={preview}>{previewing?'Checking work…':'Stop now…'}</button>
  </div>
  {status.held&&!status.canResume&&<p className="a-caption">Resume becomes available after the host confirms all tracked work is safely paused, stopped or idle.</p>}
  <p className="a-caption">Stop now interrupts owned work. It cannot undo changes or retract requests already accepted by another service.</p>
  {request?.phase==='sending'&&<p role="status">Sending your request…</p>}
  {error&&<p role="alert">{error}</p>}
  {request?.phase==='uncertain'&&<div><p>The request result is unconfirmed. No new request has been sent automatically.</p><button type="button" className="a-soft" onClick={()=>submit(request)}>Retry same request</button></div>}
  <details open={expanded} onToggle={event=>{setExpanded(event.currentTarget.open);if(!event.currentTarget.open)setOffset(0)}}>
   <summary>View work status{status.total?` (${status.total})`:''}</summary>
   {expanded&&(pageError?<p role="alert">{pageError}</p>:!detailsCurrent?<p role="status">Checking work status…</p>:<>
    {!page.roots?.length&&<p>No owned work is recorded for this pause.</p>}
    <ul>{(page.roots||[]).map(row=><li key={row.sessionId}><strong>{state.sessions?.find(item=>item.id===row.sessionId)?.title||'Chat '+row.sessionId.slice(0,8)}</strong> — {rootLabels[row.phase]||'Needs inspection'}{row.reason&&<p className="a-caption">{row.reason}</p>}</li>)}</ul>
    <div className="a-dialog-actions">{offset>0&&<button type="button" className="a-soft" onClick={()=>setOffset(Math.max(0,offset-25))}>Previous work</button>}{page.hasMore&&<button type="button" className="a-soft" onClick={()=>setOffset(offset+25)}>Next work</button>}</div>
   </>)}
  </details>
  {confirmation&&<div className="a-maintenance-confirmation">
   <div ref={dialog} role="alertdialog" aria-modal="true" aria-label="Confirm Stop now" tabIndex={-1}>
    <h4>Stop owned work now?</h4>
    <p>This interrupts {confirmation.count??'the'} owned {confirmation.count===1?'chat':'chats'} and their workers. Amplifier stays open for inspection. No update or restart is requested.</p>
    <p>Saved conversations and drafts remain. Accepted remote requests and changes already made cannot be undone. Stopped work will not resume automatically.</p>
    {!currentConfirmation&&<p role="alert">Work changed. Cancel and review a fresh Stop confirmation.</p>}
    <div className="a-dialog-actions">
     <button type="button" className="a-soft" disabled={request?.phase==='sending'} onClick={cancel}>Cancel</button>
     <button type="button" className="a-primary a-danger" data-action="maintenance.stopNow" disabled={!currentConfirmation||!!request} onClick={()=>currentConfirmation&&command('maintenance.stopNow',{...confirmation.target,confirm:true})}>Stop owned work</button>
    </div>
   </div>
  </div>}
 </section>;
}
