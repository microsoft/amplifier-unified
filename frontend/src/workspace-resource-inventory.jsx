import React,{useEffect,useRef,useState} from 'react';
import {useSettingsDraft} from './settings-drafts';
const unwrap=value=>value?.result?.accepted!==undefined?value.result:value;
export function WorkspaceResources({state,act,workspaceId,readOnly=false,navigate}){
 const currentId=workspaceId||state.selectedWorkspaceId,[visibleId,setVisibleId]=useState(currentId),visited=useRef(new Map());
 useEffect(()=>setVisibleId(currentId),[currentId]);
 const workspace=state.workspaces?.find(row=>row.id===currentId);
 if(workspace)visited.current.set(workspace.id,workspace);
 return <>{[...visited.current.values()].map(row=><div key={row.id} hidden={row.id!==visibleId}><ResourceEditor workspace={row} act={act} readOnly={readOnly} review={()=>{setVisibleId(row.id);navigate?.('workspaces')}}/></div>)}</>;
}
function ResourceEditor({workspace,act,readOnly,review}){
 const [inventory,setInventory]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[adding,setAdding]=useState(false),[draft,setDraft]=useState({kind:'',resourceId:'',owner:'',note:''}),[updating,setUpdating]=useState(null),[evidence,setEvidence]=useState(''),[status,setStatus]=useState('observed_absent');
 const discard=()=>{setAdding(false);setUpdating(null);setDraft({kind:'',resourceId:'',owner:'',note:''});setEvidence('')};
 useSettingsDraft('workspace-resource-'+workspace.id,{dirty:Object.values(draft).some(Boolean)||!!evidence,label:'Resources · '+workspace.name,discard,review});
 const run=async(action,args={})=>{const value=unwrap(await act(action,{workspaceId:workspace.id,...args}));if(!value||value.accepted===false)throw Error(value?.error||'Resource inventory unavailable.');return value.result};
 const refresh=async()=>setInventory(await run('workspace.resources.list'));
 useEffect(()=>{let active=true;run('workspace.resources.list').then(value=>{if(active)setInventory(value)}).catch(err=>{if(active)setError(err.message)});return()=>{active=false}},[workspace.id]);
 const mutate=async(fn)=>{setBusy(true);setError('');try{await fn();await refresh()}catch(err){setError(err.message)}finally{setBusy(false)}};
 if(!workspace)return null;
 return <section className="a-workspace-setup" aria-label="Workspace resources"><h3>External resources · {workspace.name}</h3><p className="a-caption">An inventory, not automatic cleanup. Keep ownership and observed state distinct. Records survive removal of working files.</p>{error&&<p role="alert">{error}</p>}<ul>{inventory?.resources.map(row=><li key={row.id}><strong>{row.kind} · {row.resourceId}</strong><p>Owner: {row.owner} · {row.status.replace('_',' ')}{row.note&&' · '+row.note}</p>{row.evidence&&<p>{row.evidence}</p>}{!readOnly&&<button type="button" disabled={busy} onClick={()=>{setUpdating(row);setEvidence('')}}>Record observation</button>}</li>)}</ul>{inventory?.resources.length===0&&<p>No external resources recorded.</p>}{readOnly?<p>Manage resource records in Settings → Workspaces.</p>:<button type="button" disabled={busy} onClick={()=>{if(adding)discard();else setAdding(true)}}>{adding?'Cancel resource':'Record resource'}</button>}
 {adding&&<form onSubmit={event=>{event.preventDefault();mutate(async()=>{await run('workspace.resources.add',draft);setAdding(false);setDraft({kind:'',resourceId:'',owner:'',note:''})})}}>{[['kind','Resource type'],['resourceId','Resource identifier'],['owner','Resource owner'],['note','Resource note']].map(([key,label])=><label key={key}>{label}<input required={key!=='note'} value={draft[key]} maxLength={1000} onChange={e=>setDraft({...draft,[key]:e.target.value})}/></label>)}<button type="submit" disabled={busy}>Save resource record</button></form>}
 {updating&&<form onSubmit={event=>{event.preventDefault();mutate(async()=>{await run('workspace.resources.update',{id:updating.id,expectedRevision:updating.revision,status,evidence});setUpdating(null);setEvidence('')})}}><label>Observed resource status<select value={status} onChange={e=>setStatus(e.target.value)}><option value="observed_absent">Observed absent — not our teardown</option><option value="reaped">Reaped — our teardown confirmed</option><option value="active">Active</option></select></label><label>Observation evidence<textarea required value={evidence} maxLength={4000} onChange={e=>setEvidence(e.target.value)}/></label><button type="submit" disabled={busy}>Save observation</button><button type="button" onClick={()=>{setUpdating(null);setEvidence('')}}>Cancel observation</button></form>}
 </section>;
}
