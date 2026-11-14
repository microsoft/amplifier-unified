import React,{useEffect,useRef,useState} from 'react';
import {useSettingsDraft} from './settings-drafts';
import {OptimizeStorageSummary} from './optimize-storage-summary';
import {optimizePrefix as prefix,optimizeRunning,validKeepHours,sameOptimizePolicy,expirySelected,optimizeScopeArgs,optimizeCutoff} from './optimize-storage';

const readLocal=key=>{
 try{
  const saved=JSON.parse(sessionStorage.getItem(key)||'null')||{};
  if(typeof saved!=='object'||Array.isArray(saved)||saved.pending&&(!saved.pending.id||!saved.pending.action||!saved.pending.args))throw Error('Invalid receipt');
  return saved;
 }catch{return {recoveryError:'The saved optimization receipt could not be read. Changes are disabled; preserve browser storage and inspect the saved host operation.'}}
};

export function OptimizeStorageSettings({act,sessionId,hostId,active=true,navigate=()=>{},page='repair'}){
 // A receipt from another host is never a retry or an execution target here.
 const storageKey=`amplifier-optimize-storage-v1:${hostId||'unidentified'}`;
 const [local,setLocal]=useState(()=>readLocal(storageKey)),[policy,setPolicy]=useState(null),[draft,setDraft]=useState(null);
 const [scope,setScope]=useState('current'),[extractImages,setExtractImages]=useState(true);
 const [operation,setOperation]=useState(null),[confirmExpiry,setConfirmExpiry]=useState(false);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const identity=JSON.stringify([hostId,sessionId,active,scope,extractImages,draft,operation?.operationId,operation?.revision,confirmExpiry]),currentIdentity=useRef(identity);
 const mounted=useRef(false),generation=useRef(0),lock=useRef(false),action=useRef(act),localRef=useRef(local);
 action.current=act;localRef.current=local;currentIdentity.current=identity;
 const pending=local.pending,dirty=!!draft&&!!policy&&!sameOptimizePolicy(draft,policy);
 const valid=draft&&validKeepHours(draft.requestKeepHours)&&validKeepHours(draft.responseKeepHours);
 const canCreateCommand=typeof globalThis.crypto?.randomUUID==='function';
 const running=optimizeRunning(operation?.status),blocked=busy||!!pending||!!local.recoveryError||!active||!hostId||!canCreateCommand;
 useSettingsDraft(`optimize-storage:${hostId}`,{label:'Optimize saved session storage',dirty:dirty||!!pending,review:()=>navigate(page),discard:()=>{if(pending||busy)return false;setDraft(policy);return true}});

 function retain(value){
  try{sessionStorage.setItem(storageKey,JSON.stringify(value))}
  catch{setError('This browser could not save the operation receipt. Nothing new was submitted. Enable session storage before making storage changes.');return false}
  localRef.current=value;setLocal(value);return true;
 }
 function settle(command,value){
  const saved=readLocal(storageKey);
  if(saved.recoveryError||saved.pending?.id!==command.id){
   localRef.current=saved;setLocal(saved);
   throw Error('The saved browser receipt changed. It was preserved; read the exact current command outcome before making changes.');
  }
  return retain(value);
 }
 function showOperation(result){
  if(!result?.operationId){if(result?.operation===null)setOperation(null);return}
  // Keep one bounded outcome page, not a growing in-browser corpus.
  setOperation(old=>old?.operationId===result.operationId&&old.revision>result.revision?old:result);
  setConfirmExpiry(false);
 }
 async function read(name,args={}){
  const response=await action.current(prefix+name,args,{feedback:false,propagateError:true});
  if(response?.accepted!==true||!response.result)throw Error(response?.error||'The host did not return a confirmed storage result.');
  return response.result;
 }
 function confirmResult(name,args,result){
  if(!Number.isSafeInteger(result?.revision)||(name==='policy.save'
   ?!validKeepHours(result.requestKeepHours)||!validKeepHours(result.responseKeepHours)
   :!result.operationId||typeof result.status!=='string'||args.operationId&&result.operationId!==args.operationId)){
   throw Error('The host returned an incomplete or mismatched storage receipt. The command identity is retained; it will not be repeated.');
  }
 }
 async function reconcile(){
  const command=localRef.current.pending;
  if(lock.current||!active||!command)return;
  lock.current=true;setBusy(true);setError('');const version=generation.current;
  try{
   const receipt=await read('read',{commandId:command.id});
   if(!mounted.current||version!==generation.current)return;
   if(receipt.commandId!==command.id||receipt.status!=='recorded'||!receipt.result){
    setNotice('The host has not confirmed the exact command outcome. Unknown is not proof that nothing happened. The command is retained and was not repeated.');return;
   }
   const name=command.action.slice(prefix.length),result=receipt.result;
   confirmResult(name,command.args,result);
   // An exact receipt can be old. Keep the pending gate until current policy
   // and operation eligibility are both read, including after a failed read.
   const [settings,current]=await Promise.all([
    read('policy.read'),
    name==='policy.save'?Promise.resolve(null):read('read',{operationId:result.operationId,limit:50})
   ]);
   if(!mounted.current||version!==generation.current)return;
   confirmResult('policy.save',{},settings);
   if(current)confirmResult(name,{operationId:result.operationId},current);
   if(!settle(command,{operationId:result.operationId||localRef.current.operationId}))return;
   setPolicy(settings);setDraft(settings);
   if(current)showOperation(current);else setOperation(null);
   setNotice('The exact saved command receipt was found. No command was repeated. Read the operation status before deciding what to do next.');
  }catch(e){if(mounted.current&&version===generation.current)setError(e.message)}
  finally{if(mounted.current&&version===generation.current){lock.current=false;setBusy(false)}}
 }
 async function refresh({append=false}={}){
  if(lock.current||!active)return;lock.current=true;setBusy(true);setError('');
  const version=generation.current;
  try{
   const [settings,result]=await Promise.all([
    read('policy.read'),
    read('read',{...(operation?.operationId||localRef.current.operationId?{operationId:operation?.operationId||localRef.current.operationId}:{}),...(append?{after:operation.nextAfter}:{}),limit:50})
   ]);
   if(!mounted.current||version!==generation.current)return;
   setDraft(old=>old&&policy&&!sameOptimizePolicy(old,policy)?old:settings);setPolicy(settings);
   showOperation(result);
   // A status read is not proof that an unknown command did or did not run.
   if(localRef.current.pending)setNotice('Saved progress refreshed. The unconfirmed command is still retained; it has not been repeated.');
  }catch(e){if(mounted.current&&version===generation.current)setError(e.message)}
  finally{if(mounted.current&&version===generation.current){lock.current=false;setBusy(false)}}
 }
 useEffect(()=>{
  mounted.current=true;const version=++generation.current;lock.current=false;setBusy(false);
  setPolicy(null);setDraft(null);setOperation(null);setError('');setNotice('');setConfirmExpiry(false);
  const saved=readLocal(storageKey);localRef.current=saved;setLocal(saved);
  lock.current=true;setBusy(true);
   Promise.all([read('policy.read'),read('read',{...(saved.operationId?{operationId:saved.operationId}:{}),limit:50})]).then(([settings,result])=>{
    if(!mounted.current||version!==generation.current)return;
    setPolicy(settings);setDraft(settings);showOperation(result);
   }).catch(e=>{if(mounted.current&&version===generation.current)setError(e.message)})
   .finally(()=>{if(mounted.current&&version===generation.current){lock.current=false;setBusy(false)}});
  return()=>{mounted.current=false;generation.current++};
 },[storageKey]);
 useEffect(()=>{
  if(!active||!running||busy||error)return;
  const timer=setTimeout(()=>refresh(),1500);return()=>clearTimeout(timer);
 },[active,running,busy,error,operation?.revision]);

 async function mutate(name,args){
  if(lock.current||blocked||localRef.current.pending||localRef.current.recoveryError||currentIdentity.current!==identity)return;
  const saved=readLocal(storageKey);
  if(saved.pending||saved.recoveryError){localRef.current=saved;setLocal(saved);setError('A saved optimization command needs reconciliation before new storage changes. Nothing was submitted.');return}
  let command;
  try{
   const id=globalThis.crypto.randomUUID();
   if(typeof id!=='string'||!id)throw Error('No command identity');
   command={id,action:prefix+name,args};
  }catch{setError('A secure command identity could not be created. Nothing was submitted. Use HTTPS or a trusted localhost connection to make storage changes. Your saved recovery references are kept.');return}
  if(!retain({...localRef.current,pending:command}))return;
  lock.current=true;setBusy(true);setError('');setNotice('');const version=generation.current;
  try{
   const response=await action.current(command.action,args,{id:command.id,feedback:false,propagateError:true});
   if(response?.accepted!==true||!response.result)throw Object.assign(Error(response?.error||'The command outcome is not confirmed.'),{accepted:response?.accepted});
   if(!mounted.current||version!==generation.current)return;
   const result=response.result;
   confirmResult(name,args,result);
   if(!settle(command,{operationId:result.operationId||localRef.current.operationId}))return;
   if(name==='policy.save'){setPolicy(result);setDraft(result);setOperation(null);setNotice('Manual optimization choices saved. No optimization ran and no schedule changed.')}
   else{showOperation(result);setNotice('The host recorded the command. Use the saved operation status below; acceptance alone is not completion.')}
  }catch(e){
   if(!mounted.current||version!==generation.current)return;
   // Only an explicit host rejection proves this command did not execute.
   if(e.accepted===false){try{settle(command,{operationId:localRef.current.operationId})}catch(conflict){setError(conflict.message);return}}
   setError(e.message||'The command outcome is unknown. Read its saved result before taking further action.');
  }finally{if(mounted.current&&version===generation.current){lock.current=false;setBusy(false)}}
 }
 const change=(key,value)=>{setDraft({...draft,[key]:value});setConfirmExpiry(false)};
 const matching=operation&&operation.scope===scope&&(scope==='all'||operation.sessionId===sessionId)&&operation.extractImages===extractImages&&operation.policy?.revision===policy?.revision&&!dirty;
 const frozenExpiry=expirySelected(operation?.policy);
 const cutoffValid=!frozenExpiry||['request','response'].every(kind=>operation.policy?.[`${kind}KeepHours`]==null||typeof operation.cutoffs?.[`${kind}Before`]==='string'&&!Number.isNaN(Date.parse(operation.cutoffs[`${kind}Before`])));
 return <section className="a-settings-section" aria-label="Optimize saved session storage">
  <h4>Optimize saved session storage</h4>
  <p>Review saved conversation storage without deleting chats. Extracting supported inline images keeps their original bytes in session-owned assets. Expiring raw request or response diagnostics is a separate, destructive choice.</p>
  <p className="a-caption">This is a manual operation, not automatic cleanup. Existing schedules and inactivity rules stay unchanged, including any configured 12-hour request-only schedule or seven-day inactivity rule. This page does not establish that those timers are configured on this host. Response expiry is never enabled for you. Publishing or opening this page does not start cleanup.</p>
  {busy&&<p role="status">Reading or submitting saved storage work…</p>}
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {local.recoveryError&&<p role="alert">{local.recoveryError}</p>}
  {draft&&policy&&draft.revision!==policy.revision&&<p role="alert">Manual choices changed elsewhere. Your edits are kept. Discard them to use the current saved choices before saving again.</p>}
  {!hostId&&<p role="alert">Host identity is unavailable. Storage changes are disabled.</p>}
  {!canCreateCommand&&<p role="alert">A secure command identity is unavailable in this browser context. Storage changes are disabled. Use HTTPS or a trusted localhost connection. Your saved recovery references are kept.</p>}
  <fieldset disabled={blocked||!policy||running}>
   <legend>Manual diagnostic expiry choices</legend>
   <p>Save these choices before previewing. They do not run expiry, change schedules or remove recovery copies.</p>
   {['request','response'].map(kind=><div key={kind}>
    <label><input type="checkbox" checked={draft?.[`${kind}KeepHours`]!=null} onChange={e=>change(`${kind}KeepHours`,e.target.checked?(kind==='request'?12:168):null)}/> Expire raw {kind} diagnostics during this manual optimization</label>
    {draft?.[`${kind}KeepHours`]!=null&&<label className="a-field">Keep raw {kind} diagnostics for hours<input type="number" min="1" step="1" value={draft[`${kind}KeepHours`]} onChange={e=>change(`${kind}KeepHours`,e.target.value===''?'':Number(e.target.value))}/></label>}
   </div>)}
   <p className="a-caption">Expiry removes only eligible owner-qualified diagnostics, not canonical messages, tool outcomes or continuation data. Recovery, active writers and unknown references can hold files.</p>
   <button type="button" className="a-soft" disabled={!dirty||!valid||draft.revision!==policy.revision} onClick={()=>mutate('policy.save',{expectedRevision:draft.revision,requestKeepHours:draft.requestKeepHours,responseKeepHours:draft.responseKeepHours})}>Save manual optimization choices</button>
   {dirty&&<button type="button" className="a-link" onClick={()=>setDraft(policy)}>Discard optimization edits</button>}
  </fieldset>
  <fieldset disabled={blocked||!policy||running}>
   <legend>Preview scope</legend>
   <label className="a-field">Saved sessions to review<select value={scope} onChange={e=>{setScope(e.target.value);setConfirmExpiry(false)}}>
    <option value="current" disabled={!sessionId}>Current conversation and its workers</option>
    <option value="workspace" disabled={!sessionId}>Current workspace and its workers</option>
    <option value="all">All owned saved sessions and workers</option>
   </select></label>
   {!sessionId&&<p>Select a conversation to use the current or workspace scope, or explicitly choose all owned saved sessions. The scope is not widened automatically.</p>}
   <label><input type="checkbox" checked={extractImages} onChange={e=>{setExtractImages(e.target.checked);setConfirmExpiry(false)}}/> Extract supported inline images without discarding their pixels</label>
   <p className="a-caption">Unknown formats, missing ownership, offline writers, malformed records and recovery holds remain protected and are reported. No automatic bulk migration is performed.</p>
   <button type="button" className="a-soft" disabled={dirty||!valid||scope!=='all'&&!sessionId||!extractImages&&!expirySelected(policy)} onClick={()=>{if(scope!=='all'&&!sessionId)return;mutate('preview',{...optimizeScopeArgs(scope,sessionId),extractImages,policyRevision:policy.revision})}}>Preview saved storage optimization</button>
   {dirty&&<p>Save or discard your diagnostic expiry edits before previewing.</p>}
  </fieldset>
  {pending&&<div role="status"><p>The outcome of <code>{pending.action}</code> is unconfirmed. Its command identity is retained in this browser. It has not been repeated. Other storage changes are disabled until its exact outcome can be reconciled.</p><code>{pending.id}</code><button type="button" className="a-soft" disabled={busy||!active} onClick={reconcile}>Read exact optimization command outcome</button></div>}
  <button type="button" className="a-soft" disabled={busy||!active} onClick={()=>refresh()}>Refresh saved optimization status</button>
  {operation&&<>
   <p>Saved operation scope: {operation.scope} {operation.sessionId&&<code>{operation.sessionId}</code>}. Images: {operation.extractImages?'extraction selected':'not selected'}.</p>
   <h5>Frozen expiry cutoffs (UTC)</h5>
   <p>Saved manual ages: requests {operation.policy?.requestKeepHours==null?'retained':`${operation.policy.requestKeepHours} hours`}; responses {operation.policy?.responseKeepHours==null?'retained':`${operation.policy.responseKeepHours} hours`}.</p>
   <p>Request diagnostics before: {optimizeCutoff(operation.cutoffs?.requestBefore)}</p>
   <p>Response diagnostics before: {optimizeCutoff(operation.cutoffs?.responseBefore)}</p>
   <OptimizeStorageSummary operation={operation}/>
   {operation.canApply&&<fieldset disabled={blocked||!matching||!cutoffValid}>
    <legend>Apply this preview</legend>
    <p>Originals and backups remain under their independent recovery policy. Applying is not a promise of reclaimed disk space. Files that changed or cannot be safely owned stay untouched.</p>
    {frozenExpiry&&<label><input type="checkbox" checked={confirmExpiry} onChange={e=>setConfirmExpiry(e.target.checked)}/> Permanently expire eligible raw diagnostics before the frozen UTC cutoffs above</label>}
    <button type="button" className={frozenExpiry?'a-soft a-danger':'a-primary'} disabled={frozenExpiry&&!confirmExpiry} onClick={()=>mutate('apply',{operationId:operation.operationId,expectedRevision:operation.revision,confirmExpiry:frozenExpiry&&confirmExpiry})}>{frozenExpiry?'Apply optimization and diagnostic expiry':'Apply image extraction'}</button>
   </fieldset>}
   {operation.canApply&&!matching&&<p>These controls no longer match the saved preview. Restore the same choices or create a fresh preview; this operation is not applied automatically.</p>}
   {operation.canCancel&&<button type="button" className="a-soft" disabled={blocked} onClick={()=>mutate('cancel',{operationId:operation.operationId,expectedRevision:operation.revision})}>Cancel optimization at a safe checkpoint</button>}
   {operation.canResume&&<button type="button" className="a-soft" disabled={blocked} onClick={()=>mutate('resume',{operationId:operation.operationId,expectedRevision:operation.revision})}>Resume saved optimization</button>}
   <p className="a-caption">Cancellation stops at safe checkpoints; already committed changes are not undone. Interrupted or partial work is not complete. Resume is always explicit and keeps the original cutoffs.</p>
   <details><summary>File outcomes ({operation.rows?.length||0} on this page)</summary>
    <ul>{(operation.rows||[]).map((row,index)=><li key={`${row.owner}:${row.sessionId}:${row.kind}:${index}`}><strong>{row.status}</strong> · {row.owner} · {row.kind} · <code>{row.sessionId}</code><p>{row.reason}</p></li>)}</ul>
    {operation.nextAfter!=null&&<button type="button" className="a-soft" disabled={busy||!active} onClick={()=>refresh({append:true})}>Next page of file outcomes</button>}
   </details>
  </>}
 </section>;
}