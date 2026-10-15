import React,{useEffect,useId,useRef,useState} from 'react';

function unwrap(outer){
 const value=outer?.result?.accepted!==undefined?outer.result:outer;
 if(!value||value.accepted===false){const error=Error(value?.error||'The environment request was not confirmed.');error.confirmed=value?.accepted===false;throw error}
 return value.result;
}

export function TaskEnvironments({workspaceId,sessionId,act,active,readOnly}){
 const form=useId(),visit=useRef(0),inFlight=useRef(false),live=useRef(active),mounted=useRef(false);
 const key='amplifier-test-environment:'+workspaceId+':'+sessionId;
 const [draft,setDraft]=useState(()=>{try{return JSON.parse(sessionStorage.getItem(key))||{profileId:'linux-python',command:'',pending:null}}catch{return {profileId:'linux-python',command:'',pending:null}}});
 const [page,setPage]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[request,setRequest]=useState(null),[removing,setRemoving]=useState(null);
 const pending=useRef(draft.pending);
 const retain=(value,required=false)=>{try{sessionStorage.setItem(key,JSON.stringify(value))}catch{if(required)return false}pending.current=value.pending;if(mounted.current)setDraft(value);return true};
 const refresh=async()=>{
  const token=++visit.current;
  try{const value=unwrap(await act('workspace.environments.read',{workspaceId,sessionId}));if(live.current&&token===visit.current){setPage(value);setRequest(null);setError('')}}
  catch(e){if(live.current&&token===visit.current)setError(e.message)}
 };
 // Hidden panels still own pending requests; only unmount ends their UI lifetime.
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useEffect(()=>{live.current=active;if(active)refresh();return()=>{live.current=false;visit.current++}},[active,workspaceId,sessionId]);
 const mutate=async(action,args={})=>{
  if(inFlight.current||!active||readOnly)return;
  const saved=pending.current||{action,args:{workspaceId,sessionId,...args,requestId:crypto.randomUUID()},id:crypto.randomUUID()};
  if(!retain({...draft,pending:saved},true)){setError('This tab cannot save a recovery receipt. Allow browser storage or use another tab before changing the environment. Nothing was sent.');return}
  // A late response from an old mount must not clear a newer saved request.
  const clearPending=()=>{try{const retained=JSON.parse(sessionStorage.getItem(key));if(retained?.pending?.id!==saved.id)return true;return retain({...retained,pending:null},true)}catch{return false}};
  inFlight.current=true;setBusy(true);setError('');
  try{
   const result=unwrap(await act(saved.action,saved.args,{id:saved.id}));
   const cleared=clearPending();
   if(live.current){setRequest(result.request||null);setRemoving(null);await refresh();if(!cleared)setError('The response was received, but this tab could not clear its saved request. Check the same request after browser storage is available.')}
  }catch(e){if(e.confirmed)clearPending();if(live.current)setError(e.message||'No confirmed response. Check the same request before trying another change.')}
  finally{inFlight.current=false;if(mounted.current)setBusy(false)}
 };
 const rows=page?.items||[],capability=page?.capability,blocked=busy||!!draft.pending||!active||readOnly||capability?.status!=='ready';
 const last=request||rows.map(row=>row.lastOperation).filter(Boolean).sort((a,b)=>(b.updatedAt||0)-(a.updatedAt||0))[0];
 const output=last?.result?.result;
 return <section aria-label="Test environments" data-part="test-environments">
  <h4>Test environments</h4>
  <p>Run checks in a separate environment for this chat. Starting uses a snapshot of its working folder. Stop keeps environment data. Removal first saves the working folder and artifacts, then deletes the containers and their volumes.</p>
  {error&&<p role="alert">{error}</p>}
  {busy&&<p role="status">Waiting for the environment request…</p>}
  {draft.pending&&<p role="status">This tab has a saved request to check. <button type="button" className="a-link" disabled={busy||!active||readOnly} onClick={()=>mutate()}>Check the same environment request</button></p>}
  {capability&&capability.status!=='ready'&&<p>{capability.reason||'Test environments are not available on this host yet.'}</p>}
  {capability?.source&&<p style={{overflowWrap:'anywhere'}}>Working folder: {capability.source}</p>}
  {page&&rows.length===0&&<p>No test environment for this chat.</p>}
  <ul>{rows.map(row=><li key={row.id} data-test-environment={row.id} style={{overflowWrap:'anywhere'}}>
   <strong>{row.label||'Test environment'}</strong><p role="status">{row.observation?.status==='unverified'?'Last known environment':'Environment'}: {row.status?.replaceAll('_',' ')||'unknown'}</p>
   {row.reason&&<p>{row.reason}</p>}
   {row.observation?.status==='unverified'&&<p>Current state could not be verified. This is a saved observation.</p>}
   {['outcome_unknown','unknown'].includes(row.status)&&<p>The previous outcome is uncertain. Inspect the environment before taking another action.</p>}
   <details><summary>Environment details</summary><pre className="a-state-view">{JSON.stringify(row,null,2)}</pre></details>
   {!readOnly&&row.status!=='removed'&&<>
    <form aria-label="Run environment check" onSubmit={event=>{event.preventDefault();mutate('workspace.environments.execute',{id:row.id,expectedRevision:row.revision,command:draft.command,timeoutSeconds:60})}}>
     <label htmlFor={form+'-command-'+row.id}>Check command</label><textarea id={form+'-command-'+row.id} maxLength={16000} placeholder="python -m unittest discover" value={draft.command} disabled={blocked} onChange={event=>retain({...draft,command:event.target.value})}/>
     <button type="submit" className="a-soft" disabled={blocked||!draft.command.trim()||!['running','ready'].includes(row.status)}>Run check</button>
    </form>
    <button type="button" className="a-soft" disabled={blocked||['stopped','not-started'].includes(row.status)} onClick={()=>mutate('workspace.environments.stop',{id:row.id,expectedRevision:row.revision})}>Stop and keep data</button>
    {removing!==row.id?<button type="button" className="a-soft" disabled={blocked||!['stopped','not-started'].includes(row.status)} onClick={()=>setRemoving(row.id)}>Remove environment…</button>:<div role="group" aria-label="Confirm environment removal">
     <p>The working folder, artifacts and saved check results will be retained. The environment will be removed only after those files are saved. Files elsewhere in the container are not retained.</p>
     <button type="button" className="a-soft" disabled={blocked} onClick={()=>mutate('workspace.environments.remove',{id:row.id,expectedRevision:row.revision})}>Save files and remove environment</button>
     <button type="button" className="a-soft" disabled={busy} onClick={()=>setRemoving(null)}>Keep environment</button>
    </div>}
   </>}
  </li>)}</ul>
  {last&&<div data-part="environment-result">
   <p role="status">Last request: {last.state?.replaceAll('_',' ')||'unknown'}</p>
   {output?.exit_code!=null&&<p>Exit code: {output.exit_code}</p>}
   {output?.stdout&&<pre className="a-state-view">{output.stdout}</pre>}
   {output?.stderr&&<pre className="a-state-view">{output.stderr}</pre>}
   {(output?.stdout_truncated||output?.stderr_truncated)&&<p>The displayed output reached its size limit.</p>}
   {last.state==='outcome_unknown'&&<p>The command may still be running. Checking the request does not run it again. Stop the environment to stop its work and keep its data.</p>}
   <details><summary>Request details</summary><pre className="a-state-view">{JSON.stringify(last,null,2)}</pre></details>
  </div>}
  <button type="button" className="a-soft" disabled={busy||!active} onClick={()=>refresh()}>Refresh test environments</button>
  {!readOnly&&capability?.status==='ready'&&!rows.some(row=>row.status!=='removed')&&<form aria-label="Start test environment" onSubmit={event=>{event.preventDefault();mutate('workspace.environments.start',{profileId:draft.profileId})}}>
   <label htmlFor={form+'-profile'}>Environment</label><select id={form+'-profile'} disabled={blocked} value={draft.profileId} onChange={event=>retain({...draft,profileId:event.target.value})}>{(capability.profiles||[]).map(row=><option key={row.id} value={row.id}>{row.label}</option>)}</select>
   <button type="submit" className="a-soft" disabled={blocked}>Start test environment</button>
  </form>}
 </section>;
}
