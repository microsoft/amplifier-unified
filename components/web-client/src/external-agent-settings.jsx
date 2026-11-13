import React,{useEffect,useRef,useState} from 'react';
import {useSettingsDraft} from './settings-drafts';
import {SettingsActions} from './settings-layout';

const blank=()=>({label:'',command:'',arguments:'',cwd:'',sessionCwd:''});
const editor=agent=>({...blank(),...agent,arguments:(agent.args||[]).join('\n')});
function configuration(draft,saved){
 const {id,label,command,cwd,sessionCwd}=draft;
 // Preserve exact launch arguments when editing only the name or directories.
 // A valid saved argument can itself contain a newline or be an empty string.
 const args=draft.arguments===saved?.arguments&&saved.args?saved.args:draft.arguments===''?[]:draft.arguments.split('\n').map(value=>value.replace(/\r$/,''));
 return {...(id?{id}:{}),label:label.trim(),command,args,...(cwd?{cwd}:{}),...(sessionCwd?{sessionCwd}:{})};
}

export function ExternalAgentSettings({act,navigate=()=>{}}){
 const [agents,setAgents]=useState(null),[draft,setDraft]=useState(null),[saved,setSaved]=useState(null),[revision,setRevision]=useState(null);
 const [busy,setBusy]=useState(false),[pending,setPending]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const alive=useRef(true),operation=useRef(false);
 const request=async(name,args={},meta={})=>{
  const response=await act(name,args,{feedback:false,...meta});
  if(!response?.accepted)throw Object.assign(Error(response?.error||'The request was not confirmed.'),{status:response?.status});
  return response.result;
 };
 const run=async(work)=>{
  if(operation.current)return;operation.current=true;setBusy(true);setError('');setNotice('');
  try{await work()}catch(e){if(alive.current)setError(e.message)}finally{operation.current=false;if(alive.current)setBusy(false)}
 };
 const load=()=>run(async()=>{const result=await request('externalAgents.list');if(alive.current)setAgents(result.agents)});
 useEffect(()=>{alive.current=true;load();return()=>{alive.current=false}},[]);
 const discard=()=>{setDraft(null);setSaved(null);setRevision(null);setPending(null);setError('')};
 const dirty=!!draft&&JSON.stringify(draft)!==JSON.stringify(saved);
 useSettingsDraft('external-agents',{label:'External agent connection',dirty:dirty||!!pending,review:()=>navigate('external-agents'),discard});
 const open=id=>run(async()=>{
  const result=await request('externalAgents.get',{id});
  if(alive.current){const value=editor(result.agent);setDraft(value);setSaved(value);setRevision(result.revision)}
 });
 const change=(key,value)=>{setDraft(current=>({...current,[key]:value}));setNotice('')};
 const mutate=kind=>run(async()=>{
  const command=pending||{id:crypto.randomUUID(),name:'externalAgents.'+kind,args:kind==='remove'?{id:draft.id,expectedRevision:revision}:{agent:configuration(draft,saved),...(draft.id?{expectedRevision:revision}:{})}};
  setPending(command);
  try{
   const result=await request(command.name,command.args,{id:command.id});
   if(alive.current){setAgents(result.agents);discard();setNotice(command.name.endsWith('remove')?'Connection removed. Saved chats are kept.':'Connection saved. Choose it when starting a new chat.')}
  }catch(e){if(alive.current&&e.status>=400&&e.status<500&&e.status!==408)setPending(null);throw e}
 });
 const valid=!!draft?.label.trim()&&!!draft?.command.trim();
 return <section className="a-settings-section" aria-label="External agents">
  <p>Connect agents that speak ACP, including Constellation instances. Choose an agent when you start a chat; its model and tools are configured in that agent.</p>
  {!draft?<>
   {agents===null?<p role="status">{busy?'Loading connections…':'Connections could not be loaded.'}</p>:agents.length?<ul className="a-external-agent-list">{agents.map(agent=><li key={agent.id}><span>{agent.label}</span><button type="button" className="a-soft" disabled={busy} onClick={()=>open(agent.id)} aria-label={'Edit '+agent.label}>Edit</button></li>)}</ul>:<p>No external agents configured yet.</p>}
   <SettingsActions><button type="button" className="a-primary" disabled={busy||agents===null} onClick={()=>{setDraft(blank());setSaved(blank());setRevision(null);setError('');setNotice('')}}>Add external agent</button><button type="button" className="a-soft" disabled={busy} onClick={load}>Refresh connections</button></SettingsActions>
  </>:<>
   <h4>{draft.id?'Edit connection':'Add external agent'}</h4>
   <fieldset className="a-external-agent-fields" disabled={busy||!!pending}>
    <label htmlFor="external-agent-name">Name</label><input id="external-agent-name" value={draft.label} onChange={e=>change('label',e.target.value)} placeholder="My Constellation agent" autoComplete="off"/>
    <label htmlFor="external-agent-command">Command</label><input id="external-agent-command" value={draft.command} onChange={e=>change('command',e.target.value)} placeholder="/path/to/agent-instance" autoComplete="off" spellCheck={false}/>
    <p className="a-caption">The executable on this Amplifier host that starts the agent’s ACP connection. Saving does not install or start it.</p>
    <label htmlFor="external-agent-arguments">Arguments — one per line</label><textarea id="external-agent-arguments" rows={5} value={draft.arguments} onChange={e=>change('arguments',e.target.value)} placeholder={'acp\n--home\n/path/to/instance\n--token-file\n/path/to/token'} autoComplete="off" spellCheck={false}/>
    <p className="a-caption">Use individual arguments without shell quotes. For credentials, use the agent’s token-file option or existing configuration.</p>
    <details><summary>Working directories</summary>
     <label htmlFor="external-agent-cwd">Launch directory (optional)</label><input id="external-agent-cwd" value={draft.cwd} onChange={e=>change('cwd',e.target.value)} autoComplete="off" spellCheck={false}/>
     <p className="a-caption">Leave blank to inherit the app’s launch directory.</p>
     <label htmlFor="external-agent-session-cwd">Agent workspace (optional)</label><input id="external-agent-session-cwd" value={draft.sessionCwd} onChange={e=>change('sessionCwd',e.target.value)} autoComplete="off" spellCheck={false}/>
     <p className="a-caption">Leave the agent workspace blank to use this chat’s workspace. For a remote instance, it must exist on that instance.</p>
    </details>
   </fieldset>
   {draft.id&&<p className="a-caption">Changing the command, arguments, or directories requires a new chat. Existing chats keep their original connection binding. Removing this connection keeps saved history.</p>}
   <SettingsActions>
    <button type="button" className="a-primary" disabled={busy||(!pending&&(!valid||!dirty))} onClick={()=>mutate('save')}>{pending?'Check the same change':'Save connection'}</button>
    <button type="button" className="a-soft" disabled={busy||!!pending} onClick={discard}>Cancel</button>
    {draft.id&&<button type="button" className="a-soft a-danger" disabled={busy||!!pending||dirty} onClick={()=>mutate('remove')}>Remove connection</button>}
    {draft.id&&!pending&&<button type="button" className="a-link" disabled={busy} onClick={()=>open(draft.id)}>Discard edits and reload</button>}
   </SettingsActions>
   {pending&&<p role="status">The change is not confirmed. Check the same change to learn its outcome. Keep Settings open until it is confirmed.</p>}
  </>}
  {notice&&<p role="status">{notice}</p>}{error&&<p role="alert">{error}</p>}
 </section>;
}
