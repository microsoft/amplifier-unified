import React,{useEffect,useState} from 'react';
import './mcp-contract-review.css';

export function McpContractReview({canvas,recovery,command,onApplied}){
 const [review,setReview]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[replace,setReplace]=useState(false),[discard,setDiscard]=useState(false);
 useEffect(()=>{setReview(null);setReplace(false);setDiscard(false);setError('')},[canvas.id,canvas.revision,recovery?.bindingRevision]);
 if(!recovery||!['ready','reconnect_required','contract_changed','review_required'].includes(recovery.status)||canvas.readOnlyVersion)return null;
 const read=async()=>{
  setBusy(true);setError('');
  try{setReview(await command('smartTools.reviewView',{canvasId:canvas.id,expectedBindingRevision:recovery.bindingRevision}))}
  catch(error){setError(error.message)}finally{setBusy(false)}
 };
 const apply=async()=>{
  setBusy(true);setError('');
  try{
   await command('smartTools.updateView',{canvasId:canvas.id,expectedBindingRevision:review.bindingRevision,reviewedContract:review.fingerprint,replaceDocument:replace,discardLocalInput:discard});
   setReview(null);await onApplied();
  }catch(error){setError(error.message)}finally{setBusy(false)}
 };
 const changes=review?.changes;
 const names=(label,values)=><p><strong>{label}:</strong> {values.length?values.join(', '):'None'}</p>;
 return <section className="a-mcp-contract-review" aria-label="Review tool changes">
  {!review?<button type="button" className="a-link" disabled={busy} onClick={read}>{busy?'Reading tool changes…':'Review tool changes'}</button>:<>
   <h3>Review tool changes</h3>
   <p>Your saved document and tool work are kept. Approval applies only to its existing action names; new actions remain unavailable.</p>
   {!changes.previousDefinitionsAvailable&&<p>Previous definitions are unavailable for this older view. Review the current definitions below; a precise old-versus-new comparison cannot be shown.</p>}
   {names('Added actions — not granted',changes.added)}
   {names('Removed or no longer available to this view',changes.removed)}
   {names('Removed from the server catalog',changes.removedCatalog)}
   {names('Changed existing actions',changes.changed.map(tool=>`${tool.name} (${tool.fields.join(', ')})`))}
   {names('Launching action changes',changes.launcherChanged)}
   <p>Connection and account: unchanged. {changes.requestedCspChanged||changes.requestedPermissionsChanged?'Requested permissions changed.':'Requested permissions unchanged.'} Network and device permissions remain denied by this host.</p>
   <details><summary>Current definitions and requested permissions</summary><pre>{JSON.stringify({tools:changes.tools,csp:changes.requestedCsp,permissions:changes.requestedPermissions},null,2)}</pre></details>
   <label><input type="checkbox" checked={replace} onChange={event=>{setReplace(event.target.checked);setDiscard(false)}}/> Open updated live view{changes.documentChanged?' — newer document available':''}</label>
   {replace&&<label><input type="checkbox" checked={discard} onChange={event=>setDiscard(event.target.checked)}/> I understand this replaces my current tool view and resets its unfinished input. Other open views keep their saved version.</label>}
   <p>Previous calls will not run again. The existing board or document identity is retained.</p>
   <button type="button" className="a-primary" disabled={busy||replace&&!discard} onClick={apply}>{busy?'Applying reviewed changes…':replace?'Approve and open updated live view':'Approve existing actions'}</button>
   <button type="button" className="a-link" disabled={busy} onClick={()=>setReview(null)}>Cancel review</button>
  </>}
  {error&&<p role="alert">{error}</p>}
 </section>;
}
