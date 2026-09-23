import React,{useEffect,useId,useRef,useState} from 'react';
import {FolderOpen,X} from 'lucide-react';
import {PathField} from './settings-ui';
import './workspace-setup.css';

// Built-in shell modules wrap action receipts once; all journeys use the same
// host actions, including the draft's workspace chooser and agent callers.
const receipt=value=>value?.result?.accepted!==undefined?value.result:value;
export function WorkspaceForm({state,act,mode='create',fromDraft=false,onDone,onCancel}){
 const [kind,setKind]=useState(mode),[name,setName]=useState(''),[path,setPath]=useState(''),[root,setRoot]=useState(''),[plan,setPlan]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const submitting=useRef(false),form=useRef(null),id=useId();
 useEffect(()=>{form.current?.querySelector('input')?.focus()},[kind]);
 const edit=setter=>value=>{setter(value);setPlan(null);setError('')};
 const run=async(action,args)=>{const response=receipt(await act(action,args));if(!response||response.accepted===false)throw Error(response?.error||'Could not save. Your choices are kept.');return response.result};
 const submit=async event=>{
  event.preventDefault();if(submitting.current)return;submitting.current=true;setBusy(true);setError('');
  try{
   if(kind==='attach'){
    const result=await run('workspace.add',{path,fromDraft});onDone?.(result);return;
   }
   const prepared=plan||await run('workspace.prepare',{name,...(root.trim()?{root:root.trim()}:{})});
   setPlan(prepared);
   if(prepared.disposition!=='create')return;
   const result=await run('workspace.create',{planId:prepared.planId,fromDraft});onDone?.(result);
  }catch(err){setError(err.message)}finally{submitting.current=false;setBusy(false)}
 };
 const useExisting=async()=>{
  if(submitting.current)return;submitting.current=true;setBusy(true);setError('');
  try{const result=await run('workspace.add',{path:plan.path,fromDraft});onDone?.(result)}catch(err){setError(err.message)}finally{submitting.current=false;setBusy(false)}
 };
 return <form ref={form} className="a-workspace-setup" onSubmit={submit} aria-label={kind==='attach'?'Use existing folder':'Create workspace'} aria-busy={busy}>
  <div className="a-workspace-setup-heading"><h3>{kind==='attach'?'Use an existing folder':'New workspace'}</h3><button type="button" className="a-icon" aria-label="Cancel workspace setup" disabled={busy} onClick={onCancel}><X/></button></div>
  {kind==='create'?<>
   <label htmlFor={id+'-name'}>Workspace name</label><input id={id+'-name'} value={name} maxLength={200} required disabled={busy} placeholder="e.g. Launch plan" onChange={e=>edit(setName)(e.target.value)}/>
   <details><summary>More options</summary><label htmlFor={id+'-root'}>Create in</label><PathField id={id+'-root'} value={root} placeholder={state.workspaceDefaults?.root||'Default workspace folder'} directory state={state} act={act} onChange={edit(setRoot)}/><p className="a-caption">A new folder will be created here. Change the default in Settings → Workspaces.</p></details>
  </>:<><label htmlFor={id+'-folder'}>Folder on {state.workspaceDefaults?.hostLabel||'this host'}</label><PathField id={id+'-folder'} value={path} directory state={state} act={act} onChange={edit(setPath)} placeholder="~/dev/my-project"/><p className="a-caption">Work with files here. Existing Amplifier chats will appear automatically.</p></>}
  {plan&&plan.disposition!=='create'&&<p role="status">{plan.disposition==='open'?'This folder already has a workspace.':plan.disposition==='attach'?'This folder already exists. Use it as a workspace?':'A file already uses this name. Choose another name.'}<span className="a-workspace-destination">{plan.path}</span></p>}
  {error&&<p role="alert" className="a-danger">{error}</p>}
  <div className="a-workspace-setup-actions"><button type="button" className="a-link" disabled={busy} onClick={onCancel}>Cancel</button>{plan&&['open','attach'].includes(plan.disposition)?<button type="button" className="a-primary" disabled={busy} onClick={useExisting}>{plan.disposition==='open'?'Open workspace':'Use folder'}</button>:<button type="submit" className="a-primary" disabled={busy||!(kind==='create'?name.trim():path.trim())}>{busy?'Saving…':kind==='create'?'Create workspace':'Use folder'}</button>}</div>
  <button type="button" className="a-link" disabled={busy} onClick={()=>{setKind(kind==='create'?'attach':'create');setPlan(null);setError('')}}>{kind==='create'?'Use an existing folder':'Create a new workspace'}</button>
 </form>;
}

export function WorkspacePicker({state,act,setup,onChange}){
 const [mode,setMode]=useState(null),[rows,setRows]=useState([]),[query,setQuery]=useState(''),[error,setError]=useState(''),[more,setMore]=useState(false),[page,setPage]=useState(0),[loading,setLoading]=useState(false);
 const select=useRef(null);
 useEffect(()=>{let current=true;setLoading(true);const timer=setTimeout(()=>{
  Promise.resolve(act('workspace.list',{query,offset:page*100})).then(value=>{const result=receipt(value);if(!result?.accepted)throw Error(result?.error||'Could not load workspaces.');if(current){setRows(result.result.items);setMore(result.result.nextOffset!=null);setError('')}}).catch(err=>{if(current)setError(err.message)}).finally(()=>{if(current)setLoading(false)});
 },query?150:0);return()=>{current=false;clearTimeout(timer)}},[query,page,state.workspaces?.length,mode]);
 const cancel=()=>{setMode(null);requestAnimationFrame(()=>select.current?.focus())};
 const done=result=>{onChange({workspace:result.path,location:{kind:'workspace'}});cancel()};
 const managed=setup.location?.kind==='managed',known=rows.some(row=>row.path===setup.workspace);
 return <div className="a-workspace-picker">
  <label htmlFor="chat-workspace"><FolderOpen/>Workspace</label>
  <select ref={select} id="chat-workspace" aria-label="Workspace" value={managed?'':setup.workspace||''} onChange={e=>{const path=e.target.value;if(path===':create:'||path===':attach:')setMode(path===':create:'?'create':'attach');else onChange({workspace:path,location:{kind:path?'workspace':'managed'}})}}>
   <option value="">No workspace</option>{!managed&&setup.workspace&&!known&&<option value={setup.workspace}>{setup.workspace.split(/[\\/]/).filter(Boolean).at(-1)}</option>}
   {rows.map(row=><option key={row.id} value={row.path}>{row.label||row.name}</option>)}
   <option value=":create:">＋ Create new workspace…</option><option value=":attach:">Use an existing folder…</option>
  </select>
  {managed&&<p className="a-caption">Files created in this chat are saved by Amplifier.</p>}
  <details><summary>Find a workspace</summary><input type="search" aria-label="Find a workspace" placeholder="Search names or folders" value={query} onChange={e=>{setPage(0);setQuery(e.target.value)}}/>{loading&&<p role="status">Finding workspaces…</p>}{(page>0||more)&&<div className="a-workspace-setup-actions"><button type="button" disabled={!page||loading} onClick={()=>setPage(page-1)}>Previous</button><button type="button" disabled={!more||loading} onClick={()=>setPage(page+1)}>More</button></div>}</details>
  {error&&<p role="alert">{error}</p>}
  {mode&&<WorkspaceForm state={state} act={act} mode={mode} fromDraft onCancel={cancel} onDone={done}/>}
 </div>;
}

export function WorkspaceSettings({state,act}){
 const [root,setRoot]=useState(state.settings?.workspaces?.defaultRoot||''),[status,setStatus]=useState(''),[busy,setBusy]=useState(false);
 const save=async e=>{e.preventDefault();setBusy(true);try{const result=await act('settings.update',{patch:{workspaces:{defaultRoot:root.trim()}}});if(!result||result.accepted===false)throw Error(result?.error||'Could not save.');setStatus('Saved. Existing workspaces stay where they are.')}catch(err){setStatus(err.message)}finally{setBusy(false)}};
 return <form className="a-workspace-setup" onSubmit={save}><h3>Where new workspaces live</h3><p>Name a workspace and Amplifier creates its folder here.</p><label htmlFor="default-workspace-root">Default workspace folder on {state.workspaceDefaults?.hostLabel||'this host'}</label><PathField id="default-workspace-root" value={root} directory state={state} act={act} onChange={setRoot} placeholder={state.workspaceDefaults?.root}/><p className="a-caption">Leave blank to use Amplifier’s workspace folder. This only affects new workspaces.</p><button className="a-primary" disabled={busy}>Save</button>{status&&<p role="status">{status}</p>}<label><input type="checkbox" checked={!!state.settings?.workspaces?.showPaths} onChange={e=>act('settings.update',{patch:{workspaces:{showPaths:e.target.checked}}})}/>Show folder paths in the sidebar</label></form>;
}
