import React,{useRef} from 'react';
import {useRegionActivity} from './activity-region';
import {modelOptions} from './setup-data';
import {useListFilter} from './list-filter.jsx';

// Only the host's exact connection entry supplies options. Saved/default IDs
// are displayed separately and never promoted into discovery results.
export function ModelSelect({id,label='Model',value='',onChange,onCommit,entry,models:provided,state,act,disabled=false,placeholder='Enter an exact model or deployment ID',editable=false}){
 const loading=entry?.phase==='working'||entry?.phase==='queued',region=useRef(null);
 const models=modelOptions(entry?.models??provided??[]);
 const manual=editable||entry?.supported===false||entry?.phase==='error'||(!loading&&!models.length);
 const listed=models.some(row=>row.id===value);
 useRegionActivity(region,loading);
 const [shown,filter]=useListFilter(state,act,'model-picker-'+id,models,row=>[row.id,row.name],'Search '+label.toLowerCase(),{simple:true});
 return <div className="a-model-select" ref={region} data-activity-region="models">
  {value&&!listed&&<small role="status">Saved selection: {value} (not in the discovered list)</small>}
  {models.length>0&&<>{filter}<select id={manual?id+'-catalog':id} aria-label={manual?'Choose '+label.toLowerCase()+' from catalog':label} value={shown.some(row=>row.id===value)?value:''} disabled={disabled} data-action="view.update" onChange={e=>{if(e.target.value){onChange(e.target.value);onCommit?.(e.target.value)}}}>
   <option value="">Choose a discovered model…</option>{shown.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}
  </select></>}
  {manual?<><label htmlFor={id}>Exact model ID</label><input id={id} aria-label={label} value={value} disabled={disabled} placeholder={placeholder} data-action="view.update" onChange={e=>onChange(e.target.value)} onBlur={e=>{if(e.target.value.trim())onCommit?.(e.target.value.trim())}} onKeyDown={e=>{if(e.key==='Enter'&&e.currentTarget.value.trim()){e.preventDefault();onCommit?.(e.currentTarget.value.trim())}}}/><small>Manual ID — not verified by discovery. The connection checks it when you send.</small></>:!models.length&&<small role="status">Loading provider models…</small>}
  {entry?.error&&<small className="a-danger" role="status">{entry.error}</small>}
  {entry?.phase==='error'&&!entry.error&&<small className="a-danger" role="status">Model list unavailable. Enter a model ID or refresh this connection.</small>}
  {entry?.loadedAt!=null&&<small>Last successful refresh: {new Date(entry.loadedAt*1000).toLocaleString()}</small>}
  {entry?.phase==='ready'&&!models.length&&<small>{entry.supported===false?'This connection does not supply verified model discovery.':'This connection returned no models.'}</small>}
 </div>;
}
