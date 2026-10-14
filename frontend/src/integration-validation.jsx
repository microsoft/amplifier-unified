import React,{useState} from 'react';

export function IntegrationValidation({session,act,candidate}){
 const key='amplifier-integration-validation:'+session.id;
 const [pending,setPending]=useState(()=>{try{return JSON.parse(sessionStorage.getItem(key)||'null')}catch{return null}});
 const [repository,setRepository]=useState(''),[command,setCommand]=useState(''),[timeout,setTimeout]=useState(300),[ids,setIds]=useState(''),[results,setResults]=useState('[]'),[value,setValue]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const retain=request=>{setPending(request);try{request?sessionStorage.setItem(key,JSON.stringify(request)):sessionStorage.removeItem(key)}catch{}};
 const run=async(action,args)=>{if(busy)return;const request=pending||{action,args:{sessionId:session.id,...args},id:crypto.randomUUID()};retain(request);setBusy(true);setError('');try{
  const response=await act(request.action,request.args,{id:request.id});
  if(!response?.accepted)throw Error('No confirmed response. Check the same request before starting another validation.');
  retain(null);setValue(response.result);
  if(response.result.kind==='validation')setIds(previous=>[...new Set([...previous.split(/\s+/).filter(Boolean),response.result.id])].join('\n'));
 }catch(e){setError(e.message)}finally{setBusy(false)}};
 const collect=()=>{try{const declared=JSON.parse(results);if(!Array.isArray(declared))throw Error('Result declarations must be an array.');return run('integration.collect',{captureId:candidate.id,validationIds:ids.split(/\s+/).filter(Boolean),results:declared})}catch(e){setError(e.message)}};
 const selected=repository||candidate?.repositories?.[0]?.repositoryPath||'';
 return <details data-part="integration-validation"><summary>Validate and collect combined work</summary>
  <p>Run a command you choose against this saved candidate. Shell permissions still apply. An assignment must name its integration owner under Tasks and workers. Inspection alone runs no command.</p>
  {pending&&<p role="status">A request has no confirmed response. <button className="a-soft" disabled={busy} onClick={()=>run()}>Check the same integration request</button> <button className="a-link" disabled={busy} onClick={()=>retain(null)}>Dismiss saved retry</button> Dismissal does not cancel admitted work.</p>}
  {candidate?.kind==='capture'?<>
   <label htmlFor="validation-repository">Repository to validate</label><select id="validation-repository" value={selected} disabled={busy||!!pending} onChange={event=>setRepository(event.target.value)}>{candidate.repositories.map(row=><option key={row.repositoryPath} value={row.repositoryPath}>{row.repositoryPath}</option>)}</select>
   <label htmlFor="validation-command">Validation command</label><textarea id="validation-command" value={command} maxLength={16000} disabled={busy||!!pending} spellCheck={false} onChange={event=>setCommand(event.target.value)}/>
   <label htmlFor="validation-timeout">Command timeout (seconds)</label><input id="validation-timeout" type="number" min={1} max={3600} value={timeout} disabled={busy||!!pending} onChange={event=>setTimeout(Number(event.target.value))}/>
   <button className="a-soft" data-action="integration.validate" disabled={busy||!!pending||!command.trim()} onClick={()=>run('integration.validate',{captureId:candidate.id,repositoryPath:selected,command,timeout})}>Run validation command</button>
   <label htmlFor="validation-ids">Validation receipt IDs to collect (one per repository)</label><textarea id="validation-ids" className="a-state-view" value={ids} disabled={busy||!!pending} onChange={event=>setIds(event.target.value)}/>
   <details><summary>Result files and publication reports</summary><p>Optional array of repositoryPath, references (local path@sha256:digest), and publication (status and reference). Publication is your report; it does not publish anything or confirm a remote release.</p><textarea aria-label="Integration result declarations" className="a-state-view" spellCheck={false} value={results} disabled={busy||!!pending} onChange={event=>setResults(event.target.value)}/></details>
   <button className="a-soft" data-action="integration.collect" disabled={busy||!!pending||!ids.trim()} onClick={collect}>Collect validation results</button>
  </>:<p>Read or save a candidate observation above to choose its exact repositories.</p>}
  {busy&&<p role="status">Preparing integration evidence…</p>}
  {value&&<article className="a-selection-card" data-part="validation-result"><p role="status">{value.kind==='collection'?'Combined result':'Validation request'}: {value.kind==='collection'?value.status:value.phase}</p><p className="a-state-view">Receipt: {value.id}</p><p>Command results cover only the commands you selected. They do not certify test quality, complete isolation or publication.</p>
   {value.repositories?.map(row=><div key={row.repositoryPath}><p><strong>{row.repositoryPath.split(/[\\/]/).filter(Boolean).pop()}</strong></p><p>{row.status.replaceAll('_',' ')} · Publication: {row.publication.status.replaceAll('_',' ')} ({row.publication.evidence.replaceAll('_',' ')})</p>{row.artifacts.map(item=>item.downloadUrl?<a className="a-link" key={item.reference} href={item.downloadUrl}>Download saved result</a>:<p key={item.reference}>{item.status}: {item.reference}</p>)}<details><summary>Repository identity</summary><p className="a-state-view">{row.repositoryPath}</p></details></div>)}
   <details><summary>Invocation and evidence details</summary><pre className="a-state-view">{JSON.stringify(value,null,2)}</pre></details>
  </article>}
  {error&&<p role="alert">{error}</p>}
 </details>;
}
