import React,{useEffect,useRef,useState} from 'react';
import {settingsPatch} from './settings-navigation';
import {draftExternalAgentIds} from './external-participants';

export function ExternalAgentPicker({state,act,refreshKey,disabled=false}){
 const selected=draftExternalAgentIds(state.view?.newSessionDraft);
 const source=state.client?.hostInstanceId;
 const [catalog,setCatalog]=useState({}),[retry,setRetry]=useState(0),[saving,setSaving]=useState(false),[error,setError]=useState(''),[adding,setAdding]=useState(false);
 const latest=useRef(),pending=useRef(false),visit=useRef();
 latest.current={state,act};
 const key=JSON.stringify([source,refreshKey,retry]);
 useEffect(()=>{
  let live=true;const current={};visit.current=current;setError('');
  setCatalog({key,loading:true,agents:[]});
  Promise.resolve().then(()=>latest.current.act('externalAgents.list',{})).then(response=>{
   if(response?.accepted!==true||!Array.isArray(response.result?.agents))throw Error(response?.error||'Could not load external agents.');
   if(live)setCatalog({key,agents:response.result.agents});
  }).catch(error=>{if(live)setCatalog({key,agents:[],error:error.message||'Could not load external agents.'})});
  return()=>{live=false;if(visit.current===current)visit.current=null};
 },[key]);
 const current=catalog.key===key?catalog:{loading:true,agents:[]};
 function choose(index,value){
  const draft=latest.current.state.view?.newSessionDraft,ids=[...draftExternalAgentIds(draft)];
  if(value&&ids.some((id,i)=>i!==index&&id===value))return;
  if(index===0&&!value)ids.length=0;
  else if(value)ids[index]=value;
  else ids.splice(index,1);
  setAdding(false);
  const {externalAgentId,externalAgentIds,...rest}=draft||{};
  return perform('view.update',{patch:{newSessionDraft:{...rest,...(ids.length===1?{externalAgentId:ids[0]}:ids.length?{externalAgentIds:ids}:{})}}});
 }
 async function perform(action,args){
  if(disabled||pending.current)return;
  const owner=visit.current;pending.current=true;setSaving(true);setError('');
  try{
   const response=await latest.current.act(action,args);
   if(response?.accepted!==true)throw Error(response?.error||'The change could not be confirmed.');
  }catch(error){if(visit.current===owner)setError(error.message||'The change could not be confirmed.')}
  finally{pending.current=false;if(visit.current)setSaving(false)}
 }
 return <div className="a-external-agent-picker">
  {(selected.length?selected:['']).concat(adding?['']:[]).map((id,index)=><label key={index}>{index===0?'Agent':'With'} <select aria-label={index===0?'Agent for new chat':`Additional agent ${index}`} value={id} disabled={disabled||saving||current.loading} onChange={event=>choose(index,event.target.value)}>
   <option value="">{index===0?'Amplifier':'Remove agent'}</option>
   {id&&!current.agents.some(agent=>agent.id===id)&&<option value={id} disabled>{id} — {current.loading?'loading':'unavailable'}</option>}
   {current.agents.map(agent=><option key={agent.id} value={agent.id} disabled={selected.some((other,i)=>i!==index&&other===agent.id)}>{agent.label}</option>)}
  </select></label>)}
  {selected.length>0&&!adding&&<button type="button" className="a-link" disabled={disabled||saving||current.loading} onClick={()=>{if(!disabled&&!saving)setAdding(true)}}>Add agent</button>}
  {selected.length>1&&<span className="a-caption">First message to {current.agents.find(agent=>agent.id===selected[0])?.label||selected[0]}. You can choose who replies next.</span>}
  <button type="button" className="a-link" disabled={disabled||saving} onClick={()=>perform('view.update',{patch:settingsPatch('external-agents')})}>Configure external agents</button>
  {current.loading&&<span className="a-caption" role="status">Loading agents…</span>}
  {(error||current.error)&&<div role="alert">{error||current.error}{current.error&&<button type="button" className="a-link" disabled={disabled||saving} onClick={()=>setRetry(value=>value+1)}>Retry</button>}</div>}
 </div>;
}
