import React,{useEffect,useRef,useState} from 'react';
import {useSettingsDraft} from './settings-drafts';

const fields=[['cpus','CPU cores'],['memoryMiB','Memory (MiB)'],['pids','Process limit'],['tmpMiB','Temporary space (MiB, Docker only)'],['shmMiB','Shared memory (MiB, Docker only)'],['defaultTimeoutSeconds','Default check time (seconds)'],['maxTimeoutSeconds','Maximum check time (seconds)'],['preparationTimeoutSeconds','Dependency preparation time (seconds)']];
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const form=data=>({queue:Object.fromEntries(Object.keys(data.effectiveQueue).map(k=>[k,String(data.queue[k]??'')])),profiles:Object.fromEntries(data.effectiveProfiles.map(p=>[p.id,Object.fromEntries(fields.map(([k])=>[k,String(data.profiles[p.id]?.[k]??'')]))]))});
function numbers(values){return Object.fromEntries(Object.entries(values).filter(([,v])=>v!=='').map(([k,v])=>[k,Number(v)]))}

export function ExecutionLimits({act,navigate}){
 const [data,setData]=useState(null),[draft,setDraft]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const pending=useRef(false),action=useRef(act);action.current=act;
 const dirty=!!data&&!same(draft,form(data));
 const accept=value=>{setData(value);setDraft(form(value))};
 useSettingsDraft('testing-execution',{dirty,label:'Test execution limits',review:()=>navigate?.('testing'),discard:()=>data&&setDraft(form(data))});
 useEffect(()=>{let live=true;action.current('testing.execution.read',{}).then(r=>{if(live){if(r?.accepted===false||!r?.result)setError(r?.error||'Could not read execution limits.');else accept(r.result)}}).catch(e=>live&&setError(String(e)));return()=>{live=false}},[]);
 async function refresh(){if(pending.current)return;pending.current=true;setBusy(true);setError('');setNotice('');try{const r=await action.current('testing.execution.read',{});if(r?.accepted===false||!r?.result)throw Error(r?.error||'Could not read execution limits.');accept(r.result)}catch(e){setError(String(e))}finally{pending.current=false;setBusy(false)}}
 async function save(){if(pending.current||!data)return;pending.current=true;setBusy(true);setError('');setNotice('');
  const intended={queue:numbers(draft.queue),profiles:Object.fromEntries(Object.entries(draft.profiles).map(([k,v])=>[k,numbers(v)]).filter(([,v])=>Object.keys(v).length))};
  try{const r=await action.current('testing.execution.save',{expectedRevision:data.revision,...intended});if(r?.accepted===false||!r?.result)throw Error(r?.error||'Save acknowledgement unavailable.');accept(r.result);setNotice('Execution limits saved. New requests will use these values.');}
  catch(e){try{const r=await action.current('testing.execution.read',{});if(r?.accepted!==false&&r?.result&&same(form({...r.result,...intended}),form(r.result))){accept(r.result);setNotice('Current execution limits match your changes. The save was not repeated.')}else setError(String(e))}catch{setError(String(e))}}
  finally{pending.current=false;setBusy(false)}
 }
 const change=(group,key,value)=>setDraft(d=>group==='queue'?{...d,queue:{...d.queue,[key]:value}}:{...d,profiles:{...d.profiles,[group]:{...d.profiles[group],[key]:value}}});
 return <section aria-label="Test execution limits" className="a-settings-section">
  <h3>Test execution limits</h3><p>Choose how long checks may wait and how much each test can use. Changes apply across all workspaces on this installation. Accepted work keeps its saved limits.</p>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {!data?<button className="a-soft" type="button" disabled={busy} onClick={refresh}>Reload execution limits</button>:<>
   <p>Up to {data.effectiveQueue.maxPending} pending checks. {data.effectiveQueue.defaultWaitSeconds===0?'Waiting checks stay queued until stopped or cancelled.':`Checks wait up to ${data.effectiveQueue.defaultWaitSeconds} seconds by default.`}</p>
   <details><summary>Advanced execution limits</summary><p>Leave a field empty to use its built-in default. Queue waiting time of 0 means until Stop or cancellation. Profile limits must be positive whole numbers. Temporary and shared-memory limits apply to Docker; Incus uses its operating system mounts within the total memory limit.</p>
    <fieldset disabled={busy}><legend>Queue</legend>{[['maxPending','Maximum pending checks'],['defaultWaitSeconds','Default queue wait (seconds)']].map(([key,label])=><label className="a-field" key={key}>{label}<input type="number" step="1" min={key==='maxPending'?1:0} max="2147483647" value={draft.queue[key]} placeholder={String(data.effectiveQueue[key])} onChange={e=>change('queue',key,e.target.value)}/></label>)}</fieldset>
    {data.effectiveProfiles.map(profile=><fieldset key={profile.id} disabled={busy}><legend>{profile.label}</legend>{fields.map(([key,label])=><label className="a-field" key={key}>{label}<input aria-label={`${profile.label}: ${label}`} type="number" step="1" min="1" max={key.endsWith('Seconds')?2147483647:1048576} value={draft.profiles[profile.id][key]} placeholder={String(profile[key])} onChange={e=>change(profile.id,key,e.target.value)}/><small>Effective: {profile[key]}</small></label>)}</fieldset>)}
   </details>
   <div className="a-dialog-actions"><button className="a-primary" type="button" disabled={busy||!dirty} onClick={save}>Save execution limits</button><button className="a-soft" type="button" disabled={busy} onClick={refresh}>Discard execution edits and refresh</button></div>
  </>}
 </section>
}
