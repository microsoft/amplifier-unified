import React from 'react';

// One tab-scoped recovery reference, never a queued command. The host resolves
// previewId exactly, including after its instance ID changes during repair.
const journalKey='amplifier.installation-repair.v1';
const id=value=>typeof value==='string'&&/^[a-f0-9]{32}$/.test(value);
function readIntent(){
 try{
  const value=JSON.parse(sessionStorage.getItem(journalKey)||'null');
  return id(value?.previewId)&&typeof value.hostInstanceId==='string'?value:null;
 }catch{return null}
}
function saveIntent(value){
 sessionStorage.setItem(journalKey,JSON.stringify(value));
 if(sessionStorage.getItem(journalKey)!==JSON.stringify(value))throw Error('Recovery reference was not saved.');
}
const settled=operation=>['failed','rolled-back'].includes(operation?.status)||installationRepairHealthy(operation);
export function installationRepairHealthy(operation){
 return !!(id(operation?.operationId)&&id(operation.previewId)&&operation.status==='ready'&&operation.healthVerified===true&&operation.reinstalled===true&&operation.serviceState==='running');
}
const statusLabels={accepted:'Repair accepted — not complete',running:'Repair in progress',ready:'Repair needs verification',reinstalled:'Application reinstalled — service health not verified','rolled-back':'Repair rolled back',failed:'Installation repair failed',unknown:'Repair outcome unknown'};
const preservationLabels={conversations:'Saved conversations',settings:'App settings',credentials:'Credentials',workspaces:'Workspace files','source-selections':'Selected package and component sources','downloaded-sources':'Downloaded source caches','operation-receipts':'Operation and recovery receipts','optional-features':'Installed optional features'};
const replacementLabels={'uv-tool-environment':'Managed application package environment','application-launch-pointer':'Active application launch pointer'};
const reconcileJournalKey='amplifier.installation-repair-reconciliation.v1';
function readReconciliation(){try{return JSON.parse(sessionStorage.getItem(reconcileJournalKey)||'null')}catch{return null}}
export function installationRepairClosedWithoutReplacement(operation){
 return !!(id(operation?.operationId)&&id(operation.previewId)&&operation.status==='failed'&&operation.stage==='reconciled-no-effect'&&operation.reinstalled===false&&operation.healthVerified===false&&operation.serviceState==='running'&&operation.reconciliation?.noReplacement===true&&Number.isFinite(operation.reconciliation.verifiedAt)&&operation.reconciliation.verifiedAt>0&&typeof operation.reconciliation.hostInstanceId==='string'&&!!operation.reconciliation.hostInstanceId);
}
const pendingRepairCompleted=(operation,pending)=>!!(pending&&operation?.operationId===pending.operationId&&operation.previewId===pending.previewId&&installationRepairHealthy(operation));
function Reconciliation({operation,target,act,onReceipt,onPending}){
 const [pending,setPending]=React.useState(readReconciliation);
 const [busy,setBusy]=React.useState(false),[notice,setNotice]=React.useState('');
 const active=React.useRef(null),flight=React.useRef(false),mounted=React.useRef(false),generation=React.useRef(0);
 const renderedGeneration=generation.current;
 const eligible=id(operation?.operationId)&&id(operation.previewId)&&['accepted','running','unknown'].includes(operation.status)&&operation.reinstalled===false&&target?.operationId===operation.operationId&&typeof target.hostInstanceId==='string'&&!!target.hostInstanceId&&target.confirm===true;
 const key=eligible?JSON.stringify([operation.previewId,target]):null;
 const waiting=pending?.operationId===operation.operationId&&pending.previewId===operation.previewId;
 React.useLayoutEffect(()=>{active.current=key;return()=>{active.current=null}},[key]);
 React.useLayoutEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 const save=value=>{
  const encoded=JSON.stringify(value);sessionStorage.setItem(reconcileJournalKey,encoded);
  if(sessionStorage.getItem(reconcileJournalKey)!==encoded)throw Error('Recovery reference was not saved.');
  setPending(value);
  onPending(value);
 };
 const proven=found=>found?.operationId===operation.operationId&&found.previewId===operation.previewId&&installationRepairClosedWithoutReplacement(found)&&(!waiting||found.reconciliation.hostInstanceId===pending.hostInstanceId);
 const completed=found=>found?.operationId===operation.operationId&&found.previewId===operation.previewId&&installationRepairHealthy(found);
 React.useEffect(()=>{if(waiting&&(proven(operation)||pendingRepairCompleted(operation,pending))){try{save(null)}catch{}}},[waiting,operation]);
 async function run(inspectOnly=false){
  if(!mounted.current||flight.current||inspectOnly&&!waiting||!inspectOnly&&(!eligible||waiting||active.current!==key||generation.current!==renderedGeneration))return;
  if(!inspectOnly)generation.current++;
  flight.current=true;setBusy(true);setNotice('');
  const requestKey=key;
  if(!inspectOnly){
   try{save({operationId:operation.operationId,previewId:operation.previewId,hostInstanceId:target.hostInstanceId})}
   catch{flight.current=false;setBusy(false);setNotice('This browser could not save the reconciliation recovery reference. No reconciliation was requested.');return}
  }
  try{
   const response=await act(inspectOnly?'maintenance.installationRepairStatus':'maintenance.installationRepairReconcile',inspectOnly?{operationId:pending.operationId}:{...target},{propagateError:true});
   if(!mounted.current||active.current!==requestKey)return;
   const found=inspectOnly?response?.result?.operation:response?.result;
   const expectedHost=inspectOnly?pending.hostInstanceId:target.hostInstanceId;
   if(response?.accepted===true&&completed(found)){
    // The original helper may finish while reconciliation is refused or its
    // response is lost. Its authenticated successor can have a new host ID.
    onReceipt(found);save(null);setNotice('The original repair completed and is verified healthy. This is not a no-replacement reconciliation result.');
   }else if(response?.accepted===true&&proven(found)&&found.reconciliation.hostInstanceId===expectedHost){
    onReceipt(found);save(null);setNotice('The backend proved no installation replacement and closed this repair. This is not a repaired-installation health result. Nothing was installed or restarted.');
   }else setNotice('No positive no-replacement settlement was confirmed. The repair remains held. Check its retained status; do not repeat the repair or reconciliation.');
  }catch(error){
   if(!mounted.current||active.current!==requestKey)return;
   if(error?.accepted===false&&!inspectOnly)setNotice('Reconciliation was refused. The existing repair remains held. Read current status before another action.');
   else setNotice('Reconciliation outcome is unknown. Only read the retained repair status; this request will not be replayed.');
  }finally{flight.current=false;if(mounted.current)setBusy(false)}
 }
 if(!eligible&&!waiting)return null;
 return <div data-part="installation-repair-reconciliation">
  <p>Reconcile this unfinished repair only if the host can prove no installation replacement occurred. Eligibility is not proof. This check cannot install, stop or restart the service, roll back, reset data, or replay the repair. Missing or conflicting evidence keeps the repair held.</p>
  {eligible&&<button type="button" className="a-soft" data-action="maintenance.installationRepairReconcile" disabled={busy||waiting} onClick={()=>run(false)}>{busy?'Checking unfinished repair…':'Reconcile unfinished repair'}</button>}
  {waiting&&<><p role="status">Reconciliation was requested for operation <code>{pending.operationId}</code>. No outcome is inferred from its phase or elapsed time.</p><button type="button" className="a-link" data-action="maintenance.installationRepairStatus" disabled={busy} onClick={()=>run(true)}>Check reconciliation status</button></>}
  {notice&&<p role="status">{notice}</p>}
 </div>;
}
const verifyJournalKey='amplifier.installation-repair-verification.v1';
const unverified=operation=>id(operation?.operationId)&&id(operation.previewId)&&operation.status==='reinstalled'&&operation.reinstalled===true&&operation.healthVerified===false;
function Verification({operation,target,act,onReceipt}){
 const [pending,setPending]=React.useState(()=>{try{return JSON.parse(sessionStorage.getItem(verifyJournalKey)||'null')}catch{return null}});
 const [busy,setBusy]=React.useState(false),[notice,setNotice]=React.useState('');
 const active=React.useRef(null),flight=React.useRef(null),mounted=React.useRef(false),generation=React.useRef(0);
 const renderedGeneration=generation.current;
 const eligible=unverified(operation)&&target?.operationId===operation.operationId&&typeof target.hostInstanceId==='string'&&!!target.hostInstanceId&&target.confirm===true;
 const key=eligible?JSON.stringify([operation.previewId,target]):null;
 const waiting=pending?.operationId===operation?.operationId;
 React.useLayoutEffect(()=>{active.current=key;return()=>{active.current=null}},[key]);
 React.useLayoutEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 const save=value=>{
  const encoded=JSON.stringify(value);sessionStorage.setItem(verifyJournalKey,encoded);
  if(sessionStorage.getItem(verifyJournalKey)!==encoded)throw Error('Recovery reference was not saved.');
  setPending(value);
 };
 React.useEffect(()=>{
  if(waiting&&pending.previewId===operation?.previewId&&installationRepairHealthy(operation)){
   try{save(null)}catch{} // A verified canonical receipt is not another probe.
  }
 },[waiting,operation]);
 const unknown=()=>setNotice('Verification outcome is unknown. Check repair status before choosing another health check. Verification is never replayed automatically.');
 async function verify(){
  if(!mounted.current||!eligible||active.current!==key||flight.current||waiting||generation.current!==renderedGeneration)return;
  generation.current++;
  const attempt={};flight.current=attempt;setBusy(true);setNotice('');
  const saved={operationId:operation.operationId,previewId:operation.previewId,hostInstanceId:target.hostInstanceId};
  try{save(saved)}catch{flight.current=null;setBusy(false);setNotice('This browser could not save the verification recovery reference. No health check was requested.');return}
  try{
   const response=await act('maintenance.installationRepairVerify',{...target},{propagateError:true});
   if(!mounted.current||active.current!==key||flight.current!==attempt)return;
   const found=response?.result;
   if(response?.accepted===false)throw Object.assign(Error(response.error||'Verification was not accepted.'),{accepted:false});
   if(response?.accepted===true&&found?.operationId===saved.operationId&&found.previewId===saved.previewId&&(unverified(found)||installationRepairHealthy(found))){
    onReceipt(found);save(null);setNotice(installationRepairHealthy(found)?'Health verification completed.':'Health verification did not establish a healthy installation. Review the result before deliberately checking again.');
   }else unknown();
  }catch(error){
   if(!mounted.current||active.current!==key||flight.current!==attempt)return;
   if(error?.accepted===false){try{save(null)}catch{}setNotice(error.message||'Verification was not accepted. Read current repair status.')}
   else unknown();
  }finally{if(flight.current===attempt){flight.current=null;if(mounted.current)setBusy(false)}}
 }
 async function reconcile(){
  if(!mounted.current||flight.current||!waiting)return;
  const attempt={};flight.current=attempt;setBusy(true);setNotice('');
  try{
   const response=await act('maintenance.installationRepairStatus',{operationId:pending.operationId},{propagateError:true});
   if(!mounted.current||active.current!==key||flight.current!==attempt)return;
   const found=response?.result?.operation;
   if(response?.accepted===true&&found?.operationId===pending.operationId&&found.previewId===pending.previewId&&(unverified(found)||installationRepairHealthy(found))){
    onReceipt(found);save(null);setNotice(installationRepairHealthy(found)?'The retained receipt verifies a healthy installation.':'The retained installation is still unverified. You may deliberately request a new health-only check; no check was replayed.');
   }else unknown();
  }catch{if(mounted.current&&active.current===key)unknown()}
  finally{if(flight.current===attempt){flight.current=null;if(mounted.current)setBusy(false)}}
 }
 if(!eligible&&!waiting)return null;
 return <div data-part="installation-repair-verification">
  <p>Verify the repaired application and current host health only. This does not install packages, stop or restart the service, roll back, or reset data.</p>
  {eligible&&<button type="button" className="a-soft" data-action="maintenance.installationRepairVerify" disabled={busy||waiting} onClick={verify}>{busy?'Checking installation health…':'Verify repaired installation'}</button>}
  {waiting&&<><p role="status">A health check was requested for operation <code>{pending.operationId}</code>. Its outcome is not inferred from the reinstalled status.</p><button type="button" className="a-link" data-action="maintenance.installationRepairStatus" disabled={busy} onClick={reconcile}>Check verification status</button></>}
  {notice&&<p role="status">{notice}</p>}
 </div>;
}

export function InstallationRepair({state,act}){
 const projection=state.maintenance?.installationRepair;
 const [preview,setPreview]=React.useState(null),[intent,setIntent]=React.useState(readIntent),[receipt,setReceipt]=React.useState(null);
 const [reconcilePending,setReconcilePending]=React.useState(readReconciliation);
 const [busy,setBusy]=React.useState(''),[error,setError]=React.useState(''),[acknowledged,setAcknowledged]=React.useState(false);
 const lock=React.useRef(false),alive=React.useRef(false),current=React.useRef(null),intentRef=React.useRef(intent),operationRef=React.useRef(null);
 const displayed=preview||projection?.preview;
 const key=JSON.stringify(displayed||null);
 const candidate=projection?.operation;
 // A saved confirmation must never pick up an unrelated latest operation.
 const matches=operation=>operation&&(!intent||operation.previewId===intent.previewId);
 const comparable=receipt&&candidate&&receipt.operationId===candidate.operationId;
 const operations=(intent||comparable?[receipt,candidate]:[candidate||receipt]).filter(matches).sort((a,b)=>(b.updatedAt||0)-(a.updatedAt||0));
 const operation=operations[0]||null;
 operationRef.current=operation;
 const anotherOperation=candidate&&intent&&candidate.previewId!==intent.previewId&&!settled(candidate);
 const closedWithoutReplacement=installationRepairClosedWithoutReplacement(operation)&&(!reconcilePending||reconcilePending.operationId===operation.operationId&&reconcilePending.previewId===operation.previewId&&reconcilePending.hostInstanceId===operation.reconciliation.hostInstanceId);
 const reconciliationHeld=!!reconcilePending&&!closedWithoutReplacement&&!pendingRepairCompleted(operation,reconcilePending);
 const currentRepairHealthy=operation?.operationId===candidate?.operationId&&operation?.previewId===candidate?.previewId&&installationRepairHealthy(operation);
 const blocked=reconciliationHeld||!!intent&&!settled(operation)||!!operation&&!settled(operation)||!!candidate&&!settled(candidate)&&!currentRepairHealthy;
 const healthy=installationRepairHealthy(operation);
 React.useLayoutEffect(()=>{current.current=key;setAcknowledged(false);return()=>{current.current=null}},[key]);
 React.useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 function remember(value){saveIntent(value);intentRef.current=value;setIntent(value)}
 async function status(){
  if(lock.current)return;
  lock.current=true;setBusy('status');setError('');
  try{
   const saved=intentRef.current;
   const previewId=saved?.previewId||operationRef.current?.previewId;
   const args=previewId?{previewId}:{};
   const response=await act('maintenance.installationRepairStatus',args,{propagateError:true});
   if(!alive.current)return;
   if(response?.accepted!==true||!response.result||!Object.hasOwn(response.result,'operation'))throw Error('Repair status was not returned.');
   const found=response.result.operation;
   if(found&&previewId&&found.previewId!==previewId)throw Error('The returned repair does not match this confirmation.');
   setReceipt(found);
   if(saved&&!found)setError('No operation receipt is available for this confirmation. Its outcome remains unconfirmed. Do not submit repair again; ask the host operator to inspect the retained receipt.');
  }catch{if(alive.current)setError('Could not verify repair status. Keep the recovery reference and check again after reconnecting. No repair was resubmitted.')}
  finally{lock.current=false;if(alive.current)setBusy('')}
 }
 // Reading recovery status on return is safe. Confirm is never an effect or retry.
 React.useEffect(()=>{
  if(!projection)return;
  void status();
  const reconnect=()=>{void status()};
  globalThis.addEventListener?.('amplifier-reconnected',reconnect);
  return()=>globalThis.removeEventListener?.('amplifier-reconnected',reconnect);
 },[!!projection]);
 async function review(){
  if(lock.current||blocked)return;
  lock.current=true;setBusy('preview');setError('');setAcknowledged(false);
  try{
   const response=await act('maintenance.installationRepairPreview',{}, {propagateError:true});
   if(!alive.current)return;
   if(response?.accepted!==true||!response.result)throw Error('The installation repair preview was not returned.');
   setPreview(response.result);
  }catch(error){if(alive.current)setError(error?.accepted===false?error.message:'Could not prepare an installation repair preview. No repair was requested.')}
  finally{lock.current=false;if(alive.current)setBusy('')}
 }
 const confirm=async()=>{
  // Fence stale gestures and same-tick double clicks before committing UI state.
  if(lock.current||blocked||intentRef.current?.previewId===displayed?.previewId||!acknowledged||current.current!==key||!displayed?.supported||!id(displayed.previewId))return;
  lock.current=true;setBusy('confirm');setError('');
  const saved={previewId:displayed.previewId,hostInstanceId:displayed.hostInstanceId,hostLabel:displayed.hostLabel,dataDir:displayed.dataDir};
  try{remember(saved)}catch{lock.current=false;setBusy('');setError('This browser could not save the repair recovery reference. No repair was requested. Enable session storage before continuing.');return}
  const unknown=()=>{if(alive.current)setError('Repair confirmation outcome is unknown. Use Check repair status after reconnecting. This confirmation will not be replayed.')};
  try{
   const response=await act('maintenance.installationRepairConfirm',{previewId:saved.previewId,hostInstanceId:saved.hostInstanceId,confirm:true},{propagateError:true});
   if(!alive.current)return;
   if(response?.accepted===false)throw Object.assign(Error(response.error||'Repair was not accepted.'),{accepted:false});
   if(response?.accepted===true&&id(response.result?.operationId)&&response.result.previewId===saved.previewId){setReceipt(response.result);setPreview(null)}
   else unknown();
  }catch(error){
   if(error?.accepted===false&&operationRef.current?.previewId!==saved.previewId){
    // Only an explicit refusal unlocks the gesture. Never infer it from a lost response.
    try{sessionStorage.removeItem(journalKey)}catch{}
    intentRef.current=null;
    if(alive.current){setIntent(null);setPreview(null);setAcknowledged(false);setError(error.message||'Repair was not accepted. Finish active work, then review a fresh preview.')}
   }else unknown();
  }finally{lock.current=false;if(alive.current)setBusy('')}
 };
 if(!projection)return <section data-part="installation-repair"><h4>Repair installation</h4><p>This host does not advertise in-app installation repair. Runtime repair and data reset are different operations.</p></section>;
 const confirmed=!!intent&&displayed?.previewId===intent.previewId;
 return <section data-part="installation-repair" aria-label="Installation repair">
  <h4>Repair installation</h4>
  {anotherOperation&&<p role="status">Another installation repair is not settled on this host. Its operation is <code>{candidate.operationId}</code>. No new repair can be requested here.</p>}
  <p>Reinstall the managed Amplifier application on its host. This is not runtime dependency repair or a data reset, and it cannot fix an unavailable package source.</p>
  <p>Finish active chats, workers, tools and calls first. Confirmation checks that the host is idle; it does not queue a repair or interrupt active work. The service will disconnect this page while it restarts.</p>
  <button type="button" className="a-soft" data-action="maintenance.installationRepairPreview" disabled={!!busy||blocked} onClick={review}>{busy==='preview'?'Preparing preview…':'Preview installation repair'}</button>
  {displayed&&!confirmed&&!blocked&&<div className="a-selection-card" data-part="installation-repair-preview">
   {displayed.supported?<>
    <h5>Review this host and target</h5>
    <dl><dt>Host</dt><dd className="a-wrap">{displayed.hostLabel} · <code>{displayed.hostInstanceId}</code></dd><dt>Scope</dt><dd>Application installation only</dd><dt>Data directory (kept)</dt><dd className="a-wrap"><code>{displayed.dataDir}</code></dd><dt>Target</dt><dd className="a-wrap">Amplifier {displayed.target?.version} · <code>{displayed.target?.revision}</code> · {displayed.target?.source}</dd></dl>
    <p>Kept unchanged:</p><ul>{(displayed.preserves||[]).map(value=><li key={value}>{preservationLabels[value]||value}</li>)}</ul>
    <p>Optional features kept: {displayed.installedExtras?.length?displayed.installedExtras.join(', '):'None reported'}</p>
    <p>Replaced:</p><ul>{(displayed.replaces||[]).map(value=><li key={value}>{replacementLabels[value]||value}</li>)}</ul>
    <p>The generated host service restarts. A reinstall receipt alone does not establish a healthy restarted host.</p>
    <label className="a-inline-checkbox"><input type="checkbox" checked={acknowledged} onChange={event=>setAcknowledged(event.target.checked)}/>I reviewed this host, target and preservation scope, and have finished active work.</label>
    <button type="button" className="a-soft" data-action="maintenance.installationRepairConfirm" disabled={!!busy||!acknowledged} onClick={confirm}>Confirm installation repair</button>
   </>:<p role="status">{displayed.reason||'Installation repair is unsupported for this host. Native Windows installations are not supported; no alternate repair was started.'}</p>}
  </div>}
  {(intent||operation)&&<div className="a-selection-card" data-part="installation-repair-operation" role="status">
   <strong>{healthy?'Installation repair verified healthy':closedWithoutReplacement?'Repair closed without installation replacement':statusLabels[operation?.status]||'Repair confirmation unverified'}</strong>
   {intent&&<p className="a-caption a-wrap">Confirmed host: {intent.hostLabel} · Data directory kept: <code>{intent.dataDir}</code> · Preview <code>{intent.previewId}</code></p>}
   {operation&&<><p className="a-wrap">Operation <code>{operation.operationId}</code> · {operation.stage} · Service: {operation.serviceState}</p><p>{operation.detail}</p></>}
   {!healthy&&<p>No healthy completion is claimed. Accepted, running, reinstalled, rolled-back and unknown outcomes are not verified recovery.</p>}
   {operation&&<Verification key={'verify:'+operation.operationId} operation={operation} target={!anotherOperation?projection.verifyTarget:null} act={act} onReceipt={setReceipt}/>}
   {operation&&<Reconciliation key={'reconcile:'+operation.operationId} operation={operation} target={!anotherOperation?projection.reconcileTarget:null} act={act} onReceipt={setReceipt} onPending={setReconcilePending}/>}
   <p className="a-caption">Reconnect reads the retained status; it never repeats confirmation.</p>
  </div>}
  <button type="button" className="a-link" data-action="maintenance.installationRepairStatus" disabled={!!busy} onClick={status}>{busy==='status'?'Checking repair status…':'Check repair status'}</button>
  {error&&<p role="alert">{error}</p>}
 </section>;
}
