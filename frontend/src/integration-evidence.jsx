import React,{useState} from 'react';
import {IntegrationValidation} from './integration-validation';

export function IntegrationEvidence({session,act}){
 const [repositories,setRepositories]=useState('[]'),[dependencies,setDependencies]=useState('[]'),[purpose,setPurpose]=useState(''),[result,setResult]=useState(null),[receipts,setReceipts]=useState([]),[next,setNext]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const source=session.worktreeInspection?.repository;
 const run=async(action,args={})=>{setBusy(true);setError('');try{
  const response=await act(action,{sessionId:session.id,...args});
  if(action==='integration.list'){setReceipts(args.before?prior=>[...prior,...response.result.items]:response.result.items);setNext(response.result.nextBefore)}
  else setResult(response.result);
  return response.result;
 }catch(e){setError(e.message||'Inspection did not finish. Read saved observations before trying again.')}finally{setBusy(false)}};
 const parsed=()=>{const repos=JSON.parse(repositories),deps=JSON.parse(dependencies);if(!Array.isArray(repos)||!Array.isArray(deps))throw Error('Expectations must be JSON arrays.');return {repos,deps}};
 const inspect=()=>{try{const {deps}=parsed();return run('runtime.provenance',{modules:deps.map(row=>row.module)})}catch(e){setError(e.message)}};
 const capture=()=>{try{const {repos,deps}=parsed();return run('integration.capture',{purpose,repositories:repos,dependencies:deps})}catch(e){setError(e.message)}};
 const addSource=()=>{try{const {repos}=parsed();setRepositories(JSON.stringify([...repos.filter(row=>row.repositoryPath!==source.root),{repositoryPath:source.root,sourceRevision:source.sourceRevision}],null,2));setError('')}catch(e){setError(e.message)}};
 return <details data-part="integration-evidence"><summary>Source and dependency evidence</summary>
  <p>Compare chosen source versions with files used by this conversation’s running worker. Save an observation before your checks, then recheck it to detect drift. These observations do not run tests or prove isolation.</p>
  <button className="a-soft" disabled={busy||!source} onClick={addSource}>Add inspected repository</button>
  <p className="a-caption">Inspect each repository above and add it here. Expectations stay fixed until you change them.</p>
  <label htmlFor="candidate-purpose">Observation purpose</label><input id="candidate-purpose" maxLength={500} value={purpose} onChange={e=>setPurpose(e.target.value)}/>
  <details><summary>Exact source and import expectations</summary>
   <label htmlFor="candidate-repositories">Repository expectations (JSON array)</label><textarea id="candidate-repositories" className="a-state-view" spellCheck={false} value={repositories} onChange={e=>setRepositories(e.target.value)}/>
   <label htmlFor="candidate-dependencies">Dependency expectations (JSON array)</label><textarea id="candidate-dependencies" className="a-state-view" spellCheck={false} value={dependencies} onChange={e=>setDependencies(e.target.value)}/>
   <p className="a-caption">Each dependency names module, repositoryPath, relative path and expected sha256. Only already-loaded modules can be observed. An empty list checks repository versions only.</p>
  </details>
  <div className="a-dialog-actions"><button className="a-soft" disabled={busy} data-action="runtime.provenance" onClick={inspect}>Inspect loaded sources</button><button className="a-soft" disabled={busy||repositories==='[]'} data-action="integration.capture" onClick={capture}>Save candidate observation</button><button className="a-soft" disabled={busy} data-action="integration.list" onClick={()=>run('integration.list')}>Load saved observations</button></div>
  {busy&&<p role="status">Inspecting…</p>}
  {result&&<article className="a-selection-card" data-part="integration-result"><p role="status">{{capture:'Candidate',check:'Recheck',validation:'Validation request',collection:'Combined result'}[result.kind]||'Loaded sources'}: {result.kind==='validation'?result.phase:result.status}</p>{result.id&&<p className="a-state-view">Receipt: {result.id}</p>}{result.kind==='capture'&&<button className="a-soft" disabled={busy} data-action="integration.check" onClick={()=>run('integration.check',{id:result.id})}>Recheck this candidate</button>}<p className="a-caption">Matching current files does not prove the exact bytes imported earlier or a successful test.</p><details><summary>Observation details</summary><pre className="a-state-view">{JSON.stringify(result,null,2)}</pre></details></article>}
  {receipts.map(row=><article className="a-selection-card" key={row.id}><p>{row.purpose||'Source observation'} · {row.kind} · {row.status}</p><button className="a-soft" disabled={busy} data-action="integration.read" onClick={()=>run('integration.read',{id:row.id})}>Read observation {row.id}</button></article>)}
  {next!==null&&<button className="a-soft" disabled={busy} data-action="integration.list" onClick={()=>run('integration.list',{before:next})}>Load older observations</button>}
  <IntegrationValidation key={session.id} session={session} act={act} candidate={result}/>
  {error&&<p role="alert">{error}</p>}
 </details>;
}
