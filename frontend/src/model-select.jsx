import React from 'react';
import {modelOptions} from './setup-data';
import {useListFilter} from './list-filter.jsx';
export function ModelSelect({id,label='Model',value='',onChange,onCommit,entry,models:provided,state,act,disabled=false,placeholder='Enter a model ID'}){
 const models=modelOptions(entry?.models??provided??[]),loading=entry?.phase==='working'||entry?.phase==='queued';
 const [shown,filter]=useListFilter(state,act,'model-picker-'+id,models,row=>[row.id,row.name],'Filter '+label.toLowerCase());
 return <div className="a-model-select">{models.length>12&&filter}{models.length||loading?<select id={id} aria-label={label} value={value} disabled={disabled||loading} data-action="view.update" onChange={e=>{onChange(e.target.value);onCommit?.(e.target.value)}}><option value="">{loading?'Loading provider models…':'Choose a model…'}</option>{value&&!shown.some(row=>row.id===value)&&<option value={value}>{value} (selected)</option>}{shown.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</select>:<input id={id} aria-label={label} value={value} disabled={disabled} placeholder={placeholder} data-action="view.update" onChange={e=>onChange(e.target.value)} onBlur={e=>onCommit?.(e.target.value)}/ >}{entry?.phase==='error'&&<small className="a-danger" role="status">{entry.error||'Model list unavailable. Enter a model ID or refresh this provider.'}</small>}{entry?.phase==='ready'&&!models.length&&<small>{entry.supported===false?'This provider does not supply a model list.':'This provider returned no models.'} Enter a model ID.</small>}</div>;
}
