import React,{useRef} from 'react';
import {useRegionActivity} from './activity-region';
import {modelOptions} from './setup-data';
import {useListFilter} from './list-filter.jsx';
export function ModelSelect({id,label='Model',value='',onChange,onCommit,entry,models:provided,state,act,disabled=false,placeholder='Enter a model ID',catalogKey=id,editable=false}){
 const loading=entry?.phase==='working'||entry?.phase==='queued',previous=useRef(null),region=useRef(null);
 let models=modelOptions(entry?.models??provided??[]);
 if(!editable&&(loading||entry?.phase==='error')&&!models.length&&previous.current?.key===catalogKey)models=previous.current.models;
 if(!loading&&entry?.phase!=='error')previous.current={key:catalogKey,models};
 useRegionActivity(region,loading);
 const [shown,filter]=useListFilter(state,act,'model-picker-'+id,models,row=>[row.id,row.name],'Filter '+label.toLowerCase());
 if(!editable)return <div className="a-model-select" ref={region} data-activity-region="models">{models.length>12&&filter}{entry?.phase!=='error'&&(models.length||loading)?<select id={id} aria-label={label} value={value} disabled={disabled||loading&&!models.length} data-action="view.update" onChange={e=>{onChange(e.target.value);onCommit?.(e.target.value)}}><option value="">{loading?'Loading provider models…':'Choose a model…'}</option>{value&&!shown.some(row=>row.id===value)&&<option value={value}>{value} (selected)</option>}{shown.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</select>:<><input list={models.length?id+'-options':undefined} id={id} aria-label={label} value={value} disabled={disabled} placeholder={placeholder} data-action="view.update" onChange={e=>onChange(e.target.value)} onBlur={e=>onCommit?.(e.target.value)}/>{models.length>0&&<datalist id={id+'-options'}>{shown.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</datalist>}</>}{entry?.phase==='error'&&<small className="a-danger" role="status">{entry.error||'Model list unavailable. Enter a model ID or refresh this provider.'}</small>}{entry?.phase==='ready'&&!models.length&&<small>{entry.supported===false?'This provider does not supply a model list.':'This provider returned no models.'} Enter a model ID.</small>}</div>;
 return <div className="a-model-select" ref={region} data-activity-region="models">
  <input list={models.length?id+'-options':undefined} id={id} aria-label={label} value={value} disabled={disabled} placeholder={placeholder} data-action="view.update" onChange={e=>onChange(e.target.value)} onBlur={e=>onCommit?.(e.target.value)}/>
  {models.length>0&&<><datalist id={id+'-options'}>{models.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</datalist>{models.length>12&&filter}<select id={id+'-catalog'} aria-label={'Choose '+label.toLowerCase()+' from catalog'} value={models.some(row=>row.id===value)?value:''} disabled={disabled} data-action="view.update" onChange={e=>{if(e.target.value){onChange(e.target.value);onCommit?.(e.target.value);}}}><option value="">Choose from {models.length} cached models…</option>{shown.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</select></>}
  {loading&&<small role="status">{models.length?'Refreshing in the background. Saved models are available.':'Discovering models. You can enter a model ID now.'}</small>}
  {entry?.phase==='error'&&<small className="a-danger" role="status">{entry.error||'Model list unavailable. Enter a model ID or refresh this provider.'}</small>}
  {entry?.loadedAt&&<small>Catalog checked {new Date(entry.loadedAt*1000).toLocaleString()}</small>}
  {entry?.phase==='ready'&&!models.length&&<small>{entry.supported===false?'This provider does not supply a model list.':'This provider returned no models.'} Enter a model ID.</small>}
 </div>;
}
