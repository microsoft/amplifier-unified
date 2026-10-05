import React,{useEffect,useId,useRef,useState} from 'react';
import {useSettingsDraft} from './settings-drafts';
export {WorkspaceResources} from './workspace-resource-inventory';

const receipt=value=>value?.result?.accepted!==undefined?value.result:value;
async function run(act,action,args={}){const value=receipt(await act(action,args));if(!value||value.accepted===false)throw Error(value?.error||'Could not save. Your edits are kept.');return value.result}
export function useStarters(state,act){
 const [catalog,setCatalog]=useState(state.workspaceStarters||null),[error,setError]=useState('');
 useEffect(()=>{let active=true;run(act,'workspace.starters.list').then(value=>{if(active)setCatalog(value)}).catch(err=>{if(active)setError(err.message)});return()=>{active=false}},[]);
 useEffect(()=>{if(state.workspaceStarters)setCatalog(state.workspaceStarters)},[state.workspaceStarters]);
 return {catalog,error,reload:async()=>setCatalog(await run(act,'workspace.starters.list'))};
}
const empty=()=>({name:'',description:'',instructions:'',bundle:'',repositories:[],trackResources:false});
export function StarterSettings({state,act,navigate}){
 const {catalog,error:loadError,reload}=useStarters(state,act),[draft,setDraft]=useState(null),[base,setBase]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[confirm,setConfirm]=useState(null),id=useId();
 const submitting=useRef(false);
 const dirty=!!draft&&JSON.stringify(draft)!==JSON.stringify(base);
 const discard=()=>{setDraft(null);setBase(null);setError('')};
 useSettingsDraft('workspace-starter',{dirty,label:'Workspace starter',discard,review:()=>navigate?.('workspaces')});
 const edit=row=>{if(dirty){setError('Save or cancel the current starter before opening another.');return}setDraft(structuredClone(row));setBase(structuredClone(row));setError('')};
 const change=(key,value)=>setDraft({...draft,[key]:value});
 const perform=async fn=>{if(submitting.current)return;submitting.current=true;setBusy(true);setError('');try{await fn();await reload()}catch(err){setError(err.message)}finally{submitting.current=false;setBusy(false)}};
 const duplicate=row=>{if(dirty){setError('Save or cancel the current starter first.');return}perform(async()=>{const value=await run(act,'workspace.starters.duplicate',{id:row.id});edit(value)})};
 const save=event=>{event.preventDefault();perform(async()=>{const {id:identity,revision,builtIn,...starter}=draft;const value=await run(act,'workspace.starters.save',{starter,...(identity?{id:identity,expectedRevision:revision}:{})});setDraft(value);setBase(value)})};
 return <section className="a-workspace-starters" aria-label="Workspace starters">
  <h3>Workspace starters</h3><p>Choose a starting setup for new workspaces. Built-ins are read-only; duplicate one to make it yours. Changes never alter existing workspaces.</p>
  <button type="button" className="a-soft" disabled={busy||dirty} onClick={()=>edit(empty())}>New starter</button>
  {(loadError||error)&&<p role="alert" className="a-danger">{error||loadError}</p>}
  {!catalog&&!loadError&&<p role="status">Loading starters…</p>}
  <ul className="a-starter-list">{catalog?.items.map(row=><li key={row.id}><div><strong>{row.name}</strong><small>{row.builtIn?'Built-in':'Custom'} · {row.repositories.length} repositories</small><p>{row.description}</p></div><div className="a-workspace-setup-actions"><button type="button" disabled={busy} onClick={()=>edit(row)}>{row.builtIn?'View':'Edit'}</button><button type="button" disabled={busy} onClick={()=>duplicate(row)}>Duplicate</button>{!row.builtIn&&<button type="button" disabled={busy||dirty} onClick={()=>setConfirm(row)}>Delete</button>}</div></li>)}</ul>
  {confirm&&<div role="alertdialog" aria-label="Delete starter" className="a-workspace-setup"><p>Delete “{confirm.name}”? Only its definition is deleted. Existing workspaces and files stay intact.</p><button type="button" disabled={busy} onClick={()=>perform(async()=>{await run(act,'workspace.starters.remove',{id:confirm.id,expectedRevision:confirm.revision});if(draft?.id===confirm.id)discard();setConfirm(null)})}>Delete starter</button><button type="button" disabled={busy} onClick={()=>setConfirm(null)}>Cancel deletion</button></div>}
  {draft&&<form className="a-workspace-setup" aria-label="Starter editor" onSubmit={save}>
   <h3>{draft.builtIn?'Built-in starter':draft.id?'Edit starter':'New starter'}</h3>
   <fieldset disabled={busy||draft.builtIn}>
    <label htmlFor={id+'name'}>Starter name</label><input id={id+'name'} required maxLength={200} value={draft.name} onChange={e=>change('name',e.target.value)}/>
    <label htmlFor={id+'description'}>Description</label><input id={id+'description'} maxLength={1000} value={draft.description} onChange={e=>change('description',e.target.value)}/>
    <label htmlFor={id+'bundle'}>Workspace bundle default</label><select id={id+'bundle'} value={draft.bundle} onChange={e=>change('bundle',e.target.value)}><option value="">Inherit app and shared defaults</option>{draft.bundle&&!catalog?.bundles?.some(row=>row.value===draft.bundle)&&<option value={draft.bundle} disabled>Unavailable: {draft.bundle} — choose a configured bundle</option>}{catalog?.bundles?.map(row=><option key={row.value} value={row.value}>{row.label} ({row.value})</option>)}</select>
    <p className="a-caption">A default for future chats, not a replacement for explicit model or bundle choices. No credentials are copied.</p>
    <label htmlFor={id+'instructions'}>Agent instructions</label><textarea id={id+'instructions'} rows={8} maxLength={16000} value={draft.instructions} onChange={e=>change('instructions',e.target.value)}/>
    <p className="a-caption">Creates .amplifier/AGENTS.md only if absent. Existing instructions are never overwritten.</p>
    <h4>Repositories</h4><p className="a-caption">Each is a separate checkout in a child folder. Leave branch or tag blank for the remote default.</p>
    {draft.repositories.map((row,index)=>{const update=(key,value)=>change('repositories',draft.repositories.map((item,i)=>i===index?{...item,[key]:value}:item));return <div className="a-starter-repository" key={index}><label>Git URL<input aria-label={'Git URL '+(index+1)} required value={row.url} onChange={e=>update('url',e.target.value)}/></label><label>Folder name<input aria-label={'Repository folder '+(index+1)} value={row.directory} onChange={e=>update('directory',e.target.value)}/></label><label>Branch or tag<input aria-label={'Repository ref '+(index+1)} value={row.ref} onChange={e=>update('ref',e.target.value)}/></label><button type="button" onClick={()=>change('repositories',draft.repositories.filter((_,i)=>i!==index))}>Remove repository {index+1}</button></div>})}
    <button type="button" disabled={draft.repositories.length>=20} onClick={()=>change('repositories',[...draft.repositories,{url:'',directory:'',ref:''}])}>Add repository</button>
    <label><input type="checkbox" checked={draft.trackResources} onChange={e=>change('trackResources',e.target.checked)}/>Track external resources</label>
   </fieldset>
   <div className="a-workspace-setup-actions">{!draft.builtIn&&<button type="submit" className="a-primary" disabled={busy||!draft.name.trim()}>Save starter</button>}<button type="button" disabled={busy} onClick={discard}>{dirty?'Cancel edits':'Close editor'}</button>{draft.builtIn&&<button type="button" onClick={()=>duplicate(draft)}>Duplicate this starter</button>}</div>
  </form>}
 </section>;
}

export function WorkspaceReadiness({state,act,workspaceId}){
 const workspace=state.workspaces?.find(row=>row.id===(workspaceId||state.selectedWorkspaceId)),setup=workspace?.setup;
 const [error,setError]=useState(''),[busy,setBusy]=useState(false);
 if(!setup)return null;
 const retry=async()=>{setBusy(true);setError('');try{await run(act,'workspace.setup.retry',{workspaceId:workspace.id,expectedRevision:setup.revision})}catch(err){setError(err.message)}finally{setBusy(false)}};
 const inspect=async()=>{setBusy(true);setError('');try{await run(act,'workspace.setup.reconcile',{workspaceId:workspace.id})}catch(err){setError(err.message)}finally{setBusy(false)}};
 return <section className="a-workspace-setup" aria-label="Workspace readiness"><h3>{setup.starter?.name||'Workspace'} setup</h3><p role="status">{setup.status==='ready'?'Ready to work':setup.status==='pending'||setup.status==='running'?'Preparing workspace…':'Some setup steps need attention. Successful work is retained.'}</p>{setup.error&&<p role="alert">{setup.error}</p>}<ul>{setup.files?.map(row=><li key={row.path}>{row.path}: {row.status}</li>)}{setup.repositories?.map(row=><li key={row.directory}><strong>{row.directory}</strong>: {row.status}{row.sha&&' · '+row.sha.slice(0,8)}{row.error&&<p>{row.error}</p>}</li>)}</ul>{['partial','failed','interrupted'].includes(setup.status)&&<><button type="button" disabled={busy} onClick={inspect}>Inspect retained imports</button><button type="button" disabled={busy} onClick={retry}>Retry unfinished setup</button></>}{error&&<p role="alert">{error}</p>}<p className="a-caption">No existing repository was updated or merged. Workspace files and chat history are retained.</p></section>;
}
