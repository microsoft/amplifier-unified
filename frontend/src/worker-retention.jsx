import {SettingsActions} from './settings-layout';
import React,{useEffect,useState} from 'react';

export function WorkerRetentionSettings({state,act}){
 const policy=state.runtime?.retention;
 const [count,setCount]=useState('32'),[hours,setHours]=useState('12'),[prepare,setPrepare]=useState(true),[saving,setSaving]=useState(false);
 useEffect(()=>{if(policy){setCount(String(policy.max_warm_workers));setHours(String(policy.idle_timeout_hours));setPrepare(policy.prewarm_on_select)}},[policy?.max_warm_workers,policy?.idle_timeout_hours,policy?.prewarm_on_select]);
 if(!policy)return null;
 const valid=count!==''&&hours!==''&&Number.isInteger(Number(count))&&Number(count)>=0&&Number.isFinite(Number(hours))&&Number(hours)>=0;
 async function save(event){event.preventDefault();if(!valid||saving)return;setSaving(true);try{await act('runtime.retention.update',{patch:{max_warm_workers:Number(count),idle_timeout_hours:Number(hours),prewarm_on_select:prepare}})}finally{setSaving(false)}}
 return <form id="settings-readiness-form" aria-label="Ready conversations" onSubmit={save}>
  <p>Keep recently used conversations ready for their next message. These limits apply to idle conversations on this host; work in progress and pending interactions stay protected.</p>
  <label htmlFor="warm-worker-count">Conversations to keep ready</label><input id="warm-worker-count" type="number" min="0" step="1" value={count} onChange={event=>setCount(event.target.value)}/>
  <label htmlFor="warm-worker-hours">Hours to keep an idle conversation ready</label><input id="warm-worker-hours" type="number" min="0" step="any" value={hours} onChange={event=>setHours(event.target.value)}/>
  <label className="a-inline-checkbox"><input type="checkbox" checked={prepare} onChange={event=>setPrepare(event.target.checked)}/>Prepare a conversation in the background when I select it</label>
  <p className="a-caption">Setting either limit to zero releases idle conversations as soon as they settle and disables background preparation. Saved history remains available.</p>
  <SettingsActions><button form="settings-readiness-form" type="submit" className="a-primary" data-action="runtime.retention.update" disabled={!valid||saving}>{saving?'Saving…':'Save readiness settings'}</button></SettingsActions>
 </form>;
}
