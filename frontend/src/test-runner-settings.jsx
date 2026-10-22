import React,{useEffect,useRef,useState} from 'react';
import {useSettingsDraft} from './settings-drafts';
import {TestRunnerBackendLimits} from './test-runner-backend-limits';
import {MachineResources} from './testing-settings';

export function TestRunnerSettings({act,navigate}){
 const [data,setData]=useState(null),[name,setName]=useState(''),[connection,setConnection]=useState(''),[configPath,setConfigPath]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const pending=useRef(false),action=useRef(act);action.current=act;
 const dirty=!!(name||connection||configPath);
 const clear=()=>{setName('');setConnection('');setConfigPath('')};
 useSettingsDraft('testing-runners',{label:'Connected test machines',dirty,review:()=>navigate('testing'),discard:clear});
 async function load(){const response=await action.current('testing.runners.read',{});if(!response?.accepted||!response.result)throw Error(response?.error||'Test machines could not be read.');return response.result}
 useEffect(()=>{let live=true;load().then(result=>{if(live)setData(result)}).catch(e=>{if(live)setError(e.message)});return()=>{live=false}},[]);
 async function refresh(){if(pending.current)return;pending.current=true;setBusy(true);setError('');try{setData(await load())}catch(e){setError(e.message)}finally{pending.current=false;setBusy(false)}}
 async function upgrade(runner){
  if(pending.current)return;pending.current=true;setBusy(true);setError('');setNotice('');
  try{const response=await action.current('testing.runners.refresh',{id:runner.id,expectedRevision:data.revision});if(!response?.accepted||!response.result)throw Error(response?.error||'Controller refresh was not confirmed. Refresh the list before trying again.');setData(response.result);setNotice('Test controller refreshed for future checks. Existing checks keep their original controller.')}catch(e){setError(e.message)}finally{pending.current=false;setBusy(false)}
 }
 async function save(runner){
  if(pending.current)return;pending.current=true;setBusy(true);setError('');setNotice('');
  const desired=runner?{id:runner.id,enabled:!runner.enabled}:{name:name.trim(),connection:connection.trim(),configPath:configPath.trim()};
  try{
   const response=await action.current(runner?'testing.runners.enable':'testing.runners.register',{...desired,expectedRevision:data.revision});
   if(!response?.accepted||!response.result)throw Error(response?.error||'The test machine change was not confirmed.');
   setData(response.result);if(!runner)clear();setNotice(runner?'Test machine preference saved. Existing checks keep their original machine.':'Test machine connected. New checks can use it when there is room.');
  }catch(e){
   let confirmed=false;try{const current=await load();setData(current);confirmed=current.runners.some(item=>runner?item.id===desired.id&&item.enabled===desired.enabled:item.name===desired.name&&item.connection===desired.connection&&item.configPath===desired.configPath);if(confirmed){if(!runner)clear();setNotice('The saved test machine matches your change. No setup was repeated.')}}catch{}
   if(!confirmed)setError(e.message+' Your entries are kept.');
  }finally{pending.current=false;setBusy(false)}
 }
 return <section aria-label="Connected test machines"><h4>Connected test machines</h4><p>Run checks on a Linux test machine from Mac, Windows, WSL or Linux. This computer needs Git and an existing SSH connection; it does not need Docker or a local test environment. Each check keeps its source, results and original machine together.</p>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {data&&<><p className="a-caption">{data.scope}</p>{data.runners.length===0?<p>No test machines connected. Connect one to start testing remotely.</p>:data.runners.map(runner=><details key={runner.id}><summary>{runner.name} · {runner.enabled?'Available for new checks':'Paused for new checks'}</summary><p>Connection: {runner.connection}</p><button className="a-soft" disabled={busy} onClick={()=>save(runner)}>{runner.enabled?'Pause new checks':'Allow new checks'}</button><button className="a-soft" disabled={busy} onClick={()=>upgrade(runner)}>Refresh test controller</button><MachineResources act={(operation,args)=>act(operation.replace('testing.machine.','testing.runners.machine.'),{...args,id:runner.id})} navigate={navigate} draftId={'testing-machine-'+runner.id} title={runner.name+' test resources'}/><TestRunnerBackendLimits act={act} navigate={navigate} id={runner.id} name={runner.name}/></details>)}
  <details><summary>Connect a test machine</summary><p>Choose a machine with testing already set up. Its SSH connection must already work without an interactive sign-in and its host identity must be trusted. Advanced setup: the destination uses a native Linux Incus runner. Amplifier installs its small, verified controller in that account’s private storage. It does not install a container service or change system settings.</p>
   <fieldset disabled={busy}><legend>Test machine connection</legend><label className="a-field">Name to show<input value={name} maxLength={80} onChange={event=>setName(event.target.value)}/></label><label className="a-field">Saved SSH connection<input value={connection} maxLength={200} placeholder="test-machine" onChange={event=>setConnection(event.target.value)}/></label><details><summary>Advanced connection settings</summary><label className="a-field">Existing runner configuration file<input value={configPath} maxLength={4096} placeholder="Use the machine’s default Amplifier configuration" onChange={event=>setConfigPath(event.target.value)}/></label></details></fieldset>
   <button className="a-primary" disabled={busy||!name.trim()||!connection.trim()} onClick={()=>save(null)}>Connect test machine</button>
  </details></>}
  <button className="a-soft" disabled={busy} onClick={refresh}>Refresh test machines</button>
 </section>;
}
