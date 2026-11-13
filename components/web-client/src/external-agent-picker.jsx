import React,{useEffect,useRef,useState} from 'react';
import {settingsPatch} from './settings-navigation';

export function ExternalAgentPicker({state,act,refreshKey,disabled=false}){
 const selected=state.view?.newSessionDraft?.externalAgentId||'';
 const source=state.client?.hostInstanceId;
 const [catalog,setCatalog]=useState({}),[retry,setRetry]=useState(0),[saving,setSaving]=useState(false),[error,setError]=useState('');
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
 const missing=selected&&!current.agents.some(agent=>agent.id===selected);
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
  <label>Agent <select aria-label="Agent for new chat" value={selected} disabled={disabled||saving||current.loading} onChange={event=>perform('view.update',{patch:{newSessionDraft:{...latest.current.state.view?.newSessionDraft,externalAgentId:event.target.value}}})}>
   <option value="">Amplifier</option>
   {missing&&<option value={selected} disabled>{selected} — {current.loading?'loading':'unavailable'}</option>}
   {current.agents.map(agent=><option key={agent.id} value={agent.id}>{agent.label}</option>)}
  </select></label>
  <button type="button" className="a-link" disabled={disabled||saving} onClick={()=>perform('view.update',{patch:settingsPatch('external-agents')})}>Configure external agents</button>
  {current.loading&&<span className="a-caption" role="status">Loading agents…</span>}
  {(error||current.error)&&<div role="alert">{error||current.error}{current.error&&<button type="button" className="a-link" disabled={disabled||saving} onClick={()=>setRetry(value=>value+1)}>Retry</button>}</div>}
 </div>;
}
