import React,{useRef,useState} from 'react';
import {IntegrationEvidence} from './integration-evidence';

const moveLabels={waiting:'Waiting to move',moving:'Moving',moved:'Moved',not_moved:'Not moved',needs_recovery:'Needs recovery'};
const continuationLabels={waiting:'Waiting to continue',backpressured:'Waiting to continue',enqueued:'Continuation queued',suppressed:'Continuation not sent'};
const deferred=row=>row.placementMode==='deferred-retirement';
export function nextFolderChoice(rows){
 return rows.filter(row=>deferred(row)&&row.phase==='pending').sort((a,b)=>b.createdAt-a.createdAt||b.id.localeCompare(a.id))[0];
}
function DeferredFolderReceipt({row}){
 const failed=row.displayState==='needs_recovery';
 const label=failed?'Folder change needs recovery':row.supersededBy?'Not applied — a newer folder was chosen':row.displayState==='not_moved'?'Folder change not applied':row.displayState==='moved'?'Folder changed':row.step==='desired'?'Folder choice saved':['retiring','committing'].includes(row.step)?'Changing folder':'Folder status unavailable';
 return <article className="a-selection-card" data-handoff-id={row.id}>
  <p role={failed?'alert':'status'} data-part="move-status">{label}</p>
  <dl><dt>From folder</dt><dd className="a-state-view">{row.source}</dd><dt>Chosen folder</dt><dd className="a-state-view">{row.target}</dd></dl>
  <p>{row.detail}</p>
  {row.continuation&&<p role="status" data-part="move-continuation">{continuationLabels[row.continuation.status]}{row.continuation.detail?' · '+row.continuation.detail:''}</p>}
 </article>;
}
function WorktreeMoveReceipt({row,sessionId,act}){
 const inFlight=useRef(false),[checking,setChecking]=useState(false),[notice,setNotice]=useState(null),[error,setError]=useState('');
 const check=async()=>{
  if(inFlight.current)return;
  inFlight.current=true;setChecking(true);setNotice(null);setError('');
  try{
   const response=await act('worktree.check',{sessionId,id:row.id},{propagateError:true});
   if(!response?.accepted||response.result?.id!==row.id)throw Error(response?.error||'No confirmed response. The saved move status is unchanged.');
   // The host projection owns placement. A check result must not overwrite it.
   setNotice({revision:response.result.revision,text:response.result.displayState==='needs_recovery'?'Move check: Needs recovery. '+response.result.detail:'Move checked. The saved move status updates here.'});
  }catch(e){setError(e.message||'Could not check the move. The saved move status is unchanged.')}
  finally{inFlight.current=false;setChecking(false)}
 };
 return <article className="a-selection-card" data-handoff-id={row.id}>
  <p role="status" data-part="move-status">{row.displayState==='waiting'&&row.waitingFor.includes('generation')?'Waiting for this turn':moveLabels[row.displayState]}</p>
  <dl><dt>From folder</dt><dd className="a-state-view">{row.source}</dd><dt>To folder</dt><dd className="a-state-view">{row.target}</dd></dl>
  <p>Local host: {row.executionHost?.label||'Not recorded for this receipt'}</p>
  <p>{row.detail}</p>
  {row.continuation&&<p role="status" data-part="move-continuation">{continuationLabels[row.continuation.status]}{row.continuation.detail?' · '+row.continuation.detail:''}</p>}
  <button className="a-soft" data-action="worktree.check" aria-disabled={checking} onClick={check}>Check move</button>
  <p className="a-caption">Checks this move without starting another move or sending work.</p>
  {checking&&<p role="status">Checking move…</p>}
  {notice&&notice.revision>=row.revision&&<p role="status">{notice.text}</p>}
  {error&&<p role="alert">{error}</p>}
 </article>;
}
export function WorktreeControls({session,act}){
 const [repositoryPath,setRepositoryPath]=useState(''),[ref,setRef]=useState('HEAD'),[branch,setBranch]=useState(''),[carry,setCarry]=useState(false),[path,setPath]=useState(''),[busy,setBusy]=useState(false),[savingChoice,setSavingChoice]=useState(false),[choiceFeedback,setChoiceFeedback]=useState(null);
 const choosing=useRef(false);
 const storageKey='amplifier-checkout-change:'+session.id;
 const [pending,setPending]=useState(()=>{try{return JSON.parse(sessionStorage.getItem(storageKey)||'null')}catch{return null}});
 const retain=value=>{setPending(value);try{value?sessionStorage.setItem(storageKey,JSON.stringify(value)):sessionStorage.removeItem(storageKey)}catch{}};
 const relocate=async(action,args)=>{if(busy)return;const request=pending||{action,args:{sessionId:session.id,...args},id:crypto.randomUUID()};retain(request);setBusy(true);try{const response=await act(request.action,request.args,{id:request.id});if(response?.accepted)retain(null)}finally{setBusy(false)}};
 const run=async(action,args={})=>{setBusy(true);try{return await act(action,{sessionId:session.id,...args})}finally{setBusy(false)}};
 const inspected=(session.worktreeInspection?.repositorySelection||'')===repositoryPath.trim()?session.worktreeInspection:null,working=session.workingDirectory||session.workspace;
 const receipts=session.worktreeHandoffs||[],next=nextFolderChoice(receipts);
 const choiceScope=JSON.stringify([session.id,working,session.executionRevision,receipts.map(row=>[row.id,row.revision]).sort()]);
 const feedback=choiceFeedback?.scope===choiceScope?choiceFeedback:null;
 const sameChoice=target=>next?next.target===target:working===target;
 const handoff=async id=>{
  const target=id===null?session.workspace:session.worktrees.find(row=>row.id===id)?.path;
  if(choosing.current||busy||session.configurationBusy||sameChoice(target))return;
  choosing.current=true;setBusy(true);setSavingChoice(true);setChoiceFeedback(null);
  try{
   const response=await act('worktree.handoff',{sessionId:session.id,id,expectedExecutionRevision:session.executionRevision||0},{propagateError:true});
   if(!response?.accepted)throw Error(response?.error||'No confirmed response. The folder choice may have been saved.');
   setChoiceFeedback({scope:choiceScope,id:response.result?.id});
  }catch(e){setChoiceFeedback({scope:choiceScope,error:e?.message||'No confirmed response. The folder choice may have been saved.'})}
  finally{choosing.current=false;setBusy(false);setSavingChoice(false)}
 };
 return <section className="a-settings-section" aria-label="Task worktrees" data-part="worktrees"><h3>Task execution folder</h3><p>Run this task in a separate Git checkout while keeping its saved conversation, voice history, artifacts and settings in their original home.</p><dl><dt>Saved history and settings home</dt><dd className="a-state-view">{session.workspace}</dd><dt>Execution host</dt><dd data-part="execution-host">{inspected?.executionHost?.label||session.executionHost?.label||'Inspect to identify this local host'}</dd><dt>Current folder</dt><dd className="a-state-view" data-part="execution-directory">{working}</dd>{next&&<><dt>Next folder</dt><dd className="a-state-view" data-part="next-execution-directory">{next.target}</dd></>}</dl>
 {next&&<p role="status">{next.step==='desired'?'Folder change is waiting for existing work to settle. Work may still run in the current folder until then.':['retiring','committing'].includes(next.step)?'Changing folder. Current folder updates when the switch is confirmed.':'Folder choice is pending. Current folder remains authoritative.'}</p>}
 {savingChoice&&<p role="status">Saving folder choice…</p>}
 {feedback&&!feedback.error&&!receipts.some(row=>row.id===feedback.id)&&<p role="status">Folder choice received. Waiting for saved folder status.</p>}
 {feedback?.error&&<p role="alert">{feedback.error}</p>}
 {pending&&<p role="status">A checkout change has no confirmed response. <button className="a-link" disabled={busy} onClick={()=>relocate()}>Check the same checkout change</button> <button className="a-link" disabled={busy} onClick={()=>retain(null)}>Dismiss retry</button></p>}
 <details><summary>Select a repository within this workspace</summary><label htmlFor="worktree-repository">Repository folder</label><input id="worktree-repository" value={repositoryPath} onChange={e=>setRepositoryPath(e.target.value)} placeholder="Leave blank for the execution folder"/><p className="a-caption">For a workspace containing several repositories, enter one repository folder. Creating its checkout keeps the conversation in place and does not configure imports or dependencies.</p></details>
 <button className="a-soft" disabled={busy} data-action="worktree.inspect" onClick={()=>run('worktree.inspect',repositoryPath.trim()?{repositoryPath:repositoryPath.trim()}:{})}>Inspect repository</button>
 {(working!==session.workspace||next&&next.target!==session.workspace)&&<button className="a-soft" disabled={busy||session.configurationBusy||sameChoice(session.workspace)} data-action="worktree.handoff" onClick={()=>handoff(null)}>Use home folder next</button>}
 {inspected?.repository&&<><p className="a-state-view" data-part="selected-repository">{inspected.repository.root}</p><p>{inspected.repository.branch||'Detached commit'} · {inspected.repository.dirty?'Uncommitted changes present':'Checkout clean'}</p><details><summary>Source changes</summary><pre className="a-state-view">{inspected.repository.status||'No changes'}</pre></details><h4>Create a checkout</h4><label htmlFor="worktree-ref">Starting Git ref or commit</label><input id="worktree-ref" value={ref} onChange={e=>setRef(e.target.value)}/><label htmlFor="worktree-branch">New branch (optional)</label><input id="worktree-branch" value={branch} onChange={e=>setBranch(e.target.value)}/><label className="a-inline-checkbox"><input type="checkbox" checked={carry} onChange={e=>setCarry(e.target.checked)}/>Include my uncommitted changes</label><p className="a-caption">{carry?'Copy staged, unstaged and untracked files with an inspectable manifest. Ignored files are excluded. Repositories with submodules need separate handling; the source stays intact.':'Start from the committed ref in a clean checkout. Your current uncommitted changes stay in the source.'}</p><button className="a-primary" disabled={busy||!ref.trim()} data-action="worktree.create" onClick={()=>run('worktree.create',{sourceRevision:inspected.repository.sourceRevision,repositoryPath:inspected.repository.root,ref:ref.trim(),...(branch.trim()?{branch:branch.trim()}:{}),mode:carry?'carry_dirty':'clean'})}>Create managed checkout</button></>}
 <details><summary>Attach an existing checkout</summary><label htmlFor="worktree-path">Existing worktree path</label><input id="worktree-path" value={path} onChange={e=>setPath(e.target.value)}/><button className="a-soft" disabled={busy||!path.trim()} data-action="worktree.attach" onClick={()=>run('worktree.attach',{path:path.trim(),...(repositoryPath.trim()?{repositoryPath:repositoryPath.trim()}:{})})}>Attach checkout</button><p className="a-caption">The folder must belong to the selected repository (the saved home when no repository is selected). Attaching does not start work or grant cleanup ownership.</p></details>
 {(session.worktrees||[]).map(row=><article className="a-selection-card" key={row.id} data-worktree-id={row.id}><p className="a-state-view">{row.path}</p><p role="status">Checkout {row.status}</p><p className="a-state-view">Source: {row.source}</p><p>Local host: {row.executionHost?.label||'Not recorded for this checkout'}</p>{row.error&&<p role="alert">{row.error}</p>}<p>{row.branch||'Detached commit'} · {row.mode==='carry_dirty'?'Changes copied':row.mode==='attached'?'Existing checkout':'Committed source'}</p>{row.status==='archived'&&<p>All files and Git history remain in the retained checkout. Archiving does not reclaim disk space or freeze external edits.</p>}<div className="a-dialog-actions"><button className="a-soft" disabled={busy} data-action="worktree.status" onClick={()=>run('worktree.status',{id:row.id})}>Inspect checkout and manifest</button>{row.status==='ready'&&<button className="a-primary" disabled={busy||session.configurationBusy||sameChoice(row.path)} data-action="worktree.handoff" onClick={()=>handoff(row.id)}>Use this folder next</button>}{row.owned&&['ready','archived'].includes(row.status)&&<button className="a-soft" disabled={busy||!!pending||working===row.path||next?.target===row.path||session.configurationBusy} data-action={row.status==='archived'?'worktree.restore':'worktree.archive'} onClick={()=>relocate(row.status==='archived'?'worktree.restore':'worktree.archive',{id:row.id,expectedRevision:row.revision})}>{row.status==='archived'?'Restore preserved checkout':'Archive and preserve files'}</button>}{row.owned&&row.status==='ready'&&<button className="a-soft" disabled={busy||working===row.path||next?.target===row.path||session.configurationBusy} data-action="worktree.remove" onClick={()=>run('worktree.remove',{id:row.id,expectedRevision:row.revision})}>Remove clean checkout</button>}</div></article>)}
 {session.worktreeStatus&&<details open><summary>Checkout inspection</summary><pre className="a-state-view">{JSON.stringify(session.worktreeStatus,null,2)}</pre></details>}
 {receipts.map(row=>deferred(row)?<DeferredFolderReceipt key={session.id+':'+row.id} row={row}/>:<WorktreeMoveReceipt key={session.id+':'+row.id} row={row} sessionId={session.id} act={act}/>)}
 <IntegrationEvidence key={session.id} session={session} act={act}/>
 </section>
}
