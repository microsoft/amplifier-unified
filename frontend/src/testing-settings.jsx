import React,{useEffect,useRef,useState} from 'react';
import {useSettingsDraft} from './settings-drafts';
import {SettingsActions} from './settings-layout';

const gib=1024**3;
const fields=[['maxRunning','Running test environments',1],['maxInstances','Running and retained environments',1],['minFreeBytes','Minimum free disk space (GiB)',gib]];
const sourceNames={global:'Global setting',runner:'Runner override',configured:'Runner configuration','backend-default':'Backend default'};
const same=(a,b)=>JSON.stringify(Object.entries(a).sort())===JSON.stringify(Object.entries(b).sort());
const inputs=values=>Object.fromEntries(fields.map(([key,,unit])=>[key,values[key]===undefined?'':String(values[key]/unit)]));
function values(draft){
 const result={};
 for(const [key,,unit] of fields){
  if(draft[key]==='')continue;
  const value=Number(draft[key])*unit;
  if(!Number.isFinite(value)||value<0||!Number.isSafeInteger(value))throw Error('Enter whole environment counts and a nonnegative disk size.');
  result[key]=value;
 }
 return result;
}
function Limits({label,draft,setDraft,disabled}){
 return <fieldset disabled={disabled}><legend>{label}</legend>{fields.map(([key,title,unit])=><label className="a-field" key={key}>{title}
  <input type="number" min="0" step={unit===1?'1':'any'} inputMode={unit===1?'numeric':'decimal'} value={draft[key]} placeholder="Inherit" onChange={event=>setDraft(old=>({...old,[key]:event.target.value}))}/>
 </label>)}</fieldset>;
}
export function TestingSettings({act,navigate}){
 const [data,setData]=useState(null),[defaults,setDefaults]=useState(inputs({})),[overrides,setOverrides]=useState(inputs({})),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const pending=useRef(false),action=useRef(act);action.current=act;
 const dirty=!!data&&(JSON.stringify(defaults)!==JSON.stringify(inputs(data.defaults))||JSON.stringify(overrides)!==JSON.stringify(inputs(data.runnerOverrides)));
 function accept(result){setData(result);setDefaults(inputs(result.defaults));setOverrides(inputs(result.runnerOverrides))}
 useSettingsDraft('testing-capacity',{label:'Testing & resources',dirty,review:()=>navigate('testing'),discard:()=>{if(data)accept(data)}});
 useEffect(()=>{let live=true;action.current('testing.capacity.read',{}).then(response=>{
  if(!response?.accepted||!response.result)throw Error(response?.error||'Testing settings could not be read.');
  if(live)accept(response.result);
 }).catch(e=>{if(live)setError(e.message)});return()=>{live=false}},[]);
 async function save(){
  if(pending.current)return;
  pending.current=true;setBusy(true);setError('');setNotice('');
  let intended;
  try{
   intended={expectedRevision:data.revision,defaults:values(defaults),...(data.runnerId?{runnerId:data.runnerId,runnerOverrides:values(overrides)}:{})};
   const response=await action.current('testing.capacity.save',intended);
   if(!response?.accepted||!response.result)throw Error(response?.error||'Testing settings could not be saved.');
   accept(response.result);setNotice('Testing limits saved. Waiting checks will reconsider capacity. Work already admitted keeps running.');
  }catch(e){
   // A lost acknowledgement is not evidence of failure. Read the saved
   // values once; never replay the mutation to discover its outcome.
   let confirmed=false;
   if(intended)try{
    const response=await action.current('testing.capacity.read',{}),current=response?.result;
    if(response?.accepted&&current&&same(current.defaults,intended.defaults)&&(!intended.runnerId||(current.runnerId===intended.runnerId&&same(current.runnerOverrides,intended.runnerOverrides)))){
     accept(current);setNotice('Current testing settings match your changes. No settings were replayed.');confirmed=true;
    }
   }catch{}
   if(!confirmed)setError(e.message+' Your edits are kept. Refresh to review the current saved values.');
  }finally{pending.current=false;setBusy(false)}
 }
 async function refresh(){
  if(pending.current)return;
  pending.current=true;setBusy(true);setError('');
  try{
   const response=await action.current('testing.capacity.read',{});
   if(!response?.accepted||!response.result)throw Error(response?.error||'Testing settings could not be refreshed.');
   // Refresh updates the comparison token but never silently rebases an edited
   // runner form onto a different connection.
   accept(response.result);setNotice('Current settings loaded.');
  }catch(e){setError(e.message)}finally{pending.current=false;setBusy(false)}
 }
 return <section aria-label="Testing and resources">
  <p>Choose how much isolated testing Amplifier can run across your workspaces. Higher limits allow more work at once when the machine has enough resources.</p>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {!data?<button className="a-soft" disabled={busy} onClick={refresh}>Reload testing settings</button>:<>
   <p>{data.scope}</p>
   <h4>{data.runner.backend?`${data.runner.backend==='incus'?'Incus':'Docker'} test runner`:'Test runner not ready'}</h4>
   <p>{data.runner.description}</p>
   <dl>{fields.map(([key,title,unit])=><React.Fragment key={key}><dt>{title}</dt><dd>{data.effective[key]===0?'No limit':data.effective[key]/unit} <small>· {sourceNames[data.sources[key]]||'Global default'}</small></dd></React.Fragment>)}</dl>
   <details><summary>Advanced limits</summary>
    <p>Zero disables a limit. Leave a field empty to inherit the runner configuration or backend default. Lowering a limit prevents new admissions until there is room; it does not stop active work.</p>
    <Limits label="Global defaults" draft={defaults} setDraft={setDefaults} disabled={busy}/>
    {data.runnerId&&<details><summary>Overrides for this runner connection</summary>
     <p>Optional values for this configured connection only. Empty fields use the global defaults. A different connection, including an alias, has its own override; these are not machine-wide settings.</p>
     <Limits label="Runner overrides" draft={overrides} setDraft={setOverrides} disabled={busy}/>
    </details>}
    <p className="a-caption">{data.limitsScope} CPU and memory budgets, automatic machine selection and queue limits are not controlled here.</p>
   </details>
   <SettingsActions><button className="a-primary" disabled={busy||!dirty} onClick={save}>Save testing limits</button><button className="a-soft" disabled={busy} onClick={refresh}>{dirty?'Discard changes and reload':'Refresh settings'}</button></SettingsActions>
  </>}
 </section>;
}
